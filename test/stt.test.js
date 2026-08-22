// The retry / vendor-rotation loop. This is the durability mechanism the whole
// bot leans on, so it is tested against a stubbed transport only — no test in this
// suite ever touches a live vendor.
//
// These tests are deliberately written to go RED if retries or rotation are
// removed: they assert the exact sequence of vendors per attempt and the exact
// attempt count, not just "eventually returns something".
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-stt-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.ELEVENLABS_API_KEY = "el-test-key";
process.env.OPENAI_API_KEY = "oa-test-key";

const { transcribe, STT_FAIL, classify, backoffMs } = await import("../src/stt.js");
const { SttHttpError, SttEmptyError, SttTransportError } = await import("../src/stt-providers.js");
const { STT_MAX_ATTEMPTS, STT_BACKOFF_MAX_MS, STT_TOTAL_BUDGET_MS, STT_ATTEMPT_TIMEOUT_MS } =
  await import("../src/config.js");

const AUDIO = join(base, "note.ogg");
writeFileSync(AUDIO, Buffer.from("fake-ogg-bytes"));

const abortError = () => Object.assign(new Error("The operation was aborted"), { name: "AbortError" });

function response({ status = 200, json = {}, body = "" }) {
  return { ok: status >= 200 && status < 300, status, json: async () => json, text: async () => body };
}

/**
 * Stubbed transport. `script` is one entry per expected call; the last entry
 * repeats forever so "every attempt fails the same way" stays readable.
 * Each entry is `{status, json, body}`, or `{throws: Error}`, or `{costMs}` to
 * make the call consume wall-clock on the fake clock.
 */
function stub(script, clock) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const step = script[calls.length] ?? script[script.length - 1];
    calls.push({
      url,
      provider: url.includes("elevenlabs") ? "elevenlabs" : "openai",
      headers: init.headers,
      form: init.body,
    });
    if (step.costMs && clock) clock.advance(step.costMs);
    if (step.throws) throw step.throws;
    return response(step);
  };
  return { fetchImpl, calls, providers: () => calls.map((c) => c.provider) };
}

function fakeClock() {
  let t = 0;
  return { now: () => t, advance: (ms) => (t += ms) };
}

/** Deterministic harness: no real sleeping, no real clock, no real network. */
function harness(script, extra = {}) {
  const clock = fakeClock();
  const s = stub(script, clock);
  const naps = [];
  return {
    ...s,
    naps,
    clock,
    opts: {
      fetchImpl: s.fetchImpl,
      sleepImpl: async (ms) => {
        naps.push(ms);
        clock.advance(ms);
      },
      now: clock.now,
      rand: () => 0.5, // jitter factor 1.0 → exact, assertable backoff values
      ...extra,
    },
  };
}

const ok = (text, extra = {}) => ({ status: 200, json: { text, ...extra } });
const httpFail = (status, body = "boom") => ({ status, body });

// ---------------------------------------------------------------------------
// A. transient failure is retried, and the vendor alternates
// ---------------------------------------------------------------------------

test("429 then 500 then success: retried, and the vendor alternates 1,2,1", async () => {
  const h = harness([httpFail(429, "rate limited"), httpFail(500, "upstream"), ok("一段中文语音", { language_code: "zho" })]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, true);
  assert.equal(r.text, "一段中文语音");
  assert.equal(r.attempts, 3);
  assert.equal(r.provider, "elevenlabs");
  // The alternation itself — this is the assertion that dies if rotation is removed.
  assert.deepEqual(h.providers(), ["elevenlabs", "openai", "elevenlabs"]);
  // …and this is the one that dies if retries are removed.
  assert.equal(h.calls.length, 3);
});

