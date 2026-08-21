#!/usr/bin/env node
// Re-run transcription for an audio attachment that is already in the vault, and
// drop the recovered words into the note that embeds it — replacing the
// "[Transcription unavailable…]" marker in place, then REGENERATING the note's
// title and tags from the now-complete text.
//
// This is the recovery half of the durability story: `ingestVoice` always saves the
// audio, so a failed transcript is never lost data, only deferred work. This is the
// thing that does the deferred work, without hand-editing the vault.
//
//   secret-run npm run retranscribe -- <attachment.ogg | /path/to/audio> [--dry-run]
//   secret-run npm run retranscribe -- --all          # every note still carrying a marker
//   secret-run npm run retranscribe -- --all --rename # …and rename the file on disk too
//
// Why regenerate the metadata: `buildLLMInput` feeds every block to the title model
// together at /done time, substituting "(no transcript)" for the failed one. Title,
// tags and themes are therefore generated ONCE, from text that never included these
// words. Recovering the transcript without redoing that pass leaves the frontmatter
// contradicting the body (the 08-14 "An empty voice note" case).
//
// It goes through the SAME src/stt.js retry+rotation loop the bot uses, so running
// it is also a live check that the loop works.
import { promises as fs } from "node:fs";
import { join, basename, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { parse as yamlParse } from "yaml";
import {
  DIGEST_DIR,
  ATTACHMENTS_DIR,
  ATTACHMENTS_VAULT_PREFIX,
  TOKENHUB_API_KEY,
} from "../src/config.js";
import { blockquote, renderProperties } from "../src/compile.js";
import { buildLLMInput, freeNotePath } from "../src/finalize.js";
import { generateTitleAndTags } from "../src/llm.js";
import { glueTitle, sanitizeTitle } from "../src/util.js";
import { transcribe } from "../src/stt.js";

// Matches both the original bare marker and the richer one compile.js writes now.
const MARKER = /^>\s*\[Transcription unavailable/;

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const all = args.includes("--all");
const rename = args.includes("--rename");
const targets = args.filter((a) => !a.startsWith("--"));

function usage(msg) {
  console.error(
    `${msg}\n\nusage: npm run retranscribe -- <attachment|path> [--dry-run] [--rename]\n` +
      `       npm run retranscribe -- --all [--dry-run] [--rename]`
  );
  process.exit(2);
}

/** Absolute path to the audio: an explicit path if given, else the vault ATTACHMENTS dir. */
function audioPath(nameOrPath) {
  return nameOrPath.includes("/") || isAbsolute(nameOrPath)
    ? nameOrPath
    : join(ATTACHMENTS_DIR, nameOrPath);
}

/**
 * Replace the failure marker directly under `![[…/<name>]]` with `transcript`.
 * Touches ONLY the marker LINE — a blockquote line a human appended underneath it
 * survives, because these are Boyang's live, hand-editable notes, not generated files.
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

/** Reverse of compile.js's `blockquote()`. */
function unquote(lines) {
  return lines.map((l) => l.replace(/^>\s?/, "")).join("\n").trim();
}

/** Split a compiled note into `---` frontmatter and body. */
export function splitNote(markdown) {
  const m = markdown.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) return null;
  return { frontmatterRaw: m[1], body: markdown.slice(m[0].length) };
}

/**
 * Parse a compiled body back into the block shapes `buildLLMInput` consumes, so the
 * recovery pass feeds the title model the same string the original compile would
 * have, had the transcript been there.
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
    const quoted = rest.filter((l) => l.trim()).every((l) => l.startsWith(">"));
    const hasQuote = rest.some((l) => l.startsWith(">"));
    if (hasQuote && quoted) {
      const raw = unquote(rest.filter((l) => l.startsWith(">")));
      // A block still carrying the marker has no transcript to offer.
      blocks.push({
        type: "voice",
        attachment,
        transcript: MARKER.test(`> ${raw}`) ? null : raw,
      });
      continue;
    }
    const userCaption = rest.join("\n").trim();
    blocks.push({
      type: IMAGE_EXT.test(attachment) ? "image" : "file",
      attachment,
      userCaption,
    });
  }
  return blocks;
}

/**
 * Rebuild the frontmatter with a fresh title + tags, keeping CREATEDAT verbatim.
 * @returns {{markdown:string, dropped:string[]}}
 */
export function rewriteFrontmatter(markdown, fullTitle, tags) {
  const parts = splitNote(markdown);
  if (!parts) return null;
  const old = yamlParse(parts.frontmatterRaw) || {};
  const yaml = renderProperties(old.CREATEDAT, fullTitle, tags);
  const kept = new Set(["CREATEDAT", "TITLE标题", ...(tags || []).map((t) => String(t.key).trim())]);
  const dropped = Object.keys(old).filter((k) => !kept.has(k));
  return { markdown: `---\n${yaml}\n---\n${parts.body}`, dropped };
}

const atomicWrite = async (path, text) => {
  const tmp = `${path}.tmp`; // atomic, same as every other vault write
  await fs.writeFile(tmp, text, "utf8");
  await fs.rename(tmp, path);
};

/**
 * Re-run the WHOLE title/tag pass over the now-complete note. Best-effort by
 * design: the recovered transcript is already committed to disk before this runs,
 * and is never rolled back because the metadata step failed.
 * @returns {Promise<string>} the note's (possibly new) path
 */
async function regenerateMetadata(notePath, markdown) {
  const label = basename(notePath);
  const parts = splitNote(markdown);
  if (!parts) {
    console.error(`  ! ${label}: no frontmatter — TITLE标题/tags left STALE (they predate this transcript)`);
    return notePath;
  }
  if (!TOKENHUB_API_KEY) {
    console.error(
      `  ! ${label}: no TENCENT_TOKENHUB_API_KEY — transcript saved, but TITLE标题/tags are STALE\n` +
        `    (they were generated from "(no transcript)"). Re-run with the key to fix them.`
    );
    return notePath;
  }

  const blocks = parseNoteBlocks(parts.body);
  const llmInput = buildLLMInput(blocks);
  if (!llmInput.trim()) {
    console.error(`  ! ${label}: nothing to title — TITLE标题/tags left STALE`);
    return notePath;
  }
  if (blocks.some((b) => b.type === "image")) {
    console.log(`  · ${label}: image vision captions are not stored in the note, so the new title sees only their user captions`);
  }

  const { title, tags } = await generateTitleAndTags(llmInput);
  // generateTitleAndTags swallows its own errors and falls back to "first line of
  // the input, no tags". Adopting that would REPLACE a good title with a worse one,
  // so treat the fallback's signature as a failure and keep what is already there.
  if (!tags.length && !title.en) {
    console.error(`  ! ${label}: title model unavailable — transcript saved, TITLE标题/tags left STALE`);
    return notePath;
  }

  const fullTitle = glueTitle(title.zh, title.en);
  const rewritten = rewriteFrontmatter(markdown, fullTitle, tags);
  if (!rewritten) {
    console.error(`  ! ${label}: could not rewrite frontmatter — TITLE标题/tags left STALE`);
    return notePath;
  }

  console.log(`  ✓ new title: ${fullTitle}`);
  console.log(`    tags: ${tags.map((t) => `${t.key}=${t.value}`).join(", ") || "(none)"}`);
  if (rewritten.dropped.length) {
    console.log(`    replaced properties: ${rewritten.dropped.join(", ")}`);
  }
  if (dryRun) {
    console.log(`  · ${label}: would rewrite TITLE标题 + tags (CREATEDAT preserved) [--dry-run]`);
    return notePath;
  }
  await atomicWrite(notePath, rewritten.markdown);
  return renameNote(notePath, fullTitle);
}

/**
 * The filename carries the title too. Renaming on disk BREAKS existing Obsidian
 * links (Obsidian only rewrites links when the rename happens inside the app), so
 * the default is to leave the file alone and say so loudly. `--rename` opts in,
 * using finalize.js's own collision-safe naming.
 */
async function renameNote(notePath, fullTitle) {
  const oldName = basename(notePath);
  const prefix = oldName.match(/^\d{8}-\d{4}/)?.[0];
  if (!prefix) return notePath;
  const safe = sanitizeTitle(fullTitle);
  const wanted = `${safe ? `${prefix} ${safe}` : prefix}.md`;
  if (wanted === oldName) return notePath;

  if (!rename) {
    console.log(
      `\n  ⚠  FILENAME STILL CARRIES THE OLD TITLE\n` +
        `     on disk : ${oldName}\n` +
        `     new title: ${fullTitle}\n` +
        `     Rename it INSIDE Obsidian (F2) so your links follow it. Passing --rename\n` +
        `     renames on the filesystem instead, which does NOT update links elsewhere.\n`
    );
    return notePath;
  }
  const { finalName, dest } = await freeNotePath(DIGEST_DIR, wanted);
  await fs.rename(notePath, dest);
  console.log(`  ✓ renamed: ${oldName} → ${finalName} (links pointing at the old name will break)`);
  return dest;
}

async function notesEmbedding(name) {
  const embed = `![[${ATTACHMENTS_VAULT_PREFIX}/${name}]]`;
  const hits = [];
  for (const f of await fs.readdir(DIGEST_DIR)) {
    if (!f.endsWith(".md")) continue;
    const path = join(DIGEST_DIR, f);
    const md = await fs.readFile(path, "utf8");
    if (md.includes(embed)) hits.push({ path, md });
  }
  return hits;
}

/** Every attachment still carrying a failure marker anywhere in the vault. */
async function pendingAttachments() {
  const names = new Set();
  const re = new RegExp(`!\\[\\[${ATTACHMENTS_VAULT_PREFIX}/([^\\]]+)\\]\\]`);
  for (const f of await fs.readdir(DIGEST_DIR)) {
    if (!f.endsWith(".md")) continue;
    const lines = (await fs.readFile(join(DIGEST_DIR, f), "utf8")).split("\n");
    for (let i = 1; i < lines.length; i++) {
      if (!MARKER.test(lines[i])) continue;
      const m = lines[i - 1].match(re);
      if (m) names.add(m[1]);
    }
  }
  return [...names];
}

async function recover(nameOrPath) {
  const path = audioPath(nameOrPath);
  const name = basename(path);
  process.stdout.write(`\n▸ ${name}\n`);

  const r = await transcribe(path);
  if (!r.ok) {
    console.error(`  ✗ still failing: reason=${r.reason} after ${r.attempts} attempt(s) via [${r.providersTried.join(", ") || "none"}]`);
    if (r.lastError) console.error(`    last error: ${r.lastError}`);
    return false;
  }
  console.log(`  ✓ ${r.text.length} chars via ${r.provider} (attempt ${r.attempts}, lang=${r.language || "?"})`);
  console.log(`    ${r.text.slice(0, 120)}${r.text.length > 120 ? "…" : ""}`);

  const notes = await notesEmbedding(name);
  if (!notes.length) {
    console.error(`  ! no note in ${DIGEST_DIR} embeds this attachment — transcript printed above, nothing written`);
    return false;
  }
  for (const { path: notePath, md } of notes) {
    const { markdown, replaced } = replaceMarker(md, name, r.text);
    if (!replaced) {
      console.log(`  – ${basename(notePath)}: no failure marker under the embed, left untouched`);
      continue;
    }
    if (dryRun) {
      console.log(`  · ${basename(notePath)}: would replace ${replaced} marker(s) [--dry-run]`);
    } else {
      // Commit the words FIRST: metadata regeneration must never be able to lose them.
      await atomicWrite(notePath, markdown);
      console.log(`  ✓ ${basename(notePath)}: replaced ${replaced} marker(s)`);
    }
    await regenerateMetadata(notePath, markdown);
  }
  return true;
}

// Run only as a CLI — tests import the pure helpers directly.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const list = all ? await pendingAttachments() : targets;
  if (!list.length) {
    if (all) {
      console.log("No notes carry a transcription-failure marker. Nothing to recover.");
      process.exit(0);
    }
    usage("Give an attachment name (or --all).");
  }

  let failures = 0;
  for (const item of list) if (!(await recover(item))) failures++;
  process.exit(failures ? 1 : 0);
}
