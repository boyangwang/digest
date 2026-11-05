#!/usr/bin/env node
// llm-smoke.mjs - LIVE check of the title + caption LLM, through the real src/llm.js
// code path and the real provider config. Spends a little money (under 1 cent per
// run on gpt-5.4-mini). Writes nothing: notes and images are only read.
//
//   secret-run node scripts/llm-smoke.mjs [note.md] [image.png|jpg]
//
// Prints which provider answered, the title, the tags, the caption, latency and token
// usage (the usage is what the bill is made of).
import "../src/net-tuning.js";
import { readFile } from "node:fs/promises";
import OpenAI from "openai";
import { generateTitleAndTags, captionImage, configuredProviders, LLM_PROVIDERS } from "../src/llm.js";

const [notePath, imagePath] = process.argv.slice(2);
const providers = configuredProviders();
console.log(`configured LLM providers: [${providers.map((p) => p.name).join(", ")}]`);
if (!providers.length) {
  console.error("no provider has a key - run under secret-run");
  process.exit(1);
}

const clients = {};
const usage = [];
const createImpl = async (body, reqOpts, name) => {
  const p = LLM_PROVIDERS[name];
  clients[name] ||= new OpenAI({ baseURL: p.baseURL, apiKey: p.key() });
  const t0 = Date.now();
  const res = await clients[name].chat.completions.create(body, reqOpts);
  usage.push({ provider: name, model: res.model, ms: Date.now() - t0, usage: res.usage });
  return res;
};

let text = "今天晚上散步的时候想到，等待别人和让别人等待其实是同一件事。Walking tonight I realised waiting and making others wait are the same thing.";
if (notePath) {
  const raw = await readFile(notePath, "utf8");
  text = raw.replace(/^---\n[\s\S]*?\n---\n/, "").trim(); // drop YAML frontmatter
}
const t = await generateTitleAndTags(text, { createImpl });
console.log("\n== title ==");
console.log(JSON.stringify(t, null, 2));

let captionOk = true;
if (imagePath) {
  const mime = /\.png$/i.test(imagePath) ? "image/png" : "image/jpeg";
  const c = await captionImage(imagePath, mime, { createImpl });
  console.log("\n== caption ==");
  console.log(c);
  captionOk = Boolean(c);
}

console.log("\n== calls ==");
for (const u of usage) {
  const det = u.usage?.completion_tokens_details?.reasoning_tokens ?? "?";
  console.log(
    `${u.provider} ${u.model} ${u.ms}ms in=${u.usage?.prompt_tokens} ` +
      `(cached ${u.usage?.prompt_tokens_details?.cached_tokens ?? 0}) out=${u.usage?.completion_tokens} (reasoning ${det})`
  );
}
const ok = !t.fallback && captionOk;
console.log(ok ? "\nSMOKE OK" : "\nSMOKE FAILED");
process.exit(ok ? 0 : 1);
