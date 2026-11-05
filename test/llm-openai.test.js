// The title + caption LLM moved from Tencent TokenHub (both vault keys 401 since
// 2026-09-13, so every title since then was the "spoken ..." fallback and every image
// caption failed) to OpenAI, using the OPENAI_API_KEY the STT fallback already ships.
// These tests pin the OpenAI request shape: GPT-5 models REJECT `max_tokens` and any
// unknown parameter (GLM's `thinking`), so a shape mistake here is a 400 on every note.
//
// Stubbed transport throughout - no test here makes a live LLM call.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

process.env.OPENAI_API_KEY = "oa-test-key";
process.env.TENCENT_TOKENHUB_API_KEY = "";
delete process.env.DIGEST_LLM_PROVIDERS;
delete process.env.DIGEST_TITLE_MODEL;
delete process.env.DIGEST_VISION_MODEL;

const { generateTitleAndTags, captionImage, classifyTitleError } = await import("../src/llm.js");

const INPUT = "一些合成的笔记内容。Some synthetic note content.";
const answer = (content) => ({ choices: [{ message: { content } }] });
const TITLE_JSON = JSON.stringify({ title_zh: "合成标题", title_en: "Synthetic title", tags: [] });

function recorder(reply) {
  const calls = [];
  return {
    calls,
    createImpl: async (body, reqOpts, provider) => {
      calls.push({ body, reqOpts, provider });
      if (reply instanceof Error) throw reply;
      return reply;
    },
  };
}

test("an OpenAI key alone is enough: titles are generated, not the fallback", async () => {
  const r = recorder(answer(TITLE_JSON));
  const out = await generateTitleAndTags(INPUT, { createImpl: r.createImpl });

  assert.equal(out.fallback, false, "with TokenHub dead, OpenAI must carry the title");
  assert.equal(out.title.zh, "合成标题");
  assert.equal(r.calls.length, 1);
  assert.equal(r.calls[0].provider, "openai");
});

test("the title request has a GPT-5 shape: max_completion_tokens, JSON mode, no GLM extras", async () => {
  const r = recorder(answer(TITLE_JSON));
  await generateTitleAndTags(INPUT, { createImpl: r.createImpl });
  const { body } = r.calls[0];

  assert.equal(body.model, "gpt-5.4-mini");
  assert.ok(body.max_completion_tokens > 0, "GPT-5 models take max_completion_tokens");
  assert.equal(body.max_tokens, undefined, "GPT-5 models reject max_tokens with a 400");
  assert.equal(body.thinking, undefined, "`thinking` is a GLM extension; OpenAI rejects it");
  assert.ok(["minimal", "low", "medium", "high"].includes(body.reasoning_effort));
  assert.deepEqual(body.response_format, { type: "json_object" });
  assert.equal(body.messages[0].role, "system");
  assert.match(body.messages[0].content, /JSON/, "JSON mode requires the word JSON in the prompt");
  assert.equal(body.messages[1].content, INPUT);
});

test("image captions go to OpenAI as a vision message with a GPT-5 shape", async () => {
  const dir = mkdtempSync(join(tmpdir(), "digest-caption-"));
  const img = join(dir, "x.png");
  writeFileSync(img, Buffer.from("89504e470d0a1a0a", "hex"));
  const r = recorder(answer("A test image. 一张测试图片。"));

  const caption = await captionImage(img, "image/png", { createImpl: r.createImpl });

  assert.equal(caption, "A test image. 一张测试图片。");
  assert.equal(r.calls.length, 1);
  const { body, provider } = r.calls[0];
  assert.equal(provider, "openai");
  assert.equal(body.model, "gpt-5.4-mini");
  assert.ok(body.max_completion_tokens > 0);
  assert.equal(body.max_tokens, undefined);
  const part = body.messages[0].content.find((p) => p.type === "image_url");
  assert.match(part.image_url.url, /^data:image\/png;base64,iVBORw0KGgo=$/);
});

test("a caption failure is a null caption, never a thrown error", async () => {
  const dir = mkdtempSync(join(tmpdir(), "digest-caption-"));
  const img = join(dir, "x.jpg");
  writeFileSync(img, Buffer.from([0xff, 0xd8, 0xff]));
  const r = recorder(Object.assign(new Error("HTTP 500"), { status: 500 }));
  assert.equal(await captionImage(img, "image/jpeg", { createImpl: r.createImpl }), null);
});

// The exact error OpenAI returned for the vault's key on 2026-11-06 (via the openai SDK).
const noCredit = () =>
  Object.assign(new Error("429 You have no credits remaining. Add credits to continue using the API"), {
    status: 429,
    code: "credit_balance_exhausted",
    type: "insufficient_quota",
  });

test("a no-credit OpenAI account (2026 shape) costs one call, not four, then falls back", async () => {
  assert.equal(classifyTitleError(noCredit()).action, "drop-vendor");
  const naps = [];
  const r = recorder(noCredit());
  const out = await generateTitleAndTags(INPUT, { createImpl: r.createImpl, sleepImpl: async (ms) => naps.push(ms) });
  assert.equal(out.fallback, true);
  assert.equal(r.calls.length, 1, "retrying an empty balance only delays the fallback");
  assert.deepEqual(naps, []);
});

test("an OpenAI account with no credit (429 insufficient_quota) is not retried as if transient", () => {
  const quota = Object.assign(new Error("You exceeded your current quota"), {
    status: 429,
    code: "insufficient_quota",
  });
  assert.equal(classifyTitleError(quota).action, "drop-vendor");
  assert.equal(classifyTitleError(Object.assign(new Error("slow down"), { status: 429 })).action, "retry");
});
