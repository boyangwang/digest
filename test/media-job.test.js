// The media pipeline's guarantees, platform-free: download ladder, deadline, sniffing,
// restart resume, /done not blocking ACKs, and the Telegram fetcher's hygiene.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-media-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.ELEVENLABS_API_KEY = "el-test-key";
process.env.OPENAI_API_KEY = "";
process.env.TENCENT_TOKENHUB_API_KEY = "";

const { downloadMedia, sniffMedia, MediaHttpError, MediaTransportError, DL_FAIL } = await import("../src/media.js");
const { telegramFetcher } = await import("../src/telegram-api.js");
const { ingestMedia, ingestText } = await import("../src/ingest.js");
const { resumeJobs, needsResume } = await import("../src/jobs.js");
const { runFinalize } = await import("../src/commands.js");
const { enqueue, drain } = await import("../src/queue.js");
const { waitForTranscriptions } = await import("../src/transcriptions.js");
const { chunkText } = await import("../src/send.js");
const { errorDetail } = await import("../src/retry.js");
const store = await import("../src/store.js");

const fast = { sleepImpl: async () => {}, backoffBaseMs: 1, backoffMaxMs: 1 };
const OGG = Buffer.concat([Buffer.from("OggS"), Buffer.alloc(20)]);
const sttOk = (text) => ({
  fetchImpl: async () => new Response(JSON.stringify({ text, language_code: "eng" }), { status: 200 }),
  sleepImpl: async () => {},
});

function collector() {
  const sent = [];
  return { sent, reply: async (text, opts = {}) => void sent.push({ text, opts }) };
}

// --- download ladder --------------------------------------------------------

test("downloadMedia: transient failures are retried and every retry is announced", async () => {
  let calls = 0;
  const notices = [];
  const r = await downloadMedia(
    async () => {
      calls++;
      if (calls < 3) throw new MediaTransportError("telegram", new TypeError("fetch failed"));
      return Buffer.from("bytes");
    },
    { platform: "telegram", maxAttempts: 5, attemptTimeoutMs: 1000, totalBudgetMs: 10_000, onRetry: (i) => notices.push(i.attempt), ...fast }
  );
  assert.equal(r.ok, true);
  assert.equal(r.attempts, 3);
  assert.deepEqual(notices, [1, 2]);
});

test("downloadMedia: a fetcher that ignores its timeout is cut off by the deadline and retried", async () => {
  let calls = 0;
  const t0 = Date.now();
  const r = await downloadMedia(
    async () => {
      calls++;
      if (calls === 1) return new Promise(() => {}); // stalls forever
      return Buffer.from("second try");
    },
    { platform: "lark", maxAttempts: 3, attemptTimeoutMs: 40, totalBudgetMs: 5000, ...fast }
  );
  assert.equal(r.ok, true);
  assert.equal(calls, 2);
  assert.ok(Date.now() - t0 < 2000);
});

test("downloadMedia: a definitive 'no' stops at once; too-big is named", async () => {
  let calls = 0;
  const r = await downloadMedia(
    async () => {
      calls++;
      throw new MediaHttpError("telegram", 400, "getFile: Bad Request: file is too big");
    },
    { platform: "telegram", maxAttempts: 5, attemptTimeoutMs: 1000, totalBudgetMs: 10_000, ...fast }
  );
  assert.equal(r.ok, false);
  assert.equal(r.reason, DL_FAIL.TOO_BIG);
  assert.equal(calls, 1);
});

test("errorDetail surfaces the hidden cause behind 'fetch failed'", () => {
  const e = new TypeError("fetch failed", { cause: Object.assign(new Error("connect timeout"), { code: "UND_ERR_CONNECT_TIMEOUT" }) });
  assert.equal(errorDetail(e), "fetch failed <- UND_ERR_CONNECT_TIMEOUT: connect timeout");
});

test("sniffMedia identifies the containers that matter", () => {
  assert.equal(sniffMedia(OGG).ext, ".ogg");
  assert.equal(sniffMedia(Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(20)])).ext, ".jpg");
  assert.equal(sniffMedia(Buffer.concat([Buffer.from("ID3"), Buffer.alloc(20)])).ext, ".mp3");
  assert.equal(sniffMedia(Buffer.alloc(20)), null);
});

// --- Telegram fetcher ---------------------------------------------------------

test("telegramFetcher: getFile then the file URL; the bot token never appears in an error", async () => {
  const urls = [];
  const fetchImpl = async (url) => {
    urls.push(url);
    if (url.includes("getFile")) return new Response(JSON.stringify({ ok: true, result: { file_path: "voice/a.oga" } }));
    return new Response("nope", { status: 502 });
  };
  const f = telegramFetcher({ token: "SECRET-TOKEN", fileId: "abc/=", fetchImpl });
  await assert.rejects(f({ timeoutMs: 1000 }), (e) => {
    assert.ok(e instanceof MediaHttpError);
    assert.equal(e.status, 502);
    assert.ok(!String(e.message + e.body).includes("SECRET-TOKEN"));
    return true;
  });
  assert.equal(urls[0], "https://api.telegram.org/botSECRET-TOKEN/getFile?file_id=abc%2F%3D");
  assert.equal(urls[1], "https://api.telegram.org/file/botSECRET-TOKEN/voice/a.oga");
});

test("telegramFetcher: getFile's 'file is too big' is a terminal error", async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ ok: false, error_code: 400, description: "Bad Request: file is too big" }), { status: 400 });
  const r = await downloadMedia(telegramFetcher({ token: "t", fileId: "f", fetchImpl }), {
    platform: "telegram",
    maxAttempts: 4,
    attemptTimeoutMs: 1000,
    totalBudgetMs: 5000,
    ...fast,
  });
  assert.equal(r.reason, DL_FAIL.TOO_BIG);
  assert.equal(r.attempts, 1);
});

