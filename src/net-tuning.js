// Network tuning that must run before ANY socket is opened. Every entry point
// imports this FIRST (pinned by test/net-tuning.test.js).
//
// Why: Node >=20 connects with Happy Eyeballs (`autoSelectFamily`) and gives each
// resolved address only 250ms before abandoning it. The Mini has no IPv6 route, and
// its TCP handshake to api.telegram.org takes ~240-300ms, so a fresh connection is
// cut off mid-handshake and every fetch() to Telegram dies as "fetch failed"
// (ETIMEDOUT). That is what silently ate the voice notes: the download opened a new
// connection, was killed at 250ms, and there was no retry. Measured 2026-11-05 with
// scripts/probe-telegram-connect.mjs: default 250ms -> 0/20 succeed; 2500ms -> 20/20.
// Known Node issue: https://github.com/nodejs/node/issues/54359
import net from "node:net";

const ms = Number(process.env.DIGEST_NET_FAMILY_ATTEMPT_TIMEOUT_MS) || 3000;
net.setDefaultAutoSelectFamilyAttemptTimeout(ms);

export const NET_FAMILY_ATTEMPT_TIMEOUT_MS = ms;
