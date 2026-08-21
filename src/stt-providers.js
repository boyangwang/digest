// STT vendors behind one small interface, so `stt.js` can rotate across them
// without knowing anything vendor-specific.
//
// A provider is `{ name, isConfigured(), request(audio, opts) }`.
// `request()` resolves to the NORMALIZED shape `{ text, language, provider }`
// and otherwise throws one of the typed errors below. Nothing else in the
// codebase may talk to a transcription vendor directly.
import {
  ELEVENLABS_API_URL,
  ELEVENLABS_MODEL,
  ELEVENLABS_API_KEY,
  OPENAI_STT_API_URL,
  OPENAI_STT_MODEL,
  OPENAI_API_KEY,
} from "./config.js";

/** Vendor answered, but with an HTTP error. `status` decides retryability. */
export class SttHttpError extends Error {
  constructor(provider, status, body) {
    super(`${provider} HTTP ${status}`);
    this.name = "SttHttpError";
    this.provider = provider;
    this.status = status;
    this.body = String(body ?? "");
  }
}

/**
 * Vendor answered 200 with a well-formed but empty transcript — a genuinely
 * silent recording. Terminal by definition: retrying or rotating cannot invent
 * speech that is not on the tape.
 */
export class SttEmptyError extends Error {
  constructor(provider) {
    super(`${provider} returned empty text`);
    this.name = "SttEmptyError";
    this.provider = provider;
  }
}

/** Vendor could not be reached / did not answer in time. Always retryable. */
export class SttTransportError extends Error {
  constructor(provider, cause) {
    super(`${provider} transport: ${cause?.message || cause}`);
    this.name = "SttTransportError";
    this.provider = provider;
    this.cause = cause;
    this.timedOut = cause?.name === "AbortError" || cause?.name === "TimeoutError";
  }
}

/**
 * POST a multipart form and hand back parsed JSON, mapping every failure onto a
 * typed error. Both vendors happen to speak multipart-in / JSON-out, which is
 * why one helper covers them.
 */
async function postForm(provider, { url, headers, form, timeoutMs, fetchImpl }) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  let res;
  try {
    res = await fetchImpl(url, { method: "POST", headers, body: form, signal: ac.signal });
  } catch (e) {
    throw new SttTransportError(provider, e);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new SttHttpError(provider, res.status, body);
  }
  try {
    return await res.json();
  } catch (e) {
    // 200 with an unparseable body is a vendor hiccup, not a bad request.
    throw new SttTransportError(provider, e);
  }
}

function audioBlob({ buffer, mime }) {
  return new Blob([buffer], { type: mime || "audio/ogg" });
}

const elevenlabs = {
  name: "elevenlabs",
  isConfigured: () => Boolean(ELEVENLABS_API_KEY),
  async request(audio, { timeoutMs, fetchImpl }) {
    const form = new FormData();
    form.append("file", audioBlob(audio), audio.filename);
    form.append("model_id", ELEVENLABS_MODEL);
    const data = await postForm(this.name, {
      url: ELEVENLABS_API_URL,
      headers: { "xi-api-key": ELEVENLABS_API_KEY },
      form,
      timeoutMs,
      fetchImpl,
    });
    const text = String(data?.text ?? "").trim();
    if (!text) throw new SttEmptyError(this.name);
    return { text, language: data?.language_code || null, provider: this.name };
  },
};

const openai = {
  name: "openai",
  isConfigured: () => Boolean(OPENAI_API_KEY),
  async request(audio, { timeoutMs, fetchImpl }) {
    const form = new FormData();
    form.append("file", audioBlob(audio), audio.filename);
    form.append("model", OPENAI_STT_MODEL);
    const data = await postForm(this.name, {
      url: OPENAI_STT_API_URL,
      headers: { Authorization: `Bearer ${OPENAI_API_KEY}` },
      form,
      timeoutMs,
      fetchImpl,
    });
    const text = String(data?.text ?? "").trim();
    if (!text) throw new SttEmptyError(this.name);
    // gpt-4o-transcribe omits a language field; ElevenLabs supplies one. Both
    // auto-detect, so a null language is informational only.
    return { text, language: data?.language || null, provider: this.name };
  },
};

export const PROVIDERS = { elevenlabs, openai };

/** Resolve config's provider names to provider objects, dropping unknown names. */
export function resolveProviders(names) {
  return names.map((n) => PROVIDERS[n]).filter(Boolean);
}
