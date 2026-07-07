import { test } from "node:test";
import assert from "node:assert/strict";
import { parse as yamlParse } from "yaml";
import { compileNote, buildProperties } from "../src/compile.js";

const startDate = new Date("2025-09-05T03:53:52Z"); // 11:53:52 SGT

function fm(markdown) {
  const m = markdown.match(/^---\n([\s\S]*?)\n---\n/);
  return yamlParse(m[1]);
}

test("buildProperties: CREATEDAT + TITLE标题 first, then dynamic bilingual tags", () => {
  const y = buildProperties(
    "超我 Ubermensch",
    [
      { key: "Category分类", value: "Life生活" },
      { key: "People人物", value: "伊瑟Yise" },
    ],
    startDate
  );
  const parsed = yamlParse(y);
  assert.equal(parsed.CREATEDAT, "2025-09-05 11:53:52");
  assert.equal(parsed["TITLE标题"], "超我 Ubermensch");
  assert.equal(parsed["Category分类"], "Life生活");
  assert.equal(parsed["People人物"], "伊瑟Yise");
  // order: CREATEDAT then TITLE标题 first
  assert.deepEqual(Object.keys(parsed).slice(0, 2), ["CREATEDAT", "TITLE标题"]);
});

test("compileNote: preserves order + renders each type + filename", () => {
  const blocks = [
    { seq: 0, ts: "2025-09-05T03:53:00Z", type: "text", text: "first thought" },
    {
      seq: 1,
      ts: "2025-09-05T03:54:00Z",
      type: "voice",
      attachment: "20250905-115400-1-voice.ogg",
      transcript: "spoken words here",
    },
    {
      seq: 2,
      ts: "2025-09-05T03:55:00Z",
      type: "image",
      attachment: "20250905-115500-2-img.jpg",
      userCaption: "under the trees",
    },
    {
      seq: 3,
      ts: "2025-09-05T03:56:00Z",
      type: "file",
      attachment: "20250905-115600-3-report.pdf",
    },
  ];
  const { filename, markdown } = compileNote({
    blocks,
    title: { zh: "去公园的一天", en: "A day at the park" },
    tags: [{ key: "Places地点", value: "Park公园" }],
    startDate,
  });

  assert.equal(filename, "20250905-1153 去公园的一天 A day at the park.md");

  // order preserved
  const iText = markdown.indexOf("first thought");
  const iVoice = markdown.indexOf("spoken words here");
  const iImg = markdown.indexOf("under the trees");
  const iFile = markdown.indexOf("report.pdf");
  assert.ok(iText < iVoice && iVoice < iImg && iImg < iFile);

  // per-block timestamp headers (date included)
  assert.match(markdown, /20250905-1153\nfirst thought/);
  assert.match(markdown, /20250905-1154\n!\[\[Heresy-Anthology\/digest\/ATTACHMENTS\/20250905-115400-1-voice\.ogg\]\]/);
  // voice transcript as blockquote after the embed
  assert.match(markdown, /voice\.ogg\]\]\n> spoken words here/);
  // frontmatter valid + has properties
  const props = fm(markdown);
  assert.equal(props["TITLE标题"], "去公园的一天 A day at the park");
  assert.equal(props["Places地点"], "Park公园");
});

test("compileNote: missing transcript → unavailable marker", () => {
  const { markdown } = compileNote({
    blocks: [{ seq: 0, ts: "2025-09-05T03:53:00Z", type: "voice", attachment: "a.ogg" }],
    title: { zh: "x", en: "y" },
    tags: [],
    startDate,
  });
  assert.match(markdown, /> \[Transcription unavailable\]/);
});
