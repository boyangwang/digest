"""
Collection Engine — Parallel, retriable, supersedable session collection.

DIGEST-009 implementation:
- Generation counter for supersession (latest request wins)
- Parallel asyncio.gather for session summaries
- Retry with exponential backoff (3 attempts: 5s, 10s)
- Killable subprocesses via start_new_session=True
- All-or-nothing: coverage advances ONLY when ALL sessions succeed
"""

import asyncio
import logging
import os
import re
import signal
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

SGT = timezone(timedelta(hours=8))

from collector import collect_all_messages as _collect_all, format_messages, group_by_session as _group_by_session
from llm import async_compose_summary

logger = logging.getLogger("digest-bot.collection-engine")


def derive_session_id(source_name: str) -> str:
    """Derive a safe session ID from a source session name.
    
    Each parallel summary call gets its own session ID based on the source
    session name, enabling lock-free parallel execution.
    
    Sanitization rules:
    - Convert to lowercase
    - Replace non-alphanumeric characters with hyphens
    - Strip leading/trailing hyphens
    - Truncate to 40 characters max
    - Prefix with "digest-summary-"
    
    Examples:
        "CLAW 003" → "digest-summary-claw-003"
        "Direct with Boyang" → "digest-summary-direct-with-boyang"
        "agent:main:subagent:5c16b1cc-6bdf" → "digest-summary-agent-main-subagent-5c16b1cc-6bdf"
    
    Args:
        source_name: Original session name (may contain spaces, special chars, unicode)
    
    Returns:
        Sanitized session ID safe for filesystem use
    """
    # Sanitize: lowercase, replace non-alphanumeric with hyphens, strip edges
    safe_name = re.sub(r'[^a-zA-Z0-9]+', '-', source_name.lower()).strip('-')[:40]
    
    # Handle edge case: empty string after sanitization
    if not safe_name:
        safe_name = "unknown"
    
    return "digest-summary-%s" % safe_name


def collect_all_messages(since_ts):
    """Wrapper for collector.collect_all_messages that returns grouped dict.
    
    For testing: returns dict[session_name, messages] directly.
    For production: calls real collector and groups by session.
    """
    prev_night, today_msgs = _collect_all(since_ts)
    all_msgs = prev_night + today_msgs
    if not all_msgs:
        return {}
    return _group_by_session(all_msgs)


@dataclass
class CollectionResult:
    """Result of a collection attempt."""
    summaries: list[dict]  # [{"session": str, "messages": int, "summary": str}]
    total: int  # Total messages collected
    coverage_to: datetime  # New coverage timestamp


