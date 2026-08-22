// In-flight transcriptions, tracked per chat.
//
// Transcription deliberately runs OUTSIDE the per-chat serial queue: the block's
// slot in the note is already reserved by `appendBlock` at ARRIVAL time, and
// `updateBlock` patches it by seq afterwards, so a late transcript still renders
// in the right place. Holding the queue for the whole retry budget would only buy
// a stall on every subsequent message from that chat - including `/done`.
//
// What the queue *did* implicitly guarantee is that `/done` never compiled while a
// transcript was still coming. That guarantee moves here: finalize waits on this
// registry, bounded, and compiles with the retryable failure marker if the bound
// is hit.
import { STT_TOTAL_BUDGET_MS } from "./config.js";
import { log } from "./log.js";

/** chatId → Set<Promise> of settled-or-pending transcription tasks. */
const inflight = new Map();

/**
 * Register a running transcription for a chat. The returned promise never
 * rejects, so a caller may fire-and-forget it without an unhandled rejection.
 */
export function trackTranscription(chatId, promise) {
  const key = String(chatId);
  let set = inflight.get(key);
  if (!set) {
    set = new Set();
    inflight.set(key, set);
  }
  const task = Promise.resolve(promise).catch((e) => {
    log.error(`transcription task failed (${key}): ${e?.message || e}`);
  });
  set.add(task);
  task.then(() => {
    set.delete(task);
    if (set.size === 0 && inflight.get(key) === set) inflight.delete(key);
  });
  return task;
}

/** How many transcriptions are still running for a chat. */
export function inflightCount(chatId) {
  return inflight.get(String(chatId))?.size ?? 0;
}

/**
 * Wait for a chat's outstanding transcriptions, bounded by `timeoutMs` so `/done`
 * can never hang forever.
 * @returns {Promise<boolean>} true if everything settled, false if the bound bit.
 */
export async function waitForTranscriptions(chatId, { timeoutMs = STT_TOTAL_BUDGET_MS } = {}) {
  const key = String(chatId);
  const deadline = Date.now() + timeoutMs;
  // Re-loop rather than return after the race: a transcript that landed while we
  // waited may have been followed by another arrival, and the set is authoritative.
  for (;;) {
    const set = inflight.get(key);
    if (!set || set.size === 0) return true;

    const remaining = deadline - Date.now();
    let settled = false;
    if (remaining > 0) {
      let timer;
      const bound = new Promise((resolve) => {
        timer = setTimeout(() => resolve(false), remaining);
        timer.unref?.();
      });
      settled = await Promise.race([Promise.all([...set]).then(() => true), bound]);
      clearTimeout(timer);
    }
    if (!settled) {
      log.warn(`finalize: ${set.size} transcription(s) still in flight after ${timeoutMs}ms — compiling anyway`);
      return false;
    }
  }
}