test("chunkText splits long transcripts for Telegram's 4096-char limit, never mid-emoji", () => {
  const long = "😀".repeat(3000); // 6000 UTF-16 units
  const parts = chunkText(long, 4000);
  assert.ok(parts.every((p) => p.length <= 4000));
  assert.equal(parts.join(""), long);
  assert.ok(parts.every((p) => !/[\ud800-\udbff]$/.test(p)));
});

// --- the job, ACK-first ---------------------------------------------------------

test("ingestMedia ACKs BEFORE the download even starts", async () => {
  const CHAT = 7001;
  const { sent, reply } = collector();
  let release;
  const gate = new Promise((r) => (release = r));
  let started = false;
  const fetchMedia = () => async () => {
    started = true;
    await gate;
    return OGG;
  };
  const { job } = await ingestMedia(
    CHAT,
    { type: "voice", source: { platform: "telegram", fileId: "x" }, mime: "audio/ogg", duration: 75 },
    reply,
    { fetchMedia, stt: sttOk("hello"), download: fast }
  );
  assert.match(sent[0].text, /ACK #1 · 🎙️ 收到语音 \(1:15\)/);
  assert.equal(sent[0].opts.done, true);
  release();
  await job;
  assert.ok(started);
  assert.match(sent[sent.length - 1].text, /transcribed \(#1\):\n\n> hello/);
  await store.clearPending(CHAT);
});

test("a long job says once that it is still working", async () => {
  const CHAT = 7002;
  const { sent, reply } = collector();
  const fetchMedia = () => async () => {
    await new Promise((r) => setTimeout(r, 80));
    return OGG;
  };
  const { job } = await ingestMedia(CHAT, { type: "voice", source: { platform: "lark" } }, reply, {
    fetchMedia,
    stt: sttOk("slow but fine"),
    download: fast,
    progressNoticeMs: 20,
  });
  await job;
  assert.equal(sent.filter((s) => /still working/.test(s.text)).length, 1);
  await store.clearPending(CHAT);
});

test("restart: a job left pending by a crash is resumed, and the user is told", async () => {
  const CHAT = 7003;
  // What a crash mid-download leaves behind: the reference, state pending, no bytes.
  await store.appendBlock(CHAT, { type: "voice", source: { platform: "telegram", fileId: "F" }, mime: "audio/ogg", job: { state: "pending" } });
  await store.appendBlock(CHAT, { type: "text", text: "after" });
  const m = await store.loadPending(CHAT);
  assert.equal(needsResume(m.blocks[0]), true);
  assert.equal(needsResume(m.blocks[1]), false);

  const { sent, reply } = collector();
  const resumed = await resumeJobs([CHAT], () => ({
    reply,
    fetchMedia: () => async () => OGG,
    stt: sttOk("resumed words"),
    download: fast,
  }));
  assert.equal(resumed, 1);
  await waitForTranscriptions(CHAT, { timeoutMs: 5000 });
  assert.match(sent[0].text, /♻️ #1/);
  const after = await store.loadPending(CHAT);
  assert.equal(after.blocks[0].transcript, "resumed words");
  assert.equal(after.blocks[0].job.state, "done");
  await store.clearPending(CHAT);
});

test("/done waits OUTSIDE the queue: a message sent meanwhile is ACKed at once, and joins the note", async () => {
  const CHAT = 7004;
  const { sent, reply } = collector();
  let release;
  const gate = new Promise((r) => (release = r));
  await ingestMedia(CHAT, { type: "voice", source: { platform: "telegram", fileId: "slow" } }, reply, {
    fetchMedia: () => async () => {
      await gate;
      return OGG;
    },
    stt: sttOk("the slow voice note"),
    download: fast,
  });
  const done = runFinalize(CHAT, reply);
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(sent.some((s) => /still processing/.test(s.text)), "the user is told /done is waiting");

  // The ACK for a message sent during the wait must not sit behind /done.
  await enqueue(CHAT, () => ingestText(CHAT, "sent while waiting", reply));
  assert.ok(sent.some((s) => s.text === "✓ ACK #2"), JSON.stringify(sent.map((s) => s.text)));

  release();
  await done;
  await drain(CHAT);
  const saved = sent.find((s) => /SAVED/.test(s.text));
  assert.ok(saved, JSON.stringify(sent.map((s) => s.text)));
  const file = saved.text.match(/<code>([^<]+)<\/code>/)[1].replace(/&amp;/g, "&");
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, file), "utf8");
  assert.match(md, /> the slow voice note/);
  assert.match(md, /sent while waiting/);
});

test("a voice note whose download failed compiles to an honest marker, not a broken embed", async () => {
  const CHAT = 7005;
  const { reply } = collector();
  const { job } = await ingestMedia(CHAT, { type: "voice", source: { platform: "telegram", fileId: "dead" } }, reply, {
    fetchMedia: () => async () => {
      throw new MediaHttpError("telegram", 404, "file gone");
    },
    download: fast,
  });
  await job;
  const { finalizeDigest } = await import("../src/finalize.js");
  const result = await finalizeDigest(CHAT);
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  assert.match(md, /\[Voice message could not be downloaded \(gone, 1 attempt\)\]/);
  assert.doesNotMatch(md, /undefined/);
  assert.doesNotMatch(md, /Transcription unavailable/, "never eligible for retranscribe: there is no audio");
});
