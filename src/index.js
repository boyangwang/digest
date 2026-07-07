// Entry point — start the Digest bot (long polling).
import { BOT_TOKEN } from "./config.js";
import { buildBot } from "./bot.js";
import { listPendingChats } from "./store.js";
import { log } from "./log.js";

if (!BOT_TOKEN) {
  console.error("FATAL: DIGEST_BOT_TOKEN not set.");
  process.exit(1);
}

const bot = buildBot();

async function main() {
  const pending = await listPendingChats();
  if (pending.length) log.info(`Resuming ${pending.length} pending digest(s): ${pending.join(", ")}`);

  await bot.api.setMyCommands([{ command: "done", description: "Compile the current digest into a note" }]);

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
