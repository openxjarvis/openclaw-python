"""Timer management matching TypeScript openclaw/src/cron/service/timer.ts

Key behaviors:
- MAX_TIMER_DELAY_MS = 60_000  (clamp so scheduler never waits > 1 min)
- If running guard fires, re-arm at MAX_TIMER_DELAY_MS instead of returning
- Invokes service._on_timer() when the sleep completes
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Awaitable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Wake at most once a minute to avoid schedule drift / process-pause recovery
MAX_TIMER_DELAY_MS = 60_000


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class CronTimer:
    """
    Timer manager — mirrors TS armTimer / onTimer pattern.

    Keeps a single asyncio task that sleeps for min(delay, MAX_TIMER_DELAY_MS),
    then calls service._on_timer().
    """

    def __init__(self, on_timer_callback: Callable[[], Awaitable[None]]):
        self._on_timer = on_timer_callback
        self._task: asyncio.Task[None] | None = None
        self.next_fire_ms: int | None = None

    # ------------------------------------------------------------------
    # arm / stop
    # ------------------------------------------------------------------

    def arm(self, next_wake_at_ms: int | None) -> None:
        """Arm (or re-arm) the timer. Mirrors TS armTimer."""
        # Cancel previous
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

        if next_wake_at_ms is None:
            self.next_fire_ms = None
            logger.debug("cron: armTimer skipped - no jobs with nextRunAtMs")
            return

        now = _now_ms()
        delay_ms = max(0, next_wake_at_ms - now)
        # Clamp: wake at least once per minute
        clamped_ms = min(delay_ms, MAX_TIMER_DELAY_MS)
        self.next_fire_ms = next_wake_at_ms

        logger.debug(
            f"cron: timer armed — nextAt={next_wake_at_ms}, delay={clamped_ms}ms"
            f"{' (clamped)' if delay_ms > MAX_TIMER_DELAY_MS else ''}"
        )

        self._task = asyncio.create_task(self._wait(clamped_ms / 1000))

    def arm_at_max_delay(self) -> None:
        """Re-arm at MAX_TIMER_DELAY_MS — used when timer fires while running."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.debug("cron: re-arming at MAX_TIMER_DELAY_MS (job running)")
        self._task = asyncio.create_task(self._wait(MAX_TIMER_DELAY_MS / 1000))

    def stop(self) -> None:
        """Stop the timer synchronously.
        
        Cancels the running task if any. The task's _wait() method will catch
        CancelledError and exit cleanly without invoking the callback.
        """
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self.next_fire_ms = None
        logger.info("cron: timer stopped")
    
    async def stop_async(self) -> None:
        """Stop the timer and wait for it to actually stop.
        
        This ensures the timer callback won't execute after stop() returns.
        Use this in async contexts like Gateway shutdown.
        """
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task  # ✅ Wait for task to actually finish
            except asyncio.CancelledError:
                pass  # Expected
            except Exception as e:
                logger.debug(f"cron: timer task exception during stop: {e}")
        self._task = None
        self.next_fire_ms = None
        logger.info("cron: timer stopped (async)")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _wait(self, delay_seconds: float) -> None:
        """Wait and fire timer callback.
        
        Catches CancelledError when task is cancelled during stop().
        Mirrors NodeJS setTimeout behavior but with async cancellation.
        """
        try:
            await asyncio.sleep(delay_seconds)
            await self._on_timer()
        except asyncio.CancelledError:
            # Normal - task was cancelled during stop()
            logger.debug("cron: timer cancelled")
        except Exception as e:
            logger.error(f"cron: timer tick failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        running = self._task is not None and not self._task.done()
        status: dict[str, Any] = {
            "running": running,
            "next_fire_ms": self.next_fire_ms,
        }
        if self.next_fire_ms:
            now = _now_ms()
            remaining = max(0, self.next_fire_ms - now)
            status["time_until_ms"] = remaining
            status["time_until_seconds"] = remaining / 1000
        return status
