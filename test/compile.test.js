import { test } from "node:test";
import assert from "node:assert/strict";
import { parse as yamlParse } from "yaml";
import { compileNote, buildProperties, transcriptFailureMarker } from "../src/compile.js";
import { GENERATOR_KEY, GENERATOR_STAMP } from "../src/provenance.js";

const startDate = new Date("2025-09-05T03:53:52Z"); // 11:53:52 SGT

function fm(markdown) {
  const m = markdown.match(/^---\n([\s\S]*?)\n---\n/);
  return yamlParse(m[1]);
}

test("buildProperties: CREATEDAT + TITLE标题 always first, then dynamic bilingual tags", () => {
  const p = yamlParse(
    buildProperties("超我 Ubermensch", [{ key: "Category分类", value: "Life生活" }], startDate)
  );
  assert.equal(p.CREATEDAT, "2025-09-05 11:53:52");
  assert.equal(p["TITLE标题"], "超我 Ubermensch");
  assert.equal(p["Category分类"], "Life生活");
  assert.deepEqual(Object.keys(p).slice(0, 3), ["CREATEDAT", "TITLE标题", GENERATOR_KEY]);
  // Provenance is stamped at creation, never inferred later — see src/provenance.js.
  assert.equal(p[GENERATOR_KEY], GENERATOR_STAMP);

  // TITLE标题 is present even for short titles (consistent key for downstream processing)
  const short = yamlParse(buildProperties("短标题 Short", [], startDate));
  assert.equal(short["TITLE标题"], "短标题 Short");
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

  // TITLE标题 always present (full title, even though it also fits the filename)
  const props = fm(markdown);
  assert.equal(props["TITLE标题"], "去公园的一天 A day at the park");
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

test("compileNote: missing transcript → unavailable marker that says how to recover", () => {
  const { markdown } = compileNote({
    blocks: [{ seq: 0, ts: "2025-09-05T03:53:00Z", type: "voice", attachment: "a.ogg" }],
    title: { zh: "x", en: "y" },
    tags: [],
    startDate,
  });
  assert.match(markdown, /> \[Transcription unavailable\]/);
  // the audio is embedded and the marker carries the retry, so the words are never
  // silently final — the note itself tells you how to get them back
  assert.match(markdown, /!\[\[Heresy-Anthology\/digest\/ATTACHMENTS\/a\.ogg\]\]/);
  assert.match(markdown, /audio saved; retry: npm run retranscribe -- "a\.ogg"/);
});

test("transcriptFailureMarker: records why it failed and how many attempts it took", () => {
  const m = transcriptFailureMarker({
    attachment: "20260102-091100-1-voice.ogg",
    sttFailure: { reason: "exhausted", attempts: 6, providers: ["elevenlabs", "openai"] },
  });
  assert.match(m, /\[Transcription unavailable \(exhausted, 6 attempts\)\]/);
  assert.match(m, /retranscribe -- "20260102-091100-1-voice\.ogg"/);
  assert.equal(transcriptFailureMarker({ attachment: "a.ogg", sttFailure: { reason: "empty", attempts: 1 } }).includes("1 attempt)"), true);
});
