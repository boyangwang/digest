# PRD — Digest v1.2 (multimodal capture → single Obsidian note)

> **Status:** 🟡 Spec locked from Boyang's answers 2026-07-07 — awaiting final GO before code.
> **Created:** 2026-07-07
> **Big pivot:** v1.2 is a **JavaScript (Node) rewrite** — treat as an almost-new project, reusing
> only the Telegram-bot idea. Same repo (`digest`); old Python archived to a branch.
> **Source of truth:** Boyang's spec + answers (`prompt_verbatim.md`) + sample `docs/samples/` +
> title corpus study (`prompts/title-and-tags.md`).

---

## 1. What Digest v1.2 IS
A **personal multimodal capture bot**. I send inputs to a Telegram bot; when I press **Done**, it
compiles everything I sent — in exact arrival order — into **one bilingual Markdown note** in my
Obsidian vault, with an LLM-generated title and tags.

**Dropped entirely (separate, dormant service — archived, not deleted):** OpenClaw-conversation
collection, nightly reflection/knowledge-extraction, scheduler / 22:30 reminder / bedtime nudges,
and the old `# Doudou's Summary` / `# Boyang's Recap` format. Digest processes **only my inputs**.

## 2. Stack & repo (DECIDED)
- **Language: JavaScript (Node, ESM).** ← the rewrite happens now, in v1.2.
- **Repo:** keep `digest` (`git@github-digest:boyangwang/digest.git`). Preserve current Python on
  branch **`archive/python-v1`**; **main is overwritten** with the JS app (docs/specs kept).
- **Telegram lib:** grammY (proposed). **LLM SDK:** `openai` npm pointed at TokenHub Chat
  Completions. **STT:** ElevenLabs Scribe v2 via `fetch`. **Deploy:** launchd
  **`network.deardiary.digest`** (retires `com.digest-bot`), secrets via sops `exec-env` (all three
  keys already in vault: `DIGEST_BOT_TOKEN`, `ELEVENLABS_API_KEY`, `TENCENT_TOKENHUB_API_KEY`).

## 3. Interaction model (DECIDED)
- **No `/start`.** Any input auto-starts a digest if none active; else appends.
- **Finish:** `/done` command **or** an inline "✅ Done / 完成" button.
- **Two messages per input, linear & deterministic:**
  1. **ACK** immediately — *atomic with persistence* (sent only after the input is durably saved).
  2. **Processed** — after per-input work (STT, save, image caption) completes.
  Inputs keep arriving into an ordered queue; if my next input comes before the 2 replies land,
  it just queues — order is fixed at persist time.
- **Explicit finish only** (no idle timeout). Empty finish (nothing captured) → ignored.
- State: `IDLE → (any input) → ACTIVE → (/done|button) → compile → IDLE`.

## 4. Input handling — exact order preserved (DECIDED)
| Type | Handling |
|---|---|
| **Text** | verbatim |
| **Voice/audio** | attach original audio embed **then** its STT transcript directly below |
| **Image** | attach + embed |
| **File** | attach + embed/link |
- **STT:** ElevenLabs Scribe v2 (bilingual zh+en #1; MiniMax has no STT API — TTS only).
- **Images get a vision caption at input time** (see §6) so the processed-message can confirm and
  the caption can feed title/tags at compile.

## 5. Storage & format (DECIDED)
- **Dir:** `/Users/claw/Documents/NotesVault/Heresy-Anthology/digest/` (Obsidian-synced).
- **Attachments:** `Heresy-Anthology/digest/ATTACHMENTS/`, embedded as
  `![[Heresy-Anthology/digest/ATTACHMENTS/<file>]]` (matches current vault convention).
- **Filename:** `YYYYMMDD-HHMM <sanitized title>.md`. Timestamp = **deterministic code**, up to the
  minute. Full title glued as **`<zh> <en>`**. Title portion **sanitized by CODE** (strip
  `: / \ | # ^ [ ]`, collapse ws) and **byte-capped ≤~200 UTF-8 bytes** (APFS 255-byte limit).
- **Markdown properties (frontmatter):** LLM-generated **dynamic bilingual** key/values in the
  `English中文` inline form (e.g. `Category分类: Life生活`) **+** deterministic
  `CREATEDAT: 2025-09-05 11:53:52` + `TITLE标题` holding the full untruncated `<zh> <en>` title.
- **Attachment naming:** keep Telegram's original filename where present, else
  `<YYYYMMDD-HHMMSS>-voice.ogg` / `-img.jpg` / `-<orig>`.
- **Body:** ordered blocks, each prefixed by a full `YYYYMMDD-HHMM` timestamp line (date included,
  since one digest can rarely span days). Voice = audio embed then transcript blockquote; image/file
  = embed + optional caption; text = verbatim.

## 6. LLM (DECIDED)
Backend = **Tencent TokenHub** (OpenAI Chat Completions, `TENCENT_TOKENHUB_API_KEY`).
- **Title + tags:** `glm-5.2` (best open model; 1M ctx). Params: `thinking:{type:"enabled"}`,
  `reasoning_effort:"high"`, **`max_tokens` ≈ 100k as a generous CAP** (not a target — output scales
  to input; a one-liner gets a short title). Prompt = `prompts/title-and-tags.md`.
- **Vision (images):** `glm-5v-turbo` on the **same** TokenHub gateway (native multimodal;
  best-available *vision-capable* model, since the top open text model has no vision). Each image →
  caption; captions + text + transcripts feed the `glm-5.2` title/tags call.
- **No separate summary section** — the (longer, ≤240-char) title carries that weight. Note holds
  **text + attachments + title + tags** only.
- **Everything LLM-generated is bilingual** (title both languages; tag keys+values `English中文`).

## 7. Title voice (C6 — title = summary)
Reusable, code-driven prompt in `prompts/title-and-tags.md` (authored by Opus 4.8 from the 552-title
corpus). **v1.2 change:** keep his exact voice/tone/word-preference **but make the title longer and
genuinely summarize the entry** (title-as-summary; no separate summary section), ≤240 chars. Model
returns JSON `{title_zh, title_en, tags[]}`; code prepends timestamp, sanitizes, byte-caps, stores the
full `<zh> <en>` title in `TITLE标题`.

## 8. Confirms — ALL LOCKED (2026-07-07)
C1 Telegram lib = **grammY** · C2 title glue = **`<zh> <en>`** · C3 `CREATEDAT: 2025-09-05 11:53:52` ·
C4 attachment naming = original-name-else-`<ts>-voice.ogg`/`-img.jpg`/`-<orig>` · C5 launchd
**`network.deardiary.digest`** · C6 title summarizes in his voice, longer.

*On GO: create `archive/python-v1`, scaffold the JS app on main, write tests, implement, verify
end-to-end on one real multimodal digest, deploy.*
