// Bot provenance: how a note records that THIS program created it.
//
// The digest folder is mostly hand-written. Measured in the live vault on
// 2026-08-22: of 577 notes, 494 have no frontmatter at all, 63 carry a legacy
// `创建时间`/`分类`/`主题` schema, 19 carry the current bot schema. The bot's own
// output is a small minority.
//
// So no tool here may INFER that a note is ours from its state - not from a schema
// that looks familiar, not from a marker it happens to contain. Inference about a
// note's origin drawn from its content is only ever as good as the guess, and the
// blast radius is the captain's personal journal. Provenance is recorded at
// creation and read back verbatim.
//
// The stamp is a frontmatter PROPERTY, deliberately not an Obsidian `tags:` entry:
// a tag would land in the tag namespace and the graph view, and this is provenance
// metadata, not a topic label. In Obsidian it shows as a text property.

/** Bilingual key, matching the convention the other fixed keys already use. */
export const GENERATOR_KEY = "GENERATOR生成器";

/** The generator segment. Detection is an exact match on this, never a substring. */
export const GENERATOR_ID = "digest";

/**
 * Bump when the STAMP's meaning changes - not when the app version changes. It is
 * what makes the next format move cheap.
 */
export const GENERATOR_STAMP_VERSION = 1;

/** The value written into every note this program creates: `digest/1`. */
export const GENERATOR_STAMP = `${GENERATOR_ID}/${GENERATOR_STAMP_VERSION}`;

/**
 * Was this note created by this program? Reads the parsed frontmatter object.
 *
 * Exact match on the segment before the `/`, so a hand-written note whose text
 * merely mentions "digest" can never qualify. There is deliberately NO override:
 * no flag, env var or config field may make an unstamped note eligible for
 * in-place editing.
 */
export function isGeneratedByDigest(frontmatter) {
  const value = frontmatter?.[GENERATOR_KEY];
  if (typeof value !== "string") return false;
  return value.split("/")[0].trim() === GENERATOR_ID;
}
