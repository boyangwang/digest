// The Lark ("Sleep Digest") transport: same product as Telegram, driven end to end
// through the real handlers against a fake Lark Open API (no network). What is
// asserted is what Boyang sees in the Lark chat and what lands in the vault.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-lark-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.ELEVENLABS_API_KEY = "el-test-key";
process.env.OPENAI_API_KEY = "";
process.env.TENCENT_TOKENHUB_API_KEY = "";
process.env.DIGEST_DOWNLOAD_MAX_ATTEMPTS = "3";
process.env.DIGEST_DOWNLOAD_BACKOFF_BASE_MS = "1";
process.env.DIGEST_DOWNLOAD_BACKOFF_MAX_MS = "2";
process.env.DIGEST_SEND_MAX_ATTEMPTS = "2";

const { parseLarkMessage, createLarkApi, createLarkHandlers, buildCard, htmlToLarkMarkdown, toChatKey } = await import(
  "../src/lark.js"
);
const { drain } = await import("../src/queue.js");
const { waitForTranscriptions } = await import("../src/transcriptions.js");
const store = await import("../src/store.js");

const BOYANG = "ou_boyang_sleep_digest";
const STRANGER = "ou_somebody_else";
const OGG = Buffer.concat([Buffer.from("OggS"), Buffer.alloc(40, 3)]);
const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(40, 1)]);

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8" } });

/**
 * A fake Lark Open API + ElevenLabs. `resources[fileKey]` is a list of outcomes per
 * call: "ok" | "500" | "gone"; the last one repeats.
 */
