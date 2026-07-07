import { test } from "node:test";
import assert from "node:assert/strict";
import {
  stampMinute,
  stampSecond,
  createdAt,
  byteCap,
  sanitizeTitle,
  buildFilename,
  glueTitle,
} from "../src/util.js";

// 2025-09-05 03:53:52Z == 11:53:52 SGT (UTC+8)
const D = new Date("2025-09-05T03:53:52Z");

test("SGT timestamp formats", () => {
  assert.equal(stampMinute(D), "20250905-1153");
  assert.equal(stampSecond(D), "20250905-115352");
  assert.equal(createdAt(D), "2025-09-05 11:53:52");
});

test("SGT crosses UTC day boundary correctly", () => {
  // 2025-09-04 20:30Z == 2025-09-05 04:30 SGT
  const d = new Date("2025-09-04T20:30:00Z");
  assert.equal(stampMinute(d), "20250905-0430");
});

test("byteCap does not split multibyte chars", () => {
  assert.equal(byteCap("你好世界", 6), "你好"); // each CJK char = 3 bytes
  assert.equal(byteCap("你好世界", 7), "你好"); // 世 would push to 9 bytes
  assert.equal(byteCap("hello", 100), "hello");
});

test("sanitizeTitle strips illegal chars, keeps hyphen + CJK, collapses ws", () => {
  assert.equal(sanitizeTitle('a/b:c*d 超我 e-550'), "a b c d 超我 e-550");
  assert.equal(sanitizeTitle('"[link]" #tag'), "link tag");
  assert.equal(sanitizeTitle("  ...leading"), "leading");
});

test("sanitizeTitle byte-caps a long CJK title", () => {
  const long = "字".repeat(200); // 600 bytes
  const out = sanitizeTitle(long, 200);
  assert.ok(Buffer.byteLength(out, "utf8") <= 200);
  assert.ok(out.length < 200);
});

test("buildFilename = timestamp + sanitized title, falls back to timestamp", () => {
  assert.equal(buildFilename("超我 Ubermensch", D), "20250905-1153 超我 Ubermensch");
  assert.equal(buildFilename("///", D), "20250905-1153"); // sanitizes to empty
});

test("glueTitle joins as <zh> <en>", () => {
  assert.equal(glueTitle("超我", "Ubermensch"), "超我 Ubermensch");
  assert.equal(glueTitle("只有中文", ""), "只有中文");
  assert.equal(glueTitle("", "only en"), "only en");
});
