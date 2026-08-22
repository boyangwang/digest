// LLM — Tencent TokenHub (OpenAI Chat Completions).
//   glm-5.2      → title + bilingual tags (system prompt = prompts/title-and-tags.md)
//   glm-5v-turbo → per-image bilingual caption (feeds title/tags at compile time)
import { readFile } from "node:fs/promises";
import OpenAI from "openai";
import {
  TOKENHUB_BASE_URL,
  TOKENHUB_API_KEY,
  TITLE_MODEL,
  VISION_MODEL,
  TITLE_MAX_TOKENS,
  VISION_MAX_TOKENS,
  LLM_MAX_ATTEMPTS,
  LLM_ATTEMPT_TIMEOUT_MS,
  LLM_BACKOFF_BASE_MS,
  LLM_BACKOFF_MAX_MS,
  LLM_TOTAL_BUDGET_MS,
} from "./config.js";
import { runWithRetry } from "./retry.js";
import { log } from "./log.js";

// Dummy key keeps the SDK from throwing at construction when unconfigured; every
// call site guards on TOKENHUB_API_KEY before actually using the client.
const client = new OpenAI({ baseURL: TOKENHUB_BASE_URL, apiKey: TOKENHUB_API_KEY || "no-key" });

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

/** Caption one image via glm-5v-turbo. Returns a short bilingual caption or null. */
export async function captionImage(imagePath, mime = "image/jpeg") {
  if (!TOKENHUB_API_KEY) return null;
  try {
    const buf = await readFile(imagePath);
    const dataUrl = `data:${mime};base64,${buf.toString("base64")}`;
    const res = await client.chat.completions.create({
      model: VISION_MODEL,
      max_tokens: VISION_MAX_TOKENS,
      messages: [
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
      ],
    });
    const text = res.choices?.[0]?.message?.content?.trim() || "";
    return text || null;
  } catch (e) {
    log.warn(`Vision caption failed: ${e?.message || e}`);
    return null;
  }
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
 *   429 / 5xx / network / timeout — transient, retry.
 *   an unparseable answer          — retry: model nondeterminism is exactly what a
 *                                   second attempt fixes, and the attempt cap still
 *                                   terminates a persistently broken response.
 *   400 / 401 / 403 and other 4xx  — terminal: our request is the problem, and
 *                                   repeating it verbatim cannot change the answer.
 */
export function classifyTitleError(err) {
  if (err instanceof LlmUnparseableError) return { action: "retry" };
  const s = statusOf(err);
  if (typeof s === "number") {
    if (s === 429 || s >= 500) return { action: "retry" };
    if (s >= 400) return { action: "stop", reason: "rejected" };
  }
  return { action: "retry" };
}

/**
 * Generate title (bilingual) + dynamic bilingual tags from the compiled note text.
 * Returns { title: { zh, en }, tags: [{key,value}], fallback }.
 *
 * Goes through the SAME attempt ladder as transcription (src/retry.js), because
 * `npm run retranscribe` cannot commit a recovered transcript without a real title:
 * one transient TokenHub blip would otherwise leave the note untouched every time.
 * Degrades gracefully at the end of the ladder rather than throwing into `/done`.
 *
 * `fallback` is true when the model never produced a usable answer (no key, every
 * attempt failed) and the title is just the input's first line. Callers that
 * OVERWRITE an existing title must check it: a real answer can legitimately be
 * Chinese-only with no tags, which is indistinguishable from the fallback by shape.
 */
export async function generateTitleAndTags(noteText, opts = {}) {
  const { createImpl, ...ladder } = opts;
  const create = createImpl || ((body, reqOpts) => client.chat.completions.create(body, reqOpts));

  const fallback = () => {
    const firstLine = (noteText.split("\n").find((l) => l.trim()) || "Untitled").trim();
    return { title: { zh: firstLine.slice(0, 60), en: "" }, tags: [], fallback: true };
  };
  if (!TOKENHUB_API_KEY) {
    log.warn("No TENCENT_TOKENHUB_API_KEY — using fallback title");
    return fallback();
  }

  const r = await runWithRetry({
    label: "llm-title",
    providers: [{ name: "tokenhub" }],
    call: async (_provider, { timeoutMs }) => {
      const res = await create(
        {
          model: TITLE_MODEL,
          max_tokens: TITLE_MAX_TOKENS,
          thinking: { type: "enabled" },
          reasoning_effort: "high",
          messages: [
            { role: "system", content: await systemPrompt() },
            { role: "user", content: noteText },
          ],
        },
        // maxRetries: 0 — this ladder is the only retry; the SDK's own would multiply it.
        { timeout: timeoutMs, maxRetries: 0 }
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
    log.error(`Title LLM gave up: reason=${r.reason} after ${r.attempts} attempt(s) — using fallback title`);
    return fallback();
  }
  const parsed = r.value;
  const tags = Array.isArray(parsed.tags)
    ? parsed.tags
        .filter((t) => t && t.key)
        .map((t) => ({ key: String(t.key).trim(), value: String(t.value ?? "").trim() }))
    : [];
  log.info(`Title generated in ${r.attempts} attempt(s) (${tags.length} tags)`);
  return { title: { zh: parsed.title_zh || "", en: parsed.title_en || "" }, tags, fallback: false };
}
