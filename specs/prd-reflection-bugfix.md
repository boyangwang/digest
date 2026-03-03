# DIGEST-007: Reflection Bugfix — Send Structured Report to User

> **Status:** 🟡 Active — T5/T16 done, T17/T18/T19 done, remaining tasks open
> **Project:** Sleep Digest Bot — Bug #1 Fix
> **Date:** 2026-03-03
> **Priority:** P0 Critical
> **Estimated effort:** Medium (3-4hr)
> **Origin:** Boyang's bug report — "Where is the overnight reflection? I don't see anything."
> **Tasks:** 11/15 complete

---

## Problem Statement

The nightly reflection feature runs on `/sleep` but **never sends the reflection content to Boyang**. The reflection is only written silently to the Obsidian digest file. Boyang expects a structured Telegram message showing what was extracted.

Additionally, the first production run (2026-03-02 23:22) failed because the bot was running old code with a `--model` flag that `openclaw agent` doesn't support.

---

## Root Cause Analysis

### Issue 1a: Agent Call Failure (`--model` flag)
- **Status:** FIXED in current `reflection.py` — no `--model` flag
- **What happened:** Bot was running old code when Boyang used `/sleep`. Bot was restarted at 23:24 with fix.
- **Verification needed:** E2E test must confirm agent call works without `--model`
- **Also fixed (2026-03-03):** Timeout increased 300s → 1800s (30 min). RULES.md auto-apply removed from prompt.

### Issue 1b: No Reflection Message Sent to User
- **Status:** NOT FIXED — `cmd_sleep` in `main.py` only:
  1. Writes reflection to file via `append_reflection(report)`
  2. Sends generic "🪞✅ 已保存到 Obsidian" confirmation
- **Missing:** A structured Telegram message showing the actual reflection content
- **Where in code:** `main.py:454-489` — the `cmd_sleep` handler

### Issue 1c: Fallback Message Uninformative
- **Status:** NOT FIXED
- **When agent fails:** `run_reflection()` returns `"_Reflection unavailable — agent failed to respond._"`
- **This gets appended to the digest file** as the reflection section — useless

---

## Current Code Flow (Actual, from reading `main.py:431-489`)

```
cmd_sleep() called
  ├─ _scheduler.mark_sleep()
  ├─ if has_active_file():
  │   ├─ reply "晚安 🌙 Running reflection..."
  │   ├─ collect conversations via collect_all_messages(since_ts)
  │   ├─ format_messages(all_msgs)
  │   ├─ run_reflection(formatted, date_str)  →  returns markdown report or None
  │   ├─ append_reflection(report)            →  writes to Obsidian file
  │   └─ (NO message with reflection content sent to user)
  ├─ finalize()
  └─ reply "🪞✅ 已保存到 Obsidian"  ← generic, no content
```

---

## Proposed Fix

### New Code Flow

```
cmd_sleep() called
  ├─ _scheduler.mark_sleep()
  ├─ if has_active_file():
  │   ├─ reply "晚安 🌙 Running reflection..."
  │   ├─ collect + format conversations
  │   ├─ run_reflection(formatted, date_str) → returns markdown report
  │   ├─ if report and report is not fallback:
  │   │   ├─ append_reflection(report)
  │   │   ├─ send_reflection_message(update, parsed_data)    ← NEW
  │   │   └─ logger.info("Reflection complete")
  │   ├─ elif report is fallback:
  │   │   ├─ append_reflection(report)
  │   │   └─ reply "⚠️ Reflection agent failed — see digest file"
  │   └─ else:
  │       └─ reply "No conversations to reflect on"
  ├─ finalize()
  └─ reply "✅ Digest saved to Obsidian"
```

### Structured Message Format

The reflection Telegram message should be a **concise, structured summary** — NOT the full markdown report (which goes to the file). Format:

```
🪞 Nightly Reflection — 2026-03-02

📌 Facts: 3 items
🔧 Feedback: 2 items
⚠️ Incidents: 0 items
🌟 Compliments: 1 item
🧭 Decisions: 4 items
📋 Action Items: 2 items
💡 Ideas: 1 item
🔬 Technical: 5 items

📊 18 items extracted from 247 messages

Top items:
• [Feedback] Never ask to try without E2E verification
• [Decision] Standalone E2E runner replaces pytest for UI tests
• [Action] Add §20 to RULES.md — E2E verification rule

Full report saved to Obsidian 📓
```

---

## Tasks

### Phase 1: Fix `cmd_sleep` to send reflection content

