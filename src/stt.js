// STT — the durability layer in front of the transcription vendors.
//
// Transcription is the only step in the pipeline with no local fallback: if it
// fails, the words are gone. So EVERY transcription in this codebase goes through
// `transcribe()` below, which retries across vendors instead of giving up on the
// first hiccup. (The incident that motivated it: two voice notes recorded
// "[Transcription unavailable]" and were later replayed against the *same* key,
// endpoint and model and transcribed on the first try. One retry would have saved
// both.)
//
// Shape of the loop — all of it data-driven from `config.js`, none of it hardcoded
// at a call site:
//   • up to STT_MAX_ATTEMPTS attempts, rotating across STT_PROVIDER_ORDER, so
//     attempt N uses vendor[(N-1) % vendors.length]: 1,2,1,2,1,2 with two vendors,
//     and all attempts on the single vendor when only one is configured;
//   • exponential backoff with jitter between attempts, under a hard total-time
//     budget (see STT_TOTAL_BUDGET_MS — a Telegram user is waiting on this reply);
//   • only *transient* failures are retried. See classify() for the rules: a bad
//     request or a silent recording is terminal and must not burn six vendor calls;
//   • every attempt logs vendor, attempt number, HTTP status, elapsed ms and the
//     truncated error body, so the next failure is answerable from the log alone.
//
// Returns a RESULT OBJECT, never a bare string — the caller needs to tell "silent
// recording" from "vendor was down", and needs a durable marker it can retry from.
import { readFile } from "node:fs/promises";
import { basename } from "node:path";
import {
  STT_PROVIDER_ORDER,
  STT_MAX_ATTEMPTS,
  STT_ATTEMPT_TIMEOUT_MS,
  STT_BACKOFF_BASE_MS,
  STT_BACKOFF_MAX_MS,
  STT_TOTAL_BUDGET_MS,
} from "./config.js";
import { resolveProviders, SttHttpError, SttEmptyError } from "./stt-providers.js";
import { runWithRetry, backoffMs as ladderBackoffMs, truncate } from "./retry.js";
import { log } from "./log.js";

/** Failure reasons. Persisted into the block, so treat them as a stable contract. */
export const STT_FAIL = {
  UNCONFIGURED: "unconfigured", // no vendor has a key — nothing was even attempted
  UNREADABLE: "unreadable", // could not read the audio file off disk
  EMPTY: "empty", // vendor heard nothing: a genuinely silent recording
  BAD_AUDIO: "bad-audio", // vendor rejected the request/file itself (4xx)
  REJECTED: "rejected", // every vendor refused us (auth/permission)
  EXHAUSTED: "exhausted", // transient failures all the way to the attempt cap
};

/**
 * Decide what an attempt's failure means for the loop.
 *   "retry"        — transient; keep this vendor in the rotation and try again.
 *   "drop-vendor"  — this vendor will keep saying no (bad key, no permission), but
 *                    another vendor might not. Remove it, do not sleep, carry on.
 *   "stop"         — the INPUT is the problem (malformed/oversized/unsupported
 *                    audio, or silence). No vendor will answer differently, so
 *                    rotating would be pointless: end the loop immediately.
 */
export function classify(err) {
  if (err instanceof SttEmptyError) return { action: "stop", reason: STT_FAIL.EMPTY };
  if (err instanceof SttHttpError) {
    const s = err.status;
    if (s === 429 || s >= 500) return { action: "retry" }; // rate limit / vendor-side fault
    // The request or the audio is bad — same answer from anyone.
    if (s === 400 || s === 413 || s === 415 || s === 422)
      return { action: "stop", reason: STT_FAIL.BAD_AUDIO };
    // Any other 4xx (401/403/404/…) is about OUR standing with THIS vendor.
    return { action: "drop-vendor", reason: STT_FAIL.REJECTED };
  }
  // Network error, DNS, socket reset, abort/timeout → transient by definition.
  return { action: "retry" };
}

/** base * 2^(n-1), capped, then jittered ±25%, with the STT ladder's defaults. */
export function backoffMs(n, opts = {}) {
  return ladderBackoffMs(n, { base: STT_BACKOFF_BASE_MS, max: STT_BACKOFF_MAX_MS, ...opts });
}

/**
 * Transcribe an audio file, surviving transient vendor failure.
 *
 * @param {string} audioPath absolute path to the saved audio
 * @param {object} [opts] injection seams for tests — production uses the defaults
 * @returns {Promise<{ok:true,text:string,language:?string,provider:string,attempts:number}
 *                  | {ok:false,reason:string,attempts:number,providersTried:string[],lastError:?string}>}
 */
export async function transcribe(audioPath, opts = {}) {
  const {
    fetchImpl = globalThis.fetch,
    sleepImpl,
    now,
    rand,
    maxAttempts = STT_MAX_ATTEMPTS,
    attemptTimeoutMs = STT_ATTEMPT_TIMEOUT_MS,
    totalBudgetMs = STT_TOTAL_BUDGET_MS,
    providerOrder = STT_PROVIDER_ORDER,
    mime,
  } = opts;

  const live = resolveProviders(providerOrder).filter((p) => p.isConfigured());
  const providersTried = [];
  if (!live.length) {
    log.warn(`stt: no transcription vendor configured (order=${providerOrder.join(",")}) — cannot transcribe`);
    return { ok: false, reason: STT_FAIL.UNCONFIGURED, attempts: 0, providersTried, lastError: null };
  }

  let buffer;
  try {
    buffer = await readFile(audioPath);
  } catch (e) {
    log.error(`stt: cannot read audio ${audioPath}: ${e?.message || e}`);
    return {
      ok: false,
      reason: STT_FAIL.UNREADABLE,
      attempts: 0,
      providersTried,
      lastError: truncate(e?.message || e),
    };
  }
  const audio = { buffer, filename: basename(audioPath), mime };

  const r = await runWithRetry({
    label: "stt",
    providers: live,
    call: (provider, { timeoutMs }) => provider.request(audio, { timeoutMs, fetchImpl }),
    classify,
    describe: (err) => ({
      status: err instanceof SttHttpError ? err.status : err?.name === "SttEmptyError" ? 200 : 0,
      detail: err instanceof SttHttpError ? err.body : err?.message,
    }),
    summarize: (out) => `chars=${out.text.length} lang=${out.language || "?"}`,
    maxAttempts,
    attemptTimeoutMs,
    totalBudgetMs,
    backoffBaseMs: STT_BACKOFF_BASE_MS,
    backoffMaxMs: STT_BACKOFF_MAX_MS,
    exhaustedReason: STT_FAIL.EXHAUSTED,
    rejectedReason: STT_FAIL.REJECTED,
    ...(sleepImpl ? { sleepImpl } : {}),
    ...(now ? { now } : {}),
    ...(rand ? { rand } : {}),
  });

  if (r.ok) {
    return { ok: true, text: r.value.text, language: r.value.language, provider: r.provider, attempts: r.attempts };
  }
  return { ok: false, reason: r.reason, attempts: r.attempts, providersTried: r.providersTried, lastError: r.lastError };
}
