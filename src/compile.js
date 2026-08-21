// Compile an ordered list of captured blocks + LLM metadata into the final note.
// Pure: no I/O. Returns { filename, markdown }. Fully unit-testable.
import { stringify as yamlStringify } from "yaml";
import { ATTACHMENTS_VAULT_PREFIX } from "./config.js";
import { stampChatTime, createdAt, buildFilename, glueTitle } from "./util.js";

/** Obsidian embed for a vault attachment filename. */
function embed(attachment) {
  return `![[${ATTACHMENTS_VAULT_PREFIX}/${attachment}]]`;
}

/** Render text as a `>`-prefixed blockquote (each line), preserving blank lines. */
export function blockquote(text) {
  return String(text)
    .split("\n")
    .map((line) => (line.length ? `> ${line}` : ">"))
    .join("\n");
}

/**
 * Durable failure marker for a voice block whose transcript never arrived.
 * The audio is always saved, so a missing transcript is recoverable, not lost —
 * the marker records WHY it is missing and the exact command that recovers it,
 * which is the difference between a retryable gap and a silent one.
 */
export function transcriptFailureMarker(block) {
  const f = block.sttFailure || {};
  const attempts = f.attempts ? `, ${f.attempts} attempt${f.attempts === 1 ? "" : "s"}` : "";
  const why = f.reason ? ` (${f.reason}${attempts})` : "";
  const retry = block.attachment
    ? ` - audio saved; retry: npm run retranscribe -- "${block.attachment}"`
    : "";
  return `[Transcription unavailable${why}]${retry}`;
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
      const bq = blockquote(block.transcript?.trim() || transcriptFailureMarker(block));
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
 * Order: CREATEDAT, TITLE标题 (ALWAYS — the filename may be byte-capped, but the property
 * always holds the full title so downstream processing has a consistent key), then the
 * LLM-generated dynamic bilingual tags.
 */
export function buildProperties(fullTitle, tags, startDate) {
  const props = {};
  props.CREATEDAT = createdAt(startDate);
  props["TITLE标题"] = fullTitle;
  for (const t of tags || []) {
    if (!t || !t.key) continue;
    const key = String(t.key).trim();
    if (!key || key in props) continue;
    props[key] = String(t.value ?? "").trim();
  }
  return yamlStringify(props, { lineWidth: 0 }).trimEnd();
}

/**
 * Compile the full note.
 * @returns {{filename:string, markdown:string, fullTitle:string}}
 */
export function compileNote({ blocks, title, tags, startDate }) {
  const fullTitle = glueTitle(title?.zh, title?.en);
  const frontmatter = buildProperties(fullTitle, tags, startDate);

  const body = blocks.map(renderBlock).join("\n\n");
  const markdown = `---\n${frontmatter}\n---\n\n${body}\n`;
  const filename = `${buildFilename(fullTitle, startDate)}.md`;

  return { filename, markdown, fullTitle };
}
