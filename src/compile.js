// Compile an ordered list of captured blocks + LLM metadata into the final note.
// Pure: no I/O. Returns { filename, markdown }. Fully unit-testable.
import { stringify as yamlStringify } from "yaml";
import { ATTACHMENTS_VAULT_PREFIX, FILENAME_TITLE_MAX_BYTES } from "./config.js";
import { stampChatTime, createdAt, buildFilename, glueTitle, sanitizeTitle } from "./util.js";

/** Obsidian embed for a vault attachment filename. */
function embed(attachment) {
  return `![[${ATTACHMENTS_VAULT_PREFIX}/${attachment}]]`;
}

/** Render text as a `>`-prefixed blockquote (each line), preserving blank lines. */
function blockquote(text) {
  return String(text)
    .split("\n")
    .map((line) => (line.length ? `> ${line}` : ">"))
    .join("\n");
}

/**
 * Render one block, IM-style: an inline `**MM-DD HH:MM**` timestamp on the SAME line
 * as the content (never a lone header line).
 */
function renderBlock(block) {
  const ts = `**${stampChatTime(new Date(block.ts))}**`;
  switch (block.type) {
    case "text":
      return `${ts} ${String(block.text ?? "").trim()}`;
    case "voice": {
      const bq = blockquote(block.transcript?.trim() || "[Transcription unavailable]");
      return `${ts} ${embed(block.attachment)}\n${bq}`;
    }
    case "image":
    case "file": {
      let out = `${ts} ${embed(block.attachment)}`;
      if (block.userCaption && block.userCaption.trim()) out += `\n${block.userCaption.trim()}`;
      return out;
    }
    default:
      return "";
  }
}

/**
 * Build the YAML frontmatter (markdown "properties").
 * Order: CREATEDAT, [TITLE标题 only when the title was truncated for the filename],
 * then the LLM-generated dynamic bilingual tags.
 */
export function buildProperties(fullTitle, tags, startDate, includeFullTitle) {
  const props = {};
  props.CREATEDAT = createdAt(startDate);
  if (includeFullTitle) props["TITLE标题"] = fullTitle;
  for (const t of tags || []) {
    if (!t || !t.key) continue;
    const key = String(t.key).trim();
    if (!key || key in props) continue;
    props[key] = String(t.value ?? "").trim();
  }
  return yamlStringify(props, { lineWidth: 0 }).trimEnd();
}

/** True if the sanitized full title exceeds the filename byte budget (→ filename truncates it). */
function titleTruncated(fullTitle) {
  const sanitizedFull = sanitizeTitle(fullTitle, Number.MAX_SAFE_INTEGER);
  return Buffer.byteLength(sanitizedFull, "utf8") > FILENAME_TITLE_MAX_BYTES;
}

/**
 * Compile the full note.
 * @returns {{filename:string, markdown:string, fullTitle:string}}
 */
export function compileNote({ blocks, title, tags, startDate }) {
  const fullTitle = glueTitle(title?.zh, title?.en);
  const includeFullTitle = titleTruncated(fullTitle);
  const frontmatter = buildProperties(fullTitle, tags, startDate, includeFullTitle);

  const body = blocks.map(renderBlock).join("\n\n");
  const markdown = `---\n${frontmatter}\n---\n\n${body}\n`;
  const filename = `${buildFilename(fullTitle, startDate)}.md`;

  return { filename, markdown, fullTitle };
}
