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

// No test may reach a paid vendor with a REAL key. A test that needs a key sets a fake
// one itself; anything inherited from the environment (a run under `secret-run`, a
// shell that exported a key) is cleared here, so a test that forgets to stub its
// transport fails offline instead of spending money.
for (const k of ["OPENAI_API_KEY", "ELEVENLABS_API_KEY", "TENCENT_TOKENHUB_API_KEY"]) {
  process.env[k] = "";
}
