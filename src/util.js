// Pure helpers: SGT timestamps + filename sanitization. No side effects, fully testable.
import { SGT_OFFSET_MIN, FILENAME_TITLE_MAX_BYTES } from "./config.js";

const pad = (n) => String(n).padStart(2, "0");

/** Break a Date into SGT (UTC+8) calendar parts. */
export function sgtParts(date = new Date()) {
  const s = new Date(date.getTime() + SGT_OFFSET_MIN * 60000);
  return {
    Y: s.getUTCFullYear(),
    M: pad(s.getUTCMonth() + 1),
    D: pad(s.getUTCDate()),
    h: pad(s.getUTCHours()),
    m: pad(s.getUTCMinutes()),
    s: pad(s.getUTCSeconds()),
  };
}

/** `YYYYMMDD-HHMM` (filename prefix + body block headers). */
export function stampMinute(date = new Date()) {
  const p = sgtParts(date);
  return `${p.Y}${p.M}${p.D}-${p.h}${p.m}`;
}

/** `YYYYMMDD-HHMMSS` (attachment fallback names). */
export function stampSecond(date = new Date()) {
  const p = sgtParts(date);
  return `${p.Y}${p.M}${p.D}-${p.h}${p.m}${p.s}`;
}

/** `YYYY-MM-DD HH:MM:SS` (CREATEDAT property value). */
export function createdAt(date = new Date()) {
  const p = sgtParts(date);
  return `${p.Y}-${p.M}-${p.D} ${p.h}:${p.m}:${p.s}`;
}

/** `MM-DD HH:MM` — IM-style inline timestamp for body blocks (month day hour minute). */
export function stampChatTime(date = new Date()) {
  const p = sgtParts(date);
  return `${p.M}-${p.D} ${p.h}:${p.m}`;
}

const utf8 = (s) => Buffer.byteLength(s, "utf8");

/**
 * Truncate a string to at most `maxBytes` UTF-8 bytes without splitting a
 * multi-byte character. Returns the largest whole-character prefix that fits.
 */
export function byteCap(str, maxBytes = FILENAME_TITLE_MAX_BYTES) {
  if (utf8(str) <= maxBytes) return str;
  let out = "";
  for (const ch of str) {
    if (utf8(out + ch) > maxBytes) break;
    out += ch;
  }
  return out.trimEnd();
}

// Characters illegal/troublesome in Obsidian/macOS filenames + control chars.
// Hyphens and spaces are kept (spaces are collapsed separately).
const ILLEGAL = new RegExp("[:/\\\\|#^\\[\\]*?<>\"\\u0000-\\u001f]", "g");

/**
 * Sanitize an LLM-produced title into a filesystem-safe filename component:
 * strip illegal chars, collapse whitespace, drop a leading dot, trim, byte-cap.
 * Code-only — the LLM never controls the final filename.
 */
export function sanitizeTitle(title, maxBytes = FILENAME_TITLE_MAX_BYTES) {
  let t = String(title || "")
    .replace(ILLEGAL, " ")
    .replace(/\s+/g, " ")
    .replace(/^[.\s]+/, "")
    .trim();
  return byteCap(t, maxBytes).trim();
}

/**
 * Compose the final note filename (no extension): `YYYYMMDD-HHMM <sanitized title>`.
 * If the title sanitizes to empty, fall back to the timestamp alone.
 */
export function buildFilename(fullTitle, date = new Date()) {
  const prefix = stampMinute(date);
  const safe = sanitizeTitle(fullTitle, FILENAME_TITLE_MAX_BYTES);
  return safe ? `${prefix} ${safe}` : prefix;
}

/** Glue bilingual title halves per spec C2: `<zh> <en>`. */
export function glueTitle(titleZh, titleEn) {
  return [String(titleZh || "").trim(), String(titleEn || "").trim()]
    .filter(Boolean)
    .join(" ");
}
