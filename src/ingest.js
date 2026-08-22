// Ingest — handle one incoming input: persist → ACK → process → Processed.
// grammy-agnostic: the bot passes buffers + a `reply(text, opts)` sender, so this is testable.
// Every message (ACK and processed) carries the block's serial number; ACK carries the Done button.
import { appendBlock, saveAttachment, updateBlock, pendingAttachmentPath } from "./store.js";
import { stampSecond } from "./util.js";
import { transcribe } from "./stt.js";
import { trackTranscription } from "./transcriptions.js";
import { captionImage } from "./llm.js";

const ack = (n) => `✓ ACK #${n}`;

function extFromMime(mime, fallback) {
  if (!mime) return fallback;
  const map = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "audio/ogg": ".ogg", "audio/mpeg": ".mp3" };
  return map[mime] || fallback;
}

/** Text: persist verbatim, ACK (+Done button), done. */
export async function ingestText(chatId, text, reply) {
  const b = await appendBlock(chatId, { type: "text", text });
  const n = b.seq + 1;
  await reply(ack(n), { done: true });
  await reply(`📝 已存 · text saved (#${n})`);
}

const RECOVER_CMD = "npm run retranscribe -- --all";

/**
 * The transcription half of `ingestVoice`, run OUTSIDE the per-chat serial queue.
 * The block's slot in the note was already reserved by `appendBlock`, so patching
 * it by seq whenever the words land keeps it in arrival order regardless.
 */
async function transcribeIntoBlock(chatId, seq, n, name, mime, reply, sttOpts) {
  const r = await transcribe(pendingAttachmentPath(chatId, name), { mime, ...sttOpts });

  if (r.ok) {
    const patched = await updateBlock(chatId, seq, {
      transcript: r.text,
      sttProvider: r.provider,
      sttAttempts: r.attempts,
      sttFailure: null,
    });
    // `patched === null` means /done already compiled this digest while the retry
    // loop was still running. The words are not lost - say where they went.
    await reply(
      patched
        ? `🎙️ 已转写 · transcribed (#${n}):\n\n> ${r.text}`
        : `🎙️ 转写迟到，笔记已编译 · transcript arrived after the note was compiled (#${n}):\n\n` +
            `> ${r.text}\n\n↻ 写进笔记 · patch it into the note: ${RECOVER_CMD}`
    );
    return;
  }

  await updateBlock(chatId, seq, {
    transcript: null,
    sttFailure: {
      reason: r.reason,
      attempts: r.attempts,
      providers: r.providersTried,
      error: r.lastError,
      at: new Date().toISOString(),
    },
  });
  const tried = r.providersTried.length ? r.providersTried.join(", ") : "none";
  await reply(
    `🎙️ 已存音频，转写失败但可恢复 · audio saved, transcription failed but recoverable (#${n})\n` +
      `原因 reason: ${r.reason} (${r.attempts} attempt(s) via ${tried})\n` +
      // --all, not the pending name: moveAttachment renames on collision at /done,
      // so the name quoted here is not guaranteed to be the one in the vault.
      `↻ 笔记保存后可重跑 · after the note is saved, re-run:\n` +
      RECOVER_CMD
  );
}

/**
 * Voice/audio: persist audio, ACK, then transcribe in the background.
 *
 * The audio is saved and ACKed BEFORE transcription is attempted, so the recording
 * survives every transcription outcome - including total vendor failure. When the
 * transcript does not arrive, the block keeps a durable failure marker (reason +
 * attachment name) and the reply tells Boyang the audio is safe and how to recover
 * the words later, instead of a dead-end "transcription unavailable".
 *
 * Returns as soon as the input is durable and ACKed, so the caller's serial queue
 * advances immediately; `.transcription` is the tracked background task, awaited by
 * `finalizeDigest` (bounded) and by tests.
 *
 * @returns {Promise<{seq:number, attachment:string, transcription:Promise<void>}>}
 */
export async function ingestVoice(chatId, { buffer, mime }, reply, opts = {}) {
  const b = await appendBlock(chatId, { type: "voice" });
  const n = b.seq + 1;
  const name = `${stampSecond()}-${b.seq}-voice${extFromMime(mime, ".ogg")}`;
  await saveAttachment(chatId, name, buffer);
  await updateBlock(chatId, b.seq, { attachment: name, mime });
  await reply(ack(n), { done: true });

  const transcription = trackTranscription(
    chatId,
    transcribeIntoBlock(chatId, b.seq, n, name, mime, reply, opts.stt)
  );
  return { seq: b.seq, attachment: name, transcription };
}

/** Image: persist, ACK, vision-caption (for title/tags at compile). */
export async function ingestImage(chatId, { buffer, mime, userCaption }, reply) {
  const b = await appendBlock(chatId, { type: "image", userCaption: userCaption || "" });
  const n = b.seq + 1;
  const name = `${stampSecond()}-${b.seq}-img${extFromMime(mime, ".jpg")}`;
  await saveAttachment(chatId, name, buffer);
  await updateBlock(chatId, b.seq, { attachment: name, mime });
  await reply(ack(n), { done: true });

  const caption = await captionImage(pendingAttachmentPath(chatId, name), mime || "image/jpeg");
  await updateBlock(chatId, b.seq, { visionCaption: caption });
  await reply(`🖼 已存 · image saved (#${n})`);
}

/** File/document: persist as attachment, ACK, done. */
export async function ingestFile(chatId, { buffer, origName, mime, userCaption }, reply) {
  const b = await appendBlock(chatId, { type: "file", userCaption: userCaption || "" });
  const n = b.seq + 1;
  const safeOrig = origName ? origName.replace(/[/\\]/g, "_") : `file${extFromMime(mime, ".bin")}`;
  const name = `${stampSecond()}-${b.seq}-${safeOrig}`;
  await saveAttachment(chatId, name, buffer);
  await updateBlock(chatId, b.seq, { attachment: name, mime });
  await reply(ack(n), { done: true });
  await reply(`📎 已存 · file saved: ${safeOrig} (#${n})`);
}
