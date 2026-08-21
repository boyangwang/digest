// Ingest — the guarantee that matters: the audio is on disk no matter what
// transcription does. Runs with every vendor key blanked, so `transcribe()`
// reports total failure without touching the network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, existsSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-ingest-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.ELEVENLABS_API_KEY = ""; // no vendor configured →
process.env.OPENAI_API_KEY = ""; //    transcription fails totally, offline
process.env.TENCENT_TOKENHUB_API_KEY = ""; // LLM falls back too

const store = await import("../src/store.js");
const { ingestVoice, ingestFile } = await import("../src/ingest.js");
const { finalizeDigest } = await import("../src/finalize.js");
const { STT_FAIL } = await import("../src/stt.js");

function collector() {
  const sent = [];
  return { sent, reply: async (text) => void sent.push(text) };
}

const AUDIO = Buffer.from("pretend this is a 4-minute voice note");

test("total transcription failure still saves the audio and ACKs", async () => {
  const CHAT = 8801;
  const { sent, reply } = collector();
  await ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply);

  const m = await store.loadPending(CHAT);
  const b = m.blocks[0];

  // 1. the recording itself is on disk, byte-for-byte
  assert.ok(b.attachment, "block records the attachment name");
  const path = store.pendingAttachmentPath(CHAT, b.attachment);
  assert.ok(existsSync(path), "audio file must exist even though transcription failed");
  assert.deepEqual(readFileSync(path), AUDIO);

  // 2. the failure is durable and diagnosable, not a silent null
  assert.equal(b.transcript, null);
  assert.equal(b.sttFailure.reason, STT_FAIL.UNCONFIGURED);
  assert.ok(b.sttFailure.at, "failure is timestamped");

  // 3. Boyang is told the audio is safe AND how to get the words back
  const ackMsg = sent[0];
  const failMsg = sent[1];
  assert.match(ackMsg, /ACK #1/);
  assert.match(failMsg, /audio saved/);
  assert.match(failMsg, /recoverable/);
  assert.match(failMsg, new RegExp(`retranscribe -- "${b.attachment}"`));
  assert.doesNotMatch(failMsg, /transcription unavailable/); // the dead-end wording is gone

  await store.clearPending(CHAT);
});

test("a failed transcript survives into the note as a retryable marker, audio embedded", async () => {
  const CHAT = 8802;
  const { reply } = collector();
  await ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply);
  await ingestFile(CHAT, { buffer: Buffer.from("%PDF"), origName: "r.pdf", mime: "application/pdf" }, reply);

  const pending = await store.loadPending(CHAT);
  const audioName = pending.blocks[0].attachment;

  const result = await finalizeDigest(CHAT);
  assert.ok(result);

  // the .ogg made it into the vault, not just the pending dir
  const attDir = join(process.env.DIGEST_OUT_DIR, "ATTACHMENTS");
  assert.ok(readdirSync(attDir).includes(audioName));

  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  assert.match(md, new RegExp(`!\\[\\[Heresy-Anthology/digest/ATTACHMENTS/${audioName}\\]\\]`));
  assert.match(md, /> \[Transcription unavailable \(unconfigured\)\]/);
  assert.match(md, new RegExp(`retranscribe -- "${audioName}"`));
});
