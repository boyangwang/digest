import { test } from "node:test";
import assert from "node:assert/strict";
import { parse as yamlParse } from "yaml";
import { compileNote, buildProperties } from "../src/compile.js";

const startDate = new Date("2025-09-05T03:53:52Z"); // 11:53:52 SGT

function fm(markdown) {
  const m = markdown.match(/^---\n([\s\S]*?)\n---\n/);
  return yamlParse(m[1]);
}

test("buildProperties: CREATEDAT first; TITLE标题 only when includeFullTitle; then tags", () => {
  const withTitle = yamlParse(
    buildProperties("超我 Ubermensch", [{ key: "Category分类", value: "Life生活" }], startDate, true)
  );
  assert.equal(withTitle.CREATEDAT, "2025-09-05 11:53:52");
  assert.equal(withTitle["TITLE标题"], "超我 Ubermensch");
  assert.equal(withTitle["Category分类"], "Life生活");
  assert.deepEqual(Object.keys(withTitle).slice(0, 2), ["CREATEDAT", "TITLE标题"]);

  const noTitle = yamlParse(buildProperties("短标题 Short", [], startDate, false));
  assert.equal(noTitle["TITLE标题"], undefined);
  assert.equal(noTitle.CREATEDAT, "2025-09-05 11:53:52");
});

test("compileNote: order preserved, IM-style inline timestamps, filename", () => {
  const blocks = [
    { seq: 0, ts: "2025-09-05T03:53:00Z", type: "text", text: "first thought" },
    { seq: 1, ts: "2025-09-05T03:54:00Z", type: "voice", attachment: "v.ogg", transcript: "spoken words here" },
    { seq: 2, ts: "2025-09-05T03:55:00Z", type: "image", attachment: "i.jpg", userCaption: "under the trees" },
    { seq: 3, ts: "2025-09-05T03:56:00Z", type: "file", attachment: "r.pdf" },
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
  const iFile = markdown.indexOf("r.pdf");
  assert.ok(iText < iVoice && iVoice < iImg && iImg < iFile);

  // inline `**MM-DD HH:MM** content` on the SAME line (no lone header line)
  assert.match(markdown, /\*\*09-05 11:53\*\* first thought/);
  assert.match(markdown, /\*\*09-05 11:54\*\* !\[\[Heresy-Anthology\/digest\/ATTACHMENTS\/v\.ogg\]\]\n> spoken words here/);
  assert.match(markdown, /\*\*09-05 11:55\*\* !\[\[Heresy-Anthology\/digest\/ATTACHMENTS\/i\.jpg\]\]\nunder the trees/);

  // short title fits → no redundant TITLE标题
  const props = fm(markdown);
  assert.equal(props["TITLE标题"], undefined);
  assert.equal(props["Places地点"], "Park公园");
});

test("compileNote: long title → filename truncated + TITLE标题 holds full title", () => {
  const longZh = "很长的中文标题".repeat(20); // 140 chars ≈ 420 bytes
  const { filename, markdown } = compileNote({
    blocks: [{ seq: 0, ts: "2025-09-05T03:53:00Z", type: "text", text: "x" }],
    title: { zh: longZh, en: "Long" },
    tags: [],
    startDate,
  });
  // filename stays within the byte budget
  assert.ok(Buffer.byteLength(filename, "utf8") <= 255);
  // full title preserved in the property
  const props = fm(markdown);
  assert.equal(props["TITLE标题"], `${longZh} Long`);
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
