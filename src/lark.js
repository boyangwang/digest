// Lark (international Feishu) adapter for the "Sleep Digest" bot.
//
// Same product as the Telegram bot, same core (ingest / jobs / commands / finalize):
//   text      -> verbatim block          voice (audio) -> download + transcribe
//   image     -> attachment + caption    file / media  -> attachment
//   post      -> its text, then its images, in order
//   /done or the ✅ Done card button -> compile;  🔁 Retry card button -> retry a job
//
// Transport: events arrive over the SDK's long connection (src/lark-index.js), so the
// Mini needs no public URL. Everything REST (token, send, download) is plain fetch()
// here, which keeps timeouts and retries under our control and makes it testable
// with an injected `fetchImpl`. Handlers return immediately (Lark redelivers an event
// not acknowledged within 3s); the work is queued per chat.
import { LARK_API_BASE, LARK_CHAT_PREFIX, LARK_ALLOWED_OPEN_IDS } from "./config.js";
import { MediaHttpError, MediaTransportError } from "./media.js";
import { enqueue } from "./queue.js";
import { ingestText, ingestMedia } from "./ingest.js";
import { runFinalize } from "./commands.js";
import { retryJob, resumeJobs } from "./jobs.js";
import { listPendingChats } from "./store.js";
import { sendWithRetry, chunkText } from "./send.js";
import { log } from "./log.js";

export const toChatKey = (chatId) => `${LARK_CHAT_PREFIX}${chatId}`;
export const fromChatKey = (key) => String(key).slice(LARK_CHAT_PREFIX.length);
export const isLarkChat = (key) => String(key).startsWith(LARK_CHAT_PREFIX);

/** Lark's own error codes that mean "your tenant token is stale - fetch a new one". */
const TOKEN_CODES = new Set([99991661, 99991663, 99991664, 99991665, 99991668, 99991671]);
/** Lark's rate-limit code (served with HTTP 400). */
const RATE_LIMIT_CODE = 99991400;

export class LarkApiError extends Error {
  constructor(op, status, code, msg) {
    super(`lark ${op}: HTTP ${status} code ${code} ${msg || ""}`.trim());
    this.name = "LarkApiError";
    this.status = code === RATE_LIMIT_CODE ? 429 : status;
    this.code = code;
  }
}

function abortAfter(ms) {
  const ac = new AbortController();
  const timer = setTimeout(
    () => ac.abort(Object.assign(new Error(`no answer within ${ms}ms`), { name: "TimeoutError" })),
    ms
  );
  timer.unref?.();
  return { signal: ac.signal, done: () => clearTimeout(timer) };
}

/**
 * Minimal Lark REST client.
 * @param {{appId:string, appSecret:string, base?:string, fetchImpl?:Function, now?:Function}} p
 */
