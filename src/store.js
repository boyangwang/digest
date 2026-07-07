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

/** Create a fresh pending digest for a chat. Returns the new manifest. */
export async function startPending(chatId, startedAt = new Date().toISOString()) {
  await fs.mkdir(chatDir(chatId), { recursive: true });
  const manifest = { chatId, startedAt, seq: 0, blocks: [] };
  await atomicWriteJSON(manifestPath(chatId), manifest);
  return manifest;
}

/** Ensure a pending digest exists (auto-start on first input). Returns the manifest. */
export async function ensurePending(chatId) {
  return (await loadPending(chatId)) || (await startPending(chatId));
}

/**
 * Append a block. Assigns { seq, ts } and persists atomically BEFORE returning,
 * so the caller can ACK knowing the input is durable. Returns the stored block.
 */
export async function appendBlock(chatId, block) {
  const manifest = await ensurePending(chatId);
  const stored = { seq: manifest.seq, ts: new Date().toISOString(), ...block };
  manifest.seq += 1;
  manifest.blocks.push(stored);
  await atomicWriteJSON(manifestPath(chatId), manifest);
  return stored;
}

/** Merge a patch into an existing block (e.g. add transcript / vision caption). */
export async function updateBlock(chatId, seq, patch) {
  const manifest = await loadPending(chatId);
  if (!manifest) return null;
  const b = manifest.blocks.find((x) => x.seq === seq);
  if (!b) return null;
  Object.assign(b, patch);
  await atomicWriteJSON(manifestPath(chatId), manifest);
  return b;
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

/** Remove the pending digest entirely (after a successful compile). */
export async function clearPending(chatId) {
  await fs.rm(chatDir(chatId), { recursive: true, force: true });
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
