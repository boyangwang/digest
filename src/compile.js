// Compile an ordered list of captured blocks + LLM metadata into the final note.
// Pure: no I/O. Returns { filename, markdown }. Fully unit-testable.
import { stringify as yamlStringify } from "yaml";
import { ATTACHMENTS_VAULT_PREFIX } from "./config.js";
import { stampMinute, createdAt, buildFilename, glueTitle } from "./util.js";

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

/** Render one captured block to markdown (without its timestamp header). */
function renderBlock(block) {
  switch (block.type) {
    case "text":
      return String(block.text ?? "").trim();
    case "voice": {
      const parts = [embed(block.attachment)];
      parts.push(blockquote(block.transcript?.trim() || "[Transcription unavailable]"));
      return parts.join("\n");
    }
    case "image":
    case "file": {
      const parts = [embed(block.attachment)];
      if (block.userCaption && block.userCaption.trim()) {
        parts.push(block.userCaption.trim());
      }
      return parts.join("\n");
    }
    default:
      return "";
  }
}

/**
 * Build the YAML frontmatter (markdown "properties").
 * Order: CREATEDAT, TITLE标题, then the LLM-generated dynamic bilingual tags.
 * @param {{key:string,value:string}[]} tags
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
  // yaml.stringify preserves insertion order and quotes values when needed.
  return yamlStringify(props, { lineWidth: 0 }).trimEnd();
}

/**
 * Compile the full note.
 * @param {object} p
 * @param {Array} p.blocks   ordered captured blocks (each with a `ts` ISO string)
 * @param {object} p.title   { zh, en }
 * @param {Array}  p.tags    [{key,value}]
 * @param {Date}   p.startDate  digest creation time (first input); anchors filename + CREATEDAT
 * @returns {{filename:string, markdown:string, fullTitle:string}}
 */
export function compileNote({ blocks, title, tags, startDate }) {
  const fullTitle = glueTitle(title?.zh, title?.en);

  const frontmatter = buildProperties(fullTitle, tags, startDate);

  const bodyBlocks = blocks.map((b) => {
    const header = stampMinute(new Date(b.ts));
    const rendered = renderBlock(b);
    return `${header}\n${rendered}`.trimEnd();
  });

  const markdown = `---\n${frontmatter}\n---\n\n${bodyBlocks.join("\n\n")}\n`;
  const filename = `${buildFilename(fullTitle, startDate)}.md`;

  return { filename, markdown, fullTitle };
}
