// Bot — grammY wiring. Any input auto-starts/appends a digest; /done or the button compiles.
// Every input runs through the per-chat serial queue (linear, deterministic).
import { Bot, InlineKeyboard } from "grammy";
import { BOT_TOKEN, ALLOWED_USER_IDS } from "./config.js";
import { enqueue } from "./queue.js";
import { ingestText, ingestImage, ingestVoice, ingestFile } from "./ingest.js";
import { finalizeDigest } from "./finalize.js";
import { log } from "./log.js";

const DONE_KEYBOARD = new InlineKeyboard().text("✅ Done 完成", "digest_done");

function allowed(ctx) {
  const id = ctx.from?.id;
  return typeof id === "number" && ALLOWED_USER_IDS.has(id);
}

/** Download a Telegram file (by file_id) to a Buffer. */
async function download(ctx, fileId) {
  const file = await ctx.api.getFile(fileId);
  const url = `https://api.telegram.org/file/bot${BOT_TOKEN}/${file.file_path}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`file download ${res.status}`);
  return Buffer.from(await res.arrayBuffer());
}

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * Reply helper that never throws. `opts.done` attaches the inline Done button;
 * `opts.html` sends as HTML (falls back to stripped plain text if parsing fails).
 */
function makeReply(ctx) {
  return async (text, opts = {}) => {
    const options = {};
    if (opts.done) options.reply_markup = DONE_KEYBOARD;
    if (opts.html) options.parse_mode = "HTML";
    try {
      await ctx.reply(text, Object.keys(options).length ? options : undefined);
    } catch (e) {
      log.warn(`reply failed: ${e?.message || e}`);
      if (opts.html) {
        try {
          await ctx.reply(text.replace(/<[^>]+>/g, ""));
        } catch {
          /* give up quietly */
        }
      }
    }
  };
}

async function runFinalize(chatId, reply) {
  await reply("⏳ 编译中 · compiling…");
  try {
    const result = await finalizeDigest(chatId);
    if (!result) {
      await reply("（没有可编译的内容）· nothing to compile — send something first");
      return;
    }
    const DIV = "━━━━━━━━━━━━━━━";
    const msg =
      `${DIV}\n` +
      `✅✅  <b>已保存 · SAVED</b>  ✅✅\n` +
      `${DIV}\n\n` +
      `🏷  <b>${esc(result.fullTitle)}</b>\n\n` +
      `📄  <code>${esc(result.filename)}</code>`;
    await reply(msg, { html: true });
  } catch (e) {
    log.error(`finalize failed: ${e?.message || e}`);
    await reply("❌ 编译失败 · compile failed — your inputs are still saved, try /done again");
  }
}

export function buildBot() {
  const bot = new Bot(BOT_TOKEN);

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

  // /done command.
  bot.command("done", (ctx) => {
    const chatId = ctx.chat.id;
    const reply = makeReply(ctx);
    enqueue(chatId, () => runFinalize(chatId, reply));
  });

  // Inline "Done" button.
  bot.callbackQuery("digest_done", async (ctx) => {
    await ctx.answerCallbackQuery().catch(() => {});
    const chatId = ctx.chat?.id;
    if (chatId == null) return;
    const reply = makeReply(ctx);
    enqueue(chatId, () => runFinalize(chatId, reply));
  });

  // Text (non-command).
  bot.on("message:text", (ctx) => {
    if (ctx.message.text.startsWith("/")) return; // ignore unknown commands
    const chatId = ctx.chat.id;
    const reply = makeReply(ctx);
    enqueue(chatId, () => ingestText(chatId, ctx.message.text, reply));
  });

  // Voice + audio. `ingestVoice` returns once the audio is persisted and ACKed;
  // its transcription runs off this queue (see src/transcriptions.js), so a retry
  // storm never stalls the next message. /done waits for it, bounded.
  bot.on(["message:voice", "message:audio"], (ctx) => {
    const chatId = ctx.chat.id;
    const reply = makeReply(ctx);
    const media = ctx.message.voice || ctx.message.audio;
    enqueue(chatId, async () => {
      const buffer = await download(ctx, media.file_id);
      await ingestVoice(chatId, { buffer, mime: media.mime_type }, reply);
    });
  });

  // Photos.
  bot.on("message:photo", (ctx) => {
    const chatId = ctx.chat.id;
    const reply = makeReply(ctx);
    const photo = ctx.message.photo[ctx.message.photo.length - 1]; // largest
    const userCaption = ctx.message.caption || "";
    enqueue(chatId, async () => {
      const buffer = await download(ctx, photo.file_id);
      await ingestImage(chatId, { buffer, mime: "image/jpeg", userCaption }, reply);
    });
  });

  // Documents / any other file.
  bot.on("message:document", (ctx) => {
    const chatId = ctx.chat.id;
    const reply = makeReply(ctx);
    const doc = ctx.message.document;
    const userCaption = ctx.message.caption || "";
    enqueue(chatId, async () => {
      const buffer = await download(ctx, doc.file_id);
      await ingestFile(
        chatId,
        { buffer, origName: doc.file_name, mime: doc.mime_type, userCaption },
        reply
      );
    });
  });

  bot.catch((err) => log.error(`bot error: ${err?.message || err}`));
  return bot;
}

