#!/usr/bin/env node
// telegram-live-voice.mjs - drive a REAL Telegram voice file through the production
// voice pipeline, against the real Bot API, without a human sending it.
//
// The bot posts the clip into the chat itself (silently: disable_notification), which
// yields a genuine file_id. Then the production code takes over exactly as for a voice
// note Boyang sends: ingestMedia -> ACK -> getFile + download from api.telegram.org
// (the step that failed with "fetch failed") on the retry ladder -> transcription ->
// replies, all sent silently so nobody is woken up.
//
//   secret-run node scripts/e2e/telegram-live-voice.mjs <chat_id> <clip.ogg>
// Run with throwaway DIGEST_DATA_DIR / DIGEST_OUT_DIR.
import "../../src/net-tuning.js";
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { Api } from "grammy";
import { BOT_TOKEN } from "../../src/config.js";
import { ingestMedia } from "../../src/ingest.js";
import { makeSender, telegramDeps } from "../../src/bot.js";
import { waitForTranscriptions } from "../../src/transcriptions.js";
import { InputFile } from "grammy";

const [chatArg, clip] = process.argv.slice(2);
const chatId = Number(chatArg);
const api = new Api(BOT_TOKEN);
// Every message this test sends is silent.
api.config.use((prev, method, payload, signal) => prev(method, { ...payload, disable_notification: true }, signal));

const t0 = Date.now();
const since = () => `+${((Date.now() - t0) / 1000).toFixed(1)}s`;
const posted = await api.sendVoice(chatId, new InputFile(readFileSync(clip), basename(clip)), {
  caption: "E2E test clip (sent by the bot itself) · 端到端测试语音",
});
const v = posted.voice;
console.log(`${since()} posted voice file_id=${v.file_id.slice(0, 12)}… duration=${v.duration}s size=${v.file_size}`);

const real = makeSender(api, chatId);
const reply = async (text, opts) => {
  console.log(`${since()} BOT -> ${JSON.stringify(text).slice(0, 220)}${opts?.done ? " [Done]" : ""}${opts?.retry != null ? " [Retry]" : ""}`);
  await real(text, opts);
};
const deps = { ...telegramDeps(api, chatId), reply };
await ingestMedia(
  chatId,
  { type: "voice", mime: v.mime_type, duration: v.duration, source: { platform: "telegram", fileId: v.file_id, fileUniqueId: v.file_unique_id } },
  reply,
  deps
);
await waitForTranscriptions(chatId, { timeoutMs: 1_800_000 });
console.log(`${since()} pipeline finished`);
