// Store — pending-digest persistence. State lives on disk so a restart never loses
// captured input. One dir per chat under PENDING_DIR; manifest.json + staged attachments.
// All manifest writes are atomic (tmp → rename). "ACK-after-persist" = caller sends the
// ACK only after appendBlock()/saveAttachment() resolve.
import { promises as fs } from "node:fs";
import { join } from "node:path";
import { PENDING_DIR } from "./config.js";

function chatDir(chatId) {
  return join(PENDING_DIR, String(chatId));
}
function manifestPath(chatId) {
  return join(chatDir(chatId), "manifest.json");
}

/**
 * Serialize every read-modify-write on one chat's manifest.
 *
 * The per-chat queue used to provide this for free, but transcription now runs
 * outside it, so a late `updateBlock` can interleave with the `appendBlock` of the
 * next message — and since both rewrite the WHOLE manifest, the loser's write is
 * silently lost. `clearPending` takes the lock too: without it an in-flight
 * updateBlock could rewrite the manifest after finalize deleted it and resurrect a
 * stale digest.
 */
const chatLocks = new Map();

function withChatLock(chatId, fn) {
  const key = String(chatId);
  const prev = chatLocks.get(key) || Promise.resolve();
  const run = prev.then(fn, fn); // a rejected predecessor must not stall the chain
  const gate = run.then(
    () => {},
    () => {}
  );
  chatLocks.set(key, gate);
  gate.then(() => {
    if (chatLocks.get(key) === gate) chatLocks.delete(key);
  });
  return run;
}

async function atomicWriteJSON(path, obj) {
  const tmp = `${path}.tmp`;
  await fs.writeFile(tmp, JSON.stringify(obj, null, 2), "utf8");
  await fs.rename(tmp, path);
}

export async function hasPending(chatId) {
  try {
    await fs.access(manifestPath(chatId));
    return true;
  } catch {
    return false;
  }
}

export async function loadPending(chatId) {
  try {
    const raw = await fs.readFile(manifestPath(chatId), "utf8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// The `*Unlocked` helpers are the real implementations; the exported wrappers hold
// the chat lock. Keeping them separate is what stops appendBlock → ensurePending →
// startPending from deadlocking on a lock it already owns.
async function startPendingUnlocked(chatId, startedAt) {
  await fs.mkdir(chatDir(chatId), { recursive: true });
  const manifest = { chatId, startedAt, seq: 0, blocks: [] };
  await atomicWriteJSON(manifestPath(chatId), manifest);
  return manifest;
}

async function ensurePendingUnlocked(chatId) {
  return (await loadPending(chatId)) || (await startPendingUnlocked(chatId, new Date().toISOString()));
}

/** Create a fresh pending digest for a chat. Returns the new manifest. */
export function startPending(chatId, startedAt = new Date().toISOString()) {
  return withChatLock(chatId, () => startPendingUnlocked(chatId, startedAt));
}

/** Ensure a pending digest exists (auto-start on first input). Returns the manifest. */
export function ensurePending(chatId) {
  return withChatLock(chatId, () => ensurePendingUnlocked(chatId));
}

/**
 * Append a block. Assigns { seq, ts } and persists atomically BEFORE returning,
 * so the caller can ACK knowing the input is durable. Returns the stored block.
 */
export function appendBlock(chatId, block) {
  return withChatLock(chatId, async () => {
    const manifest = await ensurePendingUnlocked(chatId);
    const stored = { seq: manifest.seq, ts: new Date().toISOString(), ...block };
    manifest.seq += 1;
    manifest.blocks.push(stored);
    await atomicWriteJSON(manifestPath(chatId), manifest);
    return stored;
  });
}

/**
 * Merge a patch into an existing block (e.g. add transcript / vision caption).
 * Returns null when the digest is already gone — a transcript that lands after
 * `/done` compiled has nowhere to go, and the caller needs to know that.
 */
export function updateBlock(chatId, seq, patch) {
  return withChatLock(chatId, async () => {
    const manifest = await loadPending(chatId);
    if (!manifest) return null;
    const b = manifest.blocks.find((x) => x.seq === seq);
    if (!b) return null;
    Object.assign(b, patch);
    await atomicWriteJSON(manifestPath(chatId), manifest);
    return b;
  });
}

/** Save a staged attachment into the pending dir. Returns its absolute path. */
export async function saveAttachment(chatId, filename, buffer) {
  await fs.mkdir(chatDir(chatId), { recursive: true });
  const path = join(chatDir(chatId), filename);
  const tmp = `${path}.tmp`;
  await fs.writeFile(tmp, buffer);
  await fs.rename(tmp, path);
  return path;
}

export function pendingAttachmentPath(chatId, filename) {
  return join(chatDir(chatId), filename);
}

async function clearPendingUnlocked(chatId) {
  await fs.rm(chatDir(chatId), { recursive: true, force: true });
}

/** Remove the pending digest entirely (after a successful compile). */
export function clearPending(chatId) {
  return withChatLock(chatId, () => clearPendingUnlocked(chatId));
}

/**
 * Run `fn(manifest, { clear })` with the chat's manifest lock held for the WHOLE
 * call, so a read → decide → clear sequence is atomic against `updateBlock`.
 *
 * Finalize needs this. Reading the manifest and clearing it under separate locks
 * leaves a window in which a straggler transcription's `updateBlock` succeeds -
 * telling the user the words were saved - while the note being written was
 * compiled from the copy read before that write, and the `clearPending` that
 * follows throws the transcript away. Under this lock the straggler either lands
 * BEFORE finalize reads (so the transcript is in the note) or AFTER the clear (so
 * `updateBlock` returns null and the user is told to run `retranscribe`).
 *
 * `clear` is the unlocked remove: calling the exported `clearPending` from inside
 * `fn` would deadlock on the lock `fn` already holds.
 */
export function withPendingDigest(chatId, fn) {
  return withChatLock(chatId, async () => {
    const manifest = await loadPending(chatId);
    return fn(manifest, { clear: () => clearPendingUnlocked(chatId) });
  });
}

/** List chat ids that currently have a pending digest (for startup logging). */
export async function listPendingChats() {
  try {
    const entries = await fs.readdir(PENDING_DIR, { withFileTypes: true });
    const out = [];
    for (const e of entries) {
      if (e.isDirectory() && (await hasPending(e.name))) out.push(e.name);
    }
    return out;
  } catch {
    return [];
  }
}
