// The eligibility gate on the recovery script.
//
// The digest folder is NOT a folder of bot-produced notes. Most of it is
// hand-written and correctly carries no frontmatter. A recovery run must therefore
// touch ONLY notes carrying this bot's failed-transcript marker, and must change
// ZERO bytes of everything else — assertions here are on the FULL FILE BYTES, not
// on a parsed view, because "we only rewrote the frontmatter" is exactly the bug.
//
// No live vendor and no live LLM: both are injected.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse as yamlParse } from "yaml";
import { runRecovery, checkNotes, classifyNote, REFUSAL } from "../scripts/retranscribe.mjs";

const P = "Heresy-Anthology/digest/ATTACHMENTS";
const RECOVERED = "恢复出来的合成文本。Synthetic recovered text.";

const embed = (n) => `![[${P}/${n}]]`;

/** A note the bot produced whose voice block failed — the only eligible shape. */
const eligibleNote = (attachment) => `---
CREATEDAT: 2026-01-02 09:10:11
TITLE标题: 空白条目 An empty entry
Category分类: Test测试
Themes主题: 占位Placeholder
---

**01-02 09:10** opening line 开场白

**01-02 09:11** ${embed(attachment)}
> [Transcription unavailable (exhausted, 6 attempts)] - audio saved; retry: npm run retranscribe -- --all
`;

/** Hand-written: no frontmatter at all. 494 of the captain's 577 notes look like this. */
const handWritten = (n) => `# 手写笔记 ${n}

Just prose. No properties, deliberately.
`;

/** Hand-written AND carrying a marker — still off-limits: we never create frontmatter. */
const handWrittenMarked = (attachment) => `# 手写但带标记

**01-02 09:11** ${embed(attachment)}
> [Transcription unavailable]
`;

/** Bot-shaped, no marker: nothing to recover, so nothing to rewrite. */
const botNoMarker = `---
CREATEDAT: 2026-03-04 05:06:07
TITLE标题: 好标题 A good title
Themes主题: Fine没问题
---

**03-04 05:06** ${embed("ok-voice.ogg")}
> 这条转写成功了。
`;

/** The older Chinese schema. Must not be migrated as a side effect. */
const legacySchema = `---
创建时间: 2025-05-06 07:08:09
分类: 生活
主题: 旧模式
---

**05-06 07:08** 旧笔记正文。
`;

function fixture(files) {
  const dir = mkdtempSync(join(tmpdir(), "digest-gate-"));
  for (const [name, body] of Object.entries(files)) writeFileSync(join(dir, name), body, "utf8");
  return dir;
}

const bytes = (dir) =>
  Object.fromEntries(readdirSync(dir).map((f) => [f, readFileSync(join(dir, f), "utf8")]));

const stubs = () => ({
  transcribeImpl: async () => ({
    ok: true,
    text: RECOVERED,
    language: "zho",
    provider: "elevenlabs",
    attempts: 1,
  }),
  titleImpl: async () => ({
    title: { zh: "恢复后的标题", en: "Recovered title" },
    tags: [{ key: "Category分类", value: "Recovered恢复" }],
    fallback: false,
  }),
  out: { log() {}, error() {} },
});

// ---------------------------------------------------------------------------
// The gate itself
// ---------------------------------------------------------------------------

test("classifyNote: only a marked note WITH frontmatter is eligible", () => {
  assert.equal(classifyNote(eligibleNote("v.ogg")).eligible, true);
  assert.equal(classifyNote(botNoMarker).reason, REFUSAL.NO_MARKER);
  assert.equal(classifyNote(legacySchema).reason, REFUSAL.NO_MARKER);
  assert.equal(classifyNote(handWritten(1)).reason, REFUSAL.NO_MARKER);
  assert.equal(classifyNote(handWrittenMarked("v.ogg")).reason, REFUSAL.NO_FRONTMATTER);
});

test("the bare legacy marker counts as eligible too — older notes carry it", () => {
  const bare = eligibleNote("v.ogg").replace(/> \[Transcription unavailable.*/, "> [Transcription unavailable]");
  const verdict = classifyNote(bare);
  assert.equal(verdict.eligible, true);
  assert.deepEqual(verdict.marked, ["v.ogg"]);
});

// ---------------------------------------------------------------------------
// a-d: each refused shape comes back byte-identical
// ---------------------------------------------------------------------------

test("a: a note with NO frontmatter is byte-identical, marker or not", async () => {
  const dir = fixture({
    "20260101-0101 hand.md": handWritten("one"),
    "20260102-0202 hand-marked.md": handWrittenMarked("a-voice.ogg"),
  });
  const before = bytes(dir);
  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before, "no frontmatter is ever created, and no byte is touched");

  // …and naming the attachment explicitly must not bypass the gate either.
  await runRecovery({ targets: ["a-voice.ogg"], digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before);
});

test("b: a bot-shaped note WITHOUT the marker is byte-identical", async () => {
  const dir = fixture({ "20260304-0506 ok.md": botNoMarker });
  const before = bytes(dir);
  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before, "stale-looking metadata is not licence to rewrite it");

  await runRecovery({ targets: ["ok-voice.ogg"], digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before);
});

test("c: a legacy-schema note keeps 创建时间 and never gains CREATEDAT", async () => {
  const dir = fixture({ "20250506-0708 legacy.md": legacySchema });
  const before = bytes(dir);
  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before);

  const fm = yamlParse(readFileSync(join(dir, "20250506-0708 legacy.md"), "utf8").split("---")[1]);
  assert.equal(fm["创建时间"], "2025-05-06 07:08:09");
  assert.equal(fm.CREATEDAT, undefined, "no silent migration to the current schema");
});

