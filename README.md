# Digest 🌱 (v1.2)

A Telegram bot that turns whatever you send it — text, voice, photos, files, in any
order — into **one bilingual Markdown note** in your Obsidian vault, with an
LLM-generated title and tags. "Dear diary", multimodal.

> **JavaScript (Node ≥22).** The old Python nightly-conversation-digest app is preserved on
> branch `archive/python-v1`. See [`specs/prd-digest-v1.2.md`](specs/prd-digest-v1.2.md).

## How it works

1. Send anything — the bot auto-starts a digest (no `/start`). Each input gets **two
   replies**: an instant ACK (after it's durably saved) and a "processed" message.
2. Voice → transcribed (ElevenLabs Scribe v2) with the **original audio kept** and embedded.
   Photos/files → saved as attachments and embedded. Text → kept verbatim. **Exact order
   preserved.**
3. Tap **✅ Done** or send `/done` → the bot compiles everything into one note:
   - **Title** = `YYYYMMDD-HHMM` (deterministic) + a longer, summarizing bilingual title in
     your voice (`glm-5.2`), sanitized by code.
   - **Properties** = dynamic bilingual tags (`glm-5.2`) + `CREATEDAT` + full `TITLE标题`.
   - Images are captioned by `glm-5v-turbo` so the title/tags reflect them.

## Architecture (`src/`)

| file | role |
|---|---|
| `index.js` | entry — long polling |
| `bot.js` | grammY handlers; `/done` + inline button; per-chat serial queue |
| `ingest.js` | one input: persist → ACK → process (STT/vision) → processed |
| `store.js` | pending digest on disk (ordered, atomic, crash-safe) |
| `finalize.js` | `/done`: assemble → title/tags → move attachments → compile → write |
| `compile.js` | ordered blocks + metadata → final markdown + filename (pure) |
| `util.js` | SGT timestamps, filename sanitize + byte-cap (pure) |
| `stt.js` | ElevenLabs Scribe v2 |
| `llm.js` | TokenHub `glm-5.2` (title+tags) + `glm-5v-turbo` (vision) |
| `config.js` | paths, models, keys |
| `prompts/title-and-tags.md` | the reusable title/tags prompt (the app's voice) |

## Run / test / deploy

```bash
npm install
npm test                                   # unit + offline E2E
secret-run node src/index.js               # run locally (injects vault keys)
bash launchd/deploy.sh                      # install + start launchd service
tail -f /tmp/digest.log                     # logs
```

- **Output:** `~/Documents/NotesVault/Heresy-Anthology/digest/` (Obsidian-synced);
  attachments in `ATTACHMENTS/`.
- **Secrets (sops vault):** `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `TENCENT_TOKENHUB_API_KEY`.
- **Service:** launchd `network.deardiary.digest`.
