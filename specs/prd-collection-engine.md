# DIGEST-009: Collection Engine — Parallel, Retriable, Supersedable

> **Status:** 🔴 Draft
> **Priority:** P1
> **Tasks:** 0/14 complete
> **Created:** 2026-03-03
> **Depends on:** prd-singleton-guard.md (should be implemented first)

---

## Problem Statement

The digest bot's collection mechanism (`_build_session_summaries`) has several design weaknesses:

1. **Sequential LLM calls** — sessions are summarized one-by-one (~30s each); 6 sessions = ~3 minutes blocking the event loop
2. **Fallback advances coverage** — when `compose_summary` LLM fails, a placeholder string is returned and `coverage_to` advances anyway; those sessions are never re-summarized (silent quality loss)
3. **No supersession** — if Boyang sends a text recap while a previous collection is running, the old one can't be cancelled; if `/sleep` arrives mid-collection, it blocks behind the sync subprocess calls
4. **No retry** — LLM failures get one shot; `_ask_doudou()` returns `None` → fallback, done
5. **Blocking event loop** — `subprocess.run()` (sync) blocks the entire asyncio loop for 30-180 seconds per session; no other handlers can execute during this time
6. **`except Exception: return []`** — in `get_all_session_transcripts()`, all errors are silently swallowed (bad practice, hampers debugging)

### What This Is NOT About

- The 4 cases on 2026-03-02 where re-collection returned "0 messages" were **correct behavior** — Boyang was talking to the digest bot (separate Telegram bot), not to OpenClaw sessions. There were genuinely 0 OpenClaw messages in those time windows. There is no "38% failure rate" — that analysis was wrong.
- The process kill at 23:22:01 has unknown cause — not attributable to any specific bug.

---

## Architecture

### Core Principle: Supersession with Generation Counter

Every collection attempt gets a monotonically increasing **generation ID**. Only the latest generation's results are applied. This is the standard "latest request wins" pattern used in UI debouncing and concurrent job management.

```
Generation 1 starts (text recap at 20:35)
  → collecting sessions in parallel...
Generation 2 starts (text recap at 20:36)
  → Generation 1 is ABORTED (subprocesses killed, results discarded)
  → Generation 2 collects from same coverage_to (bigger range, covers Gen 1)
Generation 2 completes
  → Results applied, coverage_to advanced
```

**Why this is safe:** A newer collection always starts from the same `coverage_to` (which hasn't advanced because the older one hasn't completed). Therefore the newer collection's range is always ≥ the older one's range. No messages are lost.

### Implementation: `CollectionEngine` Class

A new module `collection_engine.py` encapsulates all collection logic:

```python
class CollectionEngine:
    """Manages parallel, retriable, supersedable session collection."""
    
    _generation: int = 0          # Monotonic counter
    _active_task: asyncio.Task | None = None
    _active_procs: list[asyncio.subprocess.Process] = []
    
    async def collect(self, since_ts, trigger="text") -> CollectionResult | None:
        """Start a new collection, aborting any in-flight one.
        
        Returns CollectionResult on success, None on total failure.
        Advances coverage_to ONLY on full success (all sessions summarized).
        """
    
    async def _abort_active(self):
        """Cancel active task + kill all child subprocesses."""
    
    async def _summarize_session(self, name, messages, generation) -> SessionSummary | None:
        """Summarize one session via async subprocess. Checks generation before returning."""
    
    async def _run_with_retry(self, name, messages, generation, max_retries=3) -> SessionSummary | None:
        """Run _summarize_session with exponential backoff retry."""
```

### Subprocess Management

**Current:** `subprocess.run()` (sync, blocking, uncancellable)

**New:** `asyncio.create_subprocess_exec()` with process group isolation:

```python
proc = await asyncio.create_subprocess_exec(
    "openclaw", "agent", "--local", ...,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    start_new_session=True,  # New process group — killable via os.killpg
)
```

**Why `start_new_session=True`:**
- Creates a new process group for the child
- `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` kills the entire tree
- Prevents orphan `openclaw agent` processes lingering after abort
- Standard best practice per Python docs and Stack Overflow consensus

**Abort procedure:**
1. Set generation to N+1 (invalidates old generation)
2. `task.cancel()` on the active asyncio Task
3. For each tracked subprocess: `proc.terminate()` → `await asyncio.wait_for(proc.wait(), timeout=5)` → `proc.kill()` if still alive
4. Clear tracked subprocess list

**Stale return guard:** Even if a subprocess returns after abort (because `terminate()` was too slow), the result is discarded:
```python
async def _summarize_session(self, name, messages, generation):
    # ... run subprocess ...
    if generation != self._generation:
        logger.info("Discarding stale result for %s (gen %d, current %d)" % (name, generation, self._generation))
        return None
    return result
```

This is Boyang's "abort flag" pattern — the generation counter IS the abort flag.

### Collection Flow