test("d: an eligible note gets a new title and tags, keeps its other keys and CREATEDAT", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });

  const md = readFileSync(join(dir, "20260102-0910 empty.md"), "utf8");
  const fm = yamlParse(md.split("\n---\n")[0].replace(/^---\n/, ""));

  assert.match(md, new RegExp(`> ${RECOVERED}`), "the recovered words are in the body");
  assert.ok(!md.includes("Transcription unavailable"));
  assert.equal(fm["TITLE标题"], "恢复后的标题 Recovered title", "regenerated from the complete text");
  assert.equal(fm["Category分类"], "Recovered恢复");
  assert.equal(fm["Themes主题"], "占位Placeholder", "a key the new tags do not mention survives");
  assert.equal(fm.CREATEDAT, "2026-01-02 09:10:11", "identity timestamp preserved verbatim");
  // The filename is deliberately NOT renamed by default: that breaks Obsidian links.
  assert.deepEqual(readdirSync(dir), ["20260102-0910 empty.md"]);
});

// ---------------------------------------------------------------------------
// e: the whole-folder run — the test that catches the false assumption
// ---------------------------------------------------------------------------

test("e: a whole-folder run modifies ONLY the eligible notes", async () => {
  const files = { "20260102-0910 eligible.md": eligibleNote("a-voice.ogg") };
  for (let i = 0; i < 12; i++) files[`2026010${i % 9}-100${i} hand-${i}.md`] = handWritten(i);
  files["20260201-0101 hand-marked.md"] = handWrittenMarked("b-voice.ogg");
  files["20260304-0506 ok.md"] = botNoMarker;
  files["20250506-0708 legacy.md"] = legacySchema;

  const dir = fixture(files);
  const before = bytes(dir);
  const result = await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  const after = bytes(dir);

  assert.equal(result.recovered, 1);
  const changed = Object.keys(before).filter((f) => before[f] !== after[f]);
  assert.deepEqual(changed, ["20260102-0910 eligible.md"], "exactly one note may change");
  assert.deepEqual(Object.keys(after).sort(), Object.keys(before).sort(), "no note created or deleted");
});

test("--dry-run writes nothing at all", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  const before = bytes(dir);
  const seen = [];
  await runRecovery({
    all: true,
    dryRun: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    out: { log: (m) => seen.push(String(m)), error: () => {} },
  });
  assert.deepEqual(bytes(dir), before);
  assert.ok(seen.some((m) => m.includes("恢复后的标题")), "it still shows the proposed title");
});

test("--check is free: it classifies the folder, calls nothing and writes nothing", async () => {
  const dir = fixture({
    "20260102-0910 eligible.md": eligibleNote("a-voice.ogg"),
    "20260101-0101 hand.md": handWritten("one"),
    "20260304-0506 ok.md": botNoMarker,
  });
  const before = bytes(dir);
  const { eligible, refused } = await checkNotes({ digestDir: dir, out: { log() {}, error() {} } });

  assert.equal(eligible.length, 1);
  assert.deepEqual(eligible[0].marked, ["a-voice.ogg"]);
  assert.equal(refused.length, 2);
  assert.deepEqual(bytes(dir), before);
});

test("the title model being unavailable keeps the transcript and leaves metadata stale", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  const errors = [];
  await runRecovery({
    all: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    titleImpl: async () => ({ title: { zh: "first line", en: "" }, tags: [], fallback: true }),
    out: { log() {}, error: (m) => errors.push(String(m)) },
  });

  const md = readFileSync(join(dir, "20260102-0910 empty.md"), "utf8");
  assert.match(md, new RegExp(`> ${RECOVERED}`), "the transcript is never rolled back");
  assert.match(md, /TITLE标题: 空白条目 An empty entry/, "the old title is kept, not replaced by a worse one");
  assert.ok(errors.some((e) => e.includes("STALE")));
});

test("a Chinese-only title with no tags is a REAL answer, not a fallback", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  await runRecovery({
    all: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    titleImpl: async () => ({ title: { zh: "只有中文的标题", en: "" }, tags: [], fallback: false }),
  });
  const md = readFileSync(join(dir, "20260102-0910 empty.md"), "utf8");
  assert.match(md, /TITLE标题: 只有中文的标题/, "shape must not be mistaken for the fallback");
});
