// replaceMarker() edits Boyang's real, live Obsidian notes. The bar is: it changes
// the failure marker and NOTHING else — frontmatter, title, other blocks, other
// voice blocks that transcribed fine, and the trailing newline all stay byte-exact.
import { test } from "node:test";
import assert from "node:assert/strict";
import { parse as yamlParse } from "yaml";
import {
  replaceMarker,
  parseNoteBlocks,
  rewriteFrontmatter,
  splitNote,
} from "../scripts/retranscribe.mjs";
import { buildLLMInput } from "../src/finalize.js";

const NOTE = `---
CREATEDAT: 2026-01-02 09:10:11
TITLE标题: 测试笔记 A test note
Category分类: Test测试
Themes主题: 占位Placeholder
---

**01-02 09:10** first text block

second text block

**01-02 09:11** ![[Heresy-Anthology/digest/ATTACHMENTS/a-voice.ogg]]
> [Transcription unavailable]

**01-02 09:12** ![[Heresy-Anthology/digest/ATTACHMENTS/b-voice.ogg]]
> 这条已经转写成功了。
`;

test("replaces only the marker under the named embed", () => {
  const { markdown, replaced } = replaceMarker(NOTE, "a-voice.ogg", "恢复出来的中文文本。");
  assert.equal(replaced, 1);
  assert.ok(!markdown.includes("[Transcription unavailable]"));
  assert.match(markdown, /a-voice\.ogg\]\]\n> 恢复出来的中文文本。\n/);

  // everything else byte-identical
  const MARKER_LINE = 12; // 0-based index of `> [Transcription unavailable]`
  const before = NOTE.split("\n");
  const after = markdown.split("\n");
  assert.equal(before.length, after.length);
  before.forEach((line, i) => {
    if (i === MARKER_LINE) return;
    assert.equal(after[i], line, `line ${i + 1} must not change`);
  });
  assert.equal(before[MARKER_LINE], "> [Transcription unavailable]");
  assert.ok(markdown.endsWith("\n"), "trailing newline preserved");
});

test("leaves a sibling voice block that transcribed fine completely alone", () => {
  const { markdown } = replaceMarker(NOTE, "a-voice.ogg", "recovered");
  assert.match(markdown, /b-voice\.ogg\]\]\n> 这条已经转写成功了。/);
});

test("does nothing when the named embed has no marker under it", () => {
  const { markdown, replaced } = replaceMarker(NOTE, "b-voice.ogg", "should not appear");
  assert.equal(replaced, 0);
  assert.equal(markdown, NOTE);
});

test("does nothing for an attachment the note does not embed", () => {
  const { markdown, replaced } = replaceMarker(NOTE, "not-here.ogg", "nope");
  assert.equal(replaced, 0);
  assert.equal(markdown, NOTE);
});

test("recognises the richer marker compile.js writes now, and multi-line transcripts", () => {
  const note =
    "**08-14 15:20** ![[Heresy-Anthology/digest/ATTACHMENTS/v.ogg]]\n" +
    '> [Transcription unavailable (exhausted, 6 attempts)] - audio saved; retry: npm run retranscribe -- "v.ogg"\n';
  const { markdown, replaced } = replaceMarker(note, "v.ogg", "line one\n\nline two");
  assert.equal(replaced, 1);
  assert.equal(
    markdown,
    "**08-14 15:20** ![[Heresy-Anthology/digest/ATTACHMENTS/v.ogg]]\n> line one\n>\n> line two\n"
  );
});

test("a human-added blockquote line under the marker is NOT swallowed", () => {
  // These are live, hand-editable Obsidian notes; only the marker line is ours.
  const note =
    "**01-02 09:11** ![[Heresy-Anthology/digest/ATTACHMENTS/a-voice.ogg]]\n" +
    "> [Transcription unavailable (exhausted, 6 attempts)]\n" +
    "> my own note about this recording\n";
  const { markdown, replaced } = replaceMarker(note, "a-voice.ogg", "recovered words");
  assert.equal(replaced, 1);
  assert.match(markdown, /> recovered words\n> my own note about this recording/);
  assert.ok(!markdown.includes("[Transcription unavailable"));
});

// ---------------------------------------------------------------------------
// Reading a compiled note back, so title/tags can be regenerated from it
// ---------------------------------------------------------------------------

test("parseNoteBlocks reads the rendered body back into block-equivalents", () => {
  const body = splitNote(NOTE).body;
  const blocks = parseNoteBlocks(body);
  assert.deepEqual(
    blocks.map((b) => b.type),
    ["text", "voice", "voice"]
  );
  assert.equal(blocks[0].text, "first text block\n\nsecond text block");
  assert.equal(blocks[1].transcript, null, "a block still carrying the marker has no transcript");
  assert.equal(blocks[2].transcript, "这条已经转写成功了。");
});

test("parseNoteBlocks distinguishes image, file and multi-line voice blocks", () => {
  const body =
    "**01-02 09:10** ![[Heresy-Anthology/digest/ATTACHMENTS/x-img.jpg]]\n" +
    "at the park 在公园\n\n" +
    "**01-02 09:11** ![[Heresy-Anthology/digest/ATTACHMENTS/report.pdf]]\n\n" +
    "**01-02 09:12** ![[Heresy-Anthology/digest/ATTACHMENTS/v.ogg]]\n" +
    "> line one\n>\n> line two\n";
  const blocks = parseNoteBlocks(body);
  assert.deepEqual(blocks.map((b) => b.type), ["image", "file", "voice"]);
  assert.equal(blocks[0].userCaption, "at the park 在公园");
  assert.equal(blocks[1].attachment, "report.pdf");
  assert.equal(blocks[2].transcript, "line one\n\nline two");
});

test("the reconstructed LLM input feeds the SAME builder finalize uses", () => {
  const recovered = replaceMarker(NOTE, "a-voice.ogg", "恢复出来的文本").markdown;
  const input = buildLLMInput(parseNoteBlocks(splitNote(recovered).body));
  // The recovered words are now in the title model's input — which is the whole
  // point: they were not there when the title was first generated.
  assert.match(input, /\[spoken\] 恢复出来的文本/);
  assert.match(input, /\[spoken\] 这条已经转写成功了。/);
  assert.match(input, /first text block/);
});

// ---------------------------------------------------------------------------
// Rewriting the frontmatter
// ---------------------------------------------------------------------------

test("rewriteFrontmatter replaces title + tags but keeps CREATEDAT verbatim", () => {
  const { markdown, dropped } = rewriteFrontmatter(NOTE, "新标题 A new title", [
    { key: "Category分类", value: "Life生活" },
    { key: "Themes主题", value: "Recovery恢复" },
  ]);
  const fm = yamlParse(splitNote(markdown).frontmatterRaw);

  assert.equal(fm.CREATEDAT, "2026-01-02 09:10:11", "the note's identity timestamp must never be regenerated");
  assert.equal(fm["TITLE标题"], "新标题 A new title");
  assert.equal(fm["Themes主题"], "Recovery恢复");
  assert.deepEqual(dropped, [], "every old property was superseded by a new one");

  // The body is untouched, byte for byte.
  assert.equal(splitNote(markdown).body, splitNote(NOTE).body);
});

test("rewriteFrontmatter reports properties the new tag set does not cover", () => {
  const { markdown, dropped } = rewriteFrontmatter(NOTE, "T", [{ key: "Category分类", value: "X" }]);
  assert.deepEqual(dropped, ["Themes主题"]);
  assert.ok(!markdown.includes("Themes主题"));
});
