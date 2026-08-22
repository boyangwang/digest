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

/** StandardOutPath / StandardErrorPath as the plist actually declares them. */
async function plistStdioPaths() {
  const { readFileSync } = await import("node:fs");
  const plist = readFileSync(new URL("../launchd/network.deardiary.digest.plist", import.meta.url), "utf8");
  const read = (key) =>
    plist.match(new RegExp(`<key>${key}</key>\\s*<string>([^<]*)</string>`))?.[1];
  return { plist, out: read("StandardOutPath"), err: read("StandardErrorPath") };
}

test("THE SERVICE AND THE APP MUST NEVER SHARE A LOG FILE", async () => {
  // Not a spelling check. If launchd's stdout/stderr point at LOG_PATH, its fd 1/2
  // and src/log.js append to the SAME inode — and the rename-based rotation in
  // log.js then moves that inode out from under launchd, which never reopens by
  // path. Everything the service writes after the first rotation, including the
  // crash output that never reaches the logger at all, lands in an orphan nobody
  // tails and that grows outside the size cap. That is the /tmp NLINK=0 failure
  // this whole branch exists to eliminate, wearing a different hat.
  const { out, err } = await plistStdioPaths();
  const appLog = defaultConfigValue("LOG_PATH");
  const serviceLog = defaultConfigValue("SERVICE_LOG_PATH");

  assert.ok(out, "plist must declare StandardOutPath");
  assert.ok(err, "plist must declare StandardErrorPath");
  for (const [label, p] of [["StandardOutPath", out], ["StandardErrorPath", err]]) {
    assert.notEqual(p, appLog, `${label} must NOT be the app's own LOG_PATH`);
    assert.ok(!p.endsWith("/digest.log"), `${label} must not be any file named digest.log, got ${p}`);
    assert.ok(!p.startsWith("/tmp/") && !p.startsWith("/private/tmp/"), `${label} must be durable, got ${p}`);
    assert.ok(p.endsWith(serviceLog.slice(serviceLog.lastIndexOf("/"))), `${label} should be the service log, got ${p}`);
  }
});

test("the launchd plist and the code agree on the durable log directory", async () => {
  const { plist } = await plistStdioPaths();
  assert.ok(!plist.includes("/tmp/digest.log"), "plist must not point launchd at /tmp");
  // Compare the HOME-relative suffix, not a $HOME-spliced absolute path: the plist
  // hardcodes one machine's home, so splicing in $HOME makes this fail anywhere else
  // for a reason that has nothing to do with the invariant being guarded.
  const DIR_SUFFIX = ".local/share/digest/";
  assert.ok(
    defaultConfigValue("LOG_PATH").endsWith(`${DIR_SUFFIX}digest.log`),
    `LOG_PATH should end with ${DIR_SUFFIX}digest.log, got ${defaultConfigValue("LOG_PATH")}`
  );
  assert.ok(
    defaultConfigValue("SERVICE_LOG_PATH").endsWith(`${DIR_SUFFIX}digest-service.log`),
    "SERVICE_LOG_PATH should sit beside it"
  );
  assert.ok(plist.includes(DIR_SUFFIX), `plist should log under …/${DIR_SUFFIX}`);
});

test("the vendor rotation is configured with two vendors by default", () => {
  assert.equal(defaultConfigValue("STT_PROVIDER_ORDER"), "elevenlabs,openai");
  assert.equal(defaultConfigValue("STT_MAX_ATTEMPTS"), "6");
});

test("the retry budget still covers the documented worst case", () => {
  // 6 attempts x per-attempt timeout + the five jittered backoff sleeps.
  const attempts = Number(defaultConfigValue("STT_MAX_ATTEMPTS"));
  const perAttempt = Number(defaultConfigValue("STT_ATTEMPT_TIMEOUT_MS"));
  const budget = Number(defaultConfigValue("STT_TOTAL_BUDGET_MS"));
  const worstCaseSleep = (1000 + 2000 + 4000 + 8000 + 8000) * 1.25;
  assert.equal(perAttempt, 60000, "the captain ruled 60s per attempt, not 45s");
  assert.ok(
    attempts * perAttempt + worstCaseSleep <= budget,
    `budget ${budget}ms cannot cover ${attempts} x ${perAttempt}ms + ${worstCaseSleep}ms of backoff`
  );
});
