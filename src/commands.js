// Commands shared by every chat platform (Telegram, Lark): /done and Retry.
//
// /done used to run INSIDE the per-chat serial queue and wait there for in-flight
// transcriptions. With 3-minute attempts that wait can be long, and every message
// sent meanwhile would sit un-ACKed behind it. So the wait now happens OUTSIDE the
// queue: new inputs keep getting ACKed (and join the note being compiled), and only
// the short compile step itself takes the queue.
import { finalizeDigest } from "./finalize.js";
import { inflightCount, waitForTranscriptions } from "./transcriptions.js";
import { enqueue } from "./queue.js";
import { JOB_TOTAL_BUDGET_MS } from "./config.js";
import { log } from "./log.js";

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** The SAVED announcement (HTML; each platform renders it its own way). */
export function savedMessage(result) {
  const DIV = "━━━━━━━━━━━━━━━";
  return (
    `${DIV}\n` +
    `✅✅  <b>已保存 · SAVED</b>  ✅✅\n` +
    `${DIV}\n\n` +
    `🏷  <b>${esc(result.fullTitle)}</b>\n\n` +
    `📄  <code>${esc(result.filename)}</code>`
  );
}

async function compileNow(chatId, reply) {
  await reply("⏳ 编译中 · compiling…");
  try {
    // Everything we know about has settled (or the bounded wait bit); never wait
    // again here, inside the queue.
    const result = await finalizeDigest(chatId, { transcriptionWaitMs: 0 });
    if (!result) {
      await reply("（没有可编译的内容）· nothing to compile — send something first");
      return;
    }
    await reply(savedMessage(result), { html: true });
  } catch (e) {
    log.error(`finalize failed: ${e?.stack || e}`);
    await reply("❌ 编译失败 · compile failed — your inputs are still saved, try /done again", { done: true });
  }
}

/**
 * /done (command or button).
 *  1. a queue barrier, so inputs that arrived BEFORE /done are appended and their
 *     jobs started;
 *  2. wait for in-flight jobs OUTSIDE the queue (bounded), telling the user;
 *  3. compile inside the queue - unless something new started meanwhile, in which
 *     case go back to 2.
 */
export async function runFinalize(chatId, reply, { waitMs = JOB_TOTAL_BUDGET_MS, maxRounds = 5 } = {}) {
  let told = false;
  for (let round = 1; ; round++) {
    await enqueue(chatId, async () => {});
    let settled = true;
    const busy = inflightCount(chatId);
    if (busy) {
      if (!told) {
        told = true;
        await reply(
          `⏳ 还有 ${busy} 条在处理，完成后自动编译 · ${busy} item(s) still processing — ` +
            `the note compiles as soon as they finish (you can keep sending)`
        );
      }
      settled = await waitForTranscriptions(chatId, { timeoutMs: waitMs });
      if (!settled) {
        await reply("⚠️ 等待超时，先编译；未完成的标记为可恢复 · waited too long — compiling now; unfinished items are marked recoverable");
      }
    }
    const outcome = await enqueue(chatId, async () => {
      if (settled && round < maxRounds && inflightCount(chatId)) return "busy";
      await compileNow(chatId, reply);
      return "done";
    });
    if (outcome !== "busy") return;
  }
}