function fakeLark({ resources = {}, sendScript = [] } = {}) {
  const sent = [];
  const counts = { token: 0, send: 0, res: {} };
  const fetchImpl = async (url, init = {}) => {
    const u = new URL(String(url));
    if (u.pathname === "/open-apis/auth/v3/tenant_access_token/internal") {
      counts.token++;
      return json({ code: 0, msg: "ok", tenant_access_token: `t-${counts.token}`, expire: 7200 });
    }
    if (u.pathname === "/open-apis/im/v1/messages" && init.method === "POST") {
      const step = sendScript[counts.send++];
      if (step === "stale-token") return json({ code: 99991663, msg: "Invalid access token" }, 400);
      const body = JSON.parse(init.body);
      sent.push({ type: body.msg_type, content: JSON.parse(body.content), chat: body.receive_id, auth: init.headers.authorization });
      return json({ code: 0, msg: "ok", data: { message_id: `om_bot_${sent.length}` } });
    }
    const m = u.pathname.match(/^\/open-apis\/im\/v1\/messages\/([^/]+)\/resources\/([^/]+)$/);
    if (m) {
      const key = decodeURIComponent(m[2]);
      const list = resources[key] || ["ok"];
      const i = (counts.res[key] = (counts.res[key] || 0) + 1) - 1;
      const step = list[i] ?? list[list.length - 1];
      if (step === "500") return json({ code: 9499, msg: "internal error" }, 500);
      if (step === "gone") return json({ code: 234003, msg: "file not exist in message" }, 400);
      const bytes = u.searchParams.get("type") === "image" ? PNG : OGG;
      return new Response(bytes, { status: 200, headers: { "content-type": "application/octet-stream" } });
    }
    if (String(url).includes("elevenlabs")) {
      return json({ text: "飞书语音转写 lark voice transcript", language_code: "zho" });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  return { fetchImpl, sent, counts };
}

let n = 0;
function msgEvent(chatId, type, content, { openId = BOYANG, senderType = "user", messageId } = {}) {
  n++;
  return {
    schema: "2.0",
    event_id: `ev_${n}`,
    event_type: "im.message.receive_v1",
    sender: { sender_id: { open_id: openId }, sender_type: senderType, tenant_key: "t" },
    message: {
      message_id: messageId || `om_${n}`,
      chat_id: chatId,
      chat_type: "p2p",
      message_type: type,
      content: JSON.stringify(content),
      create_time: String(Date.now()),
    },
  };
}

function setup(opts) {
  const fake = fakeLark(opts);
  const api = createLarkApi({ appId: "cli_x", appSecret: "s", base: "https://open.larksuite.com", fetchImpl: fake.fetchImpl });
  // STT goes through the same fake network; its real ladder runs.
  const handlers = createLarkHandlers({
    api,
    allowedOpenIds: new Set([BOYANG]),
    depsOverride: { stt: { fetchImpl: fake.fetchImpl, sleepImpl: async () => {} } },
  });
  return { ...fake, api, handlers };
}

const textOf = (s) =>
  s.type === "text" ? s.content.text : s.content.elements.map((e) => e.text?.content ?? e.content ?? "").join("\n");
const buttonsOf = (s) =>
  s.type === "interactive"
    ? s.content.elements.filter((e) => e.tag === "action").flatMap((e) => e.actions.map((a) => a.value.action))
    : [];

async function settle(chatId) {
  const key = toChatKey(chatId);
  await drain(key);
  await waitForTranscriptions(key, { timeoutMs: 10_000 });
  await drain(key);
}

// ---------------------------------------------------------------------------
// parsing
// ---------------------------------------------------------------------------

test("parse: text, /done, audio (ms → s), image, file, sticker", () => {
  const t = parseLarkMessage(msgEvent("oc_1", "text", { text: "hello 你好" }));
  assert.deepEqual(t.items, [{ kind: "text", text: "hello 你好" }]);
  assert.equal(t.chatKey, "lark_oc_1");
  assert.equal(t.openId, BOYANG);

  assert.deepEqual(parseLarkMessage(msgEvent("oc_1", "text", { text: "/done" })).items, [{ kind: "command", name: "done" }]);

  const a = parseLarkMessage(msgEvent("oc_1", "audio", { file_key: "file_v3_a", duration: 61500 }, { messageId: "om_a" }));
  assert.equal(a.items[0].type, "voice");
  assert.equal(a.items[0].duration, 62);
  assert.deepEqual(a.items[0].source, { platform: "lark", messageId: "om_a", fileKey: "file_v3_a", resType: "file" });

  assert.equal(parseLarkMessage(msgEvent("oc_1", "image", { image_key: "img_1" })).items[0].source.resType, "image");
  const f = parseLarkMessage(msgEvent("oc_1", "file", { file_key: "file_1", file_name: "plan.pdf" }));
  assert.equal(f.items[0].origName, "plan.pdf");
  assert.deepEqual(parseLarkMessage(msgEvent("oc_1", "sticker", { file_key: "x" })).items, [
    { kind: "unsupported", msgType: "sticker" },
  ]);
});

test("parse: a rich-text post keeps text and inline images in order", () => {
  const p = parseLarkMessage(
    msgEvent("oc_1", "post", {
      title: "Today",
      content: [
        [{ tag: "text", text: "first line " }, { tag: "a", text: "link", href: "https://x.y" }],
        [{ tag: "img", image_key: "img_in_post" }],
        [{ tag: "text", text: "after the picture" }],
      ],
    })
  );
  assert.deepEqual(
    p.items.map((i) => i.kind + (i.type ? `:${i.type}` : "")),
    ["text", "media:image", "text"]
  );
  assert.equal(p.items[0].text, "Today\nfirst line link (https://x.y)");
  assert.equal(p.items[1].source.fileKey, "img_in_post");
  assert.equal(p.items[2].text, "after the picture");
});

test("cards: Done / Retry buttons and the SAVED markdown", () => {
  assert.deepEqual(
    buildCard("✓ ACK #1", { done: true }).elements[1].actions.map((a) => a.value),
    [{ action: "digest_done" }]
  );
  assert.deepEqual(
    buildCard("sorry", { retry: 4 }).elements[1].actions.map((a) => a.value),
    [{ action: "digest_retry", seq: 4 }, { action: "digest_done" }]
  );
  assert.equal(htmlToLarkMarkdown("<b>T &amp; Q</b>\n<code>a.md</code>"), "**T & Q**\n`a.md`");
});

// ---------------------------------------------------------------------------
// end to end through the handlers
// ---------------------------------------------------------------------------

test("text + long voice + Done button → ACK cards, transcript, SAVED, and the note", async () => {
  const CHAT = "oc_e2e";
  const s = setup();
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "今天的想法 today's thought" }));
  await s.handlers.onMessage(msgEvent(CHAT, "audio", { file_key: "file_v3_voice", duration: 312000 }));
  await settle(CHAT);

  const texts = s.sent.map(textOf);
  assert.match(texts[0], /✓ ACK #1/);
  assert.deepEqual(buttonsOf(s.sent[0]), ["digest_done"], "ACK carries the Done button, as on Telegram");
  assert.ok(texts.includes("📝 已存 · text saved (#1)"));
  const ack2 = s.sent.find((x) => /ACK #2/.test(textOf(x)));
  assert.match(textOf(ack2), /5:12/);
  assert.ok(texts.some((t) => /已转写 · transcribed \(#2\)/.test(t) && /飞书语音转写/.test(t)), JSON.stringify(texts));
  assert.ok(s.sent.every((x) => x.chat === CHAT));

  const toast = await s.handlers.onCardAction({
    event_id: "card_1",
    operator: { open_id: BOYANG },
    action: { value: { action: "digest_done" } },
    context: { open_chat_id: CHAT, open_message_id: "om_bot_1" },
  });
  assert.equal(toast.toast.type, "info");
  // the compile is queued; wait for it
  await new Promise((r) => setTimeout(r, 50));
  await settle(CHAT);
  const saved = s.sent.find((x) => x.type === "interactive" && /SAVED/.test(textOf(x)));
  assert.ok(saved, "SAVED banner sent as a card");
  const file = textOf(saved).match(/`([^`]+\.md)`/)[1];
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, file), "utf8");
  assert.match(md, /今天的想法 today's thought/);
  assert.match(md, /> 飞书语音转写 lark voice transcript/);
  assert.match(md, /-1-voice\.ogg\]\]/, "Lark audio sniffed as Ogg and embedded");
  assert.equal(await store.loadPending(toChatKey(CHAT)), null, "pending cleared after compile");
});

test("/done typed as text compiles too", async () => {
  const CHAT = "oc_typed_done";
  const s = setup();
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "one line" }));
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "/done" }));
  await new Promise((r) => setTimeout(r, 50));
  await settle(CHAT);
  assert.ok(s.sent.some((x) => /SAVED/.test(textOf(x))), JSON.stringify(s.sent.map(textOf)));
});

test("download failure → apology card with Retry; Retry button then succeeds", async () => {
  const CHAT = "oc_retry";
  const s = setup({ resources: { file_v3_flaky: ["500", "500", "500", "ok"] } });
  await s.handlers.onMessage(msgEvent(CHAT, "audio", { file_key: "file_v3_flaky", duration: 30000 }));
  await settle(CHAT);
  const apology = s.sent[s.sent.length - 1];
  assert.match(textOf(apology), /抱歉|Sorry/);
  assert.match(textOf(apology), /3 attempts/);
  assert.deepEqual(buttonsOf(apology), ["digest_retry", "digest_done"]);
  assert.ok(s.sent.some((x) => /download retry 2\/3/.test(textOf(x))));

  await s.handlers.onCardAction({
    event_id: "card_retry",
    operator: { open_id: BOYANG },
    action: { value: { action: "digest_retry", seq: 0 } },
    context: { open_chat_id: CHAT },
  });
  await new Promise((r) => setTimeout(r, 20));
  await settle(CHAT);
  assert.ok(s.sent.some((x) => /已转写 · transcribed \(#1\)/.test(textOf(x))), JSON.stringify(s.sent.map(textOf)));
  await store.clearPending(toChatKey(CHAT));
});

test("a file that is gone is reported at once (no pointless retries)", async () => {
  const CHAT = "oc_gone";
  const s = setup({ resources: { file_gone: ["gone"] } });
  await s.handlers.onMessage(msgEvent(CHAT, "file", { file_key: "file_gone", file_name: "x.pdf" }));
  await settle(CHAT);
  assert.equal(s.counts.res.file_gone, 1);
  const last = textOf(s.sent[s.sent.length - 1]);
  assert.match(last, /1 attempt\b/);
  assert.match(last, /234003/, "Lark's own error code reaches the chat");
  await store.clearPending(toChatKey(CHAT));
});

test("only Boyang is served; the bot ignores itself; redeliveries are dropped", async () => {
  const CHAT = "oc_gate";
  const s = setup();
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "hi" }, { openId: STRANGER }));
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "echo" }, { senderType: "app" }));
  const denied = await s.handlers.onCardAction({
    operator: { open_id: STRANGER },
    action: { value: { action: "digest_done" } },
    context: { open_chat_id: CHAT },
  });
  await settle(CHAT);
  assert.equal(s.sent.length, 0);
  assert.equal(denied.toast.type, "error");
  assert.equal(await store.loadPending(toChatKey(CHAT)), null);

  const dup = msgEvent(CHAT, "text", { text: "once" });
  await s.handlers.onMessage(dup);
  await s.handlers.onMessage({ ...dup, event_id: "ev_redelivered" });
  await settle(CHAT);
  assert.equal((await store.loadPending(toChatKey(CHAT))).blocks.length, 1);
  await store.clearPending(toChatKey(CHAT));
});

test("a stale tenant token is refreshed transparently", async () => {
  const CHAT = "oc_token";
  const s = setup({ sendScript: ["stale-token"] });
  await s.handlers.onMessage(msgEvent(CHAT, "text", { text: "x" }));
  await settle(CHAT);
  assert.equal(s.counts.token, 2, "fetched a fresh token after 99991663");
  assert.match(textOf(s.sent[0]), /ACK #1/);
  assert.equal(s.sent[0].auth, "Bearer t-2");
  await store.clearPending(toChatKey(CHAT));
});
