# PRD: Singleton Guard — Prevent Duplicate Bot Instances

> **Status:** 🔴 Draft — awaiting approval
> **Project:** Sleep Digest Bot — Infrastructure Hardening
> **Date:** 2026-03-03
> **Priority:** P1 High
> **Estimated effort:** Medium (2-4hr)
> **Origin:** Bug investigation — 12 orphan files + hard-kill crash on 2026-03-02. Root cause: multiple bot instances running simultaneously.
> **Tasks:** 0/9 complete

---

## Problem Statement

On 2026-03-02, the digest bot produced 12 orphan empty digest files (394 bytes, `status: active`, no content) and was hard-killed at 23:22:01 during an LLM call. Investigation revealed:

### Evidence

1. **29 `409 Conflict` errors** in `/tmp/digest-bot.log`:
   ```
   telegram.error.Conflict: terminated by other getUpdates request;
   make sure that only one bot instance is running
   ```

2. **11 bot restarts in one day** — multiple without graceful shutdown:
   ```
   11:47:52  Bot starting (no prior stop)
   11:49:08  Bot starting (no prior stop)  ← two instances fighting
   11:52:55  Bot starting (no prior stop)
   11:53:00  Application.stop → 11:53:05 Bot starting (first graceful pair)
   ...
   23:22:01  Bot starting (no prior stop)  ← hard-kill during LLM call
   ```

3. **Orphan files created between 10:43-12:36** during rapid restart cycles. Each instance independently ran `recover_active_on_startup()` and/or processed `/digest` commands, creating parallel files.

4. **The hard-kill at 23:22:01**: The process was killed mid-flight during `handle_text`'s re-collection phase (34-second `openclaw agent --local` subprocess). No graceful shutdown logged. No Python traceback. The process was externally terminated.

### Root Cause

**No singleton mechanism.** The bot has zero protection against concurrent instances:
- `main.py` has no PID file, no flock, no port binding
- launchd `KeepAlive: true` auto-restarts on ANY exit
- Manual `nohup python3 main.py` (during development) creates a second instance
- `launchctl kickstart -k` kills and restarts, but if the old process doesn't die fast enough, there's a window of overlap
- Telegram's `getUpdates` long-polling is exclusive — two callers cause 409 Conflict errors which can cascade into crashes

### Impact

- **Data corruption:** Orphan files pollute Obsidian vault, break timestamp chain (SPEC-TS-03)
- **Message loss:** 409 Conflict crashes can kill a process mid-handler, losing in-flight operations (the 23:22 summary loss)
- **Unstable service:** The bot enters a crash-restart loop where each restart spawns another conflict

---

## Root Cause Analysis

### Why multiple instances exist

| Cause | Mechanism | Prevention |
|-------|-----------|------------|
| **nohup + launchd** | Developer runs `nohup python3 main.py &` while launchd is active | PID lock rejects start if already running |
| **launchctl kickstart overlap** | Old process takes >1s to die (e.g., mid-LLM-call), new process starts | PID lock + stale detection |
| **KeepAlive rapid restart** | Bot exits with error → launchd restarts → new instance conflicts with leftover TCP/poll state | ThrottleInterval + PID lock |
| **Direct `python3 main.py` for testing** | Developer forgets launchd is running | PID lock + clear log message |

### Why orphan files are created

1. Instance A starts, recovers file X as active
2. Instance B starts (duplicate), also recovers file X
3. Instance A or B receives `/digest` → creates new file Y (if X was finalized by the other)
4. One instance crashes due to 409 Conflict
5. launchd restarts → new instance C sees file Y as active, potentially creates Z
6. Repeat → 12+ orphan files in one day

### Why the hard-kill at 23:22

The exact external cause is unknown (no launchd log, no memory pressure event). Most likely: `launchctl kickstart -k` from another session deploying reflection code (commits at 23:03-23:07, bot restarted at 23:14). If another kickstart was issued at ~23:22, it would SIGTERM the process during the `openclaw agent --local` subprocess call (which runs for 30+ seconds). The subprocess blocks, SIGTERM is unhandled, the process dies without cleanup.

---

## Design

### 1. PID File Lock (Primary Guard)

A file-based PID lock at `/tmp/digest-bot.pid` that prevents concurrent instances.

