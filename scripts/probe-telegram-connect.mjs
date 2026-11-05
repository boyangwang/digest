#!/usr/bin/env node
// probe-telegram-connect.mjs - how reliably can THIS machine open fresh connections to
// a host from Node? Diagnoses the Happy Eyeballs 250ms trap (see src/net-tuning.js).
//
//   node scripts/probe-telegram-connect.mjs [attemptTimeoutMs] [url] [n]
//   node scripts/probe-telegram-connect.mjs            # Node default (250ms)
//   node scripts/probe-telegram-connect.mjs 3000       # what the bot uses
import net from "node:net";

const [t = "0", url = "https://api.telegram.org/", n = "20"] = process.argv.slice(2);
if (Number(t)) net.setDefaultAutoSelectFamilyAttemptTimeout(Number(t));
let ok = 0;
const errs = {};
for (let i = 0; i < Number(n); i++) {
  try {
    const r = await fetch(url);
    await r.arrayBuffer();
    ok++;
  } catch (e) {
    const code = e.cause?.code || e.cause?.errors?.map((x) => x.code).join("+") || e.message;
    errs[code] = (errs[code] || 0) + 1;
  }
}
console.log(JSON.stringify({ url, attemptTimeoutMs: net.getDefaultAutoSelectFamilyAttemptTimeout(), ok, fail: Number(n) - ok, errs }));
