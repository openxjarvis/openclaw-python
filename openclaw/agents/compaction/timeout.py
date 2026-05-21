"""Compaction timeout utilities.

Mirrors TypeScript:
  src/agents/run/compaction-timeout.ts
  src/agents/run/compaction-retry-aggregate-timeout.ts

Provides timeout management for compaction operations so they don't
hang the agent loop indefinitely.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Default timeouts — mirrors TS defaults
_DEFAULT_COMPACTION_TIMEOUT_MS = 120_000   # 2 minutes per attempt
_DEFAULT_RETRY_AGGREGATE_TIMEOUT_MS = 300_000  # 5 minutes total across retries


class CompactionTimeout:
    """Single-attempt compaction timeout.

    Mirrors TS CompactionTimeout from run/compaction-timeout.ts.
    Used to abort a single compaction attempt if it takes too long.
    """

    def __init__(self, timeout_ms: int = _DEFAULT_COMPACTION_TIMEOUT_MS) -> None:
        self.timeout_ms = timeout_ms
        self._started_at: float | None = None
        self._task: asyncio.Task | None = None
        self._triggered = False

    def start(self) -> None:
        """Start the timeout countdown."""
        self._started_at = time.monotonic()
        self._triggered = False

    def is_triggered(self) -> bool:
        """True if the timeout has elapsed."""
        if self._triggered:
            return True
        if self._started_at is None:
            return False
        elapsed_ms = (time.monotonic() - self._started_at) * 1000
        if elapsed_ms >= self.timeout_ms:
            self._triggered = True
        return self._triggered

    def remaining_ms(self) -> float:
        """Remaining time in milliseconds (0 if triggered)."""
        if self._started_at is None:
            return float(self.timeout_ms)
        elapsed = (time.monotonic() - self._started_at) * 1000
        return max(0.0, self.timeout_ms - elapsed)

    def reset(self) -> None:
        """Reset for a new attempt."""
        self._started_at = None
        self._triggered = False

    async def sleep_or_timeout(self, task_coro) -> tuple[bool, any]:
        """Run task_coro with this timeout. Returns (timed_out, result)."""
        try:
            result = await asyncio.wait_for(
                task_coro,
                timeout=self.timeout_ms / 1000,
            )
            return False, result
        except asyncio.TimeoutError:
            self._triggered = True
            logger.warning(
                "Compaction timed out after %dms",
                self.timeout_ms,
            )
            return True, None


class CompactionRetryAggregateTimeout:
    """Aggregate timeout across multiple compaction retry attempts.

    Mirrors TS CompactionRetryAggregateTimeout from run/compaction-retry-aggregate-timeout.ts.
    Once the aggregate timeout fires, no more retries should be attempted.
    """

    def __init__(
        self,
        aggregate_timeout_ms: int = _DEFAULT_RETRY_AGGREGATE_TIMEOUT_MS,
        per_attempt_timeout_ms: int = _DEFAULT_COMPACTION_TIMEOUT_MS,
    ) -> None:
        self.aggregate_timeout_ms = aggregate_timeout_ms
        self.per_attempt_timeout_ms = per_attempt_timeout_ms
        self._started_at: float | None = None
        self._attempt_count = 0

    def start(self) -> None:
        """Start the aggregate timer."""
        self._started_at = time.monotonic()
        self._attempt_count = 0

    def should_retry(self) -> bool:
        """True if another retry attempt is allowed."""
        if self._started_at is None:
            return True
        elapsed_ms = (time.monotonic() - self._started_at) * 1000
        return elapsed_ms < self.aggregate_timeout_ms

    def new_attempt_timeout(self) -> CompactionTimeout:
        """Create a per-attempt timeout for the next retry.

        Caps the attempt timeout so it doesn't exceed remaining aggregate time.
        """
        self._attempt_count += 1
        if self._started_at is not None:
            remaining = self.aggregate_timeout_ms - (time.monotonic() - self._started_at) * 1000
            effective = min(self.per_attempt_timeout_ms, max(1000.0, remaining))
        else:
            effective = self.per_attempt_timeout_ms
        return CompactionTimeout(timeout_ms=int(effective))

    @property
    def attempt_count(self) -> int:
        return self._attempt_count