```
collect(since_ts, trigger):
  1. _abort_active()  ← kill any in-flight collection
  2. self._generation += 1
  3. gen = self._generation
  4. messages = collect_all_messages(since_ts)  ← fast, no LLM
  5. if 0 messages → return CollectionResult(total=0)
  6. groups = group_by_session(messages)
  7. Launch ALL sessions in parallel:
       tasks = [_run_with_retry(name, msgs, gen) for name, msgs in groups]
       results = await asyncio.gather(*tasks, return_exceptions=True)
  8. Check generation (may have been superseded during gather):
       if gen != self._generation → discard, return None
  9. Separate successes from failures:
       succeeded = [r for r in results if r is not None and not isinstance(r, Exception)]
       failed = [name for name, r in zip(groups, results) if r is None or isinstance(r, Exception)]
  10. ALL succeeded → return CollectionResult(summaries, total)
      ANY failed → return None (DON'T advance coverage)
  11. Log comprehensively: which sessions succeeded, which failed, how many retries
```

### Retry Policy (Per Session)

```python
async def _run_with_retry(self, name, messages, generation, max_retries=3):
    for attempt in range(max_retries):
        if generation != self._generation:
            return None  # Superseded
        
        result = await self._summarize_session(name, messages, generation)
        if result is not None:
            return result
        
        if attempt < max_retries - 1:
            delay = 5 * (2 ** attempt)  # 5s, 10s, 20s
            logger.warning("Session '%s' attempt %d failed, retry in %ds" % (name, attempt+1, delay))
            await asyncio.sleep(delay)
    
    logger.error("Session '%s' FAILED after %d attempts" % (name, max_retries))
    return None
```

### Callers

**`handle_text`:** After `append_recap(text)` and `✍️` reply:
```python
result = await engine.collect(since_ts, trigger="text")
if result is None:
    await _send_to_boyang("❌ Collection failed — will retry on next message")
elif result.total == 0:
    await _send_to_boyang("📭 0 new messages since %s" % since_ts.strftime("%H:%M"))
else:
    update_digest(new_coverage_to=result.coverage_to, session_summaries=result.summaries)
    await _send_to_boyang(format_collection_message(result))
```

**`cmd_sleep`:** Same supersession — `/sleep` aborts any running collection:
```python
# Sleep timestamp is NOW (when Boyang said /sleep)
sleep_ts = datetime.now(SGT)

# Collect (aborts any running collection — this one has bigger range)
result = await engine.collect(since_ts, trigger="sleep")
if result and result.total > 0:
    update_digest(new_coverage_to=sleep_ts, session_summaries=result.summaries)

# Always finalize (even if collection fails — /sleep must not be blocked)
# Reflection runs after collection
```

**`generate_digest` (22:30 scheduled):** Same interface, same supersession.

### Coverage Advancement Rule

**ONLY advance `coverage_to` when ALL sessions are successfully summarized.**

- All succeed → advance to NOW (or sleep_ts for /sleep)
- Any fail → DON'T advance → log which failed → next collection re-collects everything
- The `_fallback()` placeholder text in `compose_summary` must be treated as FAILURE, not success

---

## Tasks

### Phase 1: TDD — Write Failing Tests

- [ ] **T1** — `tests/test_collection_engine.py`: Core engine tests
  - `test_collect_returns_summaries_on_success` — all sessions succeed → returns CollectionResult with summaries
  - `test_collect_returns_none_on_partial_failure` — 1 of 3 sessions fails after retries → returns None (not partial results)
  - `test_collect_returns_zero_total_when_no_messages` — 0 messages → returns CollectionResult(total=0)
  - `test_generation_counter_increments` — each collect() call increments generation
  - `test_stale_generation_results_discarded` — results from old generation are discarded

- [ ] **T2** — `tests/test_collection_engine.py`: Supersession tests
  - `test_new_collect_aborts_previous` — start collect, then start another → first is cancelled
  - `test_subprocess_killed_on_abort` — verify child processes receive SIGTERM
  - `test_abort_flag_prevents_stale_write` — old collection returns after abort → results not applied

- [ ] **T3** — `tests/test_collection_engine.py`: Retry tests
  - `test_retry_on_failure` — session fails twice, succeeds on third → result included
  - `test_max_retries_then_fail` — session fails 3 times → collection returns None
  - `test_exponential_backoff` — verify delays between retries (5s, 10s, 20s)
  - `test_retry_aborted_by_supersession` — during retry sleep, new generation starts → retry stops

- [ ] **T4** — `tests/test_collection_engine.py`: Parallel execution tests
  - `test_sessions_run_in_parallel` — 3 sessions each taking 1s → total time < 2s (not 3s)
  - `test_fallback_treated_as_failure` — compose_summary returning fallback text → treated as None

- [ ] **T5** — Verify all new tests FAIL (no implementation) + existing 240+ tests PASS

### Phase 2: Implement

- [ ] **T6** — Create `collection_engine.py` with `CollectionEngine` class
  - Generation counter, `_active_task`, `_active_procs` tracking
  - `collect()`, `_abort_active()`, `_summarize_session()`, `_run_with_retry()`
  - `CollectionResult` dataclass: `summaries`, `total`, `coverage_to`
  - Convert `compose_summary` in `llm.py` from `subprocess.run` to `asyncio.create_subprocess_exec` with `start_new_session=True`

