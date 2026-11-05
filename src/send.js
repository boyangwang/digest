// Outbound chat sends with a short retry ladder.
//
// A result or an apology that never reaches the chat is indistinguishable, from the
// user's side, from the bot ignoring them - the exact failure the media pipeline was
// rebuilt to remove. So every send goes through here: a few quick attempts on
// transient errors, a definitive 4xx stops at once, and a final failure is logged
// loudly. Never throws.
import { SEND_MAX_ATTEMPTS, SEND_ATTEMPT_TIMEOUT_MS, SEND_TOTAL_BUDGET_MS } from "./config.js";
import { runWithRetry, errorDetail } from "./retry.js";

/** Pull an HTTP-ish status out of the platform SDK errors we use. */
function statusOf(err) {
  // grammY GrammyError: error_code; grammY HttpError: no status (network).
  // Lark (src/lark.js LarkApiError): status + code.
  if (typeof err?.error_code === "number") return err.error_code;
  if (typeof err?.status === "number" && err.status) return err.status;
  return 0;
}

export function classifySend(err) {
  const s = statusOf(err);
  if (!s || s === 429 || s >= 500 || err?.retryable) return { action: "retry" };
  return { action: "stop", reason: "rejected" };
}

function withDeadline(promise, ms) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(Object.assign(new Error(`send timed out after ${ms}ms`), { name: "TimeoutError" })), ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

/**
 * Run `send()` until it succeeds or the ladder gives up.
 * @returns {Promise<boolean>} true if delivered
 */
export async function sendWithRetry(send, { label = "send", sleepImpl, maxAttempts = SEND_MAX_ATTEMPTS } = {}) {
  const r = await runWithRetry({
    label,
    providers: [{ name: label }],
    call: (_p, { timeoutMs }) => withDeadline(Promise.resolve().then(send), timeoutMs),
    classify: classifySend,
    describe: (err) => ({ status: statusOf(err), detail: errorDetail(err) }),
    maxAttempts,
    attemptTimeoutMs: SEND_ATTEMPT_TIMEOUT_MS,
    totalBudgetMs: SEND_TOTAL_BUDGET_MS,
    backoffBaseMs: 1000,
    backoffMaxMs: 8000,
    exhaustedReason: "exhausted",
    rejectedReason: "rejected",
    ...(sleepImpl ? { sleepImpl } : {}),
  });
  return r.ok;
}

/** Split text into chunks of at most `max` characters, preferring line breaks. */
export function chunkText(text, max) {
  const s = String(text ?? "");
  if (s.length <= max) return [s];
  const out = [];
  let rest = s;
  while (rest.length > max) {
    let cut = rest.lastIndexOf("\n", max);
    if (cut < max * 0.5) cut = rest.lastIndexOf(" ", max);
    if (cut < max * 0.5) cut = max;
    const code = rest.charCodeAt(cut - 1);
    if (code >= 0xd800 && code <= 0xdbff) cut -= 1; // never split a surrogate pair (emoji)
    out.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^\n/, "");
  }
  if (rest) out.push(rest);
  return out;
}
