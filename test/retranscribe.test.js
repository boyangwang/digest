// replaceMarker() edits Boyang's real, live Obsidian notes. The bar is: it changes
// the failure marker and NOTHING else — frontmatter, title, other blocks, other
// voice blocks that transcribed fine, and the trailing newline all stay byte-exact.
import { test } from "node:test";
import assert from "node:assert/strict";
import { replaceMarker } from "../scripts/retranscribe.mjs";

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
