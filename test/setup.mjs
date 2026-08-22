// Loaded via `node --import` for every test file (and every child process the test
// runner spawns), BEFORE any test module reads config.
//
// LOG_PATH defaults to DATA_DIR/digest.log, i.e. the live launchd service's log —
// the very file this work exists to make trustworthy. A test that logs (queue.js
// logs a rejected task on purpose) would otherwise append to it. Pinning it here
// rather than in each test file means a future test file cannot forget.
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

if (!process.env.DIGEST_LOG_PATH) {
  process.env.DIGEST_LOG_PATH = join(mkdtempSync(join(tmpdir(), "digest-test-log-")), "digest.log");
}