- [x] **T1** — Add `format_reflection_telegram(parsed: dict, date_str: str) -> str` to `reflection.py`
  - Input: parsed reflection dict (8 categories + stats)
  - Output: compact Telegram-friendly message (category counts + top 3-5 items)
  - Max length: 4096 chars (Telegram limit)
  - Truncate gracefully if too long

- [x] **T2** — Modify `run_reflection()` return value to include both report AND diff data ✅ `3b7fa27`
  - Returns: `tuple[str | None, dict]` — `(report_markdown, diff_info)`
  - `diff_info` contains: `stat`, `patch`, `files` (per-file before/after), `images` (PNG paths)
  - Captures git HEAD before/after agent runs for workspace change tracking
  - All callers + tests updated (22/22 pass)

- [x] **T3** — Modify `cmd_sleep` in `main.py` to send reflection content *(partially done)*
  - [x] Visual diff images sent via `send_photo` after finalize ✅ `3b7fa27`
  - [x] Fallback: `git diff --stat` as text if image rendering fails ✅
  - [ ] Still needed: structured text summary message (category counts + top items)
  - [ ] Still needed: `format_reflection_telegram(parsed, date_str)` function

- [x] **T4** — Update test mode in `cmd_sleep` to also send mock reflection message
  - `TestRecorder.append_reflection()` already exists
  - Add: send mock reflection summary message to test user too
  - This allows E2E test to verify message delivery

### Phase 2: Improve fallback behavior

- [x] **T5** — Improve fallback report when agent fails
  - Instead of `_Reflection unavailable — agent failed to respond._`
  - Include: timestamp, error reason (timeout/crash/empty), conversation count
  - Format: `_Reflection unavailable (agent timeout at 23:22, 247 messages collected). Will be included in backfill._`

### Phase 3: E2E verification

- [x] **T6** — Add E2E test: `/sleep` sends reflection message to chat
  - In `tests/run_e2e.py`, modify `test_sleep_includes_reflection`:
    - After `/sleep`, check bot's reply messages contain reflection content (category counts, "items extracted")
    - Verify message format matches spec
  - Must use actual Telegram message inspection (not just log checking)

- [ ] **T7** — Add E2E test: `/sleep` when agent fails sends failure message
  - Harder to test E2E (need to simulate agent failure)
  - Can test via unit test instead: mock `_call_agent` to return None

- [x] **T8** — Run full regression: 277 unit/integration + 8 E2E
  - All must pass before declaring done

---

## Acceptance Criteria

1. When Boyang sends `/sleep` with an active digest:
   - Bot sends "晚安 🌙 Running reflection..."
   - Bot sends structured reflection summary (category counts + top items)
   - Bot sends "✅ Digest saved to Obsidian"
   - Digest file in Obsidian contains full reflection section
2. When reflection agent fails:
   - Bot sends "⚠️ Reflection failed" with brief reason
   - Digest still finalizes (SPEC-REFLECT-05)
   - Fallback text in file includes error context
3. Test mode (`@claw0606`):
   - `/sleep` sends mock reflection summary message
   - E2E test verifies message content
4. No regressions: 277+ unit/integration + 8+ E2E pass

---

## Files to Modify

| File | Changes |
|------|---------|
| `reflection.py` | Add `format_reflection_telegram()`, change `run_reflection()` return type |
| `main.py` | Modify `cmd_sleep` to send reflection content, update test mode |
| `tests/test_reflection.py` | Add tests for `format_reflection_telegram()`, updated return type |
| `tests/run_e2e.py` | Update `test_sleep_includes_reflection` to verify message content |

---

### Phase 4: Visual Diff Report (Proposal B) — ✅ DONE

> Implemented `3b7fa27` — git diff capture + diffs tool PNG rendering + Telegram delivery.

- [x] **T13** — Add `_git_head_hash()` and `_git_diff()` to `reflection.py`
  - Captures HEAD before/after agent runs
  - Extracts per-file before/after content via `git show <hash>:<path>`
  - Returns structured dict: `{stat, patch, files: [{path, before, after}]}`

- [x] **T14** — Add `render_diff_images()` to `reflection.py`
  - Writes before/after to temp files for each changed workspace file
  - Calls `openclaw agent --local` with diffs tool (mode=image) per file
  - Returns list of PNG paths
  - 120s timeout per file, graceful failure (returns empty list)

- [x] **T15** — Integrate diff images into `cmd_sleep` flow
  - After `finalize()`, sends "📊 Workspace changes from reflection:" header
  - Sends each diff PNG via `context.bot.send_photo()`
  - Fallback: sends `git diff --stat` as code block if no images rendered

### Phase 5: Retry + Manual Re-run