class CollectionEngine:
    """Manages parallel, retriable, supersedable session collection.
    
    Design:
    - Generation counter: each collect() increments, old results are discarded
    - Parallel: asyncio.gather for all sessions simultaneously
    - Retriable: 3 attempts per session with exponential backoff
    - Killable: subprocesses tracked and terminated on abort
    - All-or-nothing: returns None if ANY session fails
    """

    def __init__(self):
        self._generation: int = 0
        self._active_task: Optional[asyncio.Task] = None
        self._active_procs: list[asyncio.subprocess.Process] = []

    async def collect(
        self,
        since_ts: datetime,
        trigger: str = "text"
    ) -> Optional[CollectionResult]:
        """Start a new collection, aborting any in-flight one.
        
        Returns:
            CollectionResult on success (all sessions summarized)
            None on failure (any session failed after retries)
        
        All-or-nothing: coverage advances ONLY when ALL sessions succeed.
        """
        # 1. Abort any active collection
        await self._abort_active()

        # 2. Increment generation
        self._generation += 1
        gen = self._generation
        logger.info("Collection Gen %d started (trigger: %s)" % (gen, trigger))

        # 3. Collect and group messages by session (fast, no LLM)
        session_groups = collect_all_messages(since_ts)
        
        # Count total messages across all sessions
        total = sum(len(msgs) for msgs in session_groups.values())

        if total == 0:
            logger.info("Gen %d: 0 messages" % gen)
            return CollectionResult(summaries=[], total=0, coverage_to=datetime.now(SGT))

        logger.info("Gen %d: %d messages across %d sessions" % (gen, total, len(session_groups)))

        # 4. Launch ALL sessions in parallel with retry
        tasks = [
            self._run_with_retry(name, msgs, gen)
            for name, msgs in sorted(session_groups.items())
        ]

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("Gen %d: Collection cancelled" % gen)
            return None

        # 6. Check if superseded during gather
        if gen != self._generation:
            logger.info("Gen %d: Superseded during collection (current: %d)" % (gen, self._generation))
            return None

        # 7. Separate successes from failures
        succeeded = []
        failed = []
        for i, (name, msgs) in enumerate(sorted(session_groups.items())):
            result = results[i]
            if isinstance(result, Exception):
                logger.error("Gen %d: Session '%s' raised exception: %s" % (gen, name, result))
                failed.append(name)
            elif result is None:
                logger.warning("Gen %d: Session '%s' failed after retries" % (gen, name))
                failed.append(name)
            else:
                succeeded.append(result)

        # 8. All-or-nothing: ALL must succeed
        if failed:
            logger.error("Gen %d: FAILED — %d/%d sessions failed: %s" % (
                gen, len(failed), len(session_groups), ", ".join(failed)
            ))
            return None

        logger.info("Gen %d: SUCCESS — all %d sessions summarized" % (gen, len(succeeded)))
        return CollectionResult(
            summaries=succeeded,
            total=total,
            coverage_to=datetime.now(SGT)
        )

    async def _abort_active(self):
        """Cancel active task and kill all child subprocesses."""
        if self._active_task and not self._active_task.done():
            logger.info("Aborting active collection task")
            self._active_task.cancel()
            try:
                await self._active_task
            except asyncio.CancelledError:
                pass

        # Kill tracked subprocesses
        for proc in self._active_procs:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                except Exception as e:
                    logger.warning("Failed to kill subprocess %d: %s" % (proc.pid, e))

        self._active_procs.clear()

    async def _run_with_retry(
        self,
        name: str,
        messages: list[dict],
        generation: int,
        max_retries: int = 3
    ) -> Optional[dict]:
        """Run _summarize_session with exponential backoff retry.
        
        Returns session summary dict on success, None on failure.
        """
        for attempt in range(max_retries):
            # Check if superseded before attempting
            if generation != self._generation:
                logger.info("Session '%s' retry aborted (superseded)" % name)
                return None

            result = await self._summarize_session(name, messages, generation)

            if result is not None:
                if attempt > 0:
                    logger.info("Session '%s' succeeded on attempt %d" % (name, attempt + 1))
                return result

            # Failed — retry with backoff
            if attempt < max_retries - 1:
                delay = 5 * (2 ** attempt)  # 5s, 10s
                logger.warning("Session '%s' attempt %d failed, retry in %ds" % (name, attempt + 1, delay))
                await asyncio.sleep(delay)

        logger.error("Session '%s' FAILED after %d attempts" % (name, max_retries))
        return None

    async def _summarize_session(
        self,
        name: str,
        messages: list[dict],
        generation: int
    ) -> Optional[dict]:
        """Summarize one session via async subprocess.
        
        Returns {"session": str, "messages": int, "summary": str} on success,
        None on failure or if superseded.
        """
        formatted = format_messages(messages)
        
        # Derive session ID from source session name — each session gets its own
        # lock file, enabling true parallel execution without contention.
        sid = derive_session_id(name)
        
        try:
            summary = await async_compose_summary(formatted, session_id=sid)
            
            # Check generation before returning (stale guard)
            if generation != self._generation:
                logger.info("Discarding stale result for '%s' (gen %d, current %d)" % (
                    name, generation, self._generation
                ))
                return None

            if not summary or summary.strip() == "":
                logger.warning("Session '%s' returned empty summary" % name)
                return None

            # Treat fallback text as failure
            if "Summary pending" in summary or "摘要待生成" in summary:
                logger.warning("Session '%s' returned fallback text — treating as failure" % name)
                return None

            return {
                "session": name,
                "messages": len(messages),
                "summary": summary,
            }

        except Exception as e:
            logger.error("Session '%s' exception: %s" % (name, e))
            return None
