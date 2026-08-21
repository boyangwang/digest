// Finalize — on /done: assemble → title/tags → move attachments → compile → write note.
import { promises as fs } from "node:fs";
import { join, extname, basename } from "node:path";
import { DIGEST_DIR, ATTACHMENTS_DIR, STT_TOTAL_BUDGET_MS } from "./config.js";
import { loadPending, clearPending, pendingAttachmentPath } from "./store.js";
import { waitForTranscriptions } from "./transcriptions.js";
import { generateTitleAndTags } from "./llm.js";
import { compileNote } from "./compile.js";
import { log } from "./log.js";

/** Build the text fed to glm-5.2: substance in arrival order (text, speech, image captions, files). */
export function buildLLMInput(blocks) {
  const lines = [];
  for (const b of blocks) {
    if (b.type === "text") lines.push(b.text?.trim() || "");
    else if (b.type === "voice") lines.push(`[spoken] ${b.transcript?.trim() || "(no transcript)"}`);
    else if (b.type === "image")
      lines.push(`[image] ${b.visionCaption?.trim() || b.userCaption?.trim() || "(no description)"}`);
    else if (b.type === "file")
      lines.push(`[file: ${b.attachment}] ${b.userCaption?.trim() || ""}`.trim());
  }
  return lines.filter(Boolean).join("\n\n");
}

/** Move a staged attachment into the vault ATTACHMENTS dir, collision-safe. Returns final name. */
async function moveAttachment(chatId, name) {
  await fs.mkdir(ATTACHMENTS_DIR, { recursive: true });
  const src = pendingAttachmentPath(chatId, name);
  let finalName = name;
  const ext = extname(name);
  const stem = basename(name, ext);
  let dest = join(ATTACHMENTS_DIR, finalName);
  for (let n = 2; ; n++) {
    try {
      await fs.access(dest);
      finalName = `${stem}-${n}${ext}`;
      dest = join(ATTACHMENTS_DIR, finalName);
    } catch {
      break; // free slot
    }
  }
  try {
    await fs.rename(src, dest);
  } catch {
    // cross-device fallback
    await fs.copyFile(src, dest);
    await fs.unlink(src).catch(() => {});
  }
  return finalName;
}

/**
 * First free slot for `filename` in `dir`, disambiguating with " (2)", " (3)", …
 * Exported so the recovery script renames exactly the way finalize does.
 * @returns {Promise<{finalName:string, dest:string}>}
 */
export async function freeNotePath(dir, filename) {
  const ext = extname(filename); // ".md"
  const stem = basename(filename, ext);
  let finalName = filename;
  let dest = join(dir, finalName);
  for (let n = 2; ; n++) {
    try {
      await fs.access(dest);
      finalName = `${stem} (${n})${ext}`;
      dest = join(dir, finalName);
    } catch {
      break;
    }
  }
  return { finalName, dest };
}

/** Write markdown to the vault, resolving filename collisions. Returns the written filename. */
async function writeNote(filename, markdown) {
  await fs.mkdir(DIGEST_DIR, { recursive: true });
  const { finalName, dest } = await freeNotePath(DIGEST_DIR, filename);
  const tmp = `${dest}.tmp`;
  await fs.writeFile(tmp, markdown, "utf8");
  await fs.rename(tmp, dest);
  return finalName;
}

/**
 * Finalize the pending digest for a chat.
 *
 * Waits for that chat's still-running transcriptions first (bounded by
 * `transcriptionWaitMs`). Transcription runs outside the serial queue, so without
 * this wait the title/tags would routinely be generated from "(no transcript)".
 * If the bound bites we compile ANYWAY - the note then carries the retryable
 * failure marker, and `npm run retranscribe` regenerates title and tags later.
 *
 * @returns {Promise<{filename:string, fullTitle:string}|null>} null if nothing to compile.
 */
export async function finalizeDigest(chatId, { transcriptionWaitMs = STT_TOTAL_BUDGET_MS } = {}) {
  if (!(await loadPending(chatId))?.blocks?.length) return null;

  await waitForTranscriptions(chatId, { timeoutMs: transcriptionWaitMs });

  // Re-read: a transcript that landed while we waited rewrote the manifest on disk.
  const manifest = await loadPending(chatId);
  if (!manifest || manifest.blocks.length === 0) return null;

  // 1. Move attachments into the vault, updating block names so embeds match.
  for (const b of manifest.blocks) {
    if (b.attachment) b.attachment = await moveAttachment(chatId, b.attachment);
  }

  // 2. Title + tags from the substance.
  const llmInput = buildLLMInput(manifest.blocks);
  const { title, tags } = await generateTitleAndTags(llmInput);

  // 3. Compile + write.
  const startDate = new Date(manifest.startedAt);
  const { filename, markdown, fullTitle } = compileNote({
    blocks: manifest.blocks,
    title,
    tags,
    startDate,
  });
  const written = await writeNote(filename, markdown);
  log.info(`Wrote note: ${written}`);

  // 4. Clear pending state.
  await clearPending(chatId);
  return { filename: written, fullTitle };
}
