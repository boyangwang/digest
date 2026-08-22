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
// EXIT CODES are a contract, not an accident:
//   0  everything this tool was ALLOWED to do succeeded. Notes the gate refused are
//      still named loudly in the output, but they are not failures — a note that
//      needs recovery and is not ours to touch can never become eligible, so exiting
//      non-zero for it would be permanent, and a permanently-failing exit code is how
//      automation learns to ignore a tool.
//   1  something actually went wrong: a transcription exhausted its retries, a write
//      failed, a named attachment matched no note, the vault could not be read.
//   2  bad usage (no attachment, no --all, no --check).
//
// ELIGIBILITY IS A HARD PRECONDITION, checked before a note is read for rewriting
// and before any STT or LLM call is made for it. A note qualifies ONLY if BOTH:
//   (a) it carries this program's PROVENANCE STAMP (src/provenance.js) — "is this
//       ours to touch"; and
//   (b) it carries the failed-transcript marker, the `> [Transcription unavailable…`
//       blockquote line directly under an ATTACHMENTS embed — "does it need this
//       work". Both the original bare form and the current richer one count.
// Neither substitutes for the other, and there is NO override flag for (a): no flag,
// env var or config field may make an unstamped note eligible. The digest folder is
// mostly hand-written, correctly carries no frontmatter, and must stay that way, so
// anything missing either condition is refused with a reason and ZERO bytes changed —
// no frontmatter is ever created, no un-marked note is rewritten however stale its
// metadata looks, and no legacy-schema note is migrated as a side effect. Naming an
// attachment explicitly does not bypass the gate.
//
// Notes written before the stamp existed are therefore untouchable, and that is the
// correct outcome: acting on them would mean inferring provenance from state, which
// is exactly what the stamp replaces.
//
// Why regenerate the metadata for an eligible note: `buildLLMInput` feeds every
// block to the title model together at /done time, substituting "(no transcript)"
// for the failed one. Title and tags are therefore generated ONCE, from text that
// never included these words. Recovering the transcript without redoing that pass
// leaves the frontmatter contradicting the body. The rewrite MERGES: TITLE标题 and
// the generated tag keys are replaced, CREATEDAT and every other pre-existing key
// (a hand-added `aliases`, `cssclasses`, Dataview field…) are kept.
//
// ONE ATOMIC WRITE commits the transcript and the regenerated metadata together, so
// a note only ever moves between two consistent states: untouched with its marker
// intact and still eligible, or fully recovered. If the title model gives up, the
// recovered transcript is DISCARDED rather than committed — it is not lost data (the
// audio is still in the vault, the marker survives, a re-run redoes it at the cost of
// one STT call), whereas committing it would consume the marker and strand the note
// with metadata this tool could never fix again.
//
// It goes through the SAME src/stt.js retry+rotation loop the bot uses, so running
// it is also a live check that the loop works.
import { promises as fs } from "node:fs";
import { join, basename, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { parseDocument } from "yaml";
import { DIGEST_DIR, ATTACHMENTS_DIR, ATTACHMENTS_VAULT_PREFIX } from "../src/config.js";
import { GENERATOR_KEY, isGeneratedByDigest } from "../src/provenance.js";
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
  NO_FRONTMATTER:
    "no frontmatter — a hand-written note; this tool never creates frontmatter, not even CREATEDAT",
  NO_STAMP: `no ${GENERATOR_KEY} provenance stamp — not created by this program, so not ours to edit`,
  NO_MARKER: "carries no failed-transcript marker — nothing here needs recovering",
};

/**
 * The gate. Nothing about a note is read for rewriting, and no vendor is called for
 * it, until this returns eligible.
 *
 * Provenance first, then need. There is deliberately no way to skip either check.
 * @returns {{eligible:boolean, reason:?string, marked:string[]}}
 */
