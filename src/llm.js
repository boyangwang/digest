// LLM - title + bilingual tags, and per-image captions, over the Chat Completions API.
// Providers, in config.LLM_PROVIDER_ORDER (DIGEST_LLM_PROVIDERS):
//   openai   gpt-5.4-mini for both (the default since 2026-11-06)
//   tokenhub glm-5.2 (title+tags) + glm-5v-turbo (vision) on Tencent TokenHub;
//            its keys have returned 401 since 2026-09-13, so it is opt-in by env.
// The title system prompt is prompts/title-and-tags.md.
import { readFile } from "node:fs/promises";
import OpenAI from "openai";
import {
  LLM_PROVIDER_ORDER,
  OPENAI_LLM_BASE_URL,
  OPENAI_API_KEY,
  TITLE_MODEL,
  VISION_MODEL,
  TITLE_REASONING_EFFORT,
  VISION_REASONING_EFFORT,
  TITLE_MAX_TOKENS,
  VISION_MAX_TOKENS,
  TOKENHUB_BASE_URL,
  TOKENHUB_API_KEY,
  TOKENHUB_TITLE_MODEL,
  TOKENHUB_VISION_MODEL,
  TOKENHUB_TITLE_MAX_TOKENS,
  LLM_MAX_ATTEMPTS,
  LLM_ATTEMPT_TIMEOUT_MS,
  LLM_BACKOFF_BASE_MS,
  LLM_BACKOFF_MAX_MS,
  LLM_TOTAL_BUDGET_MS,
} from "./config.js";
import { runWithRetry, isOutOfCredit } from "./retry.js";
import { log } from "./log.js";

/**
 * Each provider owns its key, endpoint and request shape. The shapes are NOT
 * interchangeable: GPT-5 models reject `max_tokens` (they take
 * `max_completion_tokens`, which also caps the hidden reasoning) and reject any
 * parameter they do not know, such as GLM's `thinking`.
 */
export const LLM_PROVIDERS = {
  openai: {
    name: "openai",
    key: () => OPENAI_API_KEY,
    baseURL: OPENAI_LLM_BASE_URL,
    titleBody: (messages) => ({
      model: TITLE_MODEL,
      max_completion_tokens: TITLE_MAX_TOKENS,
      reasoning_effort: TITLE_REASONING_EFFORT,
      // JSON mode: the answer is always one parseable object. It requires the word
      // "JSON" in the messages, which the OUTPUT CONTRACT in systemPrompt() supplies.
      response_format: { type: "json_object" },
      messages,
    }),
    visionBody: (messages) => ({
      model: VISION_MODEL,
      max_completion_tokens: VISION_MAX_TOKENS,
      reasoning_effort: VISION_REASONING_EFFORT,
      messages,
    }),
  },
  tokenhub: {
    name: "tokenhub",
    key: () => TOKENHUB_API_KEY,
    baseURL: TOKENHUB_BASE_URL,
    titleBody: (messages) => ({
      model: TOKENHUB_TITLE_MODEL,
      max_tokens: TOKENHUB_TITLE_MAX_TOKENS,
      thinking: { type: "enabled" },
      reasoning_effort: "high",
      messages,
    }),
    visionBody: (messages) => ({ model: TOKENHUB_VISION_MODEL, max_tokens: VISION_MAX_TOKENS, messages }),
  },
};

/** The providers in `order` that exist and have a key, in that order. */
export function configuredProviders(order = LLM_PROVIDER_ORDER) {
  const out = [];
  for (const name of order) {
    const p = LLM_PROVIDERS[name];
    if (!p) log.warn(`Unknown LLM provider "${name}" in DIGEST_LLM_PROVIDERS - ignored`);
    else if (p.key()) out.push(p);
  }
  return out;
}

