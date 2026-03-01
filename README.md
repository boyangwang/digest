# Sleep Digest Bot 🌙

Standalone Telegram bot that collects OpenClaw conversation transcripts nightly,
generates intelligent summaries via Doudou (OpenClaw AI agent), and writes
them to an Obsidian vault for archival.

## Architecture

```
OpenClaw sessions (JSONL) → collector.py → raw messages
                                              ↓
                                   llm.py → save to file → Doudou reads & summarizes
                                              ↓
                                   recorder.py → Obsidian vault (YAML + Markdown)
                                              ↓
                                   main.py → Telegram DM to Boyang
```

## State Machine

```
IDLE  → /digest → collect, create file, start nudging → ACTIVE
ACTIVE → /digest → collect NEW msgs, update same file  → ACTIVE
ACTIVE → text    → append verbatim recap               → ACTIVE
ACTIVE → /sleep  → finalize file, stop nudging         → IDLE
```

## Commands

- `/digest` — Generate or update nightly digest
- `/sleep` — Finalize and go to sleep
- `/status` — Check current state

## Files

| Module | Purpose |
|--------|---------|
| `main.py` | Telegram bot, command handlers, state machine |
| `collector.py` | Read OpenClaw JSONL transcripts, extract & filter messages |
| `recorder.py` | Atomic writes to Obsidian vault, YAML frontmatter, lifecycle |
| `llm.py` | Summary composition via Doudou (file-based handoff) |
| `scheduler.py` | APScheduler: 22:30 digest, 30-min nudge cycle |
| `config.py` | All tokens, paths, constants |

## Output

- **Digest files**: `NotesVault/Artificial-Colloquia/Doudou-Digest/YYYY-MM-DD-HHMM.md`
- **Transcripts**: `NotesVault/Artificial-Colloquia/Doudou-Digest/transcripts/conv-YYYYMMDD-HHMMSS.md`

## Tests

```bash
cd ~/digest-bot
source venv/bin/activate
pip install pytest pytest-asyncio
pytest -v
```

## Service

Runs as macOS LaunchAgent: `~/Library/LaunchAgents/com.digest-bot.plist`
