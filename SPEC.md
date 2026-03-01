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

### SPEC-STATUS-01: /status returns full document
`/status` returns the complete content of the active document,
not just metadata. If it exceeds Telegram's limit, split into
multiple messages.

### SPEC-STATUS-02: /status when IDLE
Shows IDLE state and last finalized file's `coverage_to`.

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
