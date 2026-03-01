# Sleep Digest Bot — Document Format Specification v2

## Document Structure

```markdown
---
generated_at: "ISO8601"
coverage_from: "ISO8601"
coverage_to: "ISO8601"
status: "active" | "final"
finalized_at: "ISO8601"          # only when status=final
---

# Doudou's Summary

Session: CLAW 003
Messages: 167
Summary:
A long day of building the digest bot architecture...
多段文字的摘要...

Session: Telegram DM
Messages: 17
Summary:
Discussed the Obsidian vault setup...

[After incremental /digest — new entries appended below:]

Session: CLAW 003
Messages: 5
Summary:
A few more messages about test coverage...

# Boyang's Recap

**22:45** Feeling productive today 🚀

**23:10** One more thought before bed...
```

## Spec Definitions

### SPEC-STRUCT-01: Two sections only
The document body has exactly two top-level headings:
- `# Doudou's Summary`
- `# Boyang's Recap`
Nothing else. No `## Previous Night`. No `## Conversations`. No `## New Conversations (updated)`.

### SPEC-STRUCT-02: No raw conversations in digest file
Raw conversation messages are NOT stored in the digest file.
They are stored in the transcripts folder (`transcripts/conv-YYYYMMDD-HHMMSS.md`).
The digest file contains only summaries and Boyang's recap.

### SPEC-STRUCT-03: Summary entry format
Each summary entry under `# Doudou's Summary` has:
```
Session: <session display name>
Messages: <count>
Summary:
<LLM-composed summary text>
```
Entries are separated by blank lines.

### SPEC-SUMMARY-01: Summary is append-only
Each `/digest` or update appends new summary entries below existing ones.
Previous summary entries are never erased, replaced, or modified.

### SPEC-SUMMARY-02: Same session may appear multiple times
If CLAW 003 had 167 messages in the first batch and 5 more in the second,
both entries appear:
```
Session: CLAW 003
Messages: 167
Summary: ...

Session: CLAW 003
Messages: 5
Summary: ...
```
This is correct — they represent different time ranges.

### SPEC-SUMMARY-03: Zero new messages = no update
If incremental collection finds zero new messages, no summary is appended
and no update occurs.

### SPEC-RECAP-01: Verbatim, timestamped, append-only
Boyang's text messages are recorded exactly as typed with timestamps.
Never truncated, never interpreted, never modified.

### SPEC-RECAP-02: Recap is always the last section
`# Boyang's Recap` is always at the bottom of the document.

### SPEC-TS-01: coverage_to advances with each update
Each `/digest` or text-reply update advances `coverage_to` in YAML.

### SPEC-TS-02: coverage_from is immutable
Set at file creation, never changes.

### SPEC-TS-03: Timestamp chain
Next file's `coverage_from` = most recent file's `coverage_to`.

### SPEC-TS-04: First run fallback
No prior files → `coverage_from = now - 24 hours`.

### SPEC-NAME-01: YYYY-MM-DD-HHMM.md
Decoupled from calendar date. Multiple files per day supported.

### SPEC-NAME-02: New file only from IDLE
New file created only when `/digest` is called in IDLE state.

### SPEC-STATUS-01: /status returns metadata + full document
`/status` returns:
1. State (ACTIVE / IDLE)
2. File name
3. Timestamps (coverage_from, coverage_to)
4. The full raw content of the active document

All metadata is preserved. The document content is appended at the end.
If it exceeds Telegram's limit, split into multiple messages.

### SPEC-STATUS-02: /status when IDLE
Shows IDLE state and file=None. No document content.

### SPEC-FINAL-01: /sleep finalizes
Sets `status: "final"`, `finalized_at`, clears active state, stops nudging.

### SPEC-EXCLUDE-01: No Previous Night section
Removed. Documents are date-decoupled; "previous night" has no meaning.

### SPEC-EXCLUDE-02: No raw conversation sections
No `## 🗣️ Today's Conversations`. No `## 🗣️ New Conversations (updated)`.
No `## 🌃 Previous Night`. Conversations live in transcript files only.

### SPEC-EXCLUDE-03: No statistics in body
No session counts, metadata tables, or CLI output in the document body.
Session name and message count appear only in summary entry headers.

### SPEC-FRONTMATTER-01: Minimal metadata
Only: `generated_at`, `coverage_from`, `coverage_to`, `status`, `finalized_at`.
No `date`, `day` fields — document is not date-oriented.

### SPEC-NUDGE: Window 22:30–07:00
Unchanged from v1. See test_nudge_bug.py.

---

## Voice Message Handling (v2.1)

When Boyang sends a voice or audio message to the bot, it is processed as a
first-class entry in the digest — audio preserved in full, transcript alongside.

### SPEC-VOICE-01: Audio file saved to Obsidian vault
The original audio file is saved to:
```
Doudou-Digest/attachments/voice-YYYYMMDD-HHMMSS.ogg
```
- Filename uses UTC+8 timestamp of receipt
- Format preserved as-is from Telegram (.ogg opus)
- The `attachments/` directory is created if absent
- Files are synced to all devices via Obsidian Sync

### SPEC-VOICE-02: STT transcription via ElevenLabs Scribe
The audio file is transcribed using ElevenLabs Scribe v2 API:
- Endpoint: `POST https://api.elevenlabs.io/v1/speech-to-text`
- Model: `scribe_v2`
- Language detection: automatic (supports Chinese + English bilingual)
- API key: `ELEVENLABS_API_KEY` environment variable
- On STT failure: audio is still saved, transcript shows `[Transcription unavailable]`

### SPEC-VOICE-03: Recap entry format (audio + transcript)
Under `# Boyang's Recap`, a voice message produces:
```
**HH:MM** 🎙️ ![[voice-20260301-223045.ogg]]
> Transcribed text goes here, exactly as returned by STT...
```
- The `![[...]]` embed renders as an audio player in Obsidian
- The blockquote `>` contains the full transcription
- Multi-line transcriptions use continued `>` blockquote syntax
- This mirrors messaging apps: audio player + text side by side

### SPEC-VOICE-04: Telegram confirmation
After processing, the bot replies in DM with:
```
🎙️ ✍️

> <transcribed text>
```
- The transcription is shown so Boyang can verify accuracy
- If STT failed: `🎙️ ✍️ (audio saved, transcription unavailable)`

### SPEC-VOICE-05: Voice messages require ACTIVE state
Voice messages are only processed when a digest is active.
If no active digest, the voice message is silently ignored (same as text).

### SPEC-VOICE-06: Audio stored in vault, not /tmp/
Audio files are stored in the Obsidian vault (permanent, synced).
NOT in `/tmp/` (which is ephemeral). This matches the transcript storage
pattern (`transcripts/` directory).

### SPEC-VOICE-07: STT provider abstraction
The STT call is isolated in `stt.py` — a single function:
```python
def transcribe(audio_path: str) -> str | None
```
Returns transcribed text, or None on failure.
Provider can be swapped without touching any other module.

### Example: Full digest with voice

```markdown
---
generated_at: "2026-03-01T22:30:00+08:00"
coverage_from: "2026-03-01T10:00:00+08:00"
coverage_to: "2026-03-01T22:30:00+08:00"
status: "active"
---

# Doudou's Summary

Session: CLAW 003
Messages: 42
Summary:
Worked on the digest bot voice feature...
今天在搞语音消息功能...

# Boyang's Recap

**22:45** Great progress today on the bot

**22:50** 🎙️ ![[voice-20260301-225012.ogg]]
> 今天的进展很不错，明天继续把测试写完。晚安。

**23:01** One more thought before sleep
```
