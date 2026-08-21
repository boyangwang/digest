// Config defaults that have already bitten us once and must not regress.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

/**
 * Read a config export with a CLEAN environment — the defaults are the whole
 * point here, so an inherited DIGEST_* override would make the test lie.
 */
function defaultConfigValue(name) {
  const env = Object.fromEntries(
    Object.entries(process.env).filter(([k]) => !k.startsWith("DIGEST_"))
  );
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e", `import * as c from "./src/config.js"; process.stdout.write(String(c.${name}));`],
    { env, cwd: new URL("..", import.meta.url).pathname, encoding: "utf8" }
  );
  return out.trim();
}

test("the default log path is durable — never under /tmp", () => {
  const logPath = defaultConfigValue("LOG_PATH");
  // macOS purges /tmp while the launchd service holds the file open, which sent the
  // whole log history to a deleted inode and made the original failure undiagnosable.
  assert.ok(!logPath.startsWith("/tmp/"), `LOG_PATH must not live under /tmp, got ${logPath}`);
  assert.ok(!logPath.startsWith("/private/tmp/"), `LOG_PATH must not live under /tmp, got ${logPath}`);
  // It belongs beside the pending store, which was already chosen to survive a purge.
  assert.equal(logPath, `${defaultConfigValue("DATA_DIR")}/digest.log`);
});

test("the launchd plist logs to the same durable path as the code", async () => {
  const { readFileSync } = await import("node:fs");
  const plist = readFileSync(new URL("../launchd/network.deardiary.digest.plist", import.meta.url), "utf8");
  assert.ok(!plist.includes("/tmp/digest.log"), "plist must not point launchd at /tmp");
  const expected = defaultConfigValue("LOG_PATH").replace(process.env.HOME, "");
  assert.ok(plist.includes(expected), `plist should log to …${expected}`);
});

test("the vendor rotation is configured with two vendors by default", () => {
  assert.equal(defaultConfigValue("STT_PROVIDER_ORDER"), "elevenlabs,openai");
  assert.equal(defaultConfigValue("STT_MAX_ATTEMPTS"), "6");
});
