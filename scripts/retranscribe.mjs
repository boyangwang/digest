#!/usr/bin/env node
// Re-run transcription for an audio attachment that is already in the vault, drop
// the recovered words into the note that embeds it — replacing the
// "[Transcription unavailable…]" marker line — and regenerate that note's title and
// generated tags from the now-complete text.
//
// This is the recovery half of the durability story: `ingestVoice` always saves the
// audio, so a failed transcript is never lost data, only deferred work. This is the
// thing that does the deferred work, without hand-editing the vault.
//
//   npm run retranscribe -- --check                 # FREE census: what is eligible, what is refused
//   npm run retranscribe -- <attachment.ogg|path>   # recover one
//   npm run retranscribe -- --all                   # recover every eligible note
//   npm run retranscribe -- --all --dry-run         # preview; NOT free, see below
//   npm run retranscribe -- --all --rename          # …and rename the file on disk too
//
// ELIGIBILITY IS A HARD PRECONDITION, checked before a note is read for rewriting
// and before any STT or LLM call is made for it. The digest folder is NOT all
// bot-produced notes: most of it is hand-written, correctly carries no frontmatter,
// and must stay that way. A note qualifies ONLY if it carries this bot's
// failed-transcript marker — the `> [Transcription unavailable…` blockquote line
// directly under an ATTACHMENTS embed — AND already has frontmatter. Everything
// else is refused with a reason and ZERO bytes changed: no frontmatter is ever
// created, no un-marked note is rewritten however stale its metadata looks, and no
// legacy-schema note is migrated as a side effect. Naming an attachment explicitly
// does not bypass the gate.
//
// Why regenerate the metadata for an eligible note: `buildLLMInput` feeds every
// block to the title model together at /done time, substituting "(no transcript)"
// for the failed one. Title and tags are therefore generated ONCE, from text that
// never included these words. Recovering the transcript without redoing that pass
// leaves the frontmatter contradicting the body. The rewrite MERGES: TITLE标题 and
// the generated tag keys are replaced, CREATEDAT and every other pre-existing key
// (a hand-added `aliases`, `cssclasses`, Dataview field…) are left alone.
//
// It goes through the SAME src/stt.js retry+rotation loop the bot uses, so running
// it is also a live check that the loop works.
import { promises as fs } from "node:fs";
import { join, basename, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { parseDocument } from "yaml";
import { DIGEST_DIR, ATTACHMENTS_DIR, ATTACHMENTS_VAULT_PREFIX } from "../src/config.js";
import { blockquote } from "../src/compile.js";
import { buildLLMInput, freeNotePath } from "../src/finalize.js";
import { generateTitleAndTags } from "../src/llm.js";
import { glueTitle, sanitizeTitle } from "../src/util.js";
import { transcribe } from "../src/stt.js";

// Matches both the original bare marker and the richer one compile.js writes now.
// Older notes carry the bare form, so both must count as eligible.
const MARKER = /^>\s*\[Transcription unavailable/;

const EMBED_ANY = new RegExp(`!\\[\\[${ATTACHMENTS_VAULT_PREFIX}/([^\\]]+)\\]\\]`);

/** Absolute path to the audio: an explicit path if given, else the vault ATTACHMENTS dir. */
function audioPath(nameOrPath, attachmentsDir) {
  return nameOrPath.includes("/") || isAbsolute(nameOrPath)
    ? nameOrPath
    : join(attachmentsDir, nameOrPath);
}

/** Split a compiled note into `---` frontmatter and body. Null when it has none. */
export function splitNote(markdown) {
  const m = markdown.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) return null;
  return { frontmatterRaw: m[1], body: markdown.slice(m[0].length) };
}

/** Attachment names whose embed is directly followed by this bot's failure marker. */
export function markedAttachments(markdown) {
  const lines = markdown.split("\n");
  const names = [];
  for (let i = 1; i < lines.length; i++) {
    if (!MARKER.test(lines[i])) continue;
    const m = lines[i - 1].match(EMBED_ANY);
    if (m && !names.includes(m[1])) names.push(m[1]);
  }
  return names;
}

export const REFUSAL = {
  NO_MARKER: "carries no failed-transcript marker — not a note this tool may touch",
  NO_FRONTMATTER:
    "no frontmatter — a hand-written note; this tool never creates frontmatter, not even CREATEDAT",
};

/**
 * The gate. Nothing about a note is read for rewriting, and no vendor is called for
 * it, until this returns eligible.
 * @returns {{eligible:boolean, reason:?string, marked:string[]}}
 */
export function classifyNote(markdown) {
  const marked = markedAttachments(markdown);
  if (!marked.length) return { eligible: false, reason: REFUSAL.NO_MARKER, marked };
  if (!splitNote(markdown)) return { eligible: false, reason: REFUSAL.NO_FRONTMATTER, marked };
  return { eligible: true, reason: null, marked };
}

/**
 * Replace the failure marker directly under `![[…/<name>]]` with `transcript`.
 * Touches ONLY the marker LINE — a blockquote line a human appended underneath it
 * survives, because these are live, hand-editable notes, not generated files.
 * @returns {{markdown:string, replaced:number}}
 */
export function replaceMarker(markdown, name, transcript) {
  const embed = `![[${ATTACHMENTS_VAULT_PREFIX}/${name}]]`;
  const lines = markdown.split("\n");
  const out = [];
  let replaced = 0;
  for (let i = 0; i < lines.length; i++) {
    out.push(lines[i]);
    if (!lines[i].includes(embed)) continue;
    if (!(i + 1 < lines.length && MARKER.test(lines[i + 1]))) continue;
    out.push(blockquote(transcript.trim()));
    replaced++;
    i += 1; // consume the marker line, and nothing beyond it
  }
  return { markdown: out.join("\n"), replaced };
}

// --- Reading a compiled note back into block-equivalents ---------------------
// compileNote renders a stable shape: every block opens with an inline
// `**MM-DD HH:MM** ` timestamp and nothing else does, so that prefix is the only
// reliable block boundary (a text block may itself contain blank lines).

const TS_PREFIX = /^\*\*\d{2}-\d{2} \d{2}:\d{2}\*\* ?/;
const EMBED_LINE = new RegExp(`^!\\[\\[${ATTACHMENTS_VAULT_PREFIX}/([^\\]]+)\\]\\]\\s*$`);
const IMAGE_EXT = /\.(jpe?g|png|webp|gif|heic|heif|bmp|tiff?|avif)$/i;
const AUDIO_EXT = /\.(ogg|oga|opus|mp3|m4a|mp4|aac|wav|flac|webm)$/i;

/** Reverse of compile.js's `blockquote()`. */
function unquote(lines) {
  return lines
    .map((l) => l.replace(/^>\s?/, ""))
    .join("\n")
    .trim();
}

/**
 * Parse a compiled body back into the block shapes `buildLLMInput` consumes, so the
 * recovery pass feeds the title model the same string the original compile would
 * have, had the transcript been there.
 *
 * The attachment's EXTENSION decides the block type, not the blockquote shape: an
 * image or file whose user caption happens to start with `>` (a forwarded quote, a
 * Markdown callout) would otherwise be read as speech.
 *
 * One thing genuinely cannot be recovered: an image's `visionCaption` is never
 * rendered into the note, so images fall back to their user caption. Callers say so.
 */
export function parseNoteBlocks(body) {
  const chunks = [];
  for (const line of body.split("\n")) {
    if (TS_PREFIX.test(line) || chunks.length === 0) chunks.push([line]);
    else chunks[chunks.length - 1].push(line);
  }

  const blocks = [];
  for (const chunk of chunks) {
    const head = chunk[0].replace(TS_PREFIX, "");
    const rest = chunk.slice(1);
    const embed = head.match(EMBED_LINE);
    if (!embed) {
      const text = [head, ...rest].join("\n").trim();
      if (text) blocks.push({ type: "text", text });
      continue;
    }
    const attachment = embed[1];
    if (AUDIO_EXT.test(attachment)) {
      const raw = unquote(rest.filter((l) => l.startsWith(">")));
      // A block still carrying the marker has no transcript to offer.
      blocks.push({
        type: "voice",
        attachment,
        transcript: !raw || MARKER.test(`> ${raw}`) ? null : raw,
      });
      continue;
    }
    blocks.push({
      type: IMAGE_EXT.test(attachment) ? "image" : "file",
      attachment,
      userCaption: rest.join("\n").trim(),
    });
  }
  return blocks;
}

/**
 * Merge a fresh title + generated tags into a note's EXISTING frontmatter.
 *
 * Merge, never rebuild: these are live Obsidian notes, so a key this tool did not
 * generate is none of its business. CREATEDAT is never written — only preserved if
 * it is already there — and a tag named CREATEDAT is ignored rather than allowed to
 * overwrite the note's identity timestamp. `replaceAll` opts into the old
 * rebuild-from-scratch behaviour, which is the only way a key gets removed.
 *
 * The YAML is edited through the document model so untouched keys keep their
 * original formatting instead of being re-serialized.
 *
 * @returns {?{markdown:string, replaced:string[], added:string[], leftAlone:string[], removed:string[]}}
 */
export function rewriteFrontmatter(markdown, fullTitle, tags, { replaceAll = false } = {}) {
  const parts = splitNote(markdown);
  if (!parts) return null;

  const clean = (tags || [])
    .filter((t) => t && t.key)
    .map((t) => ({ key: String(t.key).trim(), value: String(t.value ?? "").trim() }))
    .filter((t) => t.key && t.key !== "CREATEDAT");

  const doc = parseDocument(parts.frontmatterRaw);
  const before = doc.toJS() || {};
  const oldKeys = Object.keys(before);
  const writes = [["TITLE标题", fullTitle], ...clean.map((t) => [t.key, t.value])];

  let out;
  if (replaceAll) {
    out = parseDocument("");
    out.contents = out.createNode({});
    if (before.CREATEDAT != null) out.set("CREATEDAT", before.CREATEDAT);
    for (const [k, v] of writes) if (!out.has(k)) out.set(k, v);
  } else {
    out = doc;
    for (const [k, v] of writes) out.set(k, v);
  }

  const written = new Set(writes.map(([k]) => k));
  const after = out.toJS() || {};
  const replaced = oldKeys.filter((k) => written.has(k) && before[k] !== after[k]);
  const added = writes.map(([k]) => k).filter((k) => !oldKeys.includes(k));
  const leftAlone = oldKeys.filter((k) => k in after && !replaced.includes(k));
  const removed = oldKeys.filter((k) => !(k in after));

  const yaml = out.toString({ lineWidth: 0 }).trimEnd();
  return { markdown: `---\n${yaml}\n---\n${parts.body}`, replaced, added, leftAlone, removed };
}

const atomicWrite = async (path, text) => {
  const tmp = `${path}.tmp`; // atomic, same as every other vault write
  await fs.writeFile(tmp, text, "utf8");
  await fs.rename(tmp, path);
};

/**
 * Re-run the whole title/tag pass over the now-complete note. Best-effort by
 * design: the recovered transcript is already committed to disk before this runs,
 * and is never rolled back because the metadata step failed.
 * @returns {Promise<string>} the note's (possibly new) path
 */
async function regenerateMetadata(notePath, markdown, opts) {
  const { dryRun, rename, replaceProperties, digestDir, titleImpl, out } = opts;
  const label = basename(notePath);
  const parts = splitNote(markdown);
  const stale = (why) =>
    out.error(`  ! ${label}: ${why} — transcript saved, TITLE标题/tags left STALE (they predate it)`);

  if (!parts) {
    stale("no frontmatter");
    return notePath;
  }

  const blocks = parseNoteBlocks(parts.body);
  const llmInput = buildLLMInput(blocks);
  if (!llmInput.trim()) {
    stale("nothing to title");
    return notePath;
  }
  if (blocks.some((b) => b.type === "image")) {
    out.log(`  · ${label}: image vision captions are not stored in the note, so the new title sees only their user captions`);
  }

  const { title, tags, fallback } = await titleImpl(llmInput);
  // The fallback title is the input's first line. Adopting it would REPLACE a good
  // title with a worse one, so keep what is already there and say so.
  if (fallback) {
    stale("title model unavailable (no key, or the request failed)");
    return notePath;
  }

  const fullTitle = glueTitle(title.zh, title.en);
  const rewritten = rewriteFrontmatter(markdown, fullTitle, tags, { replaceAll: replaceProperties });
  if (!rewritten) {
    stale("could not rewrite frontmatter");
    return notePath;
  }

  out.log(`  ✓ new title: ${fullTitle}`);
  const list = (ks) => (ks.length ? ks.join(", ") : "(none)");
  out.log(`    properties replaced: ${list(rewritten.replaced)}`);
  out.log(`    properties added:    ${list(rewritten.added)}`);
  out.log(`    properties untouched: ${list(rewritten.leftAlone)}`);
  if (rewritten.removed.length) {
    out.log(`    properties REMOVED:  ${list(rewritten.removed)} [--replace-properties]`);
  }
  if (dryRun) {
    out.log(`  · ${label}: would rewrite the above (CREATEDAT never regenerated) [--dry-run]`);
    return notePath;
  }
  await atomicWrite(notePath, rewritten.markdown);
  return renameNote(notePath, fullTitle, { rename, digestDir, out });
}

/**
 * The filename carries the title too. Renaming on disk BREAKS existing Obsidian
 * links (Obsidian only rewrites links when the rename happens inside the app), so
 * the default is to leave the file alone and say so loudly. `--rename` opts in,
 * using finalize.js's own collision-safe naming.
 */
async function renameNote(notePath, fullTitle, { rename, digestDir, out }) {
  const oldName = basename(notePath);
  const prefix = oldName.match(/^\d{8}-\d{4}/)?.[0];
  if (!prefix) return notePath;
  const safe = sanitizeTitle(fullTitle);
  const wanted = `${safe ? `${prefix} ${safe}` : prefix}.md`;
  if (wanted === oldName) return notePath;

  if (!rename) {
    out.log(
      `\n  ⚠  FILENAME STILL CARRIES THE OLD TITLE\n` +
        `     on disk  : ${oldName}\n` +
        `     new title: ${fullTitle}\n` +
        `     Rename it INSIDE Obsidian (F2) so your links follow it. Passing --rename\n` +
        `     renames on the filesystem instead, which does NOT update links elsewhere.\n`
    );
    return notePath;
  }
  const { finalName, dest } = await freeNotePath(digestDir, wanted);
  await fs.rename(notePath, dest);
  out.log(`  ✓ renamed: ${oldName} → ${finalName} (links pointing at the old name will break)`);
  return dest;
}

/** Every `.md` in the digest folder, with its current bytes. */
async function listNotes(dir) {
  const names = (await fs.readdir(dir)).filter((f) => f.endsWith(".md")).sort();
  const out = [];
  for (const f of names) {
    const path = join(dir, f);
    out.push({ path, markdown: await fs.readFile(path, "utf8") });
  }
  return out;
}

/**
 * The FREE probe: classify every note, write nothing, call nothing. This is the
 * safe way to survey a folder of hundreds of notes — `--dry-run` is not free.
 */
export async function checkNotes({ digestDir = DIGEST_DIR, out = console } = {}) {
  const notes = await listNotes(digestDir);
  const eligible = [];
  const refused = [];
  for (const { path, markdown } of notes) {
    const verdict = classifyNote(markdown);
    (verdict.eligible ? eligible : refused).push({ path, ...verdict });
  }
  out.log(`Scanned ${notes.length} note(s) in ${digestDir} — no network calls, nothing written.\n`);
  for (const n of eligible) {
    out.log(`  ✓ ELIGIBLE ${basename(n.path)}\n      marked attachment(s): ${n.marked.join(", ")}`);
  }
  const byReason = new Map();
  for (const n of refused) byReason.set(n.reason, (byReason.get(n.reason) || 0) + 1);
  if (byReason.size) out.log("");
  for (const [reason, count] of byReason) out.log(`  – refused ${count}: ${reason}`);
  for (const n of refused) {
    if (n.reason === REFUSAL.NO_FRONTMATTER) {
      out.log(`      ${basename(n.path)} carries a marker but has no frontmatter — left completely alone`);
    }
  }
  out.log(`\n${eligible.length} eligible, ${refused.length} refused.`);
  return { eligible, refused };
}

/**
 * Recover transcripts for the eligible notes in `digestDir`.
 * Every collaborator is injectable so the tests can exercise the whole folder walk
 * without touching a vendor or an LLM.
 */
export async function runRecovery({
  targets = [],
  all = false,
  dryRun = false,
  rename = false,
  replaceProperties = false,
  digestDir = DIGEST_DIR,
  attachmentsDir = ATTACHMENTS_DIR,
  transcribeImpl = transcribe,
  titleImpl = generateTitleAndTags,
  out = console,
} = {}) {
  const wanted = targets.length ? new Set(targets.map((t) => basename(audioPath(t, attachmentsDir)))) : null;
  const pathFor = new Map(targets.map((t) => [basename(audioPath(t, attachmentsDir)), audioPath(t, attachmentsDir)]));

  const notes = await listNotes(digestDir);
  const transcripts = new Map(); // attachment name → result, so one file is fetched once
  let recovered = 0;
  let refusedNamed = 0;
  let failed = 0;

  for (const { path: notePath, markdown: original } of notes) {
    const label = basename(notePath);
    const verdict = classifyNote(original);
    const embedsWanted = wanted && [...wanted].some((n) => original.includes(`![[${ATTACHMENTS_VAULT_PREFIX}/${n}]]`));

    if (!verdict.eligible) {
      // On --all, "no marker" is simply "not a candidate" and would drown the
      // output (most of the folder is hand-written); --check reports the census.
      // A named target must always hear why it was refused.
      if (embedsWanted || (all && verdict.reason === REFUSAL.NO_FRONTMATTER)) {
        out.error(`\n✗ REFUSED ${label}: ${verdict.reason}\n  Nothing was read, sent to a vendor, or written for this note.`);
        refusedNamed++;
      }
      continue;
    }

    const todo = verdict.marked.filter((n) => !wanted || wanted.has(n));
    if (!todo.length) continue;

    let markdown = original;
    let patched = 0;
    for (const name of todo) {
      if (!transcripts.has(name)) {
        out.log(`\n▸ ${name}  (for ${label})`);
        transcripts.set(name, await transcribeImpl(pathFor.get(name) ?? audioPath(name, attachmentsDir)));
      }
      const r = transcripts.get(name);
      if (!r.ok) {
        out.error(`  ✗ still failing: reason=${r.reason} after ${r.attempts} attempt(s) via [${r.providersTried.join(", ") || "none"}]`);
        if (r.lastError) out.error(`    last error: ${r.lastError}`);
        failed++;
        continue;
      }
      out.log(`  ✓ ${r.text.length} chars via ${r.provider} (attempt ${r.attempts}, lang=${r.language || "?"})`);
      out.log(`    ${r.text.slice(0, 120)}${r.text.length > 120 ? "…" : ""}`);

      const next = replaceMarker(markdown, name, r.text);
      if (!next.replaced) continue;
      markdown = next.markdown;
      patched += next.replaced;
    }
    if (!patched) continue;

    if (dryRun) {
      out.log(`  · ${label}: would replace ${patched} marker(s) [--dry-run]`);
    } else {
      // Commit the words FIRST: metadata regeneration must never be able to lose them.
      await atomicWrite(notePath, markdown);
      out.log(`  ✓ ${label}: replaced ${patched} marker(s)`);
    }
    recovered++;
    await regenerateMetadata(notePath, markdown, {
      dryRun,
      rename,
      replaceProperties,
      digestDir,
      titleImpl,
      out,
    });
  }

  if (wanted) {
    for (const name of wanted) {
      const seen = notes.some((n) => n.markdown.includes(`![[${ATTACHMENTS_VAULT_PREFIX}/${name}]]`));
      if (!seen) {
        out.error(`\n✗ ${name}: no note in ${digestDir} embeds this attachment — nothing to recover, no vendor called.`);
        refusedNamed++;
      }
    }
  }
  return { recovered, refused: refusedNamed, failed };
}

// Run only as a CLI — tests import the exported helpers directly.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const argv = process.argv.slice(2);
  const flag = (f) => argv.includes(f);
  const targets = argv.filter((a) => !a.startsWith("--"));

  if (flag("--check")) {
    await checkNotes({});
    process.exit(0);
  }
  if (!flag("--all") && !targets.length) {
    console.error(
      "Give an attachment name, or --all, or --check.\n\n" +
        "usage: npm run retranscribe -- --check                      (free: no vendor, no LLM, no writes)\n" +
        "       npm run retranscribe -- <attachment|path> [flags]\n" +
        "       npm run retranscribe -- --all [flags]\n\n" +
        "flags: --dry-run              preview only. NOT free: one STT call and one title/tag\n" +
        "                              LLM call per eligible note, because previewing the real\n" +
        "                              recovered text and the real proposed title is the point.\n" +
        "       --rename               also rename the note file on disk (breaks Obsidian links)\n" +
        "       --replace-properties   rebuild the frontmatter instead of merging into it\n" +
        "                              (REMOVES hand-added keys; default preserves them)"
    );
    process.exit(2);
  }

  const result = await runRecovery({
    targets,
    all: flag("--all"),
    dryRun: flag("--dry-run"),
    rename: flag("--rename"),
    replaceProperties: flag("--replace-properties"),
  });
  if (!result.recovered && !result.refused && !result.failed) {
    console.log("No eligible note carries a transcription-failure marker. Nothing to recover.");
  }
  process.exit(result.failed || result.refused ? 1 : 0);
}
