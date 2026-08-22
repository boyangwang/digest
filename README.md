# Digest 🌱 (v1.2)

A Telegram bot that turns whatever you send it — text, voice, photos, files, in any
order — into **one bilingual Markdown note** in your Obsidian vault, with an
LLM-generated title and tags. "Dear diary", multimodal.

> **JavaScript (Node ≥22).** The old Python nightly-conversation-digest app is preserved on
> branch `archive/python-v1`. See [`specs/prd-digest-v1.2.md`](specs/prd-digest-v1.2.md).

## How it works

1. Send anything — the bot auto-starts a digest (no `/start`). Each input gets **two
   replies**: an instant ACK (after it's durably saved) and a "processed" message.
2. Voice → transcribed with the **original audio kept** and embedded. Transcription
   retries across two vendors (ElevenLabs Scribe v2 → OpenAI) so a transient vendor
   failure doesn't lose the words; if it still fails, the audio is saved anyway and the
   note carries a retryable marker (see **Recovering a failed transcript** below).
   Retrying happens **off** the per-chat queue, so the next message is still ACKed
   immediately; `/done` waits for it (bounded) before compiling.
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
| `transcriptions.js` | in-flight transcriptions per chat; what `/done` waits on |
| `store.js` | pending digest on disk (ordered, atomic, crash-safe) |
| `finalize.js` | `/done`: assemble → title/tags → move attachments → compile → write |
| `compile.js` | ordered blocks + metadata → final markdown + filename (pure) |
| `util.js` | SGT timestamps, filename sanitize + byte-cap (pure) |
| `stt.js` | transcription with retry + vendor rotation (the durability layer) |
| `stt-providers.js` | the STT vendors behind one normalized interface |
| `llm.js` | TokenHub `glm-5.2` (title+tags) + `glm-5v-turbo` (vision) |
| `config.js` | paths, models, keys |
| `prompts/title-and-tags.md` | the reusable title/tags prompt (the app's voice) |
| `scripts/retranscribe.mjs` | re-run transcription for a saved attachment and patch the note |

## Run / test / deploy

```bash
npm install
npm test                                   # unit + offline E2E
secret-run node src/index.js               # run locally (injects vault keys)
bash launchd/deploy.sh                      # install + start launchd service
tail -f ~/.local/share/digest/digest.log    # logs (durable — never /tmp, which macOS purges)
```

- **Output:** `~/Documents/NotesVault/Heresy-Anthology/digest/` (Obsidian-synced);
  attachments in `ATTACHMENTS/`.
- **Secrets (sops vault):** `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, `TENCENT_TOKENHUB_API_KEY`.
- **Service:** launchd `network.deardiary.digest`.

## Recovering a failed transcript

The audio is always saved before transcription is attempted, so a failed transcript is
deferred work, never lost data. When a note shows `[Transcription unavailable …]`:

```bash
secret-run npm run retranscribe -- --check     # FREE census: eligible vs refused, nothing called
secret-run npm run retranscribe -- --all       # recover every eligible note
secret-run npm run retranscribe -- "20260102-091100-1-voice.ogg"   # or just one
```

`--check` is the safe probe: it classifies the folder and makes **no** network call and
**no** write. `--dry-run` is **not** free - it performs one live STT call and one title/tag
LLM call per eligible note, because previewing the real recovered text and the real
proposed title is the point of a dry run.

**Eligibility is a hard precondition.** The digest folder is not a folder of bot-produced
notes - most of it is hand-written and correctly carries no frontmatter. A note is touched
only if it carries this bot's failed-transcript marker *and* already has frontmatter.
Everything else is refused with a reason and **zero bytes changed**: no frontmatter is ever
created, no un-marked note is rewritten however stale its metadata looks, and a legacy
`创建时间`/`分类`/`主题` note is never migrated as a side effect. Naming an attachment
explicitly does not bypass the gate, and the gate is checked before any vendor is called.

For an eligible note it runs the same retry/rotation loop the bot uses and replaces the
marker line, leaving every other byte of the body alone.

It then **regenerates that note's `TITLE标题` and generated tags** from the now-complete
text. Those were generated once, at `/done`, from an input where this voice note read
`(no transcript)` - so without this the frontmatter contradicts the body. The rewrite
**merges**: `CREATEDAT` and every other pre-existing key (a hand-added `aliases`,
`cssclasses`, Dataview field…) are left alone, and each run prints which properties were
replaced, added and left untouched. `--replace-properties` opts into the old
rebuild-from-scratch behaviour, which is the only way a key is removed. The recovered
transcript is written first and is never rolled back if the title model is unavailable
(you get a loud STALE warning instead).

The **filename is left alone by default**, because renaming on disk breaks existing
Obsidian links - Obsidian only rewrites links when the rename happens inside the app.
The script prints the old filename next to the new title so you can rename it in
Obsidian (F2). Pass `--rename` if you want the filesystem rename anyway.