const clients = new Map();
function clientFor(provider) {
  if (!clients.has(provider.name)) {
    clients.set(provider.name, new OpenAI({ baseURL: provider.baseURL, apiKey: provider.key() }));
  }
  return clients.get(provider.name);
}
/** The real transport. Tests pass `createImpl` with the same (body, reqOpts, providerName). */
const realCreate = (body, reqOpts, providerName) =>
  clientFor(LLM_PROVIDERS[providerName]).chat.completions.create(body, reqOpts);

const PROMPT_URL = new URL("../prompts/title-and-tags.md", import.meta.url);
let _systemPrompt = null;
async function systemPrompt() {
  if (_systemPrompt) return _systemPrompt;
  const doc = await readFile(PROMPT_URL, "utf8");
  _systemPrompt =
    doc +
    "\n\n---\nOUTPUT CONTRACT: Respond with ONLY the raw JSON object " +
    '{ "title_zh", "title_en", "tags": [{ "key", "value" }] } — ' +
    "no markdown fence, no commentary, no reasoning in the output.";
  return _systemPrompt;
}

/** Extract the first balanced top-level JSON object from a string. */
function extractJSON(text) {
  const cleaned = text.replace(/```json/gi, "```").replace(/```/g, "");
  const start = cleaned.indexOf("{");
  if (start === -1) return null;
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < cleaned.length; i++) {
    const c = cleaned[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === '"') inStr = false;
    } else if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) {
        try {
          return JSON.parse(cleaned.slice(start, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

/**
 * Caption one image. Returns a short bilingual caption, or null when no provider is
 * configured or every provider failed: a caption is a nicety, the image itself is
 * already saved, so this never throws into the media job.
 */
export async function captionImage(imagePath, mime = "image/jpeg", { createImpl = realCreate } = {}) {
  const providers = configuredProviders();
  if (!providers.length) return null;
  let dataUrl;
  try {
    const buf = await readFile(imagePath);
    dataUrl = `data:${mime};base64,${buf.toString("base64")}`;
  } catch (e) {
    log.warn(`Vision caption: cannot read ${imagePath}: ${e?.message || e}`);
    return null;
  }
  const messages = [
    {
      role: "user",
      content: [
        {
          type: "text",
          text:
            "Describe this image concisely for journal tagging: any visible text, " +
            "people, place, notable objects, and overall subject. Reply bilingual " +
            "(English then 中文), 1-3 sentences. No preamble.",
        },
        { type: "image_url", image_url: { url: dataUrl } },
      ],
    },
  ];
  for (const p of providers) {
    try {
      const res = await createImpl(p.visionBody(messages), { timeout: LLM_ATTEMPT_TIMEOUT_MS }, p.name);
      const text = res?.choices?.[0]?.message?.content?.trim() || "";
      if (text) {
        log.info(`Vision caption via ${p.name} (${text.length} chars)`);
        return text;
      }
      log.warn(`Vision caption: ${p.name} returned no text`);
    } catch (e) {
      log.warn(`Vision caption failed (${p.name}): ${e?.message || e}`);
    }
  }
  return null;
}

/** The model answered, but not with anything we can use. Retryable: models wobble. */
export class LlmUnparseableError extends Error {
  constructor(raw) {
    super("model returned no usable JSON");
    this.name = "LlmUnparseableError";
    this.raw = String(raw ?? "");
  }
}

const statusOf = (err) => err?.status ?? err?.response?.status ?? err?.cause?.status;

/**
 * Retryability for the title model.
 *   429 / 5xx / network / timeout - transient, retry.
 *   an unparseable answer          - retry: model nondeterminism is exactly what a
 *                                   second attempt fixes, and the attempt cap still
 *                                   terminates a persistently broken response.
 *   429 out of credit              - the account has no money (isOutOfCredit).
 *                                   Not transient: drop the provider like a bad key.
 *   400 / 401 / 403 and other 4xx  - drop this provider: it will not take this
 *                                   request (bad key, no access, unknown model, a
 *                                   parameter it does not support), but another
 *                                   provider might. With one provider configured
 *                                   this ends the ladder after exactly one call.
 */
export function classifyTitleError(err) {
  if (err instanceof LlmUnparseableError) return { action: "retry" };
  const s = statusOf(err);
  if (typeof s === "number") {
    if (s === 429 && isOutOfCredit(err)) return { action: "drop-vendor", reason: "rejected" };
    if (s === 429 || s >= 500) return { action: "retry" };
    if (s >= 400) return { action: "drop-vendor", reason: "rejected" };
  }
  return { action: "retry" };
}

/**
 * Generate title (bilingual) + dynamic bilingual tags from the compiled note text.
 * Returns { title: { zh, en }, tags: [{key,value}], fallback }.
 *
 * Goes through the SAME attempt ladder as transcription (src/retry.js), because
 * `npm run retranscribe` cannot commit a recovered transcript without a real title:
 * one transient provider blip would otherwise leave the note untouched every time.
 * Degrades gracefully at the end of the ladder rather than throwing into `/done`.
 *
 * `fallback` is true when the model never produced a usable answer (no key, every
 * attempt failed) and the title is just the input's first line. Callers that
 * OVERWRITE an existing title must check it: a real answer can legitimately be
 * Chinese-only with no tags, which is indistinguishable from the fallback by shape.
 */
export async function generateTitleAndTags(noteText, opts = {}) {
  const { createImpl = realCreate, ...ladder } = opts;

  const fallback = () => {
    const firstLine = (noteText.split("\n").find((l) => l.trim()) || "Untitled").trim();
    return { title: { zh: firstLine.slice(0, 60), en: "" }, tags: [], fallback: true };
  };
  const providers = configuredProviders();
  if (!providers.length) {
    log.warn(`No key for any LLM provider in [${LLM_PROVIDER_ORDER.join(", ")}] - using fallback title`);
    return fallback();
  }
  const messages = [
    { role: "system", content: await systemPrompt() },
    { role: "user", content: noteText },
  ];

  const r = await runWithRetry({
    label: "llm-title",
    providers,
    call: async (provider, { timeoutMs }) => {
      const res = await createImpl(
        provider.titleBody(messages),
        // maxRetries: 0 - this ladder is the only retry; the SDK's own would multiply it.
        { timeout: timeoutMs, maxRetries: 0 },
        provider.name
      );
      const raw = res?.choices?.[0]?.message?.content?.trim() || "";
      const parsed = extractJSON(raw);
      if (!parsed || (!parsed.title_zh && !parsed.title_en)) throw new LlmUnparseableError(raw);
      return parsed;
    },
    classify: classifyTitleError,
    describe: (err) => ({ status: statusOf(err) || 0, detail: err?.raw || err?.message }),
    summarize: (p) => `zh=${p.title_zh?.length || 0} en=${p.title_en?.length || 0} chars`,
    maxAttempts: LLM_MAX_ATTEMPTS,
    attemptTimeoutMs: LLM_ATTEMPT_TIMEOUT_MS,
    totalBudgetMs: LLM_TOTAL_BUDGET_MS,
    backoffBaseMs: LLM_BACKOFF_BASE_MS,
    backoffMaxMs: LLM_BACKOFF_MAX_MS,
    exhaustedReason: "exhausted",
    rejectedReason: "rejected",
    ...ladder,
  });

  if (!r.ok) {
    log.error(`Title LLM gave up: reason=${r.reason} after ${r.attempts} attempt(s) - using fallback title`);
    return fallback();
  }
  const parsed = r.value;
  const tags = Array.isArray(parsed.tags)
    ? parsed.tags
        .filter((t) => t && t.key)
        .map((t) => ({ key: String(t.key).trim(), value: String(t.value ?? "").trim() }))
    : [];
  log.info(`Title generated via ${r.provider} in ${r.attempts} attempt(s) (${tags.length} tags)`);
  return { title: { zh: parsed.title_zh || "", en: parsed.title_en || "" }, tags, fallback: false };
}
