// Transcription runs OUTSIDE the per-chat serial queue, and /done waits for it.
//
// These four tests are the contract for that split. They go RED if transcription is
// put back on the queue (2nd message stops being ACKed promptly), if the late
// transcript stops landing in its reserved slot, if /done stops waiting, or if the
// wait stops being bounded.
//
// No live vendor: the transport is stubbed and gated by the test itself.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-flow-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.ELEVENLABS_API_KEY = "el-test-key"; // configured, but never really called
process.env.OPENAI_API_KEY = "";
process.env.TENCENT_TOKENHUB_API_KEY = ""; // LLM falls back — offline

const store = await import("../src/store.js");
const { ingestVoice, ingestText } = await import("../src/ingest.js");
const { finalizeDigest } = await import("../src/finalize.js");
const { enqueue, drain } = await import("../src/queue.js");
const { inflightCount } = await import("../src/transcriptions.js");

const AUDIO = Buffer.from("pretend this is a voice note");
const settle = () => new Promise((r) => setImmediate(r));

function collector() {
  const sent = [];
  return { sent, reply: async (text) => void sent.push(text) };
}

/**
 * A transport that hangs until the test releases it, then answers 200. Stands in
 * for a vendor that is slow or is being retried through the backoff ladder.
 */
function gatedTransport(text) {
  let release;
  const gate = new Promise((r) => (release = r));
  const stt = {
    fetchImpl: async () => {
      await gate;
      return {
        ok: true,
        status: 200,
        text: async () => "",
        json: async () => ({ text, language_code: "zho" }),
      };
    },
    sleepImpl: async () => {},
  };
  return { stt, release };
}

test("a second message is ACKed while a prior voice note is still transcribing", async () => {
  const CHAT = 9001;
  const { sent, reply } = collector();
  const { stt, release } = gatedTransport("迟到的转写文本");

  // Exactly how bot.js drives it: both inputs through the same per-chat queue.
  const voice = enqueue(CHAT, () => ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply, { stt }));
  const text = enqueue(CHAT, () => ingestText(CHAT, "second message 第二条", reply));

  // The queue drains without waiting for the vendor. If transcription were still on
  // the queue this would deadlock until the gate opened, and time out.
  await text;
  assert.ok(sent.includes("✓ ACK #2"), `#2 must be ACKed while #1 transcribes, got ${JSON.stringify(sent)}`);
  assert.equal(inflightCount(CHAT), 1, "the transcription is still running");

  release();
  const { transcription } = await voice;
  await transcription;
  assert.equal(inflightCount(CHAT), 0);
  await store.clearPending(CHAT);
});

test("a late transcript lands in its ORIGINAL slot, not appended at the end", async () => {
  const CHAT = 9002;
  const { reply } = collector();
  const { stt, release } = gatedTransport("这是第一条语音的内容");

  const voice = enqueue(CHAT, () => ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply, { stt }));
  await enqueue(CHAT, () => ingestText(CHAT, "text arrived second 文字第二", reply));

  release();
  const { transcription } = await voice;
  await transcription;

  const m = await store.loadPending(CHAT);
  assert.equal(m.blocks[0].type, "voice");
  assert.equal(m.blocks[0].transcript, "这是第一条语音的内容");
  assert.equal(m.blocks[1].type, "text");

  const result = await finalizeDigest(CHAT);
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  const body = md.split("\n---\n")[1];
  assert.ok(
    body.indexOf("这是第一条语音的内容") < body.indexOf("text arrived second"),
    "the voice block must still render before the text that arrived after it"
  );
});

test("/done waits for an in-flight transcription before compiling", async () => {
  const CHAT = 9003;
  const { reply } = collector();
  const { stt, release } = gatedTransport("必须等到的转写");

  const { transcription } = await ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply, { stt });

  let done = false;
  const finalize = finalizeDigest(CHAT).then((r) => {
    done = true;
    return r;
  });
  await settle();
  await settle();
  assert.equal(done, false, "finalize must not compile while the transcript is still coming");

  release();
  await transcription;
  const result = await finalize;
  assert.ok(done);

  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  assert.match(md, /> 必须等到的转写/);
  assert.doesNotMatch(md, /Transcription unavailable/);
});

test("/done still completes when the wait bound is hit, and the note carries the marker", async () => {
  const CHAT = 9004;
  const { sent, reply } = collector();
  const { stt, release } = gatedTransport("太晚了 too late");

  const { transcription } = await ingestVoice(CHAT, { buffer: AUDIO, mime: "audio/ogg" }, reply, { stt });

  // The bound is what stops /done hanging forever behind a stuck vendor.
  const result = await finalizeDigest(CHAT, { transcriptionWaitMs: 25 });
  assert.ok(result, "finalize must complete even with a transcription still in flight");

  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  assert.match(md, /> \[Transcription unavailable/);
  assert.match(md, /npm run retranscribe/);

  // Let the straggler finish so it cannot leak into another test (or hold the loop
  // open). Its updateBlock finds no pending digest and must not resurrect one.
  release();
  await transcription;
  assert.equal(await store.loadPending(CHAT), null, "a late transcript must not recreate the compiled digest");

  // And the user must be told the truth: the words arrived too late for this note,
  // NOT that they were saved into a note that carries the failure marker.
  const last = sent[sent.length - 1];
  assert.match(last, /arrived after the note was compiled/);
  assert.match(last, /太晚了 too late/, "the words themselves are still handed back");
  assert.ok(
    !sent.some((m) => m.startsWith("🎙️ 已转写")),
    "a straggler must never claim the transcript was written into the note"
  );
});
