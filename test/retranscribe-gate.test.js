// The eligibility gate on the recovery script.
//
// A note is ours to edit ONLY if it carries the GENERATOR生成器 provenance stamp AND
// a failed-transcript marker. The digest folder is mostly hand-written and correctly
// carries no frontmatter; provenance is recorded at creation, never inferred from a
// note's contents. Assertions here are on the FULL FILE BYTES, not on a parsed view,
// because "we only rewrote the frontmatter" is exactly the bug.
//
// No live vendor and no live LLM: both are injected.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parse as yamlParse } from "yaml";
import {
  runRecovery,
  checkNotes,
  classifyNote,
  rewriteFrontmatter,
  REFUSAL,
} from "../scripts/retranscribe.mjs";
import { GENERATOR_KEY, GENERATOR_STAMP } from "../src/provenance.js";

const P = "Heresy-Anthology/digest/ATTACHMENTS";
const RECOVERED = "恢复出来的合成文本。Synthetic recovered text.";

const embed = (n) => `![[${P}/${n}]]`;

/** Stamped by the bot AND carrying a marker — the only eligible shape. */
const eligibleNote = (attachment) => `---
CREATEDAT: 2026-01-02 09:10:11
TITLE标题: 空白条目 An empty entry
${GENERATOR_KEY}: ${GENERATOR_STAMP}
Category分类: Test测试
Themes主题: 占位Placeholder
---

**01-02 09:10** opening line 开场白

**01-02 09:11** ${embed(attachment)}
> [Transcription unavailable (exhausted, 6 attempts)] - audio saved; retry: npm run retranscribe -- --all
`;

/**
 * Bot-SHAPED and marked, but written before the stamp existed. Looks exactly like a
 * note we made; is not provably one. Untouchable.
 */
const unstampedMarked = (attachment) => `---
CREATEDAT: 2026-01-02 09:10:11
TITLE标题: 空白条目 An empty entry
Themes主题: 占位Placeholder
---

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

/** Stamped, but no marker: nothing to recover, so nothing to rewrite. */
const botNoMarker = `---
CREATEDAT: 2026-03-04 05:06:07
TITLE标题: 好标题 A good title
${GENERATOR_KEY}: ${GENERATOR_STAMP}
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

const seenFirst = { done: false };

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

test("classifyNote: eligible needs BOTH the provenance stamp and the marker", () => {
  assert.equal(classifyNote(eligibleNote("v.ogg")).eligible, true);
  assert.equal(classifyNote(botNoMarker).reason, REFUSAL.NO_MARKER, "stamped, but nothing to do");
  assert.equal(classifyNote(unstampedMarked("v.ogg")).reason, REFUSAL.NO_STAMP, "marked, but not ours");
  assert.equal(classifyNote(legacySchema).reason, REFUSAL.NO_STAMP, "a legacy schema is NOT an implicit stamp");
  assert.equal(classifyNote(handWritten(1)).reason, REFUSAL.NO_FRONTMATTER);
  assert.equal(classifyNote(handWrittenMarked("v.ogg")).reason, REFUSAL.NO_FRONTMATTER);
});

test("the stamp is matched on the generator segment, never as a substring", () => {
  const impostor = (v) => eligibleNote("v.ogg").replace(`${GENERATOR_KEY}: ${GENERATOR_STAMP}`, `${GENERATOR_KEY}: ${v}`);
  assert.equal(classifyNote(impostor("digest/1")).eligible, true);
  assert.equal(classifyNote(impostor("digest/2")).eligible, true, "a version bump stays ours");
  assert.equal(classifyNote(impostor("not-digest/1")).reason, REFUSAL.NO_STAMP);
  assert.equal(classifyNote(impostor("mydigest/1")).reason, REFUSAL.NO_STAMP);
  assert.equal(classifyNote(impostor("a digest of the day")).reason, REFUSAL.NO_STAMP);
});

