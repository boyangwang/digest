// Offline end-to-end of the compile+write path: no API keys → LLM/STT/vision fall back,
// but the full pending→attachments-move→compile→write→clear flow runs for real.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, existsSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const base = mkdtempSync(join(tmpdir(), "digest-final-"));
process.env.DIGEST_DATA_DIR = join(base, "data");
process.env.DIGEST_OUT_DIR = join(base, "vault");
process.env.TENCENT_TOKENHUB_API_KEY = ""; // force LLM fallback
process.env.ELEVENLABS_API_KEY = ""; // STT returns null

const store = await import("../src/store.js");
const { finalizeDigest, buildLLMInput } = await import("../src/finalize.js");
const { GENERATOR_KEY, GENERATOR_STAMP } = await import("../src/provenance.js");

test("buildLLMInput orders + labels substance", () => {
  const txt = buildLLMInput([
    { type: "text", text: "hello" },
    { type: "voice", transcript: "spoken" },
    { type: "image", visionCaption: "a cat" },
    { type: "file", attachment: "r.pdf", userCaption: "the report" },
  ]);
  assert.match(txt, /hello[\s\S]*\[spoken\] spoken[\s\S]*\[image\] a cat[\s\S]*\[file: r\.pdf\] the report/);
});

test("finalize with nothing pending returns null", async () => {
  assert.equal(await finalizeDigest(42), null);
});

test("full offline finalize writes a note + moves attachments + clears pending", async () => {
  const CHAT = 111;
  await store.startPending(CHAT, "2025-09-05T03:53:52Z");
  await store.appendBlock(CHAT, { type: "text", text: "opening line 开场白" });

  const v = await store.appendBlock(CHAT, { type: "voice" });
  await store.saveAttachment(CHAT, "20250905-115400-1-voice.ogg", Buffer.from("audio"));
  await store.updateBlock(CHAT, v.seq, { attachment: "20250905-115400-1-voice.ogg" });

  const img = await store.appendBlock(CHAT, { type: "image", userCaption: "at the park" });
  await store.saveAttachment(CHAT, "20250905-115500-2-img.jpg", Buffer.from("jpegbytes"));
  await store.updateBlock(CHAT, img.seq, { attachment: "20250905-115500-2-img.jpg" });

  const result = await finalizeDigest(CHAT);
  assert.ok(result && result.filename.endsWith(".md"));

  // note written to the vault
  const notePath = join(process.env.DIGEST_OUT_DIR, result.filename);
  assert.ok(existsSync(notePath));
  const md = readFileSync(notePath, "utf8");
  assert.match(md, /CREATEDAT: 2025-09-05 11:53:52/);
  assert.match(md, /opening line 开场白/);
  assert.match(md, /> \[Transcription unavailable\]/); // STT fell back
  assert.match(md, /at the park/);
  assert.match(md, /!\[\[Heresy-Anthology\/digest\/ATTACHMENTS\/20250905-115400-1-voice\.ogg\]\]/);

  // attachments moved into the vault ATTACHMENTS dir
  const attDir = join(process.env.DIGEST_OUT_DIR, "ATTACHMENTS");
  const files = readdirSync(attDir);
  assert.ok(files.includes("20250905-115400-1-voice.ogg"));
  assert.ok(files.includes("20250905-115500-2-img.jpg"));

  // pending cleared
  assert.equal(await store.hasPending(CHAT), false);
});

test("a note the bot creates carries the provenance stamp — asserted on the written FILE", async () => {
  // If this stops being written, every future note becomes untouchable by the
  // recovery tool, silently. Assert on disk so it cannot regress unnoticed.
  const CHAT = 77001;
  await store.startPending(CHAT, "2025-09-05T03:53:52Z");
  await store.appendBlock(CHAT, { type: "text", text: "something worth a note 值得记一笔" });

  const result = await finalizeDigest(CHAT);
  const md = readFileSync(join(process.env.DIGEST_OUT_DIR, result.filename), "utf8");
  const frontmatter = md.split("\n---\n")[0].replace(/^---\n/, "");

  assert.match(md, /^---\n/, "the note has frontmatter");
  assert.equal(
    frontmatter.split("\n").find((l) => l.startsWith(GENERATOR_KEY)),
    `${GENERATOR_KEY}: ${GENERATOR_STAMP}`
  );
});
