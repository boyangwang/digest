# CLAUDE.md — Digest Bot Project Guide

## What is this?
A Telegram bot that creates nightly digests of Boyang's conversations with his AI agent (Doudou). Runs as a launchd service on Mac Mini.

## Tech Stack
- Python 3.14, python-telegram-bot, APScheduler
- STT: ElevenLabs Scribe
- LLM: OpenClaw agent (`openclaw agent --local`)
- Storage: Obsidian vault (markdown files)
- Tests: pytest + pytest-asyncio

## Key Commands
```bash
# Run all tests (exclude live E2E and slow e2e)
python3 -m pytest tests/ --ignore=tests/test_live_e2e.py --ignore=tests/test_e2e.py -q

# Run specific test file
python3 -m pytest tests/test_voice_collection_trigger.py -v

# Run live E2E (requires Telegram Desktop running)
python3 tests/run_e2e.py --test all
```

## Conventions
- **Timezone:** Always `datetime.now(SGT)` — never naive `datetime.now()`
- **Error handling:** Never `except Exception: pass` — always log
- **Logging:** `logger = logging.getLogger("digest-bot")` — use `logger.info()`, `logger.error()`
- **Async:** All Telegram handlers are `async def`. Use `pytest.mark.asyncio` for async tests.
- **Config:** All paths and constants in `config.py`
- **Test isolation:** Tests must NOT write to production Obsidian vault. `conftest.py` sets `DIGEST_BOT_ENV=test` which redirects `DIGEST_DIR` to `/tmp/`.

## Architecture
- `main.py` — Bot handlers, commands, orchestration
- `recorder.py` — Digest file CRUD (create, update, finalize, append recap)
- `reflection.py` — Nightly reflection agent
- `collection_engine.py` — Conversation collection from OpenClaw sessions
- `llm.py` — LLM summarization
- `stt.py` — Speech-to-text
- `scheduler.py` — APScheduler timing
- `config.py` — All configuration

## Pre-existing Test Failures
5 tests in `tests/test_text_recollection.py` are known failures — do not fix them.
