// Entry point — start the Digest bot on Telegram (long polling).
// The Lark transport is a separate process: src/lark-index.js.
import "./net-tuning.js"; // FIRST: see the file - the root cause of "fetch failed"
import { BOT_TOKEN } from "./config.js";
import { buildBot, resumeTelegramJobs, isTelegramChat } from "./bot.js";
import { listPendingChats } from "./store.js";
import { log } from "./log.js";

if (!BOT_TOKEN) {
  console.error("FATAL: DIGEST_BOT_TOKEN not set.");
  process.exit(1);
}

const bot = buildBot();

async function main() {
  const pending = (await listPendingChats()).filter(isTelegramChat);
  if (pending.length) log.info(`Resuming ${pending.length} pending digest(s): ${pending.join(", ")}`);

  // Best-effort: a network blip here used to be FATAL (2026-09-11), restarting the
  // whole service over a cosmetic command menu.
  await bot.api
    .setMyCommands([{ command: "done", description: "Compile the current digest into a note" }])
    .catch((e) => log.warn(`setMyCommands failed (non-fatal): ${e?.message || e}`));

  const resumed = await resumeTelegramJobs(bot).catch((e) => {
    log.error(`resume failed: ${e?.stack || e}`);
    return 0;
  });
  if (resumed) log.info(`Resumed ${resumed} unfinished media job(s)`);

  const stop = (sig) => {
    log.info(`${sig} — stopping`);
    bot.stop();
  };
  process.once("SIGINT", () => stop("SIGINT"));
  process.once("SIGTERM", () => stop("SIGTERM"));

  log.info("Digest bot starting (long polling)…");
  await bot.start({ allowed_updates: ["message", "callback_query"] });
}

main().catch((e) => {
  log.error(`fatal: ${e?.message || e}`);
  process.exit(1);
});
