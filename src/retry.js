// The attempt ladder, shared by every vendor call in this codebase.
//
// Lifted out of `stt.js` unchanged in behaviour, because the shape is generic and
// the second call site needs exactly the same durability: bounded attempts rotating
// across an ordered provider list, transient-vs-terminal classification, exponential
// backoff with jitter, a hard total-time budget, and one log line per attempt
// carrying provider, attempt number, status, elapsed ms and a truncated error body.
//
// Callers supply the domain: the providers, how to call one, how to classify a
// failure, and what the two give-up reasons are called. Everything timing-related is
// data-driven from `config.js` at the call site, never hardcoded here.
import { log } from "./log.js";

export const truncate = (s, n = 300) => String(s ?? "").replace(/\s+/g, " ").slice(0, n);

/** base * 2^(n-1), capped, then jittered ±25%. `n` is 1-based. */
export function backoffMs(n, { base, max, rand = Math.random } = {}) {
  const flat = Math.min(base * 2 ** (n - 1), max);
  return Math.round(flat * (0.75 + rand() * 0.5));
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Classification contract, returned by the caller's `classify(err)`:
 *   "retry"        — transient; keep this provider in the rotation and try again.
 *   "drop-vendor"  — this provider will keep saying no (bad key, no permission), but
 *                    another might not. Remove it, do not sleep, carry on.
 *   "stop"         — the INPUT is the problem. No provider will answer differently,
 *                    so rotating would be pointless: end the ladder immediately.
 */

/**
 * Run `call` against `providers` until one succeeds or the ladder runs out.
 *
 * @returns {Promise<{ok:true,value:*,provider:string,attempts:number}
 *                  | {ok:false,reason:string,attempts:number,providersTried:string[],lastError:?string}>}
 */
export async function runWithRetry({
  label,
  providers,
  call,
  classify,
  describe = (err) => ({ status: 0, detail: err?.message }),
  summarize = () => "",
  maxAttempts,
  attemptTimeoutMs,
  totalBudgetMs,
  backoffBaseMs,
  backoffMaxMs,
  exhaustedReason,
  rejectedReason,
  sleepImpl = wait,
  now = () => Date.now(),
  rand = Math.random,
}) {
  const live = [...providers];
  const providersTried = [];
  if (!live.length) {
    return { ok: false, reason: rejectedReason, attempts: 0, providersTried, lastError: null };
  }

  const startedAt = now();
  let cursor = 0;
  let attempts = 0;
  let lastError = null;
  let stopReason = null;

  for (let attempt = 1; attempt <= maxAttempts && live.length; attempt++) {
    const remaining = totalBudgetMs - (now() - startedAt);
    if (remaining <= 0) {
      log.warn(`${label}: total budget ${totalBudgetMs}ms exhausted after ${attempts} attempt(s)`);
      break;
    }
    // Rotate: with N live providers this alternates 1,2,…,N,1,2,… across attempts.
    const provider = live[cursor % live.length];
    if (!providersTried.includes(provider.name)) providersTried.push(provider.name);
    attempts = attempt;

    const t0 = now();
    try {
      const value = await call(provider, { timeoutMs: Math.min(attemptTimeoutMs, remaining) });
      log.info(
        `${label} attempt ${attempt}/${maxAttempts} provider=${provider.name} status=200 ` +
          `elapsed=${now() - t0}ms ${summarize(value)}`.trimEnd()
      );
      return { ok: true, value, provider: provider.name, attempts };
    } catch (err) {
      const { status, detail } = describe(err);
      lastError = `${provider.name}: ${truncate(detail)}`;
      log.warn(
        `${label} attempt ${attempt}/${maxAttempts} provider=${provider.name} status=${status || "-"} ` +
          `elapsed=${now() - t0}ms error="${truncate(detail, 300)}"`
      );

      const { action, reason } = classify(err);
      if (action === "stop") {
        stopReason = reason;
        break;
      }
      if (action === "drop-vendor") {
        // Removing at `cursor % live.length` leaves the cursor pointing at the
        // NEXT live provider, so no attempt is wasted re-picking the dead one.
        live.splice(cursor % live.length, 1);
        stopReason = reason; // only survives if no other provider works out
        continue; // a different provider needs no cool-off
      }
      stopReason = null;
      cursor += 1; // transient → rotate to the next provider for the next attempt
      if (attempt < maxAttempts && live.length) {
        const nap = Math.min(
          backoffMs(attempt, { base: backoffBaseMs, max: backoffMaxMs, rand }),
          Math.max(0, totalBudgetMs - (now() - startedAt))
        );
        if (nap > 0) await sleepImpl(nap);
      }
    }
  }

  const reason = stopReason || (live.length ? exhaustedReason : rejectedReason);
  log.error(
    `${label}: giving up after ${attempts} attempt(s) across [${providersTried.join(", ")}] ` +
      `reason=${reason} last="${truncate(lastError, 200)}"`
  );
  return { ok: false, reason, attempts, providersTried, lastError };
}
