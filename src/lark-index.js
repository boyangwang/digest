// Entry point — the Digest bot on Lark ("Sleep Digest"), over the SDK's long
// connection. A separate process from the Telegram bot (src/index.js): a fault in one
// transport can never take the other down, and each has its own launchd label and
// its own log file (one writer per log file - see src/log.js).
import "./net-tuning.js"; // FIRST: see the file - the root cause of "fetch failed"
import * as Lark from "@larksuiteoapi/node-sdk";
import { LARK_APP_ID, LARK_APP_SECRET, LARK_ALLOWED_OPEN_IDS } from "./config.js";
import { createLarkApi, createLarkHandlers } from "./lark.js";
import { log } from "./log.js";

if (!LARK_APP_ID || !LARK_APP_SECRET) {
  console.error("FATAL: DIGEST_LARK_APP_ID / DIGEST_LARK_APP_SECRET not set.");
  process.exit(1);
}
if (!LARK_ALLOWED_OPEN_IDS.size) log.warn("lark: DIGEST_LARK_ALLOWED_OPEN_IDS is empty - every sender will be rejected");

// Route the SDK's own logging into our private, rotated log instead of stdout (which
// is launchd's service log, the net for crashes only).
const sdkLogger = {
  error: (...a) => log.error(`[lark-sdk] ${a.map(String).join(" ")}`),
  warn: (...a) => log.warn(`[lark-sdk] ${a.map(String).join(" ")}`),
  info: (...a) => log.info(`[lark-sdk] ${a.map(String).join(" ")}`),
  debug: () => {},
  trace: () => {},
};

const api = createLarkApi({ appId: LARK_APP_ID, appSecret: LARK_APP_SECRET });
const handlers = createLarkHandlers({ api });

async function main() {
  await api.tenantToken().catch((e) => log.warn(`lark: initial token fetch failed (will retry on use): ${e?.message || e}`));

  const resumed = await handlers.resume().catch((e) => {
    log.error(`lark resume failed: ${e?.stack || e}`);
    return 0;
  });
  if (resumed) log.info(`lark: resumed ${resumed} unfinished media job(s)`);

  const dispatcher = new Lark.EventDispatcher({ logger: sdkLogger, loggerLevel: Lark.LoggerLevel.info }).register({
    "im.message.receive_v1": (data) => {
      handlers.onMessage(data).catch((e) => log.error(`lark onMessage: ${e?.stack || e}`));
    },
    // Subscribed by the app template; nothing to do, but registered so the SDK does
    // not log "no handle" for every read receipt.
    "im.message.message_read_v1": () => {},
    "im.message.reaction.created_v1": () => {},
    "im.message.reaction.deleted_v1": () => {},
    "card.action.trigger": (data) =>
      handlers.onCardAction(data).catch((e) => {
        log.error(`lark onCardAction: ${e?.stack || e}`);
        return { toast: { type: "error", content: "error" } };
      }),
  });

  const ws = new Lark.WSClient({
    appId: LARK_APP_ID,
    appSecret: LARK_APP_SECRET,
    domain: Lark.Domain.Lark,
    logger: sdkLogger,
    loggerLevel: Lark.LoggerLevel.info,
  });

  const stop = (sig) => {
    log.info(`lark: ${sig} — stopping`);
    try {
      ws.close?.();
    } catch {
      /* closing anyway */
    }
    process.exit(0);
  };
  process.once("SIGINT", () => stop("SIGINT"));
  process.once("SIGTERM", () => stop("SIGTERM"));

  log.info("Sleep Digest (Lark) starting (long connection)…");
  await ws.start({ eventDispatcher: dispatcher });
}

main().catch((e) => {
  log.error(`lark fatal: ${e?.stack || e}`);
  process.exit(1);
});
