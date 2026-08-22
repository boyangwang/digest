// The log file holds journal-derived content on purpose (raw model output is kept so
// an unparseable response is diagnosable), so it must be PRIVATE and BOUNDED.
// Everything here runs against a temp dir — never the real DATA_DIR.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, existsSync, readFileSync, statSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createLogger } from "../src/log.js";

const quiet = { log() {}, error() {} };
const fixture = () => join(mkdtempSync(join(tmpdir(), "digest-log-")), "nested", "digest.log");
const mode = (p) => statSync(p).mode & 0o777;

test("a fresh log file is created 0600 inside a 0700 directory", () => {
  const path = fixture();
  const log = createLogger({ path, sink: quiet });
  log.info("hello");

  assert.ok(existsSync(path));
  assert.equal(mode(path), 0o600, "the log must not be readable by other local users");
  assert.equal(mode(join(path, "..")), 0o700, "nor its directory listable");
});

test("a log that exceeds the cap rotates, and the previous file is preserved as .1", () => {
  const path = fixture();
  const log = createLogger({ path, maxBytes: 400, retain: 5, sink: quiet });

  log.info("first line worth keeping 第一行");
  assert.ok(!existsSync(`${path}.1`), "nothing rotates before the cap is reached");

  // Stop at the FIRST rotation, so .1 is unambiguously the file we just filled.
  let i = 0;
  while (!existsSync(`${path}.1`)) {
    assert.ok(i++ < 100, "the cap must eventually trigger a rotation");
    log.info(`filler line ${i} 填充`);
  }

  assert.match(readFileSync(`${path}.1`, "utf8"), /first line worth keeping/, "history is kept, not discarded");
  assert.ok(statSync(path).size < 400, "the active file starts fresh under the cap");
  assert.equal(mode(`${path}.1`), 0o600, "rotated files inherit the private mode");
});

test("retention drops only the oldest beyond the configured count", () => {
  const path = fixture();
  const log = createLogger({ path, maxBytes: 120, retain: 3, sink: quiet });
  for (let i = 0; i < 60; i++) log.info(`line ${i} 行`);

  for (const n of [1, 2, 3]) assert.ok(existsSync(`${path}.${n}`), `.${n} must be kept`);
  assert.ok(!existsSync(`${path}.4`), "nothing beyond the retention count survives");
});

test("history survives across restarts — a new logger appends, it does not truncate", () => {
  const path = fixture();
  createLogger({ path, sink: quiet }).info("from the first run 第一次");
  createLogger({ path, sink: quiet }).info("from the second run 第二次");

  const body = readFileSync(path, "utf8");
  assert.match(body, /from the first run/);
  assert.match(body, /from the second run/);
});

test("a rotation failure never throws into the caller", () => {
  const path = fixture();
  const log = createLogger({ path, maxBytes: 50, retain: 2, sink: quiet });
  log.info("seed the file so a rotation is due");

  // Make the directory unwritable so both the rename and the re-create fail.
  const dir = join(path, "..");
  chmodSync(dir, 0o500);
  try {
    assert.doesNotThrow(() => log.info("this must not crash the bot 不能崩溃"));
    assert.doesNotThrow(() => log.error("nor this"));
  } finally {
    chmodSync(dir, 0o700);
  }
});

test("an unwritable log degrades to console only, silently", () => {
  const dir = mkdtempSync(join(tmpdir(), "digest-log-ro-"));
  const path = join(dir, "digest.log");
  writeFileSync(path, "existing\n");
  chmodSync(path, 0o400);
  try {
    const log = createLogger({ path, sink: quiet });
    assert.doesNotThrow(() => log.warn("cannot be written 写不进去"));
    assert.equal(readFileSync(path, "utf8"), "existing\n");
  } finally {
    chmodSync(path, 0o600);
  }
});