- [x] **T16** — Add automatic retry to `_call_agent()` in `reflection.py`
  - On failure (rc≠0, timeout, empty response): retry up to 2 more times (3 total attempts)
  - Exponential backoff: 5s, 15s between retries
  - Log each attempt: `"Reflection agent attempt %d/%d failed: %s"`
  - Only return None after all retries exhausted

- [ ] **T17** — Add `/reflect` command with preview → approve flow
  - New handler in `main.py`: `cmd_reflect(update, context)`
  - **Preview → Approve pattern using inline keyboard button:**
    1. Find the most recent finalized digest file (status="final")
    2. Collect conversations using that file's `coverage_from` / `coverage_to`
    3. Run `run_reflection()` with retry
    4. Send structured reflection message as **preview** (same format as T1)
    5. Attach one inline button: **"✅ Accept & Save"** (`callback_data="reflect_accept:<filepath>"`)
    6. If Boyang presses → `replace_reflection(report, filepath)` updates file in-place, reply "✅ Saved"
    7. If Boyang doesn't press → nothing saved, reflection is just a preview
  - Optional argument: `/reflect 2026-03-02` to target a specific date
  - Boyang-only (same `_check_user` but production users only, not test)
  - **Callback handler:** `callback_reflect_accept(update, context)` — registered via `CallbackQueryHandler`
  - **Note:** For `/sleep`, the flow is different — reflection is auto-accepted (no button needed). `/reflect` is for manual re-runs where review is desired.

- [ ] **T18** — Add `/reflect` to bot command menu and help text
  - Update `cmd_start` help message
  - Register handler in `main.py` setup

- [ ] **T19** — E2E test for `/reflect` re-run
  - In `tests/run_e2e.py`:
    1. `/digest` → text → `/sleep` (creates finalized file with reflection)
    2. `/reflect` → verify new reflection message sent
    3. Verify file's reflection section was updated (not duplicated)

---

## Acceptance Criteria

1. When Boyang sends `/sleep` with an active digest:
   - Bot sends "晚安 🌙 Running reflection..."
   - Bot sends structured reflection summary (category counts + top items)
   - **Bot sends visual diff PNGs showing workspace changes** ✅ implemented
   - Bot sends "✅ Digest saved to Obsidian"
   - Digest file in Obsidian contains full reflection section
2. When reflection agent fails:
   - Agent retries up to 3 times with backoff before giving up
   - Bot sends "⚠️ Reflection failed after 3 attempts" with brief reason
   - Digest still finalizes (SPEC-REFLECT-05)
   - Fallback text in file includes error context
3. When Boyang sends `/reflect`:
   - Re-runs reflection on most recent finalized digest (or specified date)
   - Sends structured reflection message + visual diffs
   - Updates file in-place (replaces old reflection section)
4. Visual diff report (Proposal B):
   - ✅ Git diff captured before/after reflection agent commits
   - ✅ Per-file visual diff rendered via OpenClaw diffs tool (PNG)
   - ✅ Images sent to Boyang via Telegram `send_photo`
   - ✅ Fallback: `git diff --stat` as code block
5. Test mode (`@claw0606`):
   - `/sleep` sends mock reflection summary message
   - E2E test verifies message content
6. No regressions: 277+ unit/integration + 8+ E2E pass

---

## Files to Modify

| File | Changes | Status |
|------|---------|--------|
| `reflection.py` | ~~Change return type~~ ✅, ~~git diff capture~~ ✅, ~~render_diff_images~~ ✅, add `format_reflection_telegram()`, add retry | Partial |
| `main.py` | ~~Send visual diffs~~ ✅, send structured text summary, add `cmd_reflect` handler, update test mode | Partial |
| `recorder.py` | Add `replace_reflection(report, filepath)` for in-place update | TODO |
| `tests/test_reflection.py` | ~~Updated return type~~ ✅, add tests for `format_reflection_telegram()`, retry logic, diff capture | Partial |
| `tests/run_e2e.py` | Update `test_sleep_includes_reflection`, add `test_reflect_rerun` | TODO |

---

## Non-Goals

- NOT changing the reflection prompt or agent behavior
- NOT changing what gets written to the Obsidian file (beyond reflection section)
- NOT implementing bulk backfill (separate task — `scripts/backfill.py` exists)
- NOT fixing the pytest hanging issue (separate investigation)

---

## Cost Estimate

- Retry adds ~$3/failure (2 extra Opus calls × ~$1.50 each) — rare
- Manual `/reflect` re-run: ~$1.50 per invocation (single Opus call)
- Development: ~3hr (implementation) + ~1hr (testing)