export function classifyNote(markdown) {
  const marked = markedAttachments(markdown);
  const parts = splitNote(markdown);
  if (!parts) return { eligible: false, reason: REFUSAL.NO_FRONTMATTER, marked };

  let frontmatter;
  try {
    frontmatter = parseDocument(parts.frontmatterRaw).toJS();
  } catch {
    frontmatter = null;
  }
  if (!isGeneratedByDigest(frontmatter)) return { eligible: false, reason: REFUSAL.NO_STAMP, marked };
  if (!marked.length) return { eligible: false, reason: REFUSAL.NO_MARKER, marked };
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
 * The YAML is edited through the document model, which preserves untouched keys'
 * VALUES and any comments — but NOT their byte-for-byte layout: yaml v2 re-serializes
 * the block, so a flat `aliases:\n- x` comes back indented and `[a, b]` comes back
 * `[ a, b ]`. Content is safe; whitespace may be normalized.
 *
 * @returns {?{markdown:string, replaced:string[], added:string[], leftAlone:string[], removed:string[]}}
 */
export function rewriteFrontmatter(markdown, fullTitle, tags, { replaceAll = false } = {}) {
  const parts = splitNote(markdown);
  if (!parts) return null;

  const clean = (tags || [])
    .filter((t) => t && t.key)
    .map((t) => ({ key: String(t.key).trim(), value: String(t.value ?? "").trim() }))
    .filter((t) => t.key && t.key !== "CREATEDAT" && t.key !== GENERATOR_KEY);

  const doc = parseDocument(parts.frontmatterRaw);
  const before = doc.toJS() || {};
  const oldKeys = Object.keys(before);
  const writes = [["TITLE标题", fullTitle], ...clean.map((t) => [t.key, t.value])];

  let out;
  if (replaceAll) {
    out = parseDocument("");
    out.contents = out.createNode({});
    if (before.CREATEDAT != null) out.set("CREATEDAT", before.CREATEDAT);
    // An existing provenance stamp survives a rebuild — dropping it would make the
    // note ineligible for every future run. But it is only ever COPIED, never
    // synthesised: this helper takes an arbitrary markdown string, so manufacturing a
    // stamp here would be a way to make an unstamped note eligible, and directive 3
    // says that gate has no override.
    if (before[GENERATOR_KEY] != null) out.set(GENERATOR_KEY, before[GENERATOR_KEY]);
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
 * Re-run the whole title/tag pass over the now-complete note, IN MEMORY.
 *
 * Writes nothing: the caller commits the transcript and this frontmatter together in
 * one atomic write, so a failure here leaves the note byte-identical with its marker
 * intact and still eligible for another run.
 * @returns {Promise<{ok:true, markdown:string, fullTitle:string}|{ok:false, why:string}>}
 */
async function regenerateMetadata(notePath, markdown, opts) {
  const { replaceProperties, titleImpl, out } = opts;
  const label = basename(notePath);
  const parts = splitNote(markdown);
  if (!parts) return { ok: false, why: "no frontmatter" };

  const blocks = parseNoteBlocks(parts.body);
  const llmInput = buildLLMInput(blocks);
  if (!llmInput.trim()) return { ok: false, why: "nothing to title" };
  if (blocks.some((b) => b.type === "image")) {
    out.log(`  · ${label}: image vision captions are not stored in the note, so the new title sees only their user captions`);
  }

  const { title, tags, fallback } = await titleImpl(llmInput);
  // The fallback title is the input's first line. Adopting it would REPLACE a good
  // title with a worse one, and committing it would consume the marker.
  if (fallback) return { ok: false, why: "title model gave up (no key, or every attempt failed)" };

  const fullTitle = glueTitle(title.zh, title.en);
  const rewritten = rewriteFrontmatter(markdown, fullTitle, tags, { replaceAll: replaceProperties });
  if (!rewritten) return { ok: false, why: "could not rewrite frontmatter" };

  out.log(`  ✓ new title: ${fullTitle}`);
  const list = (ks) => (ks.length ? ks.join(", ") : "(none)");
  out.log(`    properties replaced: ${list(rewritten.replaced)}`);
  out.log(`    properties added:    ${list(rewritten.added)}`);
  out.log(`    properties untouched: ${list(rewritten.leftAlone)}`);
  if (rewritten.removed.length) {
    out.log(`    properties REMOVED:  ${list(rewritten.removed)} [--replace-properties]`);
  }
  return { ok: true, markdown: rewritten.markdown, fullTitle };
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
async function listNotes(dir, out = console) {
  let names;
  try {
    names = (await fs.readdir(dir)).filter((f) => f.endsWith(".md")).sort();
  } catch (e) {
    out.error(`✗ cannot read ${dir}: ${e?.message || e}`);
    return [];
  }
  const notes = [];
  for (const f of names) {
    const path = join(dir, f);
    try {
      notes.push({ path, markdown: await fs.readFile(path, "utf8") });
    } catch (e) {
      out.error(`✗ cannot read ${f}: ${e?.message || e} — skipped`);
    }
  }
  return notes;
}

/**
 * The FREE probe: classify every note, write nothing, call nothing. This is the
 * safe way to survey a folder of hundreds of notes — `--dry-run` is not free.
 */
export async function checkNotes({ digestDir = DIGEST_DIR, out = console } = {}) {
  const notes = await listNotes(digestDir, out);
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

  // Name only the refusals that are actionable: a note carrying a failure marker that
  // this tool may not touch is something the captain can decide about. The un-marked
  // bulk is most of the folder and naming it would drown the census.
  const blocked = refused.filter((n) => n.marked.length > 0);
  if (blocked.length) {
    out.log(`\n  ⚠  ${blocked.length} note(s) NEED recovery but are refused — decide by hand:`);
    for (const n of blocked) {
      out.log(`      ${basename(n.path)}\n        marked: ${n.marked.join(", ")}\n        why: ${n.reason}`);
    }
  }
  out.log(`\n${eligible.length} eligible, ${refused.length} refused (${blocked.length} of them marked).`);
  return { eligible, refused, blocked };
}

/**
 * Recover transcripts for the eligible notes in `digestDir`.
 * Every collaborator is injectable so the tests can exercise the whole folder walk
 * without touching a vendor or an LLM.
 */
export async function runRecovery({
  targets = [],
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

  const notes = await listNotes(digestDir, out);
  const transcripts = new Map(); // attachment name → result, so one file is fetched once
  let recovered = 0;
  let previewed = 0;
  // `blocked` is "needs a human", NOT a failure: the gate refused a note that carries
  // a marker. `failed` is "something went wrong" and is the only thing that exits 1.
  let blocked = 0;
  let failed = 0;

  for (const { path: notePath, markdown: original } of notes) {
    const label = basename(notePath);
    // One note's failure never ends the run: over hundreds of notes a locked file,
    // a full disk or an odd frontmatter shape must cost that note only.
    try {
      const verdict = classifyNote(original);
      const embedsWanted =
        wanted && [...wanted].some((n) => original.includes(`![[${ATTACHMENTS_VAULT_PREFIX}/${n}]]`));

      if (!verdict.eligible) {
        // A note carrying a failure marker that we may not touch is the ONE refusal
        // worth interrupting for: it needs recovery and only the captain can unblock
        // it. Un-marked notes are simply not candidates — they are most of the folder
        // and naming them would drown the output; `--check` reports the full census.
        if (embedsWanted || verdict.marked.length > 0) {
          out.error(
            `\n✗ REFUSED ${label}: ${verdict.reason}\n` +
              (verdict.marked.length
                ? `  It DOES carry a failure marker (${verdict.marked.join(", ")}), so it needs recovery — decide by hand.\n`
                : "") +
              `  Nothing was read, sent to a vendor, or written for this note.`
          );
          blocked++;
        }
        continue;
      }

      const todo = verdict.marked.filter((n) => !wanted || wanted.has(n));
      if (!todo.length) {
        if (embedsWanted) {
          out.log(`\n– ${label}: eligible, but the attachment you named carries no failure marker here — left untouched`);
        }
        continue;
      }

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

      // Metadata BEFORE any write: the marker must survive until both halves are ready.
      const meta = await regenerateMetadata(notePath, markdown, { replaceProperties, titleImpl, out });
      if (!meta.ok) {
        out.error(
          `  ✗ ${label}: ${meta.why} — note left UNCHANGED, marker intact, still eligible.\n` +
            `    The transcript was discarded on purpose: the audio is still saved, so a re-run redoes it.`
        );
        failed++;
        continue;
      }

      if (dryRun) {
        out.log(`  · ${label}: would replace ${patched} marker(s) and rewrite the above (CREATEDAT never regenerated) [--dry-run]`);
        previewed++;
        continue;
      }

      // ONE write: transcript + regenerated frontmatter, or nothing at all.
      await atomicWrite(notePath, meta.markdown);
      out.log(`  ✓ ${label}: replaced ${patched} marker(s) and regenerated TITLE标题 + tags`);
      recovered++;

      try {
        await renameNote(notePath, meta.fullTitle, { rename, digestDir, out });
      } catch (e) {
        // The note is already committed; a rename failure costs only the filename.
        out.error(`  ! ${label}: rename skipped: ${e?.message || e}`);
      }
    } catch (e) {
      out.error(`  ✗ ${label}: ${e?.message || e} — skipped, note left as it was`);
      failed++;
    }
  }

  if (wanted) {
    for (const name of wanted) {
      const seen = notes.some((n) => n.markdown.includes(`![[${ATTACHMENTS_VAULT_PREFIX}/${name}]]`));
      if (!seen) {
        // You asked for something that is not there: a real failure of the requested
        // operation, unlike the gate declining a note during a sweep.
        out.error(`\n✗ ${name}: no note in ${digestDir} embeds this attachment — nothing to recover, no vendor called.`);
        failed++;
      }
    }
  }
  return { recovered, previewed, blocked, failed };
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

  const dryRun = flag("--dry-run");
  const result = await runRecovery({
    targets,
    dryRun,
    rename: flag("--rename"),
    replaceProperties: flag("--replace-properties"),
  });

  const touched = result.recovered + result.previewed;
  if (!touched && !result.blocked && !result.failed) {
    console.log("\nNo eligible note carries a transcription-failure marker. Nothing to recover.");
    console.log("(`--check` lists what is eligible and why each other note was refused.)");
  } else {
    const verb = dryRun ? "would recover" : "recovered";
    console.log(
      `\n${verb} ${touched} note(s); ${result.failed} failed, ${result.blocked} blocked (need a human).` +
        (dryRun ? " Nothing was written [--dry-run]." : "")
    );
  }
  // Blocked notes are reported, never fatal: see the exit-code contract up top.
  process.exit(result.failed ? 1 : 0);
}