export function createLarkApi({ appId, appSecret, base = LARK_API_BASE, fetchImpl = globalThis.fetch, now = Date.now }) {
  let token = null;
  let expiresAt = 0;

  async function tenantToken(force = false) {
    if (!force && token && now() < expiresAt - 60_000) return token;
    const t = abortAfter(30_000);
    try {
      const res = await fetchImpl(`${base}/open-apis/auth/v3/tenant_access_token/internal`, {
        method: "POST",
        headers: { "content-type": "application/json; charset=utf-8" },
        body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
        signal: t.signal,
      });
      const j = await res.json();
      if (!res.ok || j.code !== 0) throw new LarkApiError("tenant_access_token", res.status, j.code, j.msg);
      token = j.tenant_access_token;
      expiresAt = now() + (Number(j.expire) || 7200) * 1000;
      return token;
    } finally {
      t.done();
    }
  }

  /** JSON API call with one transparent token refresh. Throws LarkApiError. */
  async function call(method, path, { body, timeoutMs = 30_000 } = {}) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const tok = await tenantToken(attempt > 0);
      const t = abortAfter(timeoutMs);
      let res, j;
      try {
        res = await fetchImpl(`${base}${path}`, {
          method,
          headers: { authorization: `Bearer ${tok}`, "content-type": "application/json; charset=utf-8" },
          ...(body ? { body: JSON.stringify(body) } : {}),
          signal: t.signal,
        });
        j = await res.json().catch(() => ({ code: -1, msg: `non-JSON response (HTTP ${res.status})` }));
      } finally {
        t.done();
      }
      if (TOKEN_CODES.has(j.code) && attempt === 0) continue;
      if (!res.ok || j.code !== 0) throw new LarkApiError(`${method} ${path.split("?")[0]}`, res.status, j.code, j.msg);
      return j.data;
    }
    throw new LarkApiError(`${method} ${path}`, 401, -1, "token refresh did not help");
  }

  /** Send a message to a chat. `content` is the object form; it is JSON-encoded here. */
  function sendMessage(chatId, msgType, content) {
    return call("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id", {
      body: { receive_id: chatId, msg_type: msgType, content: JSON.stringify(content) },
    });
  }

  /**
   * A media FETCHER (see src/media.js) for one message resource. Lark serves the raw
   * bytes on success and a JSON {code,msg} body on failure, often with HTTP 400.
   */
  function fetcher({ messageId, fileKey, resType }) {
    return async ({ timeoutMs }) => {
      for (let attempt = 0; attempt < 2; attempt++) {
        let tok;
        try {
          tok = await tenantToken(attempt > 0);
        } catch (e) {
          if (e instanceof LarkApiError && e.status && e.status < 500 && e.status !== 429) {
            throw new MediaHttpError("lark", e.status, e.message);
          }
          throw new MediaTransportError("lark", e);
        }
        const t = abortAfter(timeoutMs);
        try {
          const url =
            `${base}/open-apis/im/v1/messages/${encodeURIComponent(messageId)}` +
            `/resources/${encodeURIComponent(fileKey)}?type=${resType}`;
          let res;
          try {
            res = await fetchImpl(url, { headers: { authorization: `Bearer ${tok}` }, signal: t.signal });
          } catch (e) {
            throw new MediaTransportError("lark", e);
          }
          const ctype = String(res.headers?.get?.("content-type") || "");
          if (!res.ok || ctype.includes("application/json")) {
            const j = await res.json().catch(() => ({}));
            if (TOKEN_CODES.has(j.code) && attempt === 0) continue;
            const status = j.code === RATE_LIMIT_CODE ? 429 : res.ok ? 400 : res.status;
            throw new MediaHttpError("lark", status, `${j.code ?? ""} ${j.msg ?? ""}`.trim());
          }
          try {
            return Buffer.from(await res.arrayBuffer());
          } catch (e) {
            throw new MediaTransportError("lark", e);
          }
        } finally {
          t.done();
        }
      }
      throw new MediaHttpError("lark", 401, "token refresh did not help");
    };
  }

  return { tenantToken, call, sendMessage, fetcher };
}

// ---------------------------------------------------------------------------
// Outbound: text, and cards for the Done / Retry buttons and the SAVED banner
// ---------------------------------------------------------------------------

const LARK_TEXT_MAX = 12000; // text messages cap at 150 KB; keep each one readable

