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
} from "./config.js";
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

/**
 * Generate title (bilingual) + dynamic bilingual tags from the compiled note text.
 * Returns { title: { zh, en }, tags: [{key,value}] }. Falls back gracefully.
 */
export async function generateTitleAndTags(noteText) {
  const fallback = () => {
    const firstLine = (noteText.split("\n").find((l) => l.trim()) || "Untitled").trim();
    return { title: { zh: firstLine.slice(0, 60), en: "" }, tags: [] };
  };
  if (!TOKENHUB_API_KEY) {
    log.warn("No TENCENT_TOKENHUB_API_KEY — using fallback title");
    return fallback();
  }
  try {
    const res = await client.chat.completions.create({
      model: TITLE_MODEL,
      max_tokens: TITLE_MAX_TOKENS,
      thinking: { type: "enabled" },
      reasoning_effort: "high",
      messages: [
        { role: "system", content: await systemPrompt() },
        { role: "user", content: noteText },
      ],
    });
    const raw = res.choices?.[0]?.message?.content?.trim() || "";
    const parsed = extractJSON(raw);
    if (!parsed || (!parsed.title_zh && !parsed.title_en)) {
      log.warn("Title LLM returned unparseable output — using fallback");
      return fallback();
    }
    const tags = Array.isArray(parsed.tags)
      ? parsed.tags
          .filter((t) => t && t.key)
          .map((t) => ({ key: String(t.key).trim(), value: String(t.value ?? "").trim() }))
      : [];
    log.info(`Title generated (zh=${parsed.title_zh?.length || 0} en=${parsed.title_en?.length || 0} chars, ${tags.length} tags)`);
    return { title: { zh: parsed.title_zh || "", en: parsed.title_en || "" }, tags };
  } catch (e) {
    log.error(`Title LLM error: ${e?.message || e}`);
    return fallback();
  }
}
