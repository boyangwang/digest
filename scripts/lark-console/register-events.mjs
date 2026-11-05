#!/usr/bin/env node
// register-events.mjs - add the bot's event + callback subscriptions to the existing
// "Sleep Digest" Lark app via the SDK's device-authorization flow (registerApp with
// appId = update mode), instead of clicking through the developer console.
//
//   node scripts/lark-console/register-events.mjs [appId]
//
// Prints the verification URL (open it in a browser logged in as the app owner and
// confirm), waits, then prints the confirming user's open_id FOR THIS APP - the value
// that goes into the allowlist. The app secret it returns is never printed; only
// whether it matches DIGEST_LARK_APP_SECRET in the environment (run via secret-run).
import * as lark from "@larksuiteoapi/node-sdk";
import { writeFileSync } from "node:fs";

const appId = process.argv[2] || process.env.DIGEST_LARK_APP_ID || "cli_aa662277abb8df05";
const urlFile = process.env.REGISTER_URL_FILE || "/tmp/lark-register-url.txt";

const result = await lark.registerApp({
  appId,
  domain: "accounts.larksuite.com",
  source: "digest-bot",
  addons: {
    scopes: { tenant: ["im:message", "im:message:send_as_bot", "im:message.p2p_msg:readonly", "im:resource"] },
    events: { items: { tenant: ["im.message.receive_v1"] } },
    callbacks: { items: ["card.action.trigger"] },
  },
  onQRCodeReady(info) {
    console.log(`VERIFY_URL ${info.url}`);
    console.log(`expires in ${info.expireIn}s`);
    writeFileSync(urlFile, info.url);
  },
  onStatusChange(info) {
    console.log(`status ${info.status}`);
  },
});

const secretMatches = process.env.DIGEST_LARK_APP_SECRET
  ? result.client_secret === process.env.DIGEST_LARK_APP_SECRET
  : "unknown (no DIGEST_LARK_APP_SECRET in env)";
console.log(JSON.stringify({ client_id: result.client_id, secret_matches_vault: secretMatches, user_info: result.user_info }, null, 1));
