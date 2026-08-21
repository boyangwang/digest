# CLAUDE.md — Digest (v1.2)

## 📍 Status + Next Steps · (updated 2026-08-21)
- **Working on:** Digest v1.2 — DONE. Multimodal Telegram capture → one bilingual Obsidian note.
  v1.2.1 adds transcription durability (retry + vendor rotation + durable log + recovery script).
- **Status:** ✅ Shipped. 52/52 tests green; all 3 live APIs verified; Boyang UAT passed ("pretty good"); deployed to launchd `network.deardiary.digest`; pushed to `origin/main`.
- **Done recently:** JS rewrite; reusable title prompt from 552 vault titles; UAT fixes (serial# on every msg, ACK+Done button, IM inline timestamps, always-`TITLE标题`); deploy.
- **Known blockers:** Obsidian **must stay running on the mini** for Sync to propagate notes to phone (it's a server; Sync only runs while the app is open) — offered to add a keep-alive, pending Boyang's call. Vision caption ~15s/image (acceptable).
- **Next step:** captain must `bash launchd/deploy.sh` (or reload `network.deardiary.digest`)
  for the new durable `StandardOutPath` to take effect — a plist edit alone does nothing to a
  running service.

### Log (newest first)
- 2026-08-21 — v1.2.1 review round: transcription moved off the per-chat queue (+ per-chat
  manifest lock, bounded `/done` wait), attempt timeout back to 60s (budget 390s) and now
  covering the response body, `retranscribe` regenerates title/tags and gained `--rename`,
  tests no longer write the production log.
- 2026-08-21 — v1.2.1: STT retry + vendor rotation (ElevenLabs → OpenAI), durable LOG_PATH,
  retryable failure marker + `npm run retranscribe`; backfilled two lost transcripts.
- 2026-07-07 — v1.2 shipped: JS build, live APIs verified, UAT passed, deployed launchd + pushed main.

### Decisions (append-only)
1. v1.2 is **JavaScript (Node ≥22)**, not Python. Old app preserved on `archive/python-v1`; main overwritten.
2. Digest = **only user inputs** (dropped OpenClaw-conversation collection, reflection, scheduler/nudges).
3. STT = **ElevenLabs Scribe v2** primary + **OpenAI `gpt-4o-transcribe`** backup, rotating
   per attempt (`src/stt.js`). MiniMax was the original suggestion but publishes **no ASR
   endpoint** (speech API is T2A/voice-clone only), and TokenHub exposes no ASR model —
   re-verified 2026-08-21. OpenAI needs no new credential: `OPENAI_API_KEY` is already in the
   same sops store the launchd service loads. LLM = **TokenHub** `glm-5.2` (title+tags) + `glm-5v-turbo` (vision), via the `openai` npm client pointed at TokenHub (NOT openai.com).
4. Title = deterministic `YYYYMMDD-HHMM` + LLM title, **summarizing in Boyang's voice, bilingual `<zh> <en>`**, code-sanitized + byte-capped (≤200 bytes for the title portion). Full title always mirrored in `TITLE标题`.
5. Properties = **dynamic, LLM-chosen** bilingual `English中文` tags (NOT a fixed schema) + always `CREATEDAT: YYYY-MM-DD HH:MM:SS` + always `TITLE标题`.
6. Two Telegram replies per input, both carrying the serial `#N`; ACK = `✓ ACK #N` with the inline Done button attached; ACK is atomic-with-persistence; per-chat serial queue = linear/deterministic.
7. Body blocks use IM-style **inline** timestamps: `**MM-DD HH:MM** content` (same line, month-day-hour-minute).
8. **Transcription runs OUTSIDE the per-chat serial queue** (`src/transcriptions.js`). The
   block's slot is reserved by `appendBlock` at arrival, so a late transcript still renders in
   order; holding the queue for the retry budget would only stall the next message. `/done`
   waits on the in-flight set, bounded by `STT_TOTAL_BUDGET_MS`, then compiles anyway with the
   retryable marker. Manifest read-modify-writes take a per-chat lock in `store.js` because the
   queue no longer serializes them.
9. `npm run retranscribe` **regenerates `TITLE标题` + tags** after a successful recovery (they
   were generated once at `/done` from `(no transcript)`), preserving `CREATEDAT` verbatim. The
   filename is deliberately NOT renamed by default — a filesystem rename breaks Obsidian links;
   `--rename` opts in.

## What this is
Telegram bot: send text/voice/photos/files in any order → tap ✅ Done / `/done` → one bilingual
Markdown note in the Obsidian vault. See `README.md` and `specs/prd-digest-v1.2.md`.

## Stack
Node ≥22 (ESM), grammY, `openai` client → Tencent TokenHub, `yaml`. STT = ElevenLabs Scribe v2
→ OpenAI `gpt-4o-transcribe` (plain `fetch`/`FormData`, no SDK).
Tests: `node --test` via `npm test`, which loads `test/setup.mjs` with `--import` to pin
`DIGEST_LOG_PATH` at a temp dir — otherwise the suite appends to the live service log.

## Key commands
```bash
npm test                          # unit + offline E2E
secret-run node src/index.js      # run locally (vault keys injected)
bash launchd/deploy.sh            # deploy launchd network.deardiary.digest
tail -f ~/.local/share/digest/digest.log   # NEVER /tmp — macOS purges it under the running service
secret-run npm run retranscribe -- --all --dry-run   # recover any failed transcript
```

## Conventions
- **Timezone:** all timestamps SGT (UTC+8) via `util.js`. **Never** hand-format dates elsewhere.
- **Filename is code-sanitized, never LLM-controlled**; byte-capped ≤200 bytes (APFS limit).
- **ACK only after persistence** (`store.appendBlock`/`saveAttachment` resolve first).
- **LLM/STT/vision must degrade gracefully** — a failure never loses input; `/done` can be retried.
- **All transcription goes through `transcribe()` in `src/stt.js`** — never call a vendor
  directly. Retry/rotation/attempt-cap are data-driven from `config.js`; add a vendor in
  `stt-providers.js` and name it in `STT_PROVIDER_ORDER`.
- **Never default a log or state path under `/tmp`** — macOS purges it while launchd holds the
  handle, so the history goes to a deleted inode. `DATA_DIR` is the durable home. Corollary:
  **tests must never write to the real log** — `test/setup.mjs` pins `DIGEST_LOG_PATH`.
- **The STT attempt timeout must cover the response BODY, not just the headers** — `fetch()`
  resolves on headers, so an abort timer cleared there lets a stalled body outlive the budget.
- Output vault: `~/Documents/NotesVault/Heresy-Anthology/digest/` (+ `ATTACHMENTS/`).
- Secrets: `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, `TENCENT_TOKENHUB_API_KEY` (sops vault).

## The title voice
`prompts/title-and-tags.md` — authored from Boyang's 552 real note titles. It IS the reusable
prompt fed to `glm-5.2`. Keep it as the single source of truth for title/tag style.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
