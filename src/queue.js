// Per-key serial async queue. Guarantees inputs for one chat run one at a time,
// in arrival order — the "linear and deterministic" requirement.
import { log } from "./log.js";

const chains = new Map();

export function enqueue(key, task) {
  const prev = chains.get(key) || Promise.resolve();
  const next = prev
    .catch(() => {}) // isolate: a failed task must not break the chain
    .then(() => task())
    .catch((err) => log.error(`queue task failed (${key}): ${err?.message || err}`));
  chains.set(key, next);
  next.finally(() => {
    if (chains.get(key) === next) chains.delete(key);
  });
  return next;
}

/** For tests: wait until a key's queue drains. */
export async function drain(key) {
  await (chains.get(key) || Promise.resolve());
}
