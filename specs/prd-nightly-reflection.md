# PRD: Nightly Reflection — Automated Knowledge Extraction from Conversations

> **Project:** Doudou Infrastructure — Memory & Learning System
> **Date:** 2026-03-02
> **Priority:** P1 High
> **Estimated effort:** Large (4-8hr)
> **Origin:** Boyang's request + "How to Hire an AI" proposals A3, G3, G4

---

## Context

### The Problem

Every day, Boyang and I have rich conversations across multiple sessions — discussions about investments, health decisions, technical architecture, personal preferences, ideas, corrections to my behavior. Currently, only ONE output captures this: the **Doudou Digest**, a verbatim conversation record saved to Obsidian.

The digest is a diary. It preserves *what was said* but extracts nothing *structured* from it. If Boyang mentions Ashley's birthday, or corrects how I format tables, or makes an investment decision — that knowledge lives only in a narrative transcript. To recover it later, I'd have to search through hundreds of pages of conversation text and hope I find it.

Meanwhile, my workspace files (RULES.md, INCIDENTS.md, MEMORY.md, memory/*.md) are updated **only when I remember to do so during the conversation**. This is unreliable — I miss things, especially corrections and preferences that Boyang states casually.

每天我们进行大量对话——投资讨论、健康决策、技术架构、个人偏好、想法、对我行为的纠正。目前唯一的输出是 Doudou Digest（逐字对话记录）。它保留了"说了什么"，但没有提取任何结构化知识。如果 Boyang 提到了一个偏好或做了一个决策，这个信息只存在于叙事文本中，之后要找回来非常困难。

### The Solution

**Nightly Reflection** — an automated, AI-powered extraction task that runs at the end of each day's cycle. When Boyang says goodnight, the system:

1. Reads ALL conversations from the current cycle
2. Extracts structured knowledge across 8 categories
3. Stores extracted items in the correct workspace locations
4. Appends a full reflection report to the Obsidian digest document
5. Commits all workspace changes to git

The result: every morning I wake up with yesterday's knowledge already crystallized and searchable. No more relying on my in-session memory to document things.

---

## Architecture

### Trigger & Timing

**Primary trigger:** Boyang says goodnight / "I go to sleep" / similar signal in the sleep digest session (CLAW 008).

**No fallback timer.** The digest document seals when the sleep digest bot's session naturally concludes. A document can't grow forever — it will eventually seal. No artificial deadline.

**Sequence:**

```
22:30  Cron fires → daily-digest.py generates DRAFT digest → [DAILY_DIGEST] sent to CLAW 008
       ↓
       CLAW 008 sends bedtime message to Boyang, asks for recap
       ↓
       Boyang gives recap + signals sleep ("晚安" / "good night" / "I go to sleep")
       ↓
       CLAW 008: (a) Appends recap VERBATIM → marks digest "final"
                 (b) Triggers Nightly Reflection ← NEW
       ↓
       Reflection sub-agent runs (Opus) — 3-8 min
       ↓
       Results: workspace updated + reflection appended to digest + git commit
```

### Cycle Scope

The reflection covers the **same cycle as the digest**: from the previous digest's `coverage_to` to the current digest's `coverage_to`. This is NOT strictly date-based — it follows the sleep-wake rhythm.

Example: If yesterday's digest had `coverage_to: 2026-03-01T22:30:00+08:00` and today's has `coverage_to: 2026-03-02T22:30:00+08:00`, the reflection covers exactly that 24-hour window.

### Data Source

The reflection reads the same conversation transcripts as the digest script:
- All session transcripts in `~/.openclaw/agents/main/sessions/`
- Filtered by the cycle's time window
- Includes ALL sessions (DM, groups, webchat) — except cron/run sessions

### Execution Model

**Decision: Sub-agent on Opus.**

CLAW 008 spawns a dedicated Opus sub-agent via `sessions_spawn` for each reflection run. Rationale:

1. **Reusability** — the same reflection prompt works for nightly runs AND historical backfill. One extraction engine, any input.
2. **Clean context** — fresh context window dedicated entirely to reflection. No noise from CLAW 008's digest/recap flow.
3. **Non-blocking** — CLAW 008 stays free (though Boyang is asleep, so this is secondary).
4. **Model: Opus always** — extraction quality matters. This is knowledge that persists forever. No cost-cutting on the extraction engine.

### Interaction with Sleep Digest Bot

The sleep digest bot (CLAW 008) currently has its own cron + Python script. The reflection integrates as follows:

**daily-digest.py remains unchanged** — it generates the DRAFT digest and sends [DAILY_DIGEST].

**CLAW 008's procedure gets extended** — after saving Boyang's recap and marking status `final`, it triggers the reflection:

```
CLAW 008 detects goodnight signal
  ↓
Marks digest "final" (existing behavior)
  ↓
Calls `exec` to run `scripts/nightly-reflection.py` with today's date
  ↓
nightly-reflection.py:
  1. Reads today's digest file to get coverage_from / coverage_to
  2. Collects all conversation messages within that cycle (reuses daily-digest.py logic)
  3. Writes conversation data to a temp file: /tmp/reflection-input-YYYY-MM-DD.json
  4. Calls `openclaw sessions send` to spawn Opus sub-agent with:
     - The reflection prompt template (from workspace)
     - Path to the conversation data file
     - Path to today's digest file
     - Paths to all workspace memory files (for deduplication)
  ↓
Sub-agent (Opus):
  1. Reads conversation data from temp file
  2. Reads existing workspace files for dedup context
  3. Extracts all 8 categories
  4. Writes to workspace files (memory/facts/, memory/feedback-lessons.md, etc.)
  5. Appends reflection section to the Obsidian digest document
  6. Runs `git add -A && git commit -m "nightly-reflection: YYYY-MM-DD" && git push origin main`
  7. Reports completion
```

**Key design choice:** The Python script handles data collection (deterministic, fast, $0). The Opus sub-agent handles extraction (requires reasoning). Clean separation of concerns.

**For historical backfill:** `scripts/backfill-reflection.py` runs the same pipeline in a loop for each historical date, substituting the cycle boundaries. Same sub-agent prompt, different input data.

---

## Extraction Categories (8)

Each category has: definition, examples, storage location in workspace, and format.

### 1. 📌 Durable Facts
**Definition:** Information that remains true beyond today. About people, places, companies, numbers, relationships, status changes.

**Examples:**
- "Ashley's birthday is March 15"
- "Portfolio company X raised Series A at $50M"
- "Boyang's VO2max measured at 46"
- "Office lease renewal is in September 2026"

**Workspace location:** `memory/facts/YYYY-MM-DD.md` (new directory)
**Format:**
```markdown
## Facts extracted from 2026-03-02 cycle

- **[People/Ashley]** Birthday is March 15
- **[Health/Biometrics]** VO2max: 46 mL/min/kg (up from 44, measured 2026-03-02)
- **[Portfolio/CompanyX]** Raised Series A at $50M valuation, led by Fund Y
```
**Tagging:** Each fact is tagged with a category (People, Health, Portfolio, Infrastructure, Personal, etc.) for searchability.

### 2. 🔧 Feedback Lessons (Corrections → Preferences)
**Definition:** Direct or indirect corrections Boyang gives me. Things I did wrong, ways I should change my behavior, preferences about output style/format/approach.

**Examples:**
- "Don't use markdown tables in Telegram"
- "Always search before answering infra questions"
- "Stop opening with 'Great question!'"
- "When I say X, I mean Y"

**Workspace location:** `memory/feedback-lessons.md` (append-only, persistent across days)
**Also:** If the lesson warrants a rule change → update RULES.md directly.
**Format:**
```markdown
## 2026-03-02

- **[Formatting]** No markdown tables in Telegram — they render poorly on mobile
  - _Context:_ Boyang corrected me when I sent a comparison table
  - _Action:_ Use code blocks or bullet lists instead
- **[Communication]** When Boyang says "search and research," always include cost estimate
  - _Context:_ Missed the cost estimation step in research report
  - _Action:_ Updated USER.md keyword instructions
```

### 3. ⚠️ Rules & Incidents
**Definition:** New rules that should be formalized, near-misses, mistakes I made, patterns that should be prevented.

**Examples:**
- "I fabricated a technical explanation — P1 incident"
- "Gateway config change without validation — near-miss"
- "New rule: always verify X before doing Y"

**Workspace location:** INCIDENTS.md (for incidents), RULES.md (for rules)
**Format:** Follows existing INCIDENTS.md format (date, severity, what happened, root cause, prevention).
**Note:** Only PROPOSE additions to RULES.md and INCIDENTS.md in the reflection report. Actual file edits require careful review — the reflection sub-agent should generate the proposed text, and the main agent (or a review step) applies it.

### 4. 🌟 Compliments & Positive Feedback
**Definition:** Things Boyang praised, things that went well, positive signals about my performance. Used for calibration — knowing what works well is as important as knowing what to fix.

**Examples:**
- "That analysis was excellent"
- "Good catch on the security issue"
- "This is exactly what I wanted"
- Boyang sharing my work with others (implicit approval)

**Workspace location:** `memory/compliments.md` (append-only)
**Format:**
```markdown
## 2026-03-02

- **"That hire-ai analysis was thorough and well-organized"** — context: 28-proposal report on Felix Craft's guide
- **Boyang approved the nightly reflection idea immediately** — implicit: the PRD approach is working
```

### 5. 🧭 Decisions & Rationale
**Definition:** Choices made during the day and WHY. Decisions without rationale are useless for future reference — the "why" is what matters.

**Examples:**
- "Decided to use Cloudflare Tunnel over Tailscale Funnel — reason: DNS stability"
- "Chose Sonnet over Opus for daily digest — reason: cost, sufficient quality"
- "Declined to implement feature X — reason: YAGNI, revisit in Q2"

**Workspace location:** `memory/decisions/YYYY-MM-DD.md` (new directory)
**Format:**
```markdown
## Decisions from 2026-03-02 cycle

### Use sub-agent on Sonnet for nightly reflection
- **Choice:** Sonnet sub-agent, not inline Opus
- **Rationale:** Cost control (~$0.05/run vs ~$0.40), non-blocking, sufficient quality for extraction
- **Alternatives considered:** Inline Opus (expensive), Haiku (insufficient reasoning)
- **Reversible:** Yes — can switch model anytime
```

### 6. 📋 Action Items & Commitments
**Definition:** Things mentioned in conversation that should be tracked — promises made, tasks planned, follow-ups needed. If it's not on KANBAN, it might be forgotten.

**Examples:**
- "I'll set up the Cloudflare Tunnel tomorrow"
- "Need to check Ashley's flight details"
- "Boyang asked me to research X — not yet started"

**Workspace location:** KANBAN.md (add to appropriate section)
**Format:** Standard KANBAN checkbox format.
**Policy:** Auto-add directly to KANBAN.md. Check for duplicates before adding.

### 7. 💡 Ideas & Brainstorms
**Definition:** Ideas mentioned in passing, creative suggestions, "what if" explorations, future possibilities. These deserve capture even if not actionable today.

**Examples:**
- "What if we built a voice-first interface for the fund?"
- "Could use LLMs for LP report generation"
- "Idea: automated morning health briefing from CGM data"

**Workspace location:** `memory/ideas.md` (append-only)
**Format:**
```markdown
## 2026-03-02

- **Voice-first fund interface** — Boyang mused about LPs being able to call an AI to get fund updates. Not actionable now, but worth revisiting.
- **CGM morning briefing** — auto-pull FreeStyle Libre data, generate health summary. Needs API research.
```

### 8. 🔬 Technical Learnings
**Definition:** New tools discovered, API quirks, debugging insights, architectural patterns, things that would save time if remembered.

**Examples:**
- "Tailscale Funnel supports TCP but not UDP"
- "OpenClaw sessions.json uses mtime for cache invalidation"
- "canvas.snapshot after navigate kills companion app (>25MB)"

**Workspace location:** `memory/YYYY-MM-DD.md` (existing daily memory files) + TOOLS.md if critical
**Format:** Follows existing memory file format.

---

## Output: Reflection Report in Obsidian Digest

After extraction, the reflection report is appended to the current day's digest file in Obsidian. This gives Boyang a single document per day that contains both the verbatim conversations AND the structured extractions.

### Digest File Structure (Updated)

```markdown
---
date: 2026-03-02
day: Monday
generated_at: "..."
coverage_from: "..."
coverage_to: "..."
status: final
reflection_at: "2026-03-02T23:45:00+08:00"    # NEW
reflection_model: "sonnet"                       # NEW
---

# March 2, 2026 — Monday

## 🌃 Previous Night
[existing content]

## 🗣️ Today's Conversations
[existing content]

## 📝 Boyang's Day Recap
[verbatim recap]

## 🪞 Nightly Reflection                          ← NEW SECTION

> Extracted from today's conversations by Doudou.
> All items below have also been stored in the workspace.

### 📌 Durable Facts (3)
- **[People/Ashley]** Birthday is March 15
- **[Health]** VO2max: 46 (up from 44)
- **[Portfolio/CompanyX]** Series A at $50M

### 🔧 Feedback Lessons (1)
- **[Formatting]** No markdown tables in Telegram
  - Action: Use bullet lists instead

### ⚠️ Rules & Incidents (0)
_None identified today._

### 🌟 Compliments (1)
- "That analysis was thorough" — re: hire-ai report

### 🧭 Decisions (2)
- Use Sonnet sub-agent for nightly reflection (cost: ~$0.05/run)
- Implement all 3 proposals from hire-ai analysis

### 📋 Action Items (1)
- [ ] Set up Cloudflare Tunnel evaluation

### 💡 Ideas (1)
- CGM morning health briefing — pull FreeStyle Libre data automatically

### 🔬 Technical Learnings (1)
- OpenClaw sessions_spawn supports `thread: true` for persistent sub-agents

### 📊 Reflection Stats
- Messages processed: 142
- Sessions scanned: 5
- Items extracted: 10
- Model: Sonnet
- Cost: ~$0.05
```

---

## Workspace File Changes Summary

After each reflection run, the following files may be modified:

| File | Action | Notes |
|------|--------|-------|
| `memory/facts/YYYY-MM-DD.md` | Create | Tagged durable facts |
| `memory/feedback-lessons.md` | Append | Corrections and preferences |
| `memory/compliments.md` | Append | Positive feedback log |
| `memory/decisions/YYYY-MM-DD.md` | Create | Decisions with rationale |
| `memory/ideas.md` | Append | Ideas and brainstorms |
| `memory/YYYY-MM-DD.md` | Append | Technical learnings (existing pattern) |
| `KANBAN.md` | Append | New action items (deduped) |
| `INCIDENTS.md` | Append | New incidents (if any, proposed — review before applying) |
| `RULES.md` | Append (sparingly) | New rules auto-applied when unambiguous; logged in report |

**RULES.md policy:** The reflection task MAY auto-apply new rules and incidents directly to RULES.md and INCIDENTS.md — but **sparingly and cautiously**. Only add rules when the evidence is clear and the correction is unambiguous. When in doubt, propose in the report rather than auto-apply. Every auto-applied change is logged in the reflection report for auditability.

---

## Requirements

### Core

- [ ] R1: Trigger detection — CLAW 008 recognizes goodnight signals (multi-language: "good night", "晚安", "I go to sleep", "sleep mode", etc.)
- [ ] R2: Conversation data collection — read all session transcripts for the current cycle, same scope as digest
- [ ] R3: 8-category extraction — all categories defined above, with consistent formatting
- [ ] R4: Workspace storage — each category stored in its designated location, correctly formatted
- [ ] R5: Deduplication — don't add facts/items that already exist in workspace files
- [ ] R6: Obsidian append — full reflection report appended to the day's digest document
- [ ] R7: Git commit — all workspace changes committed and pushed after reflection
- [ ] R8: Non-blocking — reflection sub-agent doesn't block CLAW 008's interaction with Boyang
- [ ] R9: Idempotent — running reflection twice for the same cycle produces no duplicate entries
- [ ] R10: Auditable — reflection report in Obsidian shows exactly what was extracted and where it was stored
- [ ] R11: RULES.md auto-apply — new rules applied directly but sparingly; every change logged in report

### Model & Execution

- [ ] R12: Model is Opus always — no cost-cutting on extraction quality
- [ ] R13: Execution via sub-agent spawn — fresh context, reusable for backfill
- [ ] R14: No fallback timer — document seals when the sleep digest session naturally concludes

### Historical Backfill

- [ ] R15: Generate digest + reflection for all days Feb 7 → Mar 1 (24 days)
- [ ] R16: 22:30 SGT as universal cycle boundary for backfill
- [ ] R17: No Boyang recap in historical documents (system didn't exist)
- [ ] R18: Historical documents marked with `status: backfill`, `backfill: true`
- [ ] R19: Process chronologically (oldest first) for correct cumulative deduplication
- [ ] R20: No overwrites — skip dates that already have digest files

---

## Tasks

### Phase 1: Infrastructure Setup

- [ ] T1: Create directory structure: `memory/facts/`, `memory/decisions/`
- [ ] T2: Create seed files: `memory/feedback-lessons.md`, `memory/compliments.md`, `memory/ideas.md`
- [ ] T3: Write the reflection prompt template (the master prompt sent to the Opus sub-agent)
- [ ] T4: Write `scripts/nightly-reflection.py` — data collection + sub-agent orchestration script
- [ ] T5: Write `scripts/backfill-reflection.py` — batch runner for historical backfill

### Phase 2: CLAW 008 Integration

- [ ] T6: Update `procedures/daily-digest.md` with the reflection step
- [ ] T7: Add goodnight trigger detection to CLAW 008's digest procedure
- [ ] T8: Test the full flow: digest → recap → goodnight → reflection → report

### Phase 3: Extraction Logic (in the sub-agent prompt)

- [ ] T9: Implement extraction for each of the 8 categories
- [ ] T10: Implement deduplication logic (check existing files before adding)
- [ ] T11: Implement the Obsidian append logic (add reflection section to digest)
- [ ] T12: Implement git commit + push after successful extraction

### Phase 4: Testing & Verification

- [ ] T13: Dry run on yesterday's (2026-03-01) conversations — verify extraction quality
- [ ] T14: Verify Obsidian file is correctly formatted and syncs
- [ ] T15: Verify workspace files are correctly updated
- [ ] T16: Verify git commit includes all changes
- [ ] T17: Cost measurement — confirm Opus cost is within expected range (~$0.45/run)

### Phase 5: Historical Backfill

- [ ] T18: Run backfill for Feb 7 → Mar 1 (24 days, chronological order)
- [ ] T19: Verify all 24 digest files created in Obsidian with correct formatting
- [ ] T20: Verify workspace memory files populated with historical extractions
- [ ] T21: Verify timestamp chain integrity (no gaps in coverage_from/coverage_to)
- [ ] T22: Final git commit with all backfill results

---

## Acceptance Criteria

- [ ] AC1: Full end-to-end flow works: Boyang says goodnight → reflection sub-agent spawns → results in Obsidian + workspace
- [ ] AC2: Reflection report is readable, well-formatted, and appears in Obsidian digest
- [ ] AC3: Workspace files (memory/*, KANBAN.md, RULES.md, INCIDENTS.md) are correctly updated
- [ ] AC4: No duplicate entries on re-run (idempotent)
- [ ] AC5: RULES.md changes are sparse, cautious, and logged in reflection report
- [ ] AC6: Total cost per nightly run ≤ $0.50 (Opus)
- [ ] AC7: Reflection completes within 10 minutes
- [ ] AC8: Historical backfill produces 24 digest files (Feb 7 → Mar 1) with correct formatting
- [ ] AC9: All workspace memory files populated with historical extractions
- [ ] AC10: Timestamp chain has zero gaps across all digest files

---

## Historical Backfill

### Overview

Generate historical digest + reflection documents for ALL days with available transcript data, retroactively populating the workspace with extracted knowledge.

**Available data range:** 2026-02-07 to 2026-03-01 (24 days)
- Transcript data exists from Feb 7 (Mac Mini migration date)
- Feb 1-6 data was on VPS (currently inaccessible) — excluded from backfill
- 88 transcript files across ~13 active sessions

### Backfill Rules (Strict)

**Rule 1: Cycle boundaries use 22:30 SGT as universal cutoff.**
- Day X's cycle: `2026-XX-XX T22:30:00+08:00` (previous day) → `2026-XX-XX T22:30:00+08:00` (current day)
- Example: Feb 8's document covers Feb 7 22:30 → Feb 8 22:30
- First document (Feb 7): covers from earliest available transcript timestamp → Feb 7 22:30

**Rule 2: Historical documents have NO Boyang recap.**
- The recap feature didn't exist during this period
- `## 📝 Boyang's Day Recap` section reads: `_No recap — historical backfill. Recap system started [date]._`
- `status: backfill` (not `draft` or `final`)

**Rule 3: Historical documents DO get full conversation content + reflection.**
- Conversations are collected and formatted identically to current digests
- Reflection extraction runs on each day's conversations
- All 8 extraction categories apply

**Rule 4: Historical documents are clearly marked.**
- YAML frontmatter includes: `backfill: true`, `backfill_at: "ISO8601 timestamp"`
- Status is `backfill`, never `final`

**Rule 5: Extracted items are tagged with their source date.**
- Facts, lessons, decisions etc. are filed under the correct historical date
- `memory/facts/2026-02-08.md` contains facts from Feb 8's conversations
- `memory/feedback-lessons.md` entries include the date they occurred

**Rule 6: No overwrites.**
- If a digest file already exists for a date, skip it (don't overwrite)
- If workspace entries already exist from manual documentation, don't duplicate

**Rule 7: Backfill processes days chronologically (oldest first).**
- This ensures cumulative knowledge builds correctly
- Deduplication works against all previously extracted items

### Backfill Execution Plan

```
For each day from 2026-02-07 to 2026-03-01 (chronological order):
  1. Define cycle: previous day 22:30 → current day 22:30 SGT
  2. Collect all transcript messages within cycle (same logic as daily-digest.py)
  3. Generate digest document (conversations only, no recap)
  4. Spawn Opus sub-agent for reflection extraction
  5. Sub-agent extracts all 8 categories → writes to workspace files
  6. Append reflection report to the digest document
  7. Save digest to Obsidian: Doudou-Digest/YYYY-MM-DD.md
  8. Git commit workspace changes: "nightly-reflection: backfill YYYY-MM-DD"
  9. Brief pause between days (rate limiting, context reset)
```

### Backfill Document Format

```yaml
---
date: 2026-02-08
day: Saturday
generated_at: "2026-03-02T23:00:00+08:00"      # when backfill ran
coverage_from: "2026-02-07T22:30:00+08:00"
coverage_to: "2026-02-08T22:30:00+08:00"
status: backfill
backfill: true
backfill_at: "2026-03-02T23:00:00+08:00"
reflection_at: "2026-03-02T23:05:00+08:00"
reflection_model: "opus"
---

# February 8, 2026 — Saturday

## 🌃 Previous Night
[conversations from Feb 7 22:30 to Feb 8 00:00]

## 🗣️ Today's Conversations
[conversations from Feb 8 00:00 to Feb 8 22:30]

## 📝 Boyang's Day Recap
_No recap — historical backfill. Recap system started March 2026._

## 🪞 Nightly Reflection
[full 8-category extraction]
```

### Backfill Cost Estimate

| Item | Calculation | Cost |
|------|-------------|------|
| 24 days × Opus sub-agent | 24 × ~$0.45 | ~$10.80 |
| Conversation data collection | Python script | $0.00 |
| Git operations | Shell | $0.00 |
| **Total backfill** | | **~$10.80** |

---

## Decisions Log (Resolved)

All open questions have been resolved by Boyang (2026-03-02 22:08):

| Question | Decision | Rationale |
|----------|----------|-----------|
| Execution model | Sub-agent | Reusability for backfill; clean context |
| Model | Opus always | Quality matters for persistent knowledge |
| RULES.md safety | Auto-apply, sparingly | Less friction; caution built into extraction prompt |
| Fallback timing | None | Document seals naturally when session concludes |
| KANBAN auto-add | Direct add | Reduce friction |
| Historical backfill | YES | 24 days of data (Feb 7 → Mar 1); valuable retroactive knowledge |

---

## Cost Estimate

### Nightly Run (Ongoing)

| Component | Model | Tokens (est.) | Cost (est.) |
|-----------|-------|---------------|-------------|
| Conversation data prep | Python script | 0 | $0.00 |
| Extraction sub-agent | Opus ($15/$75 per 1M) | ~15K input, ~3K output | ~$0.45 |
| Git operations | Shell | 0 | $0.00 |
| **Total per nightly run** | | | **~$0.45** |
| **Monthly (30 days)** | | | **~$13.50** |

### Historical Backfill (One-time)

| Component | Calculation | Cost |
|-----------|-------------|------|
| 24 daily reflections | 24 × $0.45 | ~$10.80 |
| **Total backfill** | | **~$10.80** |

### Total Year 1

| Component | Cost |
|-----------|------|
| Backfill (one-time) | $10.80 |
| Nightly runs (12 months) | $162.00 |
| **Annual total** | **~$172.80** |

---

*PRD version 1.0 — 2026-03-02 — Doudou 🦮*
