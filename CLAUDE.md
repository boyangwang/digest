# CLAUDE.md — Digest (v1.2)

## 📍 Status + Next Steps · (updated 2026-07-07)
- **Working on:** Digest v1.2 — DONE. Multimodal Telegram capture → one bilingual Obsidian note.
- **Status:** ✅ Shipped. 22/22 tests green; all 3 live APIs verified; Boyang UAT passed ("pretty good"); deployed to launchd `network.deardiary.digest`; pushed to `origin/main`.
- **Done recently:** JS rewrite; reusable title prompt from 552 vault titles; UAT fixes (serial# on every msg, ACK+Done button, IM inline timestamps, always-`TITLE标题`); deploy.
- **Known blockers:** Obsidian **must stay running on the mini** for Sync to propagate notes to phone (it's a server; Sync only runs while the app is open) — offered to add a keep-alive, pending Boyang's call. Vision caption ~15s/image (acceptable).
- **Next step:** (optional) keep-alive for Obsidian on the mini so Digest notes always sync.

### Log (newest first)
- 2026-07-07 — v1.2 shipped: JS build, live APIs verified, UAT passed, deployed launchd + pushed main.

### Decisions (append-only)
1. v1.2 is **JavaScript (Node ≥22)**, not Python. Old app preserved on `archive/python-v1`; main overwritten.
2. Digest = **only user inputs** (dropped OpenClaw-conversation collection, reflection, scheduler/nudges).
3. STT = **ElevenLabs Scribe v2** (MiniMax has no STT API). LLM = **TokenHub** `glm-5.2` (title+tags) + `glm-5v-turbo` (vision), via the `openai` npm client pointed at TokenHub (NOT openai.com).
4. Title = deterministic `YYYYMMDD-HHMM` + LLM title, **summarizing in Boyang's voice, bilingual `<zh> <en>`**, code-sanitized + byte-capped (≤200 bytes for the title portion). Full title always mirrored in `TITLE标题`.
5. Properties = **dynamic, LLM-chosen** bilingual `English中文` tags (NOT a fixed schema) + always `CREATEDAT: YYYY-MM-DD HH:MM:SS` + always `TITLE标题`.
6. Two Telegram replies per input, both carrying the serial `#N`; ACK = `✓ ACK #N` with the inline Done button attached; ACK is atomic-with-persistence; per-chat serial queue = linear/deterministic.
7. Body blocks use IM-style **inline** timestamps: `**MM-DD HH:MM** content` (same line, month-day-hour-minute).

## What this is
Telegram bot: send text/voice/photos/files in any order → tap ✅ Done / `/done` → one bilingual
Markdown note in the Obsidian vault. See `README.md` and `specs/prd-digest-v1.2.md`.

## Stack
Node ≥22 (ESM), grammY, `openai` client → Tencent TokenHub, `yaml`. ElevenLabs Scribe v2 for STT.
Tests: `node --test`.

## Key commands
```bash
npm test                          # unit + offline E2E
secret-run node src/index.js      # run locally (vault keys injected)
bash launchd/deploy.sh            # deploy launchd network.deardiary.digest
tail -f /tmp/digest.log
```

## Conventions
- **Timezone:** all timestamps SGT (UTC+8) via `util.js`. **Never** hand-format dates elsewhere.
- **Filename is code-sanitized, never LLM-controlled**; byte-capped ≤200 bytes (APFS limit).
- **ACK only after persistence** (`store.appendBlock`/`saveAttachment` resolve first).
- **LLM/STT/vision must degrade gracefully** — a failure never loses input; `/done` can be retried.
- Output vault: `~/Documents/NotesVault/Heresy-Anthology/digest/` (+ `ATTACHMENTS/`).
- Secrets: `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `TENCENT_TOKENHUB_API_KEY` (sops vault).

## The title voice
`prompts/title-and-tags.md` — authored from Boyang's 552 real note titles. It IS the reusable
prompt fed to `glm-5.2`. Keep it as the single source of truth for title/tag style.
