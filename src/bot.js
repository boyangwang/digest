// Telegram adapter — grammY wiring. Any input auto-starts/appends a digest; /done or
// the button compiles. Every input runs through the per-chat serial queue (linear,
// deterministic), and NOTHING on that queue touches the network except the ACK:
// voice/photo/file are stored as a Telegram file_id reference and ACKed at once; the
// download, transcription and result run off the queue in src/jobs.js.
import { Bot, InlineKeyboard } from "grammy";
import { BOT_TOKEN, ALLOWED_USER_IDS } from "./config.js";
import { enqueue } from "./queue.js";
import { ingestText, ingestMedia } from "./ingest.js";
import { runFinalize } from "./commands.js";
import { retryJob, resumeJobs } from "./jobs.js";
import { listPendingChats } from "./store.js";
import { telegramFetcher } from "./telegram-api.js";
import { sendWithRetry, chunkText } from "./send.js";
import { log } from "./log.js";

const DONE_BTN = ["✅ Done 完成", "digest_done"];
const TG_MAX = 4000; // Telegram rejects messages over 4096 chars; a long transcript is split

function keyboardFor(opts) {
  if (opts.retry != null) {
    return new InlineKeyboard().text("🔁 重试 Retry", `digest_retry:${opts.retry}`).text(...DONE_BTN);
  }
  if (opts.done) return new InlineKeyboard().text(...DONE_BTN);
  return null;
}

/**
 * A chat sender independent of any update context, so a background job (or a job
 * resumed after a restart) can still talk to the chat. Never throws.
 *   opts.done  - attach the Done button      opts.retry - attach Retry (+Done) for block seq
 *   opts.html  - send as HTML (falls back to stripped plain text if Telegram rejects it)
 */
export function makeSender(api, chatId) {
  return async (text, opts = {}) => {
    const kb = keyboardFor(opts);
    const parts = opts.html ? [text] : chunkText(text, TG_MAX);
    for (let i = 0; i < parts.length; i++) {
      const last = i === parts.length - 1;
      const options = {};
      if (last && kb) options.reply_markup = kb;
      if (opts.html) options.parse_mode = "HTML";
      const ok = await sendWithRetry(
        () => api.sendMessage(chatId, parts[i], Object.keys(options).length ? options : undefined),
        { label: "telegram-send" }
      );
      if (!ok && opts.html) {
        const plain = { ...(kb ? { reply_markup: kb } : {}) };
        await sendWithRetry(() => api.sendMessage(chatId, text.replace(/<[^>]+>/g, ""), plain), {
          label: "telegram-send-plain",
        });
      }
    }
  };
}

function allowed(ctx) {
  const id = ctx.from?.id;
  return typeof id === "number" && ALLOWED_USER_IDS.has(id);
}

/** Job deps for a Telegram chat: how to talk to it and how to fetch its files. */
export function telegramDeps(api, chatId) {
  return {
    reply: makeSender(api, chatId),
    fetchMedia: (source) => telegramFetcher({ token: BOT_TOKEN, fileId: source.fileId }),
  };
}

const tgSource = (m) => ({
  platform: "telegram",
  fileId: m.file_id,
  fileUniqueId: m.file_unique_id,
  ...(m.file_size ? { fileSize: m.file_size } : {}),
});

export function buildBot() {
  // grammY's default request timeout is 500s; an ACK must not hang that long.
  const bot = new Bot(BOT_TOKEN, { client: { timeoutSeconds: 60 } });

  // Access gate.
  bot.use(async (ctx, next) => {
    if (!allowed(ctx)) {
      log.info(`rejected user ${ctx.from?.id} (@${ctx.from?.username || "?"})`);
      return;
    }
    await next();
  });

  bot.command("start", (ctx) =>
    ctx.reply(
      "🌱 Dear Diary — 直接发文字/语音/图片/文件，我就开始记；发完点 ✅ Done 或 /done 即编译成一篇笔记。\n" +
        "Just send text / voice / photos / files — I start a digest automatically. Tap ✅ Done or /done to compile."
    )
  );

  const finalize = (chatId) => {
    runFinalize(chatId, makeSender(bot.api, chatId)).catch((e) => log.error(`runFinalize: ${e?.stack || e}`));
  };

  // /done command and inline button.
  bot.command("done", (ctx) => finalize(ctx.chat.id));
  bot.callbackQuery("digest_done", async (ctx) => {
    await ctx.answerCallbackQuery().catch(() => {});
    if (ctx.chat?.id != null) finalize(ctx.chat.id);
  });

  // 🔁 Retry on a failed download / transcription.
  bot.callbackQuery(/^digest_retry:(\d+)$/, async (ctx) => {
    await ctx.answerCallbackQuery({ text: "🔁 重试中 · retrying" }).catch(() => {});
    const chatId = ctx.chat?.id;
    if (chatId == null) return;
    const seq = Number(ctx.match[1]);
    retryJob(chatId, seq, telegramDeps(bot.api, chatId)).catch((e) => log.error(`retry: ${e?.stack || e}`));
  });

  // Text (non-command).
  bot.on("message:text", (ctx) => {
    if (ctx.message.text.startsWith("/")) return; // ignore unknown commands
    const chatId = ctx.chat.id;
    const reply = makeSender(ctx.api, chatId);
    enqueue(chatId, () => ingestText(chatId, ctx.message.text, reply));
  });

  // Voice + audio: store the file_id, ACK, then download + transcribe off the queue.
  bot.on(["message:voice", "message:audio"], (ctx) => {
    const chatId = ctx.chat.id;
    const media = ctx.message.voice || ctx.message.audio;
    const deps = telegramDeps(ctx.api, chatId);
    enqueue(chatId, () =>
      ingestMedia(
        chatId,
        { type: "voice", source: tgSource(media), mime: media.mime_type, duration: media.duration },
        deps.reply,
        deps
      )
    );
  });

  // Photos.
  bot.on("message:photo", (ctx) => {
    const chatId = ctx.chat.id;
    const photo = ctx.message.photo[ctx.message.photo.length - 1]; // largest
    const deps = telegramDeps(ctx.api, chatId);
    enqueue(chatId, () =>
      ingestMedia(
        chatId,
        { type: "image", source: tgSource(photo), mime: "image/jpeg", userCaption: ctx.message.caption || "" },
        deps.reply,
        deps
      )
    );
  });

  // Documents / any other file.
  bot.on("message:document", (ctx) => {
    const chatId = ctx.chat.id;
    const doc = ctx.message.document;
    const deps = telegramDeps(ctx.api, chatId);
    enqueue(chatId, () =>
      ingestMedia(
        chatId,
        {
          type: "file",
          source: tgSource(doc),
          mime: doc.mime_type,
          origName: doc.file_name,
          userCaption: ctx.message.caption || "",
        },
        deps.reply,
        deps
      )
    );
  });

  bot.catch((err) => log.error(`bot error: ${err?.message || err}`));
  return bot;
}

/** Telegram chats are numeric ids; Lark chats in the shared store are prefixed. */
export const isTelegramChat = (key) => /^-?\d+$/.test(String(key));

/** Resume every unfinished Telegram job left by a previous process. */
export async function resumeTelegramJobs(bot) {
  const chats = (await listPendingChats()).filter(isTelegramChat).map(Number);
  return resumeJobs(chats, (chatId) => telegramDeps(bot.api, chatId));
}
