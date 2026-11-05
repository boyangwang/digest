// Provider rotation for the title + caption LLM (DIGEST_LLM_PROVIDERS). TokenHub is
// kept as an env-only re-enable, so a provider that refuses (bad key, no access, a
// parameter it does not take) must hand over to the next one, not end the ladder,
// and each provider must get ITS OWN request shape.
//
// Stubbed transport throughout - no test here makes a live LLM call.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.OPENAI_API_KEY = "oa-test-key";
process.env.TENCENT_TOKENHUB_API_KEY = "th-test-key";
process.env.DIGEST_LLM_PROVIDERS = "openai,tokenhub";

const { generateTitleAndTags, captionImage } = await import("../src/llm.js");

const INPUT = "合成内容 synthetic";
const answer = (content) => ({ choices: [{ message: { content } }] });
const httpError = (status) => Object.assign(new Error(`HTTP ${status}`), { status });

function script(steps) {
  const calls = [];
  const naps = [];
  return {
    calls,
    naps,
    opts: {
      createImpl: async (body, reqOpts, provider) => {
        const step = steps[calls.length] ?? steps[steps.length - 1];
        calls.push({ body, provider });
        if (step instanceof Error) throw step;
        return step;
      },
      sleepImpl: async (ms) => naps.push(ms),
      now: () => 0,
      rand: () => 0.5,
    },
  };
}

test("a 401 from OpenAI drops it and TokenHub answers, with no backoff sleep", async () => {
  const s = script([httpError(401), answer(JSON.stringify({ title_zh: "标题", title_en: "Title", tags: [] }))]);
  const r = await generateTitleAndTags(INPUT, s.opts);

  assert.equal(r.fallback, false);
  assert.equal(r.title.en, "Title");
  assert.deepEqual(s.calls.map((c) => c.provider), ["openai", "tokenhub"]);
  assert.deepEqual(s.naps, [], "a different provider needs no cool-off");
});

test("each provider gets its own request shape", async () => {
  const s = script([httpError(403), answer(JSON.stringify({ title_zh: "标题" }))]);
  await generateTitleAndTags(INPUT, s.opts);
  const [oa, th] = s.calls.map((c) => c.body);

  assert.equal(oa.model, "gpt-5.4-mini");
  assert.ok(oa.max_completion_tokens > 0);
  assert.equal(oa.thinking, undefined);

  assert.equal(th.model, "glm-5.2");
  assert.ok(th.max_tokens > 0, "TokenHub's GLM takes max_tokens");
  assert.deepEqual(th.thinking, { type: "enabled" });
});

test("when every provider refuses, the title falls back after one call each", async () => {
  const s = script([httpError(401)]);
  const r = await generateTitleAndTags(INPUT, s.opts);

  assert.equal(r.fallback, true);
  assert.deepEqual(s.calls.map((c) => c.provider), ["openai", "tokenhub"]);
});

test("a caption falls through to the next provider when the first one fails", async () => {
  const dir = mkdtempSync(join(tmpdir(), "digest-caption-"));
  const img = join(dir, "x.jpg");
  writeFileSync(img, Buffer.from([0xff, 0xd8, 0xff]));
  const s = script([httpError(500), answer("caption from glm")]);

  const caption = await captionImage(img, "image/jpeg", s.opts);

  assert.equal(caption, "caption from glm");
  assert.deepEqual(s.calls.map((c) => c.provider), ["openai", "tokenhub"]);
  assert.equal(s.calls[1].body.model, "glm-5v-turbo");
});