**On startup (`main.py`, before any Telegram operations):**
```python
def acquire_pid_lock(pidfile="/tmp/digest-bot.pid"):
    """Acquire exclusive PID lock. Exit if another instance is running."""
    
    # Check for existing PID file
    if os.path.exists(pidfile):
        try:
            with open(pidfile) as f:
                old_pid = int(f.read().strip())
            # Check if that PID is still alive
            os.kill(old_pid, 0)  # signal 0 = existence check
            # Process exists — refuse to start
            logger.fatal("Another instance running (PID %d). Exiting." % old_pid)
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            # Process is dead — stale PID file, safe to proceed
            logger.warning("Stale PID file found (PID %s). Cleaning up." % old_pid)
            os.remove(pidfile)
        except PermissionError:
            # Process exists but we can't signal it
            logger.fatal("Another instance running (PID in %s, permission denied). Exiting." % pidfile)
            sys.exit(1)
    
    # Write our PID
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    
    # Register cleanup
    import atexit
    atexit.register(lambda: _remove_pidfile(pidfile))
```

**On shutdown (atexit + signal handlers):**
```python
def _remove_pidfile(pidfile="/tmp/digest-bot.pid"):
    """Remove PID file on clean shutdown."""
    try:
        with open(pidfile) as f:
            stored_pid = int(f.read().strip())
        if stored_pid == os.getpid():
            os.remove(pidfile)
    except Exception:
        pass
```

**Why PID file over flock:**
- PID file is visible to external tools (`cat /tmp/digest-bot.pid`, `pgrep`)
- PID file survives inspection — flock is invisible and harder to debug
- PID file allows stale detection via `os.kill(pid, 0)`
- flock auto-releases on process death, which is nice, but PID stale detection handles this too

### 2. SIGTERM Handler (Graceful Shutdown on External Kill)

Register SIGTERM handler so `launchctl kickstart -k` and external kills trigger graceful cleanup:

```python
import signal

def _handle_sigterm(signum, frame):
    """Handle SIGTERM — log and exit cleanly."""
    logger.info("Received SIGTERM — shutting down gracefully.")
    _remove_pidfile()
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
```

This ensures:
- PID file is cleaned up on SIGTERM (launchctl kickstart sends SIGTERM first)
- Log message appears (distinguishes graceful kill from crash)
- If SIGKILL follows (e.g., launchctl timeout), PID file remains → stale detection on next startup

### 3. Startup Logging — Clear Singleton Status

Add explicit log messages on startup:

```
[digest-bot] INFO: PID lock acquired: /tmp/digest-bot.pid (PID 12345)
[digest-bot] INFO: Sleep Digest Bot starting...
```

Or on duplicate detection:
```
[digest-bot] FATAL: Another instance running (PID 12344). Exiting.
```

Or on stale cleanup:
```
[digest-bot] WARNING: Stale PID file found (PID 99999). Cleaning up.
[digest-bot] INFO: PID lock acquired: /tmp/digest-bot.pid (PID 12345)
```

### 4. Orphan File Prevention (Secondary)

The PID lock prevents the root cause (duplicate instances). However, as a belt-and-suspenders measure, also add a check in `generate_digest()` and `handle_text()`:

```python
# At the top of generate_digest():
if has_active_file():
    status = get_active_status()
    # If active file is empty (no summary content) and older than 2 hours,
    # log a warning — this indicates a past orphan scenario
    ...
```

This is NOT the primary fix — it's a diagnostic tool to detect if the PID lock is failing.

### 5. launchd Plist Hardening

Add `ThrottleInterval` to prevent rapid restart loops:

```xml
<key>ThrottleInterval</key>
<integer>10</integer>
```

macOS default is already 10s, but making it explicit documents the intent. If the bot crashes and restarts repeatedly, launchd enforces a 10-second cooldown between restarts, reducing the window for duplicate instances.

---

## Tasks

### Phase 1: TDD — Write Failing Tests

- [ ] **T1** — Write unit tests for PID lock (`tests/test_singleton.py`):
  - `test_acquire_creates_pidfile` — after acquire, `/tmp/digest-bot.pid` exists with correct PID
  - `test_acquire_rejects_if_running` — if PID file exists with a LIVE pid, `acquire_pid_lock()` calls `sys.exit(1)`
  - `test_acquire_cleans_stale` — if PID file exists with a DEAD pid, `acquire_pid_lock()` removes it and proceeds
  - `test_release_removes_pidfile` — `_remove_pidfile()` deletes the file if PID matches
  - `test_release_ignores_other_pid` — `_remove_pidfile()` does NOT delete if PID doesn't match (safety: another instance started after us)
  - `test_atexit_registered` — after `acquire_pid_lock()`, atexit has our cleanup registered
  - Use `tmp_path` fixture + mock `/tmp/digest-bot.pid` to test in isolation

