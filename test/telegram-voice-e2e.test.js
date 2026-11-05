// The 2026-09/10 incident, replayed through the REAL Telegram adapter (src/bot.js).
//
// Seven times the log shows `queue task failed: fetch failed` and nothing else: a
// voice note arrived, the bot tried to download it BEFORE acknowledging it, the one
// bare fetch() failed, and Boyang got no reply at all. These tests feed a genuine
// Telegram update into grammY (`bot.handleUpdate`) with the Bot API and the network
// stubbed, and assert what Boyang sees in the chat.
//
// RED on the old code (no ACK, no message: silence). GREEN once media is ACKed first
// and downloaded on a retry ladder that always ends in an explicit outcome.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-tg-e2e-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.DIGEST_BOT_TOKEN = "123456:TEST-TOKEN";
process.env.ELEVENLABS_API_KEY = "el-test-key";
process.env.OPENAI_API_KEY = "";
process.env.TENCENT_TOKENHUB_API_KEY = ""; // title LLM falls back, offline
// Fast ladders: the shape is under test, not the wall-clock.
process.env.DIGEST_DOWNLOAD_MAX_ATTEMPTS = "3";
process.env.DIGEST_DOWNLOAD_BACKOFF_BASE_MS = "1";
process.env.DIGEST_DOWNLOAD_BACKOFF_MAX_MS = "2";
process.env.DIGEST_STT_BACKOFF_BASE_MS = "1";
process.env.DIGEST_STT_BACKOFF_MAX_MS = "2";
process.env.DIGEST_SEND_MAX_ATTEMPTS = "1";

const { buildBot } = await import("../src/bot.js");
const { drain } = await import("../src/queue.js");
const { waitForTranscriptions } = await import("../src/transcriptions.js");
const store = await import("../src/store.js");

const BOYANG = 411364623;
const OGG = Buffer.concat([Buffer.from("OggS"), Buffer.alloc(64, 7)]);

/** Network stub: Telegram getFile + file download, ElevenLabs. Script per URL kind. */
function installFetch({ download = [], stt = [] } = {}) {
  const calls = { getFile: 0, file: 0, stt: 0 };
  const real = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const u = String(url);
    const step = (list, i) => list[i] ?? list[list.length - 1];
    if (u.includes("/getFile")) {
      calls.getFile++;
      return new Response(JSON.stringify({ ok: true, result: { file_id: "F", file_path: "voice/file_7.oga" } }), { status: 200 });
    }
    if (u.includes("/file/bot")) {
      // Where the incident happened: the raw download of the file bytes.
      const s = step(download, calls.file++);
      if (s === "fail") throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNRESET", message: "socket hang up" } });
      return new Response(OGG, { status: 200, headers: { "content-type": "audio/ogg" } });
    }
    if (u.includes("elevenlabs")) {
      const s = step(stt.length ? stt : ["ok"], calls.stt++);
      if (s === "503") return new Response("down", { status: 503 });
      return new Response(JSON.stringify({ text: "这是一条很长的语音 a long voice note", language_code: "zho" }), { status: 200 });
    }
    throw new Error(`unexpected fetch ${u}`);
  };
  return { calls, restore: () => (globalThis.fetch = real) };
}

/** A bot whose Bot API calls are captured instead of sent. */
function harness() {
  const bot = buildBot();
  bot.botInfo = { id: 1, is_bot: true, first_name: "Digest", username: "digest_test_bot" };
  const sent = [];
  bot.api.config.use(async (prev, method, payload) => {
    if (method === "sendMessage") {
      sent.push({ text: payload.text, buttons: payload.reply_markup?.inline_keyboard?.flat().map((b) => b.callback_data) || [] });
      return { ok: true, result: { message_id: sent.length, date: 0, chat: { id: payload.chat_id, type: "private" } } };
    }
    if (method === "getFile") {
      // The OLD code fetched the file path through the Bot API; answer it the same
      // way the network stub does so both versions hit the same failure point.
      return { ok: true, result: { file_id: payload.file_id, file_path: "voice/file_7.oga" } };
    }
    return { ok: true, result: true };
  });
  return { bot, sent };
}

