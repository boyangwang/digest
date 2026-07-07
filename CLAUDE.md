# CLAUDE.md — Digest (v1.2)

## 📍 Status + Next Steps · (updated 2026-07-07)
- **Working on:** Digest v1.2 — JS rewrite. Multimodal Telegram capture → one bilingual Obsidian note.
- **Status:** Implemented; 21/21 unit+offline-E2E green; all 3 live APIs probed OK (STT, glm-5v-turbo, glm-5.2). Awaiting Boyang's live Telegram UAT, then deploy.
- **Done recently:** archived Python → `archive/python-v1`; scaffolded JS app; wrote reusable title prompt from 552 vault titles; live-probed TokenHub + ElevenLabs.
- **Known blockers:** none. (Vision caption ~15s/image is acceptable.)
- **Next step:** run bot for Boyang to try via Telegram → on OK, `bash launchd/deploy.sh` + verify live.

### Log (newest first)
- 2026-07-07 — v1.2 JS build: capture/queue/store/compile/finalize + grammY bot; tests green; APIs verified.

### Decisions (append-only)
1. v1.2 is **JavaScript (Node ≥22)**, not Python. Old app preserved on `archive/python-v1`; main overwritten.
2. Digest = **only user inputs** (dropped OpenClaw-conversation collection, reflection, scheduler/nudges).
3. STT = **ElevenLabs Scribe v2** (MiniMax has no STT API). LLM = **TokenHub** `glm-5.2` (title+tags) + `glm-5v-turbo` (vision), via the `openai` npm client pointed at TokenHub (NOT openai.com).
4. Title = deterministic `YYYYMMDD-HHMM` + LLM title, **summarizing in Boyang's voice, bilingual `<zh> <en>`**, code-sanitized + byte-capped. Full title also in `TITLE标题`.
5. Properties = dynamic bilingual `English中文` tags + `CREATEDAT: YYYY-MM-DD HH:MM:SS`.
6. Two Telegram replies per input; ACK is atomic-with-persistence; per-chat serial queue = linear/deterministic.

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