- [ ] **T2** — Write unit test for SIGTERM handler:
  - `test_sigterm_handler_cleans_pidfile` — calling `_handle_sigterm()` removes PID file
  - `test_sigterm_handler_logs` — SIGTERM handler logs "Received SIGTERM"

- [ ] **T3** — Write integration test for startup sequence:
  - `test_main_acquires_lock_before_polling` — verify `acquire_pid_lock()` is called before `app.run_polling()`
  - `test_duplicate_startup_exits` — simulate: write PID file with current PID → call `acquire_pid_lock()` → verify `sys.exit(1)`

- [ ] **T4** — Verify all new tests FAIL (no implementation yet) + existing tests still PASS

### Phase 2: Implement

- [ ] **T5** — Implement `acquire_pid_lock()`, `_remove_pidfile()`, SIGTERM handler in `main.py`
  - PID file path: `/tmp/digest-bot.pid` (configurable via `config.py`)
  - Call `acquire_pid_lock()` at top of `main()`, before `Application.builder()`
  - Register `signal.SIGTERM` handler
  - Register `atexit` cleanup

- [ ] **T6** — Add `ThrottleInterval` to launchd plist
  - Add `<key>ThrottleInterval</key><integer>10</integer>` to `com.digest-bot.plist`

- [ ] **T7** — Run all tests — new tests PASS + existing 240+ PASS

### Phase 3: E2E Verification

- [ ] **T8** — Manual E2E test: attempt to start second instance
  - With bot running via launchd, run `python3 main.py` manually
  - Verify: exits immediately with "Another instance running" log message
  - Verify: launchd instance unaffected
  - Verify: no 409 Conflict errors in log

- [ ] **T9** — Manual E2E test: launchctl kickstart -k
  - Run `launchctl kickstart -k gui/$(id -u)/com.digest-bot`
  - Verify: old PID file cleaned up (SIGTERM handler)
  - Verify: new instance acquires PID lock
  - Verify: no orphan digest files created
  - Verify: no 409 Conflict errors

---

## Acceptance Criteria

1. ✅ Only one bot instance can run at any time — second attempt exits with clear error
2. ✅ Stale PID file from crashed process is auto-cleaned on next startup
3. ✅ SIGTERM (launchctl kickstart, manual kill) triggers graceful PID cleanup
4. ✅ PID file is at `/tmp/digest-bot.pid`, readable by external tools
5. ✅ `launchctl kickstart -k` produces clean handoff (no 409 Conflict window)
6. ✅ No orphan files created during restart cycles
7. ✅ All new tests pass + existing 240+ tests pass (no regressions)

---

## Files to Modify

| File | Changes |
|------|---------|
| `main.py` | Add `acquire_pid_lock()`, `_remove_pidfile()`, SIGTERM handler, call at startup |
| `config.py` | Add `PID_FILE = Path("/tmp/digest-bot.pid")` |
| `com.digest-bot.plist` | Add `ThrottleInterval` (documentation of intent) |
| `tests/test_singleton.py` | New: PID lock unit + integration tests |
| `specs/SPEC.md` | Add SPEC-SINGLETON-01: "Only one bot instance may run at any time" |

---

## Non-Goals

- NOT implementing flock (PID file is more debuggable)
- NOT adding systemd-style socket activation (overkill for a single-user bot)
- NOT retroactively cleaning existing orphan files (manual cleanup, separate task)
- NOT fixing Bug A (handle_text crash resilience) — that's a separate PRD

---

## Cost Estimate

- Zero runtime cost — pure Python, no API calls
- Development: ~2hr implementation + ~1hr testing
- Risk: Very low — PID file is a well-understood Unix pattern

---

## Decisions Log

| Question | Decision | Rationale |
|----------|----------|-----------|
| PID file vs flock | PID file | Visible, debuggable, supports stale detection via `os.kill(pid, 0)` |
| PID file location | `/tmp/digest-bot.pid` | Standard location, survives reboots (macOS /tmp is actually /private/tmp, persistent) |
| SIGTERM handling | Yes | launchctl sends SIGTERM before SIGKILL — catch it for cleanup |
| ThrottleInterval | 10s (explicit) | macOS default, but explicit is better than implicit |
| Orphan cleanup on startup | Diagnostic only | Root cause is duplicate instances — fix that, not the symptom |

---

*PRD v1.0 — 2026-03-03*