test("REGRESSION GUARD — an UNSTAMPED note is never edited, even carrying the marker", async () => {
  // The captain's instruction, encoded. If the provenance check is ever removed this
  // test must fail loudly: the blast radius is his personal journal, and a note the
  // bot did not create is not ours to rewrite no matter how bot-shaped it looks.
  const dir = fixture({ "20260102-0910 unstamped.md": unstampedMarked("a-voice.ogg") });
  const before = bytes(dir);

  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before, "--all must not touch an unstamped note");

  await runRecovery({ targets: ["a-voice.ogg"], digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.deepEqual(bytes(dir), before, "naming it explicitly must not bypass the stamp check");

  // And no opt-in flag may unlock it either — there is deliberately no override.
  await runRecovery({
    all: true,
    replaceProperties: true,
    rename: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
  });
  assert.deepEqual(bytes(dir), before, "no flag makes an unstamped note eligible");
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

test("a stamped note keeps its stamp through a recovery, and through a full rebuild", async () => {
  for (const replaceProperties of [false, true]) {
    const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
    await runRecovery({ all: true, replaceProperties, digestDir: dir, attachmentsDir: dir, ...stubs() });
    const fm = yamlParse(readFileSync(join(dir, "20260102-0910 empty.md"), "utf8").split("\n---\n")[0].replace(/^---\n/, ""));
    assert.equal(fm[GENERATOR_KEY], GENERATOR_STAMP, `stamp must survive (replaceProperties=${replaceProperties})`);
  }
});

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
  // Built at the REAL ratio: overwhelmingly unstamped hand-written notes, a stamped
  // minority, and the near-misses that must still be refused.
  const files = { "20260102-0910 eligible.md": eligibleNote("a-voice.ogg") };
  for (let i = 0; i < 20; i++) files[`2026010${i % 9}-10${String(i).padStart(2, "0")} hand-${i}.md`] = handWritten(i);
  files["20260201-0101 hand-marked.md"] = handWrittenMarked("b-voice.ogg");
  files["20260202-0202 unstamped-marked.md"] = unstampedMarked("c-voice.ogg");
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

test("a metadata failure leaves the note BYTE-IDENTICAL, marker intact, still eligible", async () => {
  // The trap this replaces: committing the transcript first consumed the marker, so a
  // single transient title-model blip stranded the note as permanently ineligible.
  // The transcript is deferred work, not lost data — the audio is still in the vault.
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  const before = bytes(dir);
  const errors = [];
  const result = await runRecovery({
    all: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    titleImpl: async () => ({ title: { zh: "first line", en: "" }, tags: [], fallback: true }),
    out: { log() {}, error: (m) => errors.push(String(m)) },
  });

  assert.deepEqual(bytes(dir), before, "nothing is written when the metadata half fails");
  assert.equal(result.recovered, 0);
  assert.equal(result.failed, 1);
  assert.ok(errors.some((e) => e.includes("still eligible")));

  // Still eligible, so the next run can finish the job.
  const md = readFileSync(join(dir, "20260102-0910 empty.md"), "utf8");
  assert.equal(classifyNote(md).eligible, true);
  await runRecovery({ all: true, digestDir: dir, attachmentsDir: dir, ...stubs() });
  assert.match(readFileSync(join(dir, "20260102-0910 empty.md"), "utf8"), new RegExp(`> ${RECOVERED}`));
});

test("one note throwing does not abort the run — the rest still recover", async () => {
  const dir = fixture({
    "20260101-0101 broken.md": eligibleNote("a-voice.ogg"),
    "20260202-0202 fine.md": eligibleNote("b-voice.ogg"),
  });
  const errors = [];
  const result = await runRecovery({
    all: true,
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    titleImpl: async (input) => {
      if (input.includes("恢复出来的合成文本")) {
        // Both notes reach the title model; blow up only on the first one processed.
        if (!errors.length && !seenFirst.done) {
          seenFirst.done = true;
          throw new Error("EACCES: vault file is locked");
        }
      }
      return {
        title: { zh: "恢复后的标题", en: "Recovered title" },
        tags: [{ key: "Category分类", value: "Recovered恢复" }],
        fallback: false,
      };
    },
    out: { log() {}, error: (m) => errors.push(String(m)) },
  });

  assert.equal(result.failed, 1, "the throwing note is counted, not fatal");
  assert.equal(result.recovered, 1, "the other note still recovered");
  assert.ok(errors.some((e) => e.includes("EACCES")));
  assert.match(readFileSync(join(dir, "20260202-0202 fine.md"), "utf8"), new RegExp(`> ${RECOVERED}`));
});

test("a missing digest folder is reported, not thrown", async () => {
  const errors = [];
  const result = await runRecovery({
    all: true,
    digestDir: join(tmpdir(), "digest-gate-does-not-exist-12345"),
    attachmentsDir: tmpdir(),
    ...stubs(),
    out: { log() {}, error: (m) => errors.push(String(m)) },
  });
  assert.deepEqual(result, { recovered: 0, previewed: 0, blocked: 0, failed: 0 });
  assert.ok(errors.some((e) => e.includes("cannot read")));
});

test("naming an attachment that is eligible but unmarked says so instead of going quiet", async () => {
  const dir = fixture({ "20260304-0506 ok.md": botNoMarker });
  const logs = [];
  await runRecovery({
    targets: ["ok-voice.ogg"],
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    out: { log: (m) => logs.push(String(m)), error: (m) => logs.push(String(m)) },
  });
  assert.ok(
    logs.some((m) => m.includes("no failure marker") || m.includes("nothing here needs recovering")),
    `the named-target path must always explain itself, got ${JSON.stringify(logs)}`
  );
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

// ---------------------------------------------------------------------------
// Reporting: the refusals that need a human must be visible BY NAME
// ---------------------------------------------------------------------------

test("--check names marked-but-refused notes and does NOT claim un-marked ones carry a marker", async () => {
  // The real folder is ~494 frontmatter-less notes; a per-note line for that whole
  // bucket would drown the census, and claiming each one "carries a marker" is false.
  const files = { "20260102-0910 blocked.md": unstampedMarked("a-voice.ogg") };
  for (let i = 0; i < 15; i++) files[`2026010${i % 9}-11${String(i).padStart(2, "0")} hand-${i}.md`] = handWritten(i);

  const dir = fixture(files);
  const lines = [];
  const { eligible, refused, blocked } = await checkNotes({
    digestDir: dir,
    out: { log: (m) => lines.push(String(m)), error: (m) => lines.push(String(m)) },
  });
  const text = lines.join("\n");

  assert.equal(eligible.length, 0);
  assert.equal(refused.length, 16);
  assert.deepEqual(blocked.map((n) => n.marked).flat(), ["a-voice.ogg"]);

  // The one actionable refusal is named…
  assert.match(text, /20260102-0910 blocked\.md/);
  assert.match(text, /NEED recovery but are refused/);
  // …and the un-marked bulk is counted, never named, never mislabelled.
  assert.ok(!text.includes("hand-0.md"), "the un-marked bulk must not be listed per note");
  assert.ok(
    !/hand-\d+\.md carries a marker/.test(text),
    "a note with no marker must never be described as carrying one"
  );
});

test("--all names a marked-but-unstamped note instead of skipping it in silence", async () => {
  // "This note needs recovery and I may not touch it" is the only refusal the captain
  // can act on, so it must never be silent.
  const dir = fixture({
    "20260102-0910 blocked.md": unstampedMarked("a-voice.ogg"),
    "20260304-0506 ok.md": botNoMarker,
    "20260101-0101 hand.md": handWritten("one"),
  });
  const errors = [];
  const result = await runRecovery({
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
    out: { log() {}, error: (m) => errors.push(String(m)) },
  });
  const text = errors.join("\n");

  assert.equal(result.blocked, 1, "exactly the marked-but-unstamped note is announced");
  assert.equal(result.failed, 0, "a blocked note is NOT a failure — see the exit-code contract");
  assert.match(text, /20260102-0910 blocked\.md/);
  assert.match(text, /DOES carry a failure marker/);
  assert.ok(!text.includes("20260304-0506 ok.md"), "a stamped note with nothing to do stays quiet");
  assert.ok(!text.includes("20260101-0101 hand.md"), "the hand-written bulk stays quiet");
});

test("--dry-run reports what it previewed instead of claiming there was nothing to do", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  const before = bytes(dir);
  const result = await runRecovery({ dryRun: true, digestDir: dir, attachmentsDir: dir, ...stubs() });

  assert.deepEqual(bytes(dir), before, "a dry run still writes nothing");
  assert.equal(result.recovered, 0, "nothing was committed");
  assert.equal(result.previewed, 1, "but the preview is counted, so the summary cannot contradict it");
});

test("GUARD — rewriteFrontmatter can never MANUFACTURE a provenance stamp", async () => {
  // The stamp is what makes a note ours to edit and directive 3 gives it no override,
  // so this helper must be structurally incapable of adding one to a note that lacks
  // it — not merely unreachable from the gated call path.
  const unstamped = unstampedMarked("a-voice.ogg");
  for (const replaceAll of [false, true]) {
    const { markdown } = rewriteFrontmatter(unstamped, "新标题 New title", [{ key: "Category分类", value: "X" }], {
      replaceAll,
    });
    assert.ok(!markdown.includes(GENERATOR_KEY), `no stamp may appear (replaceAll=${replaceAll})`);
    assert.equal(classifyNote(markdown).reason, REFUSAL.NO_STAMP, "and the note stays ineligible");
  }
});

test("a blocked note is reported but is NOT counted as a failure", async () => {
  // A note that needs recovery and is not ours to touch can never become eligible,
  // so treating it as a failure would make the exit code permanently non-zero — and
  // a permanently-failing exit code is how automation learns to ignore a tool.
  const dir = fixture({
    "20260102-0910 blocked.md": unstampedMarked("a-voice.ogg"),
    "20260202-0202 fine.md": eligibleNote("b-voice.ogg"),
  });
  const result = await runRecovery({ digestDir: dir, attachmentsDir: dir, ...stubs() });

  assert.equal(result.recovered, 1, "the eligible note still recovered");
  assert.equal(result.blocked, 1, "the blocked note is still counted and named");
  assert.equal(result.failed, 0, "but it is not a failure");
});

test("naming an attachment no note embeds IS a failure — you asked for something absent", async () => {
  const dir = fixture({ "20260102-0910 empty.md": eligibleNote("a-voice.ogg") });
  const result = await runRecovery({
    targets: ["not-in-any-note.ogg"],
    digestDir: dir,
    attachmentsDir: dir,
    ...stubs(),
  });
  assert.equal(result.failed, 1);
  assert.equal(result.blocked, 0);
});