let updateId = 1;
function voiceUpdate(chatId, duration = 312) {
  return {
    update_id: updateId++,
    message: {
      message_id: updateId,
      date: Math.floor(Date.now() / 1000),
      chat: { id: chatId, type: "private" },
      from: { id: BOYANG, is_bot: false, first_name: "Boyang" },
      voice: { file_id: "VOICE-FILE-ID", file_unique_id: "U1", duration, mime_type: "audio/ogg", file_size: 150000 },
    },
  };
}

async function settle(chatId) {
  await drain(chatId);
  await waitForTranscriptions(chatId, { timeoutMs: 10_000 });
  await drain(chatId);
}

test("download keeps failing → ACK first, retry notices, then an explicit apology with Retry (never silence)", async () => {
  const net = installFetch({ download: ["fail"] });
  const { bot, sent } = harness();
  try {
    await bot.handleUpdate(voiceUpdate(BOYANG));
    await settle(BOYANG);
  } finally {
    net.restore();
  }
  const texts = sent.map((s) => s.text);
  assert.ok(texts.length >= 2, `Boyang must hear SOMETHING, got ${JSON.stringify(texts)}`);
  assert.match(texts[0], /ACK #1/, "the very first message is the ACK");
  assert.match(texts[0], /5:12/, "the ACK says how long the voice note is");
  assert.ok(sent[0].buttons.includes("digest_done"), "ACK carries the Done button");
  assert.ok(texts.some((t) => /download retry 2\/3/.test(t)), `retries are announced: ${JSON.stringify(texts)}`);
  const last = sent[sent.length - 1];
  assert.match(last.text, /抱歉|Sorry/);
  assert.match(last.text, /3 attempts/);
  assert.match(last.text, /ECONNRESET|fetch failed/, "the real network reason is surfaced, not just 'fetch failed'");
  assert.ok(last.buttons.includes("digest_retry:0"), "the apology offers Retry");

  // The reference is durable, so Retry (or a restart) can still fetch it.
  const m = await store.loadPending(BOYANG);
  assert.equal(m.blocks[0].source.fileId, "VOICE-FILE-ID");
  assert.equal(m.blocks[0].job.state, "failed");
  assert.equal(m.blocks[0].downloadFailure.attempts, 3);
  await store.clearPending(BOYANG);
});

test("download fails twice then succeeds → transcript delivered, and it lands in the note", async () => {
  const CHAT = BOYANG;
  const net = installFetch({ download: ["fail", "fail", "ok"] });
  const { bot, sent } = harness();
  try {
    await bot.handleUpdate(voiceUpdate(CHAT, 41));
    await settle(CHAT);
  } finally {
    net.restore();
  }
  const texts = sent.map((s) => s.text);
  assert.match(texts[0], /ACK #1/);
  assert.ok(texts.some((t) => /已转写 · transcribed \(#1\)/.test(t)), `got ${JSON.stringify(texts)}`);
  assert.ok(texts.some((t) => /这是一条很长的语音/.test(t)));
  assert.equal(net.calls.file, 3, "two failed downloads, then the one that worked");

  const { finalizeDigest } = await import("../src/finalize.js");
  const result = await finalizeDigest(CHAT);
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  assert.match(md, /> 这是一条很长的语音 a long voice note/);
  assert.match(md, /-0-voice\.ogg\]\]/, "the audio is embedded");
});

test("transcription vendor down → apology says audio is saved + recoverable, with Retry", async () => {
  const CHAT = BOYANG;
  process.env.DIGEST_STT_MAX_ATTEMPTS = "2"; // read at import time: affects nothing here
  const net = installFetch({ download: ["ok"], stt: ["503"] });
  const { bot, sent } = harness();
  try {
    await bot.handleUpdate(voiceUpdate(CHAT, 90));
    await settle(CHAT);
  } finally {
    net.restore();
  }
  const last = sent[sent.length - 1];
  assert.match(last.text, /抱歉|Sorry/);
  assert.match(last.text, /audio saved/);
  assert.ok(last.buttons.includes("digest_retry:0"));
  assert.ok(sent.some((s) => /transcription retry 2\/6/.test(s.text)), "STT retries are announced too");
  await store.clearPending(CHAT);
});