- [ ] **T7** — Integrate engine into `main.py`
  - Replace `_build_session_summaries()` calls in `handle_text`, `cmd_sleep`, `generate_digest` with `engine.collect()`
  - `handle_text`: collect → advance only on full success
  - `cmd_sleep`: collect (supersedes any running) → advance → finalize (never blocked)
  - `generate_digest`: collect → advance only on full success
  - Remove old `_build_session_summaries()` function

- [ ] **T8** — Fix `get_all_session_transcripts()` error handling
  - Replace bare `except Exception: return []` with proper logging
  - Retry with 500ms backoff (up to 3 attempts) on JSONDecodeError
  - Log the actual exception for debugging

- [ ] **T9** — Run all tests — new tests PASS + existing 240+ PASS

### Phase 3: E2E Verification

- [ ] **T10** — E2E: basic collection works
  - Send text to bot → verify ✍️ → verify 📬 or 📭 response
  - Verify coverage_to advanced (check file YAML)

- [ ] **T11** — E2E: supersession works
  - Send text → while collection runs, send another text → verify first collection aborted
  - Verify no orphan `openclaw agent` processes left running after abort
  - Verify only ONE coverage advance happened

- [ ] **T12** — E2E: /sleep supersedes collection
  - Send text → while collection runs, send /sleep → verify collection aborted
  - Verify finalize completed (file status = final)
  - Verify no stale collection results written after finalize

### Phase 4: Cleanup

- [ ] **T13** — Update specs
  - Add SPEC-COLLECTION-01 through SPEC-COLLECTION-05 to `specs/SPEC.md`
  - Update `specs/TESTING.md` with collection engine test patterns

- [ ] **T14** — Update TODO.md, mark old backlog items as addressed

---

## Acceptance Criteria

1. ✅ Sessions are summarized in parallel (N sessions in ~30s, not N×30s)
2. ✅ `coverage_to` advances ONLY when ALL sessions succeed (no fallback advances)
3. ✅ New collection aborts any running collection (generation counter pattern)
4. ✅ Aborted collection's results are never written to the digest file
5. ✅ Failed sessions retry up to 3 times with exponential backoff
6. ✅ `/sleep` supersedes any running collection and is never blocked
7. ✅ Child subprocesses are killed on abort (no orphan `openclaw agent` processes)
8. ✅ Comprehensive logging: which sessions succeeded/failed, retry attempts, abort events
9. ✅ `get_all_session_transcripts()` logs errors instead of silently swallowing them
10. ✅ All new tests pass + existing 240+ tests pass (no regressions)

---

## Files to Create/Modify

| File | Changes |
|------|---------|
| `collection_engine.py` | **NEW**: CollectionEngine class, CollectionResult dataclass |
| `llm.py` | Convert `compose_summary` to async (`async_compose_summary`) using `asyncio.create_subprocess_exec` |
| `main.py` | Replace `_build_session_summaries()` with `engine.collect()` in handle_text, cmd_sleep, generate_digest |
| `collector.py` | Fix `except Exception: return []` → log + retry |
| `tests/test_collection_engine.py` | **NEW**: All engine tests (supersession, retry, parallel, generation) |
| `specs/SPEC.md` | Add SPEC-COLLECTION-01..05 |

---

## Non-Goals

- NOT changing what `compose_summary` prompts the LLM with (content unchanged)
- NOT changing the digest file format
- NOT implementing partial advance (all-or-nothing is simpler and safer)
- NOT adding a queue — supersession means at most ONE collection running

---

## Cost Estimate

- **Development:** ~4-6 hours (engine is ~200 lines, tests ~300 lines, integration ~100 lines)
- **Runtime cost per collection:** Same as before (same number of LLM calls). Retries add cost only on failure (~$0.02/retry)
- **Risk:** Medium — replacing sync with async subprocesses is a significant refactor; thorough testing required

---

## Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Parallel mechanism | `asyncio.gather` with `asyncio.create_subprocess_exec` | Standard asyncio pattern; each session is independent |
| Abort mechanism | Generation counter + `proc.terminate()` + `task.cancel()` | Generation counter prevents stale writes even if kill fails; belt AND suspenders |
| Process isolation | `start_new_session=True` | Creates new process group; `os.killpg()` kills entire child tree |
| Retry policy | 3 attempts, exponential backoff (5s, 10s, 20s) | Handles transient failures; 35s max wait per session |
| Partial success | All-or-nothing (don't advance on any failure) | Boyang's explicit decision: "advance if ALL worked, don't advance if ANY fail" |
| Fallback text | Treated as failure (not success) | Placeholder text should never advance coverage |
| `/sleep` behavior | Supersedes collection, always finalizes | `/sleep` must not be blocked; Boyang wants to sleep NOW |

---

*PRD v1.0 — 2026-03-03*
