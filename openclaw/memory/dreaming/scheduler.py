"""Dreaming scheduler.

Mirrors TypeScript memory-host-sdk/dreaming.ts DreamingScheduler.

Uses APScheduler to register cron jobs for each enabled phase.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import DreamingConfig
from .phases import run_deep_phase, run_light_phase, run_rem_phase

logger = logging.getLogger(__name__)


class DreamingScheduler:
    """Schedules dreaming phases using APScheduler.

    Mirrors TS DreamingScheduler from memory-host-sdk/dreaming.ts.
    Usage:
        scheduler = DreamingScheduler(config, agent_id, memory_manager)
        await scheduler.start()
        # ... later:
        await scheduler.stop()
    """

    def __init__(
        self,
        config: DreamingConfig,
        agent_id: str = "main",
        memory_manager: Any = None,
    ) -> None:
        self.config = config
        self.agent_id = agent_id
        self.memory_manager = memory_manager
        self._scheduler: Any = None
        self._running = False

    async def start(self) -> None:
        """Start the scheduler and register phase jobs."""
        if not self.config.enabled:
            logger.debug("Dreaming disabled — scheduler not started")
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning(
                "APScheduler not installed — dreaming scheduler unavailable. "
                "Install with: pip install apscheduler"
            )
            return

        tz = self.config.timezone or "UTC"
        self._scheduler = AsyncIOScheduler(timezone=tz)

        phases = self.config.phases

        # Light phase
        if phases.light.enabled:
            self._scheduler.add_job(
                self._run_light,
                CronTrigger.from_crontab(phases.light.cron, timezone=tz),
                id=f"dream_light_{self.agent_id}",
                name=f"Light Dreaming [{self.agent_id}]",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info(
                "Scheduled light dreaming for agent '%s': %s",
                self.agent_id,
                phases.light.cron,
            )

        # Deep phase
        if phases.deep.enabled:
            self._scheduler.add_job(
                self._run_deep,
                CronTrigger.from_crontab(phases.deep.cron, timezone=tz),
                id=f"dream_deep_{self.agent_id}",
                name=f"Deep Dreaming [{self.agent_id}]",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info(
                "Scheduled deep dreaming for agent '%s': %s",
                self.agent_id,
                phases.deep.cron,
            )

        # REM phase
        if phases.rem.enabled:
            self._scheduler.add_job(
                self._run_rem,
                CronTrigger.from_crontab(phases.rem.cron, timezone=tz),
                id=f"dream_rem_{self.agent_id}",
                name=f"REM Dreaming [{self.agent_id}]",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info(
                "Scheduled REM dreaming for agent '%s': %s",
                self.agent_id,
                phases.rem.cron,
            )

        self._scheduler.start()
        self._running = True
        logger.info("Dreaming scheduler started for agent '%s'", self.agent_id)

    async def stop(self) -> None:
        """Stop the scheduler."""
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("Dreaming scheduler stopped for agent '%s'", self.agent_id)

    async def _run_light(self) -> None:
        try:
            result = await run_light_phase(self.config, self.agent_id, self.memory_manager)
            if self.config.verbose_logging:
                logger.info("Light dreaming result: %s", result)
        except Exception:
            logger.exception("Light dreaming phase error")

    async def _run_deep(self) -> None:
        try:
            result = await run_deep_phase(self.config, self.agent_id, self.memory_manager)
            if self.config.verbose_logging:
                logger.info("Deep dreaming result: %s", result)
        except Exception:
            logger.exception("Deep dreaming phase error")

    async def _run_rem(self) -> None:
        try:
            result = await run_rem_phase(self.config, self.agent_id, self.memory_manager)
            if self.config.verbose_logging:
                logger.info("REM dreaming result: %s", result)
        except Exception:
            logger.exception("REM dreaming phase error")

    @property
    def is_running(self) -> bool:
        return self._running
