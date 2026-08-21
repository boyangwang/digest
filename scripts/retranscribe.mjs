#!/usr/bin/env node
// Re-run transcription for an audio attachment that is already in the vault, and
// drop the recovered words into the note that embeds it — replacing the
// "[Transcription unavailable…]" marker in place.
//
// This is the recovery half of the durability story: `ingestVoice` always saves the
// audio, so a failed transcript is never lost data, only deferred work. This is the
// thing that does the deferred work, without hand-editing the vault.
//
//   secret-run npm run retranscribe -- <attachment.ogg | /path/to/audio> [--dry-run]
//   secret-run npm run retranscribe -- --all          # every note still carrying a marker
//
// It goes through the SAME src/stt.js retry+rotation loop the bot uses, so running
// it is also a live check that the loop works.
import { promises as fs } from "node:fs";
import { join, basename, isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import { DIGEST_DIR, ATTACHMENTS_DIR, ATTACHMENTS_VAULT_PREFIX } from "../src/config.js";
import { blockquote } from "../src/compile.js";
import { transcribe } from "../src/stt.js";

// Matches both the original bare marker and the richer one compile.js writes now.
const MARKER = /^>\s*\[Transcription unavailable/;

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const all = args.includes("--all");
const targets = args.filter((a) => !a.startsWith("--"));

function usage(msg) {
  console.error(`${msg}\n\nusage: npm run retranscribe -- <attachment|path> [--dry-run]\n       npm run retranscribe -- --all [--dry-run]`);
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
 * Touches ONLY that blockquote run — every other byte of the note is preserved,
 * because these are Boyang's real notes, not generated files.
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
    // Swallow the whole contiguous `>` run that the marker opens.
    let j = i + 1;
    while (j < lines.length && lines[j].startsWith(">")) j++;
    out.push(blockquote(transcript.trim()));
    replaced++;
    i = j - 1;
  }
  return { markdown: out.join("\n"), replaced };
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
      continue;
    }
    const tmp = `${notePath}.tmp`; // atomic, same as every other vault write
    await fs.writeFile(tmp, markdown, "utf8");
    await fs.rename(tmp, notePath);
    console.log(`  ✓ ${basename(notePath)}: replaced ${replaced} marker(s)`);
  }
  return true;
}

// Run only as a CLI — tests import replaceMarker() directly.
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
