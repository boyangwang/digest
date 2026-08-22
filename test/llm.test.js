// The title model now sits on the recovery critical path: `retranscribe` cannot
// commit a recovered transcript without it. So it gets the SAME attempt ladder as
// transcription, and these tests go RED if that ladder is removed.
//
// Stubbed transport throughout — no test here makes a live LLM call.
import { test } from "node:test";
import assert from "node:assert/strict";

process.env.TENCENT_TOKENHUB_API_KEY = "th-test-key";

const { generateTitleAndTags, classifyTitleError, LlmUnparseableError } = await import("../src/llm.js");
const { LLM_MAX_ATTEMPTS, LLM_ATTEMPT_TIMEOUT_MS, LLM_TOTAL_BUDGET_MS } = await import("../src/config.js");

const INPUT = "一些合成的笔记内容。Some synthetic note content.";

const answer = (extra = {}) => ({
  choices: [
    {
      message: {
        content: JSON.stringify({
          title_zh: "合成标题",
          title_en: "Synthetic title",
          tags: [{ key: "Category分类", value: "Test测试" }],
          ...extra,
        }),
      },
    },
  ],
});

const httpError = (status) => Object.assign(new Error(`HTTP ${status}`), { status });

/** Deterministic harness: no real sleeping, no real clock, no real network. */
function harness(script) {
  const calls = [];
  let t = 0;
  const naps = [];
  return {
    calls,
    naps,
    opts: {
      createImpl: async (body, reqOpts) => {
        const step = script[calls.length] ?? script[script.length - 1];
        calls.push({ body, reqOpts });
        if (typeof step === "function") return step();
        if (step instanceof Error) throw step;
        return step;
      },
      sleepImpl: async (ms) => {
        naps.push(ms);
        t += ms;
      },
      now: () => t,
      rand: () => 0.5,
    },
  };
}

test("a transient LLM failure (429, then 5xx) is retried and the call succeeds", async () => {
  const h = harness([httpError(429), httpError(503), answer()]);
  const r = await generateTitleAndTags(INPUT, h.opts);

  assert.equal(r.fallback, false);
  assert.equal(r.title.zh, "合成标题");
  assert.equal(r.title.en, "Synthetic title");
  assert.deepEqual(r.tags, [{ key: "Category分类", value: "Test测试" }]);
  assert.equal(h.calls.length, 3, "this dies if retries are removed");
  assert.deepEqual(h.naps, [1000, 2000]);
});

test("the attempt cap is respected and the ladder terminates with fallback", async () => {
  const h = harness([httpError(500)]);
  const r = await generateTitleAndTags(INPUT, h.opts);

  assert.equal(r.fallback, true, "it degrades gracefully instead of throwing into /done");
  assert.equal(h.calls.length, LLM_MAX_ATTEMPTS);
  assert.ok(LLM_MAX_ATTEMPTS > 1, "a single-shot ladder is the bug this replaces");
});

test("400 / 401 / 403 are terminal and do not burn the cap", async () => {
  for (const status of [400, 401, 403]) {
    const h = harness([httpError(status)]);
    const r = await generateTitleAndTags(INPUT, h.opts);
    assert.equal(r.fallback, true);
    assert.equal(h.calls.length, 1, `HTTP ${status} must cost exactly one call`);
    assert.deepEqual(h.naps, []);
  }
});

test("an unparseable answer is retried — model wobble is what a second attempt fixes", async () => {
  const h = harness([{ choices: [{ message: { content: "sorry, I cannot do that" } }] }, answer()]);
  const r = await generateTitleAndTags(INPUT, h.opts);

  assert.equal(r.fallback, false);
  assert.equal(r.title.zh, "合成标题");
  assert.equal(h.calls.length, 2);
});

test("a PERSISTENTLY unparseable answer still terminates, with fallback: true", async () => {
  const h = harness([{ choices: [{ message: { content: "no json here, ever" } }] }]);
  const r = await generateTitleAndTags(INPUT, h.opts);

  assert.equal(r.fallback, true);
  assert.equal(h.calls.length, LLM_MAX_ATTEMPTS, "bounded by the same cap, not an infinite loop");
  assert.equal(r.title.zh, INPUT.split("\n")[0].slice(0, 60), "fallback title is the input's first line");
});

test("a Chinese-only answer with no tags is a REAL answer, not the fallback", async () => {
  const h = harness([
    { choices: [{ message: { content: JSON.stringify({ title_zh: "只有中文", tags: [] }) } }] },
  ]);
  const r = await generateTitleAndTags(INPUT, h.opts);

  assert.equal(r.fallback, false, "shape must never be used to infer 'the model was unavailable'");
  assert.equal(r.title.zh, "只有中文");
  assert.deepEqual(r.tags, []);
});

test("classifyTitleError: only 429/5xx/network/unparseable retry", () => {
  assert.equal(classifyTitleError(httpError(429)).action, "retry");
  assert.equal(classifyTitleError(httpError(500)).action, "retry");
  assert.equal(classifyTitleError(httpError(502)).action, "retry");
  assert.equal(classifyTitleError(new LlmUnparseableError("junk")).action, "retry");
  assert.equal(classifyTitleError(Object.assign(new Error("ECONNRESET"), { name: "TypeError" })).action, "retry");
  for (const s of [400, 401, 403, 404]) {
    assert.equal(classifyTitleError(httpError(s)).action, "stop", `HTTP ${s} must be terminal`);
  }
});

test("each attempt carries the per-attempt timeout and disables the SDK's own retries", async () => {
  const h = harness([answer()]);
  await generateTitleAndTags(INPUT, h.opts);
  const { reqOpts } = h.calls[0];
  assert.equal(reqOpts.maxRetries, 0, "the SDK's implicit retries would multiply this ladder");
  assert.ok(reqOpts.timeout > 0 && reqOpts.timeout <= LLM_ATTEMPT_TIMEOUT_MS);
});

test("the documented worst-case bound is arithmetically honest", () => {
  const worstCaseSleep = (1000 + 2000 + 4000) * 1.25;
  assert.ok(
    LLM_MAX_ATTEMPTS * LLM_ATTEMPT_TIMEOUT_MS + worstCaseSleep <= LLM_TOTAL_BUDGET_MS,
    `budget ${LLM_TOTAL_BUDGET_MS}ms cannot cover ${LLM_MAX_ATTEMPTS} x ${LLM_ATTEMPT_TIMEOUT_MS}ms + backoff`
  );
});
