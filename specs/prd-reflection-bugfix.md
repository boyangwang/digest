# PRD: Reflection Bugfix — Send Structured Report to User

> **Project:** Sleep Digest Bot — Bug #1 Fix
> **Date:** 2026-03-03
> **Priority:** P0 Critical
> **Estimated effort:** Small-Medium (2-3hr)
> **Origin:** Boyang's bug report — "Where is the overnight reflection? I don't see anything."

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

- [ ] **T1** — Add `format_reflection_telegram(parsed: dict, date_str: str) -> str` to `reflection.py`
  - Input: parsed reflection dict (8 categories + stats)
  - Output: compact Telegram-friendly message (category counts + top 3-5 items)
  - Max length: 4096 chars (Telegram limit)
  - Truncate gracefully if too long

- [ ] **T2** — Modify `run_reflection()` return value to include both report AND parsed data
  - Currently returns: `str | None` (markdown report)
  - Change to: `tuple[str, dict] | None` — `(report_markdown, parsed_dict)`
  - This allows `cmd_sleep` to use parsed dict for Telegram message while using report for file
  - Update all callers

- [ ] **T3** — Modify `cmd_sleep` in `main.py` to send reflection content
  - After `run_reflection()` succeeds: call `format_reflection_telegram(parsed, date_str)`
  - Send result via `update.message.reply_text(telegram_msg, parse_mode="Markdown")`
  - On failure: send "⚠️ Reflection failed" with brief error reason
  - Ensure message is sent BEFORE `finalize()` (user sees content while file is being saved)

- [ ] **T4** — Update test mode in `cmd_sleep` to also send mock reflection message
  - `TestRecorder.append_reflection()` already exists
  - Add: send mock reflection summary message to test user too
  - This allows E2E test to verify message delivery

### Phase 2: Improve fallback behavior

- [ ] **T5** — Improve fallback report when agent fails
  - Instead of `_Reflection unavailable — agent failed to respond._`
  - Include: timestamp, error reason (timeout/crash/empty), conversation count
  - Format: `_Reflection unavailable (agent timeout at 23:22, 247 messages collected). Will be included in backfill._`

### Phase 3: E2E verification

- [ ] **T6** — Add E2E test: `/sleep` sends reflection message to chat
  - In `tests/run_e2e.py`, modify `test_sleep_includes_reflection`:
    - After `/sleep`, check bot's reply messages contain reflection content (category counts, "items extracted")
    - Verify message format matches spec
  - Must use actual Telegram message inspection (not just log checking)

- [ ] **T7** — Add E2E test: `/sleep` when agent fails sends failure message
  - Harder to test E2E (need to simulate agent failure)
  - Can test via unit test instead: mock `_call_agent` to return None

- [ ] **T8** — Run full regression: 277 unit/integration + 8 E2E
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

### Phase 4: Retry + Manual Re-run

- [ ] **T9** — Add automatic retry to `_call_agent()` in `reflection.py`
  - On failure (rc≠0, timeout, empty response): retry up to 2 more times (3 total attempts)
  - Exponential backoff: 5s, 15s between retries
  - Log each attempt: `"Reflection agent attempt %d/%d failed: %s"`
  - Only return None after all retries exhausted

- [ ] **T10** — Add `/reflect` command for manual re-run
  - New handler in `main.py`: `cmd_reflect(update, context)`
  - Behavior:
    1. Find the most recent finalized digest file (status="final")
    2. If it already has a real reflection (not fallback), ask for confirmation: "Reflection already exists. Re-run? Reply /reflect confirm"
    3. Collect conversations using that file's `coverage_from` / `coverage_to`
    4. Run `run_reflection()` with retry
    5. Overwrite the reflection section in the file (replace, not append)
    6. Send structured reflection message to user (same format as T1)
  - Optional argument: `/reflect 2026-03-02` to target a specific date
  - Boyang-only (same `_check_user` but production users only, not test)

- [ ] **T11** — Add `/reflect` to bot command menu and help text
  - Update `cmd_start` help message
  - Register handler in `main.py` setup

- [ ] **T12** — E2E test for `/reflect` re-run
  - In `tests/run_e2e.py`:
    1. `/digest` → text → `/sleep` (creates finalized file with reflection)
    2. `/reflect` → verify new reflection message sent
    3. Verify file's reflection section was updated (not duplicated)

---

## Acceptance Criteria

1. When Boyang sends `/sleep` with an active digest:
   - Bot sends "晚安 🌙 Running reflection..."
   - Bot sends structured reflection summary (category counts + top items)
   - Bot sends "✅ Digest saved to Obsidian"
   - Digest file in Obsidian contains full reflection section
2. When reflection agent fails:
   - Agent retries up to 3 times with backoff before giving up
   - Bot sends "⚠️ Reflection failed after 3 attempts" with brief reason
   - Digest still finalizes (SPEC-REFLECT-05)
   - Fallback text in file includes error context
3. When Boyang sends `/reflect`:
   - Re-runs reflection on most recent finalized digest (or specified date)
   - Sends structured reflection message
   - Updates file in-place (replaces old reflection section)
4. Test mode (`@claw0606`):
   - `/sleep` sends mock reflection summary message
   - E2E test verifies message content
5. No regressions: 277+ unit/integration + 8+ E2E pass

---

## Files to Modify

| File | Changes |
|------|---------|
| `reflection.py` | Add `format_reflection_telegram()`, change `run_reflection()` return type, add retry to `_call_agent()` |
| `main.py` | Modify `cmd_sleep` to send reflection content, add `cmd_reflect` handler, update test mode |
| `recorder.py` | Add `replace_reflection(report, filepath)` for in-place update |
| `tests/test_reflection.py` | Add tests for `format_reflection_telegram()`, retry logic, updated return type |
| `tests/run_e2e.py` | Update `test_sleep_includes_reflection`, add `test_reflect_rerun` |

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
