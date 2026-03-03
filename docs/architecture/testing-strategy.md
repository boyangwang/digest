# Testing Strategy — Sleep Digest Bot

## Three-Tier Testing Principle (MANDATORY)

Every feature in this repository MUST have tests at all three tiers. No exceptions.

### Tier 1: Unit Tests
- **What:** Pure logic, all dependencies mocked
- **Where:** `tests/test_<module>.py`
- **Run:** `pytest tests/test_<module>.py -v`
- **Speed:** Fast (<1s per test)

### Tier 2: Integration Tests
- **What:** Module interactions with real file I/O, mocked external services (LLM, Telegram API)
- **Where:** `tests/test_integration.py`, `tests/test_<module>.py` (mixed in)
- **Run:** `pytest tests/ -v --ignore=tests/test_live_e2e.py`
- **Speed:** Medium (1-5s per test)

### Tier 3: Live E2E Tests
- **What:** Full lifecycle via REAL Telegram messages using UI automation
- **Where:** `tests/test_live_e2e.py`
- **Infrastructure:** `tests/telegram_ui.py` (Peekaboo-based Telegram Desktop automation)
- **Run:** `pytest tests/test_live_e2e.py -v -s` (use -s for real-time output)
- **Speed:** Slow (5-15s per test, UI automation)

**Prerequisites for E2E:**
- Mac Mini with Telegram Desktop running (logged in as @claw0606)
- Bot running via launchd (`com.digest-bot`)
- Bot chat @sleep_digest_bot already opened in Telegram

### Why E2E Is Non-Negotiable

Unit and integration tests catch logic bugs. E2E catches:
- Telegram handler registration issues
- Real network/API behavior
- Obsidian file sync interactions
- Bot process lifecycle (launchd restart, crash recovery)
- Real LLM response format variations
- UI/UX regressions (reply format, emoji, timing)

**If a feature doesn't have E2E tests, it's not done.**

---

## Running Tests

```bash
# All tests (except live E2E)
cd ~/digest-bot && source venv/bin/activate
pytest -v --ignore=tests/test_live_e2e.py

# Live E2E only (requires Telegram + bot running)
pytest tests/test_live_e2e.py -v -s

# Everything
pytest -v -s
```

---

*Established: 2026-03-02. Based on patterns from test_live_e2e.py (402 lines, 9 tests) and telegram_ui.py.*