/** The tiny HTML subset the core emits (<b>, <code>, entities) -> Lark card markdown. */
export function htmlToLarkMarkdown(html) {
  return String(html)
    .replace(/<b>([\s\S]*?)<\/b>/g, "**$1**")
    // Lark's card-1.0 markdown prints backticks literally, so code spans become plain text.
    .replace(/<code>([\s\S]*?)<\/code>/g, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

const button = (text, value, type = "default") => ({
  tag: "button",
  text: { tag: "plain_text", content: text },
  type,
  value,
});

/** Build the card for a reply that needs buttons or formatting. */
export function buildCard(text, opts = {}) {
  const elements = [
    opts.html
      ? { tag: "markdown", content: htmlToLarkMarkdown(text) }
      : { tag: "div", text: { tag: "plain_text", content: String(text) } },
  ];
  const actions = [];
  if (opts.retry != null) actions.push(button("🔁 重试 Retry", { action: "digest_retry", seq: opts.retry }));
  if (opts.done || opts.retry != null) actions.push(button("✅ Done 完成", { action: "digest_done" }, "primary"));
  if (actions.length) elements.push({ tag: "action", actions });
  return { config: { wide_screen_mode: true }, elements };
}

/** A chat sender (never throws). Plain text goes as text; buttons/HTML go as a card. */
export function makeLarkSender(api, chatId) {
  return async (text, opts = {}) => {
    if (opts.html || opts.done || opts.retry != null) {
      const ok = await sendWithRetry(() => api.sendMessage(chatId, "interactive", buildCard(text, opts)), {
        label: "lark-send-card",
      });
      if (ok || !opts.html) return;
      // A card the platform refused must not swallow the message: fall back to text.
      text = htmlToLarkMarkdown(text);
    }
    for (const part of chunkText(text, LARK_TEXT_MAX)) {
      await sendWithRetry(() => api.sendMessage(chatId, "text", { text: part }), { label: "lark-send" });
    }
  };
}

// ---------------------------------------------------------------------------
// Inbound: parse im.message.receive_v1 into the core's inputs
// ---------------------------------------------------------------------------

function parseContent(raw) {
  try {
    return JSON.parse(raw || "{}");
  } catch {
    return {};
  }
}

/** Flatten a `post` (rich text) into ordered items: text runs and inline images. */
function parsePost(content, messageId) {
  const body = content?.content ? content : Object.values(content || {}).find((v) => v?.content) || {};
  const items = [];
  let buf = [];
  const flush = () => {
    const t = buf.join("\n").trim();
    if (t) items.push({ kind: "text", text: t });
    buf = [];
  };
  if (body.title) buf.push(String(body.title));
  for (const para of body.content || []) {
    let line = "";
    for (const el of para || []) {
      if (el.tag === "text" || el.tag === "md" || el.tag === "code_block") line += el.text || "";
      else if (el.tag === "a") line += el.href && el.href !== el.text ? `${el.text} (${el.href})` : el.text || el.href || "";
      else if (el.tag === "at") line += `@${el.user_name || el.user_id || ""}`;
      else if (el.tag === "emotion") line += `[${el.emoji_type || "emoji"}]`;
      else if (el.tag === "img" && el.image_key) {
        if (line) buf.push(line);
        line = "";
        flush();
        items.push({ kind: "media", type: "image", source: { platform: "lark", messageId, fileKey: el.image_key, resType: "image" } });
      } else if (el.tag === "media" && el.file_key) {
        if (line) buf.push(line);
        line = "";
        flush();
        items.push({ kind: "media", type: "file", source: { platform: "lark", messageId, fileKey: el.file_key, resType: "file" } });
      }
    }
    if (line) buf.push(line);
  }
  flush();
  return items;
}

/**
 * Turn one received message into the core's inputs.
 * @returns {{chatId:string, chatKey:string, messageId:string, openId:?string, senderType:?string, items:object[]}}
 */
export function parseLarkMessage(data) {
  const ev = data?.event ?? data ?? {};
  const msg = ev.message || {};
  const sender = ev.sender || {};
  const chatId = msg.chat_id;
  const messageId = msg.message_id;
  const content = parseContent(msg.content);
  const src = (fileKey, resType) => ({ platform: "lark", messageId, fileKey, resType });
  let items;

  switch (msg.message_type) {
    case "text": {
      const text = String(content.text ?? "").replace(/@_user_\d+\s*/g, "").trim();
      const cmd = text.match(/^\/([a-z]+)\b/i);
      if (cmd) items = [{ kind: "command", name: cmd[1].toLowerCase() }];
      else items = text ? [{ kind: "text", text }] : [];
      break;
    }
    case "audio":
      items = [
        {
          kind: "media",
          type: "voice",
          source: src(content.file_key, "file"),
          duration: content.duration ? Math.round(Number(content.duration) / 1000) : undefined,
        },
      ];
      break;
    case "image":
      items = [{ kind: "media", type: "image", source: src(content.image_key, "image") }];
      break;
    case "file":
      items = [{ kind: "media", type: "file", source: src(content.file_key, "file"), origName: content.file_name }];
      break;
    case "media":
      items = [{ kind: "media", type: "file", source: src(content.file_key, "file"), origName: content.file_name }];
      break;
    case "post":
      items = parsePost(content, messageId);
      break;
    default:
      items = [{ kind: "unsupported", msgType: msg.message_type }];
  }
  return {
    chatId,
    chatKey: toChatKey(chatId),
    messageId,
    openId: sender.sender_id?.open_id || null,
    senderType: sender.sender_type || null,
    items,
  };
}

/** A small bounded set, for "have I already handled this event/message?". */
function seenSet(max = 2000) {
  const s = new Set();
  return {
    has: (k) => s.has(k),
    add(k) {
      s.add(k);
      if (s.size > max) s.delete(s.values().next().value);
    },
  };
}

export const HELP_TEXT =
  "🌱 Dear Diary — 直接发文字/语音/图片/文件，我就开始记；发完点 ✅ Done 或发 /done 即编译成一篇笔记。\n" +
  "Just send text / voice / photos / files — I start a digest automatically. Tap ✅ Done or send /done to compile.";

/**
 * The event handlers wired to the SDK's EventDispatcher by src/lark-index.js.
 * Both return within milliseconds; the work goes onto the per-chat queue.
 */
export function createLarkHandlers({ api, allowedOpenIds = LARK_ALLOWED_OPEN_IDS, depsOverride } = {}) {
  const seen = seenSet();

  /** Job deps for a chat (raw Lark chat_id): its sender and the resource fetcher. */
  const depsFor = (chatId) => ({
    reply: makeLarkSender(api, chatId),
    fetchMedia: (source) => api.fetcher(source),
    ...(depsOverride || {}),
  });

  function isAllowed(openId) {
    return Boolean(openId) && allowedOpenIds.has(openId);
  }

  async function onMessage(data) {
    const p = parseLarkMessage(data);
    const eventId = data?.event_id || data?.header?.event_id;
    if (!p.chatId || !p.messageId) return;
    if (p.senderType && p.senderType !== "user") return; // never react to bots (incl. ourselves)
    if (!isAllowed(p.openId)) {
      log.info(`lark: rejected sender open_id=${p.openId} chat=${p.chatId}`);
      return;
    }
    if (seen.has(p.messageId) || (eventId && seen.has(eventId))) {
      log.info(`lark: duplicate delivery of ${p.messageId} ignored`);
      return;
    }
    seen.add(p.messageId);
    if (eventId) seen.add(eventId);

    const key = p.chatKey;
    const deps = depsFor(p.chatId);
    for (const item of p.items) {
      if (item.kind === "text") {
        enqueue(key, () => ingestText(key, item.text, deps.reply));
      } else if (item.kind === "media") {
        const { type, source, duration, origName } = item;
        enqueue(key, () => ingestMedia(key, { type, source, duration, origName }, deps.reply, deps));
      } else if (item.kind === "command") {
        if (item.name === "done") finalize(p.chatId);
        else if (item.name === "start" || item.name === "help") enqueue(key, () => deps.reply(HELP_TEXT));
        // other /commands are ignored, exactly like the Telegram bot
      } else if (item.kind === "unsupported") {
        enqueue(key, () =>
          deps.reply(`（暂不支持这种消息）· this message type is not supported yet: ${item.msgType}`)
        );
      }
    }
  }

  function finalize(chatId) {
    const key = toChatKey(chatId);
    runFinalize(key, depsFor(chatId).reply).catch((e) => log.error(`lark runFinalize: ${e?.stack || e}`));
  }

  /** card.action.trigger: the ✅ Done and 🔁 Retry buttons. Returns a toast. */
  async function onCardAction(data) {
    const value = data?.action?.value || {};
    const chatId = data?.context?.open_chat_id;
    const openId = data?.operator?.open_id;
    const eventId = data?.event_id;
    if (!chatId || !isAllowed(openId)) {
      log.info(`lark: rejected card action from open_id=${openId}`);
      return { toast: { type: "error", content: "not allowed" } };
    }
    if (eventId) {
      if (seen.has(eventId)) return { toast: { type: "info", content: "…" } };
      seen.add(eventId);
    }
    if (value.action === "digest_done") {
      finalize(chatId);
      return { toast: { type: "info", content: "编译中 · compiling…" } };
    }
    if (value.action === "digest_retry") {
      const seq = Number(value.seq);
      retryJob(toChatKey(chatId), seq, depsFor(chatId)).catch((e) => log.error(`lark retry: ${e?.stack || e}`));
      return { toast: { type: "info", content: "重试中 · retrying…" } };
    }
    return { toast: { type: "info", content: "?" } };
  }

  /** Resume every unfinished Lark job left by a previous process. */
  async function resume() {
    const chats = (await listPendingChats()).filter(isLarkChat);
    return resumeJobs(chats, (key) => depsFor(fromChatKey(key)));
  }

  return { onMessage, onCardAction, resume, depsFor };
}

