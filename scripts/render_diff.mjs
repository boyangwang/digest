#!/usr/bin/env node
/**
 * render_diff.mjs — Render diff PNGs without LLM.
 * 
 * Usage: node render_diff.mjs <before_file> <after_file> <display_path> <output_png>
 * 
 * Pure code: difflib → HTML → playwright screenshot. Zero LLM calls.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("/opt/homebrew/lib/node_modules/openclaw/extensions/diffs/node_modules/playwright-core");

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function unifiedDiff(before, after, filepath) {
  // Simple line-by-line diff using LCS
  const oldLines = before.split("\n");
  const newLines = after.split("\n");
  
  const result = [];
  let oi = 0, ni = 0;
  
  // Simple diff: find common lines, mark additions/removals
  // Use a map for matching
  const oldSet = new Map();
  oldLines.forEach((line, i) => {
    if (!oldSet.has(line)) oldSet.set(line, []);
    oldSet.get(line).push(i);
  });
  
  // Generate unified diff output lines
  const hunks = [];
  let currentHunk = null;
  
  // Use a simple edit script approach
  const lines = [];
  let i = 0, j = 0;
  
  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      lines.push({ type: "context", content: oldLines[i], oldLine: i + 1, newLine: j + 1 });
      i++; j++;
    } else if (j < newLines.length && (i >= oldLines.length || !oldLines.slice(i).includes(newLines[j]))) {
      lines.push({ type: "add", content: newLines[j], newLine: j + 1 });
      j++;
    } else {
      lines.push({ type: "del", content: oldLines[i], oldLine: i + 1 });
      i++;
    }
  }
  
  // Group into hunks (contiguous changes with 3 lines context)
  const CTX = 3;
  let inChange = false;
  let hunkStart = -1;
  
  for (let k = 0; k < lines.length; k++) {
    if (lines[k].type !== "context") {
      if (!inChange) {
        hunkStart = Math.max(0, k - CTX);
        inChange = true;
      }
    } else if (inChange) {
      // Check if next change is within context distance
      let nextChange = -1;
      for (let m = k + 1; m < Math.min(k + CTX * 2 + 1, lines.length); m++) {
        if (lines[m].type !== "context") { nextChange = m; break; }
      }
      if (nextChange === -1) {
        const hunkEnd = Math.min(k + CTX, lines.length);
        hunks.push(lines.slice(hunkStart, hunkEnd));
        inChange = false;
      }
    }
  }
  if (inChange) {
    hunks.push(lines.slice(hunkStart));
  }
  
  return hunks;
}

function buildHtml(hunks, displayPath) {
  let body = "";
  
  for (const hunk of hunks) {
    body += `<div class="hunk">`;
    for (const line of hunk) {
      const cls = line.type === "add" ? "added" : line.type === "del" ? "removed" : "ctx";
      const prefix = line.type === "add" ? "+" : line.type === "del" ? "-" : " ";
      const num = line.type === "del" ? (line.oldLine || "") : (line.newLine || "");
      body += `<div class="line ${cls}"><span class="num">${num}</span><span class="pfx">${prefix}</span><span class="txt">${escapeHtml(line.content)}</span></div>\n`;
    }
    body += `</div>`;
  }
  
  if (!hunks.length) {
    body = `<div class="no-change">No changes</div>`;
  }
  
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body { background:#1e1e2e; color:#cdd6f4; font:13px/1.6 'SF Mono','Menlo',monospace; margin:0; padding:0; }
.header { color:#89b4fa; font-weight:700; padding:10px 16px; border-bottom:1px solid #313244; background:#181825; }
.hunk { border-bottom:1px solid #313244; }
.line { display:flex; border-bottom:1px solid #1e1e2e; }
.num { color:#6c7086; min-width:50px; text-align:right; padding:0 8px; flex-shrink:0; background:#181825; }
.pfx { width:20px; text-align:center; flex-shrink:0; font-weight:700; }
.txt { white-space:pre-wrap; word-break:break-all; flex:1; padding-right:16px; }
.added { background:rgba(166,227,161,0.1); }
.added .pfx { color:#a6e3a1; }
.added .txt { color:#a6e3a1; }
.removed { background:rgba(243,139,168,0.1); }
.removed .pfx { color:#f38ba8; }
.removed .txt { color:#f38ba8; }
.ctx { color:#a6adc8; }
.no-change { padding:16px; color:#6c7086; text-align:center; }
</style></head><body>
<div class="header">${escapeHtml(displayPath || "diff")}</div>
${body}
</body></html>`;
}

async function main() {
  const [,, beforeFile, afterFile, displayPath, outputPng] = process.argv;
  if (!beforeFile || !afterFile || !outputPng) {
    console.error("Usage: node render_diff.mjs <before> <after> <path> <output.png>");
    process.exit(1);
  }
  
  const before = fs.readFileSync(beforeFile, "utf-8");
  const after = fs.readFileSync(afterFile, "utf-8");
  
  if (before === after) {
    console.error("No changes");
    process.exit(0);
  }
  
  const hunks = unifiedDiff(before, after, displayPath);
  const html = buildHtml(hunks, displayPath);
  
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  try {
    const page = await browser.newPage();
    await page.setViewportSize({ width: 2000, height: 600 });
    await page.setContent(html, { waitUntil: "load" });
    
    const height = await page.evaluate(() => Math.min(Math.max(document.body.scrollHeight, 200), 16384));
    await page.setViewportSize({ width: 2000, height: height + 20 });
    
    fs.mkdirSync(path.dirname(outputPng), { recursive: true });
    await page.screenshot({ path: outputPng, fullPage: true, type: "png" });
    console.log(outputPng);
  } finally {
    await browser.close();
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
