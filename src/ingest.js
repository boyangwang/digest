// Ingest — handle one incoming input: persist → ACK → (media) start the job.
// Platform-agnostic: an adapter passes a `reply(text, opts)` sender and, for media,
// either the bytes (tests, legacy callers) or a stored REFERENCE plus a fetcher.
// Every message (ACK and result) carries the block's serial number; ACK carries Done.
//
// ACK-first is the rule for media too: the reference (Telegram file_id, Lark
// message_id + file_key) is written to the manifest and ACKed BEFORE any network
// call, so nothing that can fail - download, transcription - can make an input
// vanish without a word. See src/jobs.js for what happens after the ACK.
import { appendBlock, saveAttachment, updateBlock } from "./store.js";
import { extFromMime } from "./media.js";
import { startMediaJob, mediaAck, attachmentName, KIND } from "./jobs.js";

const ack = (n) => `✓ ACK #${n}`;

/** Text: persist verbatim, ACK (+Done button), done. */
export async function ingestText(chatId, text, reply) {
  const b = await appendBlock(chatId, { type: "text", text });
  const n = b.seq + 1;
  await reply(ack(n), { done: true });
  await reply(`📝 已存 · text saved (#${n})`);
}

/**
 * Voice / image / file by REFERENCE (the bot paths). Persists the reference, ACKs,
 * then downloads + processes off the queue.
 *
 * @param {object} input  { type: "voice"|"image"|"file", source, mime?, duration?, origName?, userCaption? }
 * @param {object} deps   { fetchMedia(source) -> fetcher, stt?, download?, progressNoticeMs? }
 * @returns {Promise<{seq:number, job:Promise<void>}>}
 */
export async function ingestMedia(chatId, input, reply, deps = {}) {
  const { type, source, mime, duration, origName, userCaption } = input;
  if (!KIND[type]) throw new Error(`ingestMedia: unknown type ${type}`);
  const b = await appendBlock(chatId, {
    type,
    source,
    mime: mime || null,
    ...(duration != null ? { duration } : {}),
    ...(origName ? { origName } : {}),
    ...(type !== "voice" ? { userCaption: userCaption || "" } : {}),
    job: { state: "pending" },
  });
  await reply(mediaAck(b.seq + 1, type, { duration, origName }), { done: true });
  const job = startMediaJob(chatId, b.seq, { reply, ...deps });
  return { seq: b.seq, job };
}

/** Bytes already in hand: persist them, ACK, then process off the queue. */
async function ingestBytes(chatId, type, { buffer, mime, origName, userCaption }, reply, deps = {}) {
  const b = await appendBlock(chatId, {
    type,
    mime: mime || null,
    ...(origName ? { origName } : {}),
    ...(type !== "voice" ? { userCaption: userCaption || "" } : {}),
    job: { state: "pending" },
  });
  const name = attachmentName(b, extFromMime(mime, KIND[type].fallbackExt));
  await saveAttachment(chatId, name, buffer);
  await updateBlock(chatId, b.seq, { attachment: name, bytes: buffer.length });
  await reply(mediaAck(b.seq + 1, type, { origName }), { done: true });
  const job = startMediaJob(chatId, b.seq, { reply, ...deps });
  return { seq: b.seq, attachment: name, job };
}

/**
 * Voice/audio with the bytes in hand: persist audio, ACK, then transcribe in the
 * background. The audio is saved and ACKed BEFORE transcription is attempted, so the
 * recording survives every transcription outcome.
 * @returns {Promise<{seq:number, attachment:string, transcription:Promise<void>}>}
 */
export async function ingestVoice(chatId, { buffer, mime }, reply, opts = {}) {
  const r = await ingestBytes(chatId, "voice", { buffer, mime }, reply, { stt: opts.stt, ...opts.deps });
  return { seq: r.seq, attachment: r.attachment, transcription: r.job };
}

/** Image with the bytes in hand: persist, ACK, vision-caption (for title/tags at compile). */
export async function ingestImage(chatId, { buffer, mime, userCaption }, reply, deps = {}) {
  return ingestBytes(chatId, "image", { buffer, mime, userCaption }, reply, deps);
}

/** File/document with the bytes in hand: persist as attachment, ACK, report. */
export async function ingestFile(chatId, { buffer, origName, mime, userCaption }, reply, deps = {}) {
  return ingestBytes(chatId, "file", { buffer, origName, mime, userCaption }, reply, deps);
}
