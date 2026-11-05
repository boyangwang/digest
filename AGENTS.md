# AGENTS.md — Digest (v1.3: Telegram + Lark)

`CLAUDE.md` is a symlink to this file - edit this one.

## 📍 Status + Next Steps · (updated 2026-11-05)
- **Working on:** nothing in flight. v1.3 - voice durability (ACK-first, 3-minute retry
  ladder, never silent) + the **Lark transport** ("Sleep Digest" app) - merged to `main`
  (boyangwang/digest#2, 2026-11-05).
- **Status:** ✅ `npm test` green, E2E-verified on live Telegram + live Lark, and **deployed
  from the `main` checkout** (both launchd services).
- **Done recently:** root cause of the silent voice notes found and fixed (Node's 250ms Happy
  Eyeballs window, decision 15); media now ACKed before any network call (decision 16);
  Lark bot created, configured, published, deployed (decisions 18-20).
- **Known blockers:** **TokenHub key is dead** (401 since 2026-09-13; both vault keys,
  incl. `_EMAILTRIAGE`): every title since then is the "spoken ..." fallback and image captions
  fail. Needs a new key from the Tencent console (Boyang), then `secret-put
  TENCENT_TOKENHUB_API_KEY` + `bash launchd/deploy.sh`. Obsidian must stay running on the Mini
  for Sync.
- **Next step:** Boyang: send one real voice note on Lark and one on Telegram; approve → merge
  the PR to `main` (the services keep running from this checkout either way).

### Log (newest first)
- 2026-11-05 — v1.3: voice notes that "just gave up" traced to `fetch failed` x7 in the log
  (2026-09-24..10-29) = Happy Eyeballs connect timeouts; fixed (net-tuning) and the pipeline
  rebuilt ACK-first with download/STT retry ladders, retry notices, Retry button, restart
  resume. Lark transport added; Sleep Digest app `cli_aa662277abb8df05` created + published
  (v1.0.0, v1.0.1 adds the card callback). Live E2E: Telegram 112s voice (download 3.8s, STT
  4.4s), Lark text / image / audio / Retry / Done. 148 tests.
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
5. Properties = **dynamic, LLM-chosen** bilingual `English中文` tags (NOT a fixed schema) + always `CREATEDAT: YYYY-MM-DD HH:MM:SS` + always `TITLE标题` (+ the provenance stamp, decision 12).
6. Two Telegram replies per input, both carrying the serial `#N`; ACK = `✓ ACK #N` with the inline Done button attached; ACK is atomic-with-persistence; per-chat serial queue = linear/deterministic.
7. Body blocks use IM-style **inline** timestamps: `**MM-DD HH:MM** content` (same line, month-day-hour-minute).
8. **Transcription runs OUTSIDE the per-chat serial queue** (`src/transcriptions.js`). The
   block's slot is reserved by `appendBlock` at arrival, so a late transcript still renders in
   order; holding the queue for the retry budget would only stall the next message. `/done`
   waits on the in-flight set, bounded by `STT_TOTAL_BUDGET_MS`, then compiles anyway with the
   retryable marker. Manifest read-modify-writes take a per-chat lock in `store.js` because the
   queue no longer serializes them. *(2026-11-05: `/done` now waits OUTSIDE the queue, bounded by
   `JOB_TOTAL_BUDGET_MS`; see 17.)*
9. `npm run retranscribe` **regenerates `TITLE标题` + tags** after a successful recovery (they
   were generated once at `/done` from `(no transcript)`), preserving `CREATEDAT` verbatim. The
   filename is deliberately NOT renamed by default — a filesystem rename breaks Obsidian links;
   `--rename` opts in.
10. **The recovery script's eligibility gate is a hard precondition.** Measured 2026-08-22: of
   577 notes in the digest folder, 494 have NO frontmatter (hand-written), 63 carry a legacy
   `创建时间`/`分类`/`主题` schema, 19 carry the current schema - i.e. the folder is mostly NOT
   ours. The gate itself is decision 12 (stamp AND marker); everything else is refused with a
   reason and zero bytes changed. Never create frontmatter, never migrate a legacy note, never
   let an explicitly named attachment bypass the gate.
11. Frontmatter rewriting **MERGES** into the existing block (`parseDocument`): only `TITLE标题`
   and the generated tag keys are written. Hand-added keys keep their VALUES and comments —
   but yaml v2 re-serializes the block, so layout may be normalized (`aliases:\n- x` comes back
   indented). `--replace-properties` is the only way a key is removed.
12. **Provenance is stamped, never inferred** (`src/provenance.js`). Every note the bot creates
   carries `GENERATOR生成器: digest/1` as the third fixed property. `retranscribe` — and any
   future tool that edits a note in place — may act only if the note carries BOTH that stamp
   AND the failed-transcript marker. No flag or env var overrides the stamp check. Notes
   predating it are untouchable on purpose; do NOT retro-stamp them.
13. **Recovery is one atomic write**: transcript + regenerated frontmatter together. If the
   title model gives up, the transcript is DISCARDED, not committed — committing it consumes
   the marker and strands the note with metadata nothing can fix. (This supersedes the earlier
   "never roll back the transcript" rule.)
14. `generateTitleAndTags` runs on the SAME ladder as STT (`src/retry.js`, `LLM_*` knobs in
   `config.js`). It sits on the recovery critical path, so a single blip must not cost a run.
15. **`src/net-tuning.js` is the FIRST import of every entry point.** Node >=20 gives each
   address 250ms to connect (Happy Eyeballs); the Mini has no IPv6 route and ~240-300ms RTT to
   api.telegram.org, so fresh connections died as `fetch failed`. Measured: 250ms -> 0-6/20
   succeed, 3000ms -> 20/20 (`scripts/probe-telegram-connect.mjs`). nodejs/node#54359.
16. **Media is stored by REFERENCE and ACKed before any network call** (Telegram `file_id`,
   Lark `message_id`+`file_key`). Download and processing run off the queue in `src/jobs.js`.
   Supersedes "ACK only after the bytes are persisted" for media: the reference is the durable
   thing, and Boyang asked for the ACK first.
17. **Attempt timeouts are 3 minutes** (STT and download; Boyang's ruling 2026-11-05, was 60s).
   Every retry is announced in chat, a long job says "still working" once, and a give-up is an
   explicit apology with a 🔁 Retry button. `block.job` state makes jobs resumable on restart.
   `/done` waits for in-flight jobs OUTSIDE the queue (`src/commands.js`), so new messages are
   still ACKed meanwhile.
18. **Lark runs as a separate launchd service** (`network.deardiary.digest-lark`,
   `src/lark-index.js`) over the SDK long connection: a fault in one transport never stops
   the other, and each process owns its own log file. Lark chats are `lark_<chat_id>` in the
   shared pending store; notes land in the same vault folder.
19. **Lark REST is plain fetch** (`src/lark.js`), the SDK is used only for the WS client +
   dispatcher - timeouts/retries stay ours and tests inject `fetchImpl`. Allowlist =
   Boyang's open_id **for this app** (`BOYANG_LARK_OPEN_ID`; open_ids are per-app); the app's
   availability is also limited to him. Lark re-keys a file when it is sent: always use the
   `file_key` inside the received message.
20. Sleep Digest app: `cli_aa662277abb8df05` (tenant Brain Computer Interface), created via
   the `lark-cli config init --new` page, events/callbacks in long-connection mode
   (`im.message.receive_v1`, `card.action.trigger`). Credentials in the vault as
   `DIGEST_LARK_APP_ID` / `DIGEST_LARK_APP_SECRET`. Console automation helpers:
   `scripts/lark-console/`.

## What this is
Telegram bot: send text/voice/photos/files in any order → tap ✅ Done / `/done` → one bilingual
Markdown note in the Obsidian vault. See `README.md` and `specs/prd-digest-v1.2.md`.

## Stack
Node ≥22 (ESM), grammY (Telegram), `@larksuiteoapi/node-sdk` (Lark long connection only),
`openai` client → Tencent TokenHub, `yaml`. STT = ElevenLabs Scribe v2
→ OpenAI `gpt-4o-transcribe` (plain `fetch`/`FormData`, no SDK).
Tests: `node --test` via `npm test`, which loads `test/setup.mjs` with `--import` to pin
`DIGEST_LOG_PATH` at a temp dir — otherwise the suite appends to the live app log.

## Key commands
```bash
npm test                          # unit + offline E2E
secret-run node src/index.js      # run locally (vault keys injected)
bash launchd/deploy.sh            # deploy BOTH services (or: deploy.sh telegram | lark)
node scripts/probe-telegram-connect.mjs 3000   # can this box open fresh connections to Telegram?
tail -f ~/.local/share/digest/digest.log           # the APP's log: rotated, 0600, the one to read
tail -f ~/.local/share/digest/digest-service.log  # launchd's net; NON-EMPTY = something escaped the logger
#   NEVER /tmp — macOS purges it under the running service.
#   These two paths must NEVER be the same file (see conventions below).
secret-run npm run retranscribe -- --check           # FREE census: eligible vs refused notes
#   eligible = carries GENERATOR生成器 stamp AND a failed-transcript marker
#   --check NAMES any refused note that still carries a marker — the only refusal
#   that is actionable ("needs recovery, not ours to touch"); the un-marked bulk is
#   only counted, never listed.
secret-run npm run retranscribe -- --all             # recover any failed transcript
#   exit 0 = everything it was ALLOWED to do succeeded (notes the gate blocked are
#   named loudly but are NOT failures — that refusal is permanent by design);
#   exit 1 = a real failure (STT exhausted, write error, unknown attachment, unreadable vault);
#   exit 2 = bad usage.
#   --dry-run is NOT free: 1 STT + 1 title/tag LLM call per eligible note
```

## Conventions
- **Timezone:** all timestamps SGT (UTC+8) via `util.js`. **Never** hand-format dates elsewhere.
- **Filename is code-sanitized, never LLM-controlled**; byte-capped ≤200 bytes (APFS limit).
- **ACK only after persistence** (`store.appendBlock`/`saveAttachment` resolve first).
- **LLM/STT/vision must degrade gracefully** — a failure never loses input; `/done` can be retried.
- **`finalizeDigest` holds the store's per-chat lock across read → compile → write → clear**
  (`withPendingDigest`). Splitting those across separate locks lets a straggler transcript's
  `updateBlock` succeed — telling the user the words were saved — into a manifest that was
  already compiled without them and is about to be deleted.
- **All transcription goes through `transcribe()` in `src/stt.js`** — never call a vendor
  directly. Retry/rotation/attempt-cap are data-driven from `config.js`; add a vendor in
  `stt-providers.js` and name it in `STT_PROVIDER_ORDER`.
- **TWO log files, one writer each.** `DATA_DIR/digest.log` is the app's, written only by
  `src/log.js`, rotated and 0600. `DATA_DIR/digest-service.log` is launchd's capture of
  stdout/stderr and exists to catch what never reaches the logger (an early FATAL, an uncaught
  stack) — if it is non-empty, read it. They must NEVER be the same path: one shared inode plus
  rename-rotation orphans launchd's fd, and the crash output is exactly what goes missing. A test
  named "THE SERVICE AND THE APP MUST NEVER SHARE A LOG FILE" enforces it. `log.js` therefore
  mirrors to the console only on a TTY (or `DIGEST_LOG_CONSOLE=1`), so the launchd-owned file
  never gets an unrotatable duplicate of the private log — but it always falls back to the
  console if the file write fails, because losing the file must not mean losing the line.
- **The log is private and bounded** (`src/log.js`): created mode 0600 in a 0700 dir and
  size-capped with `LOG_MAX_BYTES`/`LOG_RETAIN` rotation, because it deliberately records raw
  model output (that is what makes an unparseable response diagnosable) and so holds
  journal-derived content. Rotation must never throw — logging never crashes the bot.
- **Never default a log or state path under `/tmp`** — macOS purges it while launchd holds the
  handle, so the history goes to a deleted inode. `DATA_DIR` is the durable home. Corollary:
  **tests must never write to the real log** — `test/setup.mjs` pins `DIGEST_LOG_PATH`.
- **The STT attempt timeout must cover the response BODY, not just the headers** — `fetch()`
  resolves on headers, so an abort timer cleared there lets a stalled body outlive the budget.
- Output vault: `~/Documents/NotesVault/Heresy-Anthology/digest/` (+ `ATTACHMENTS/`).
- Secrets: `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`, `TENCENT_TOKENHUB_API_KEY`,
  `DIGEST_LARK_APP_ID`, `DIGEST_LARK_APP_SECRET` (sops vault).
- **Platform code stays in the adapters** (`src/bot.js` Telegram, `src/lark.js` Lark). The
  core (`ingest` / `jobs` / `commands` / `finalize`) only sees `reply(text, {done, retry,
  html})` and `fetchMedia(source)`. A new platform = a new adapter + entry point.
- **Never fail silently.** Every path that can drop an input ends in a chat message; sends go
  through `sendWithRetry` (`src/send.js`). A test that feeds a real update through the adapter
  (`test/telegram-voice-e2e.test.js`) pins it.

## The title voice
`prompts/title-and-tags.md` — authored from Boyang's 552 real note titles. It IS the reusable
prompt fed to `glm-5.2`. Keep it as the single source of truth for title/tag style.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
