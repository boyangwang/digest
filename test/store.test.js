import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Point the store at a temp dir BEFORE importing it (config reads env at load).
process.env.DIGEST_DATA_DIR = mkdtempSync(join(tmpdir(), "digest-store-"));
const store = await import("../src/store.js");

const CHAT = 12345;

test("startPending / hasPending / clearPending lifecycle", async () => {
  assert.equal(await store.hasPending(CHAT), false);
  await store.startPending(CHAT, "2025-09-05T03:53:52Z");
  assert.equal(await store.hasPending(CHAT), true);
  const m = await store.loadPending(CHAT);
  assert.equal(m.startedAt, "2025-09-05T03:53:52Z");
  assert.equal(m.blocks.length, 0);
  await store.clearPending(CHAT);
  assert.equal(await store.hasPending(CHAT), false);
});

test("appendBlock assigns sequential seq + preserves order", async () => {
  const CHAT2 = 999;
  await store.startPending(CHAT2, "2025-09-05T03:53:52Z");
  const a = await store.appendBlock(CHAT2, { type: "text", text: "one" });
  const b = await store.appendBlock(CHAT2, { type: "text", text: "two" });
  const c = await store.appendBlock(CHAT2, { type: "voice" });
  assert.deepEqual([a.seq, b.seq, c.seq], [0, 1, 2]);
  const m = await store.loadPending(CHAT2);
  assert.deepEqual(m.blocks.map((x) => x.text || x.type), ["one", "two", "voice"]);
  assert.ok(a.ts && b.ts); // timestamps stamped at persist
  await store.clearPending(CHAT2);
});

test("ensurePending auto-starts on first input", async () => {
  const CHAT3 = 777;
  assert.equal(await store.hasPending(CHAT3), false);
  await store.appendBlock(CHAT3, { type: "text", text: "auto" });
  assert.equal(await store.hasPending(CHAT3), true);
  await store.clearPending(CHAT3);
});

test("updateBlock merges a patch (transcript)", async () => {
  const CHAT4 = 555;
  await store.appendBlock(CHAT4, { type: "voice" });
  await store.updateBlock(CHAT4, 0, { transcript: "hello" });
  const m = await store.loadPending(CHAT4);
  assert.equal(m.blocks[0].transcript, "hello");
  await store.clearPending(CHAT4);
});

test("saveAttachment persists a staged file", async () => {
  const CHAT5 = 333;
  await store.startPending(CHAT5, "2025-09-05T03:53:52Z");
  const p = await store.saveAttachment(CHAT5, "x.ogg", Buffer.from("audio"));
  const { readFileSync } = await import("node:fs");
  assert.equal(readFileSync(p, "utf8"), "audio");
  await store.clearPending(CHAT5);
});
