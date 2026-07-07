// STT — ElevenLabs Scribe v2. Ported from the proven Python stt.py.
// Auto-detects language (bilingual zh+en). Returns transcript string, or null on any failure.
import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import {
  ELEVENLABS_API_URL,
  ELEVENLABS_MODEL,
  ELEVENLABS_API_KEY,
} from "./config.js";
import { log } from "./log.js";

export async function transcribe(audioPath, { timeoutMs = 60000 } = {}) {
  if (!ELEVENLABS_API_KEY) {
    log.warn("No ELEVENLABS_API_KEY — cannot transcribe");
    return null;
  }
  try {
    const buf = await readFile(audioPath);
    const form = new FormData();
    form.append("file", new Blob([buf], { type: "audio/ogg" }), basename(audioPath));
    form.append("model_id", ELEVENLABS_MODEL);

    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), timeoutMs);
    let res;
    try {
      res = await fetch(ELEVENLABS_API_URL, {
        method: "POST",
        headers: { "xi-api-key": ELEVENLABS_API_KEY },
        body: form,
        signal: ac.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      log.warn(`ElevenLabs API error ${res.status}: ${body.slice(0, 300)}`);
      return null;
    }
    const data = await res.json();
    const text = (data.text || "").trim();
    if (!text) {
      log.warn("ElevenLabs returned empty text");
      return null;
    }
    log.info(`Transcribed ${text.length} chars (lang=${data.language_code || "?"})`);
    return text;
  } catch (e) {
    log.warn(`STT error: ${e?.message || e}`);
    return null;
  }
}
