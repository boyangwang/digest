// The ROOT CAUSE of the silent voice notes, measured on the Mini 2026-11-05:
// Node >=20 connects with Happy Eyeballs and gives each address 250ms. The Mini has
// no IPv6 route and its TCP handshake to api.telegram.org takes ~240-300ms, so a new
// connection is abandoned mid-handshake -> "fetch failed" (ETIMEDOUT). Probe
// (scripts/probe-telegram-connect.mjs): default 250ms -> 0/20 fetches succeed;
// 2500ms -> 20/20. Known Node issue: https://github.com/nodejs/node/issues/54359
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const root = new URL("..", import.meta.url);

test("importing net-tuning raises the per-address connect window well above the RTT", () => {
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e", 'import net from "node:net"; await import("./src/net-tuning.js"); process.stdout.write(String(net.getDefaultAutoSelectFamilyAttemptTimeout()));'],
    { cwd: root.pathname, encoding: "utf8", env: { ...process.env, DIGEST_NET_FAMILY_ATTEMPT_TIMEOUT_MS: "" } }
  );
  assert.ok(Number(out) >= 2000, `attempt timeout must be >= 2000ms, got ${out}`);
});

for (const entry of ["src/index.js", "src/lark-index.js", "scripts/retranscribe.mjs"]) {
  test(`${entry} loads net-tuning BEFORE anything else`, () => {
    const src = readFileSync(new URL(entry, root), "utf8");
    const firstImport = src.match(/^import\s+[^;]*?["']([^"']+)["'];?/m)?.[1];
    assert.match(String(firstImport), /net-tuning\.js$/, `${entry}: first import is ${firstImport}`);
  });
}