test("a timeout/abort is transient and is retried on the next vendor", async () => {
  const h = harness([{ throws: abortError() }, ok("recovered after timeout")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, true);
  assert.equal(r.text, "recovered after timeout");
  assert.deepEqual(h.providers(), ["elevenlabs", "openai"]);
});

test("a network error (no HTTP response at all) is transient and is retried", async () => {
  const h = harness([{ throws: Object.assign(new Error("ECONNRESET"), { name: "TypeError" }) }, ok("back")]);
  const r = await transcribe(AUDIO, h.opts);
  assert.equal(r.ok, true);
  assert.equal(h.calls.length, 2);
});

test("all-transient: stops at the attempt cap having alternated the whole way", async () => {
  const h = harness([httpFail(503, "vendor down")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.EXHAUSTED);
  assert.equal(STT_MAX_ATTEMPTS, 6, "the captain asked for 6 total attempts");
  assert.equal(r.attempts, STT_MAX_ATTEMPTS);
  assert.equal(h.calls.length, STT_MAX_ATTEMPTS);
  assert.deepEqual(h.providers(), ["elevenlabs", "openai", "elevenlabs", "openai", "elevenlabs", "openai"]);
  assert.deepEqual(r.providersTried, ["elevenlabs", "openai"]);
  assert.match(r.lastError, /vendor down/);
});

test("one configured vendor: all 6 attempts go to it, no rotation", async () => {
  const h = harness([httpFail(500), httpFail(500), httpFail(500), httpFail(500), httpFail(500), ok("solo")], {
    providerOrder: ["elevenlabs"],
  });
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, true);
  assert.equal(r.attempts, 6);
  assert.deepEqual(new Set(h.providers()), new Set(["elevenlabs"]));
  assert.equal(h.calls.length, 6);
});

test("vendor order is data-driven, not hardcoded at the call site", async () => {
  const h = harness([httpFail(429), ok("second-first")], { providerOrder: ["openai", "elevenlabs"] });
  const r = await transcribe(AUDIO, h.opts);
  assert.equal(r.ok, true);
  assert.deepEqual(h.providers(), ["openai", "elevenlabs"]);
});

// ---------------------------------------------------------------------------
// B. non-retryable failures must not burn the budget
// ---------------------------------------------------------------------------

test("400 is terminal: one call, no retry, no pointless rotation", async () => {
  const h = harness([httpFail(400, "invalid file format")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.BAD_AUDIO);
  assert.equal(r.attempts, 1);
  assert.equal(h.calls.length, 1);
  assert.deepEqual(h.providers(), ["elevenlabs"]); // never rotated
  assert.deepEqual(h.naps, []); // never backed off
});

test("a silent recording (200 + empty text) is terminal: one call, not six", async () => {
  const h = harness([ok("   ")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.EMPTY);
  assert.equal(r.attempts, 1);
  assert.equal(h.calls.length, 1);
});

test("401 disqualifies that vendor only — the other still gets its chance", async () => {
  const h = harness([httpFail(401, "unauthorized"), ok("saved by the backup")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, true);
  assert.equal(r.provider, "openai");
  assert.equal(r.attempts, 2);
  assert.deepEqual(h.providers(), ["elevenlabs", "openai"]);
  assert.deepEqual(h.naps, []); // switching vendors needs no cool-off
});

test("401 from every vendor ends after one call each — nowhere near the cap", async () => {
  const h = harness([httpFail(401, "unauthorized"), httpFail(403, "missing_permissions")]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.REJECTED);
  assert.equal(r.attempts, 2);
  assert.ok(r.attempts < STT_MAX_ATTEMPTS);
  assert.equal(h.calls.length, 2);
});

test("classify(): only 429/5xx/transport are retryable", () => {
  assert.equal(classify(new SttHttpError("x", 429, "")).action, "retry");
  assert.equal(classify(new SttHttpError("x", 500, "")).action, "retry");
  assert.equal(classify(new SttHttpError("x", 502, "")).action, "retry");
  assert.equal(classify(new SttTransportError("x", abortError())).action, "retry");
  for (const s of [400, 413, 415, 422]) {
    assert.equal(classify(new SttHttpError("x", s, "")).action, "stop", `HTTP ${s} must be terminal`);
  }
  for (const s of [401, 403, 404]) {
    assert.equal(classify(new SttHttpError("x", s, "")).action, "drop-vendor", `HTTP ${s} is vendor-specific`);
  }
  assert.equal(classify(new SttEmptyError("x")).action, "stop");
});

// ---------------------------------------------------------------------------
// Backoff + total-time bound
// ---------------------------------------------------------------------------

test("backoff grows exponentially, is capped, and is jittered", () => {
  const flat = { rand: () => 0.5 };
  assert.deepEqual([1, 2, 3, 4, 5].map((n) => backoffMs(n, flat)), [1000, 2000, 4000, 8000, 8000]);
  assert.equal(backoffMs(9, flat), STT_BACKOFF_MAX_MS);
  // jitter actually moves the value, within ±25%
  assert.equal(backoffMs(3, { rand: () => 0 }), 3000);
  assert.equal(backoffMs(3, { rand: () => 1 }), 5000);
});

test("the retry loop sleeps between transient attempts, inside the documented bound", async () => {
  const h = harness([httpFail(503)]);
  await transcribe(AUDIO, h.opts);

  assert.deepEqual(h.naps, [1000, 2000, 4000, 8000, 8000]); // 5 sleeps for 6 attempts
  const worstCaseSleep = h.naps.reduce((a, b) => a + b, 0) * 1.25; // max jitter
  assert.ok(worstCaseSleep <= 28750);
  // The stated worst case: attempts × per-attempt timeout + jittered sleeps ≤ budget.
  // Read from config, never a literal — that literal is exactly how the two drifted.
  assert.ok(STT_MAX_ATTEMPTS * STT_ATTEMPT_TIMEOUT_MS + worstCaseSleep <= STT_TOTAL_BUDGET_MS);
});

test("the total-time budget stops the loop even when attempts remain", async () => {
  // Each call burns a fifth of the budget, so the loop runs out of time before it
  // runs out of attempts.
  const costMs = Math.ceil(STT_TOTAL_BUDGET_MS / 5);
  const h = harness([{ ...httpFail(503), costMs }]);
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.ok(h.calls.length < STT_MAX_ATTEMPTS, `expected the budget to bite, got ${h.calls.length} calls`);
  assert.ok(h.clock.now() <= STT_TOTAL_BUDGET_MS + costMs);
});

test("the per-attempt timeout covers the response BODY, not just the headers", async () => {
  // A vendor that answers 200 and then stalls mid-body used to escape the attempt
  // budget entirely: the abort timer was cleared as soon as fetch() resolved.
  let aborted = false;
  const fetchImpl = async (url, init) => ({
    ok: true,
    status: 200,
    text: async () => "",
    json: () =>
      new Promise((_, reject) => {
        init.signal.addEventListener("abort", () => {
          aborted = true;
          reject(Object.assign(new Error("The operation was aborted"), { name: "AbortError" }));
        });
      }),
  });
  const r = await transcribe(AUDIO, {
    fetchImpl,
    sleepImpl: async () => {},
    attemptTimeoutMs: 20,
    maxAttempts: 1,
    providerOrder: ["elevenlabs"],
  });

  assert.equal(aborted, true, "a stalled body must be aborted by the attempt timeout");
  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.EXHAUSTED); // transport error → transient, not bad audio
});

// ---------------------------------------------------------------------------
// Both vendors are really exercised, and normalize to one shape
// ---------------------------------------------------------------------------

test("elevenlabs speaks its own dialect and normalizes to {text, language, provider}", async () => {
  const h = harness([ok("语音内容", { language_code: "zho", language_probability: 0.99 })]);
  const r = await transcribe(AUDIO, h.opts);

  assert.deepEqual(
    { text: r.text, language: r.language, provider: r.provider },
    { text: "语音内容", language: "zho", provider: "elevenlabs" }
  );
  const call = h.calls[0];
  assert.match(call.url, /api\.elevenlabs\.io\/v1\/speech-to-text/);
  assert.equal(call.headers["xi-api-key"], "el-test-key");
  assert.equal(call.form.get("model_id"), "scribe_v2");
  assert.ok(call.form.get("file"), "audio must be attached");
});

test("openai speaks its own dialect and normalizes to the SAME shape", async () => {
  const h = harness([ok("语音内容")], { providerOrder: ["openai"] });
  const r = await transcribe(AUDIO, h.opts);

  assert.deepEqual(
    { text: r.text, language: r.language, provider: r.provider },
    { text: "语音内容", language: null, provider: "openai" }
  );
  const call = h.calls[0];
  assert.match(call.url, /api\.openai\.com\/v1\/audio\/transcriptions/);
  assert.equal(call.headers.Authorization, "Bearer oa-test-key");
  assert.equal(call.form.get("model"), "gpt-4o-transcribe");
  assert.ok(call.form.get("file"), "audio must be attached");
});

test("both vendors return an identical result contract", async () => {
  const el = await transcribe(AUDIO, harness([ok("同样的形状", { language_code: "zho" })]).opts);
  const oa = await transcribe(AUDIO, harness([ok("同样的形状")], { providerOrder: ["openai"] }).opts);
  assert.deepEqual(Object.keys(el).sort(), Object.keys(oa).sort());
  assert.equal(el.text, oa.text);
});

// ---------------------------------------------------------------------------
// Degenerate inputs still degrade gracefully (never lose the audio)
// ---------------------------------------------------------------------------

test("no vendor available: reports it without pretending to have tried", async () => {
  const h = harness([ok("never called")], { providerOrder: [] });
  const r = await transcribe(AUDIO, h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.UNCONFIGURED);
  assert.equal(r.attempts, 0);
  assert.equal(h.calls.length, 0);
});

test("the audio MIME follows the file extension when the caller has none", async () => {
  // The recovery script only has a path. Mislabelling an .mp3 as audio/ogg invites a
  // 400, and 400 is TERMINAL — the operator would be told the audio is bad when it
  // is fine.
  const mp3 = join(base, "note.mp3");
  writeFileSync(mp3, Buffer.from("fake-mp3-bytes"));
  const h = harness([ok("from an mp3")]);
  await transcribe(mp3, h.opts);
  assert.equal(h.calls[0].form.get("file").type, "audio/mpeg");

  const h2 = harness([ok("from an ogg")]);
  await transcribe(AUDIO, h2.opts);
  assert.equal(h2.calls[0].form.get("file").type, "audio/ogg");

  // An explicit MIME still wins over the extension.
  const h3 = harness([ok("explicit")]);
  await transcribe(mp3, { ...h3.opts, mime: "audio/mp4" });
  assert.equal(h3.calls[0].form.get("file").type, "audio/mp4");
});

test("unreadable audio fails fast without calling any vendor", async () => {
  const h = harness([ok("never called")]);
  const r = await transcribe(join(base, "does-not-exist.ogg"), h.opts);

  assert.equal(r.ok, false);
  assert.equal(r.reason, STT_FAIL.UNREADABLE);
  assert.equal(h.calls.length, 0);
});
