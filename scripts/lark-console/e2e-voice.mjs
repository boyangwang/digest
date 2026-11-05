#!/usr/bin/env node
// e2e-voice.mjs - drive a REAL Lark audio message through the bot's voice pipeline.
//
// Lark web cannot record voice, so: the bot uploads an opus clip and posts it to the
// chat as a genuine `audio` message; then this script replays the exact
// im.message.receive_v1 event Lark delivers for a user's voice note (Boyang's open_id,
// that real message_id + file_key) into the real handlers. Everything after that is
// production code against production Lark + ElevenLabs: download over the retry
// ladder, Ogg sniffing, transcription, chat replies.
//
//   secret-run node scripts/lark-console/e2e-voice.mjs <chat_id> <clip.opus> <duration_ms>
// Run with the same DIGEST_DATA_DIR/DIGEST_OUT_DIR as the bot under test.
import "../../src/net-tuning.js";
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { createLarkApi, createLarkHandlers, toChatKey } from "../../src/lark.js";
import { LARK_APP_ID, LARK_APP_SECRET, LARK_API_BASE, BOYANG_LARK_OPEN_ID } from "../../src/config.js";
import { drain } from "../../src/queue.js";
import { waitForTranscriptions } from "../../src/transcriptions.js";

const [chatId, clip, durationMs = "8000"] = process.argv.slice(2);
if (!chatId || !clip) {
  console.error("usage: e2e-voice.mjs <chat_id> <clip.opus> [duration_ms]");
  process.exit(64);
}
const api = createLarkApi({ appId: LARK_APP_ID, appSecret: LARK_APP_SECRET });

// 1. upload the clip as an opus file and post it as an audio message
const form = new FormData();
form.append("file_type", "opus");
form.append("file_name", basename(clip));
form.append("duration", String(durationMs));
form.append("file", new Blob([readFileSync(clip)], { type: "audio/ogg" }), basename(clip));
const tok = await api.tenantToken();
const up = await (await fetch(`${LARK_API_BASE}/open-apis/im/v1/files`, { method: "POST", headers: { authorization: `Bearer ${tok}` }, body: form })).json();
if (up.code !== 0) throw new Error(`upload failed: ${JSON.stringify(up)}`);
const fileKey = up.data.file_key;
const sent = await api.sendMessage(chatId, "audio", { file_key: fileKey });
// Lark re-keys the file on send: the key INSIDE the message is not the upload key.
const got = await api.call("GET", `/open-apis/im/v1/messages/${sent.message_id}`);
const msgContent = JSON.parse(got.items[0].body.content);
console.log(`posted audio message ${sent.message_id} upload_key=${fileKey} message_key=${msgContent.file_key}`);

// 2. replay the receive event exactly as Lark delivers a user's voice note
const handlers = createLarkHandlers({ api });
await handlers.onMessage({
  schema: "2.0",
  event_id: `e2e-${Date.now()}`,
  event_type: "im.message.receive_v1",
  sender: { sender_id: { open_id: BOYANG_LARK_OPEN_ID }, sender_type: "user" },
  message: {
    message_id: sent.message_id,
    chat_id: chatId,
    chat_type: "p2p",
    message_type: "audio",
    content: JSON.stringify({ file_key: msgContent.file_key, duration: Number(durationMs) }),
  },
});
const key = toChatKey(chatId);
await drain(key);
await waitForTranscriptions(key, { timeoutMs: 600_000 });
await drain(key);
console.log("voice job finished");
