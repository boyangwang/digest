# Findings — "Daily part" vs "Doc-creation part" (separation analysis)

> Written 2026-07-07 while studying the repo, ahead of Boyang's separation spec.
> Purpose: name the two responsibilities the bot currently fuses, show exactly where
> they are coupled today, and give the JS rewrite a clean seam to build against.
> This is an *analysis* doc — no behavior has changed yet.

---

## The two responsibilities

The bot secretly does **two different jobs** that happen to share a Telegram
front-end, a collector, and a `/sleep` trigger:

### A. The DAILY part — capture + journal (the nightly digest loop)
The per-night journaling function. Its output is **one dated digest file** in the
Obsidian vault (`Doudou-Digest/YYYY-MM-DD-HHMM.md`).

- **Trigger / timing** — `scheduler.py`: 22:30 reminder, 30-min nudges, `/sleep` stop.
- **State machine** — `IDLE → /digest → ACTIVE → /sleep → IDLE` (in `main.py` + `recorder.py`).
- **Collect conversations** — `collector.py` (read OpenClaw `.jsonl` transcripts) →
  `collection_engine.py` (parallel, retriable) → `llm.py` (`compose_summary`, per-session
  bilingual summary via `openclaw agent`).
- **Capture Boyang's live input** — text recap, voice+STT (`stt.py`), photos, files —
  all appended verbatim/timestamped to the digest (`recorder.append_*`).
- **Write the digest file** — `recorder.py` (YAML frontmatter + `# Doudou's Summary` +
  `# Boyang's Recap`, append-only, atomic writes, coverage-timestamp chain).

**Nature:** high-frequency, interactive, cheap, deterministic. Runs many times a night
(every `/digest`, every message). Product = the *journal entry*.

### B. The DOC-CREATION part — knowledge extraction into the workspace
The "turn tonight's conversations into durable, structured project docs" function. Its
output is **edits across the OpenClaw workspace** (`~/.openclaw/workspace`), not the digest.

- **Everything in `reflection.py`.** One Opus agent reads the full conversation dump and
  writes to 8 destinations in the workspace:
  `memory/facts/<date>.md`, `memory/feedback-lessons.md`, `INCIDENTS.md`,
  `memory/compliments.md`, `memory/decisions/<date>.md`, `KANBAN.md`,
  `memory/ideas.md`, `memory/<date>.md` + `TOOLS.md`; proposes `RULES.md` changes;
  then `git add -A && commit && push` the workspace repo.
- Returns a `# 🪞 Nightly Reflection` markdown report + a git diff stat/images.

**Nature:** low-frequency (once per night at most), expensive (Opus, up to 30-min timeout),
non-deterministic, mutates a *separate git repo*. Product = the *knowledge base / project docs*.

---

## Where they are coupled today (the seam to cut)

The two jobs are welded together in **exactly one place**: `cmd_sleep` in `main.py`
(lines ~553–659). A single `/sleep` does, in order:

1. `mark_sleep()` + a final collection to advance coverage — **DAILY**
2. `run_reflection(...)` — **DOC-CREATION** (the whole of B)
3. `append_reflection(report)` — **couples the two outputs**: the doc-creation report is
   written *back into the daily digest file*, then also sent to Telegram
4. `finalize()` — **DAILY** (sets `status: final`)

Secondary coupling points:

- **`recorder.py`** owns both the daily file ops *and* `append_reflection` /
  `replace_reflection` — i.e. the daily-file module also knows about doc-creation output.
- **`/reflect` command + `callback_reflect_accept`** (`main.py` ~733–922) is a second,
  independent entry into the doc-creation job (re-run reflection on a past digest, preview →
  accept). This one is *already* somewhat separated (it does NOT finalize), and is the
  closest existing model for a decoupled design.
- **Shared plumbing** (not really coupling, just reuse): both jobs use `collector.py` to
  read transcripts and both save conversation dumps into the same `transcripts/` dir.

### One-line summary of the coupling
> `/sleep` = "finalize the journal" **and** "regenerate the knowledge base" in one
> irreversible keystroke, and the knowledge-base report is stapled into the journal file.

---

## Why separating is reasonable (design read, not a decision)

- **Different cadence & cost.** Daily is cheap/frequent; doc-creation is expensive/rare.
  Fusing them means every `/sleep` pays the Opus + git-push cost, and a doc-creation failure
  risks the daily finalize (today it's guarded by try/except + "finalize anyway", but the
  concern is real).
- **Different data stores.** Daily writes the Obsidian vault; doc-creation writes the
  OpenClaw workspace git repo. Two stores, two lifecycles.
- **Different failure semantics.** A missed journal entry ≠ a missed knowledge extraction;
  each wants its own retry/idempotency story.
- **The JS rewrite** (next version — see below) is the natural moment to land the seam as a
  real module boundary rather than a shared `cmd_sleep`.

---

## Context: Python → JS rewrite is imminent

Boyang has stated the **next version moves this codebase from Python to JavaScript (Node)**
(consistent with the boyang-dev "JavaScript first" default). Implications for the separation
work:

- The separation should be designed as a **clean module boundary** ("daily" vs "doc" as two
  cohesive units with a narrow interface), so it survives the port instead of being a
  Python-only refactor thrown away in the rewrite.
- Worth deciding whether separation lands **in the current Python code first** (to de-risk
  behavior) or is **done as part of the JS rewrite** (green-field, cleaner). ← open question
  for the spec.

---

## Open questions for the spec (to confirm with Boyang)

1. **What does "separate" mean operationally?** Different *triggers* (e.g. `/sleep` finalizes
   the journal only; doc-creation moves to its own command / schedule)? Different *processes /
   services*? Different *repos*? Or just clean *code modules* within one bot?
2. **Should the reflection report still be appended into the digest file** (current behavior),
   or should the journal stop carrying doc-creation output entirely?
3. **Do daily and doc-creation stay in one bot** (shared Telegram + collector) **or split into
   two deployables**?
4. **Sequencing vs the JS rewrite** — refactor Python now, or fold the separation into the JS
   port?
5. **Naming** — what are the two halves called in the new design (e.g. "Journal" vs
   "Reflection/Knowledge")?

---

## File-to-responsibility map (quick reference)

| File | Daily | Doc-creation | Notes |
|---|:---:|:---:|---|
| `scheduler.py` | ✅ | | timing, nudges |
| `collector.py` | ✅ | (shared) | reads transcripts; doc-creation also uses it |
| `collection_engine.py` | ✅ | | parallel per-session summaries |
| `llm.py` | ✅ | | `compose_summary`, `compose_nudge` |
| `stt.py` | ✅ | | voice → text |
| `recorder.py` | ✅ | ⚠️ | daily file CRUD **+** `append/replace_reflection` (leak) |
| `reflection.py` | | ✅ | the entire doc-creation job |
| `main.py` `cmd_sleep` | ✅ | ✅ | **the fused trigger — primary seam** |
| `main.py` `/reflect` + callback | | ✅ | already-decoupled doc-creation entry (model to follow) |
| `config.py` | ✅ | (paths) | `WORKSPACE_DIR` lives in `reflection.py`, not here |
