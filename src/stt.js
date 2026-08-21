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

const truncate = (s, n = 300) => String(s ?? "").replace(/\s+/g, " ").slice(0, n);

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

/** base * 2^(n-1), capped, then jittered ±25%. `n` is 1-based. */
export function backoffMs(n, { base = STT_BACKOFF_BASE_MS, max = STT_BACKOFF_MAX_MS, rand = Math.random } = {}) {
  const flat = Math.min(base * 2 ** (n - 1), max);
  return Math.round(flat * (0.75 + rand() * 0.5));
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

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
    sleepImpl = wait,
    now = () => Date.now(),
    rand = Math.random,
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

  const startedAt = now();
  let cursor = 0;
  let attempts = 0;
  let lastError = null;
  let stopReason = null;

  for (let attempt = 1; attempt <= maxAttempts && live.length; attempt++) {
    const elapsedTotal = now() - startedAt;
    const remaining = totalBudgetMs - elapsedTotal;
    if (remaining <= 0) {
      log.warn(`stt: total budget ${totalBudgetMs}ms exhausted after ${attempts} attempt(s)`);
      break;
    }
    // Rotate: with N live vendors this alternates 1,2,…,N,1,2,… across attempts.
    const provider = live[cursor % live.length];
    if (!providersTried.includes(provider.name)) providersTried.push(provider.name);
    attempts = attempt;

    const t0 = now();
    try {
      const out = await provider.request(audio, {
        timeoutMs: Math.min(attemptTimeoutMs, remaining),
        fetchImpl,
      });
      log.info(
        `stt attempt ${attempt}/${maxAttempts} provider=${provider.name} status=200 ` +
          `elapsed=${now() - t0}ms chars=${out.text.length} lang=${out.language || "?"}`
      );
      return { ok: true, text: out.text, language: out.language, provider: provider.name, attempts };
    } catch (err) {
      const status = err instanceof SttHttpError ? err.status : err?.name === "SttEmptyError" ? 200 : 0;
      const detail = err instanceof SttHttpError ? err.body : err?.message;
      lastError = `${provider.name}: ${truncate(detail)}`;
      log.warn(
        `stt attempt ${attempt}/${maxAttempts} provider=${provider.name} status=${status || "-"} ` +
          `elapsed=${now() - t0}ms error="${truncate(detail, 300)}"`
      );

      const { action, reason } = classify(err);
      if (action === "stop") {
        stopReason = reason;
        break;
      }
      if (action === "drop-vendor") {
        // Removing at `cursor % live.length` leaves the cursor pointing at the
        // NEXT live vendor, so no attempt is wasted re-picking the dead one.
        live.splice(cursor % live.length, 1);
        stopReason = reason; // only survives if no other vendor works out
        continue; // a different vendor needs no cool-off
      }
      stopReason = null;
      cursor += 1; // transient → rotate to the next vendor for the next attempt
      if (attempt < maxAttempts && live.length) {
        const nap = Math.min(backoffMs(attempt, { rand }), Math.max(0, totalBudgetMs - (now() - startedAt)));
        if (nap > 0) await sleepImpl(nap);
      }
    }
  }

  const reason = stopReason || (live.length ? STT_FAIL.EXHAUSTED : STT_FAIL.REJECTED);
  log.error(
    `stt: giving up after ${attempts} attempt(s) across [${providersTried.join(", ")}] ` +
      `reason=${reason} last="${truncate(lastError, 200)}"`
  );
  return { ok: false, reason, attempts, providersTried, lastError };
}
