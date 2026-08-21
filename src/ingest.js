// Ingest — handle one incoming input: persist → ACK → process → Processed.
// grammy-agnostic: the bot passes buffers + a `reply(text, opts)` sender, so this is testable.
// Every message (ACK and processed) carries the block's serial number; ACK carries the Done button.
import { appendBlock, saveAttachment, updateBlock, pendingAttachmentPath } from "./store.js";
import { stampSecond } from "./util.js";
import { transcribe } from "./stt.js";
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

/**
 * Voice/audio: persist audio, ACK, transcribe, show transcript.
 *
 * The audio is saved and ACKed BEFORE transcription is attempted, so the recording
 * survives every transcription outcome - including total vendor failure. When the
 * transcript does not arrive, the block keeps a durable failure marker (reason +
 * attachment name) and the reply tells Boyang the audio is safe and how to recover
 * the words later, instead of a dead-end "transcription unavailable".
 */
export async function ingestVoice(chatId, { buffer, mime }, reply) {
  const b = await appendBlock(chatId, { type: "voice" });
  const n = b.seq + 1;
  const name = `${stampSecond()}-${b.seq}-voice${extFromMime(mime, ".ogg")}`;
  await saveAttachment(chatId, name, buffer);
  await updateBlock(chatId, b.seq, { attachment: name, mime });
  await reply(ack(n), { done: true });

  const r = await transcribe(pendingAttachmentPath(chatId, name), { mime });
  if (r.ok) {
    await updateBlock(chatId, b.seq, {
      transcript: r.text,
      sttProvider: r.provider,
      sttAttempts: r.attempts,
      sttFailure: null,
    });
    await reply(`🎙️ 已转写 · transcribed (#${n}):\n\n> ${r.text}`);
    return;
  }

  await updateBlock(chatId, b.seq, {
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
      `↻ 笔记保存后可重跑 · after the note is saved, re-run:\n` +
      `npm run retranscribe -- "${name}"`
  );
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
