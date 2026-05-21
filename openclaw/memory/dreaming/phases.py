"""Dreaming phase execution.

Mirrors TypeScript memory-host-sdk/dreaming.ts phase run functions.

Each phase runs a memory consolidation cycle using the configured
execution settings (speed/thinking/budget/model).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from .config import DeepPhaseConfig, DreamingConfig, LightPhaseConfig, RemPhaseConfig

logger = logging.getLogger(__name__)


def _emit_dream_completed_event(
    workspace_dir: str | None,
    phase: str,
    results: dict[str, Any],
    storage_mode: str = "separate",
) -> None:
    """Emit a memory.dream.completed event after a phase finishes.
    Mirrors TS appendMemoryHostEvent("memory.dream.completed").
    """
    if not workspace_dir:
        return
    try:
        from openclaw.memory.events import MemoryDreamCompletedEvent, append_memory_host_event
        evt = MemoryDreamCompletedEvent(
            phase=phase,
            inline_path=results.get("inline_path"),
            report_path=results.get("report_path"),
            line_count=int(results.get("line_count", results.get("processed", 0))),
            storage_mode=storage_mode,  # type: ignore[arg-type]
        )
        append_memory_host_event(workspace_dir, evt)
    except Exception as exc:
        logger.debug("Failed to emit dream completed event: %s", exc)


class DreamPhase(str, Enum):
    """Dreaming phase names."""

    LIGHT = "light"
    DEEP = "deep"
    REM = "rem"


async def run_light_phase(
    config: DreamingConfig,
    agent_id: str = "main",
    memory_manager: Any = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run the light dreaming phase.

    Light phase: daily digest from recent sessions/recall.
    Mirrors TS runLightDreaming() in dreaming.ts.
    """
    phase_cfg = config.phases.light
    if not phase_cfg.enabled:
        return {"phase": "light", "skipped": True, "reason": "disabled"}

    logger.info(
        "Starting light dreaming phase for agent '%s' (lookback=%dd, limit=%d)",
        agent_id,
        phase_cfg.lookback_days,
        phase_cfg.limit,
    )

    try:
        results = await _run_phase_consolidation(
            phase=DreamPhase.LIGHT,
            agent_id=agent_id,
            sources=phase_cfg.sources,
            lookback_days=phase_cfg.lookback_days,
            limit=phase_cfg.limit,
            execution=phase_cfg.execution or config.execution,
            memory_manager=memory_manager,
        )
        logger.info("Light dreaming phase completed: %d items processed", results.get("processed", 0))
        _emit_dream_completed_event(workspace_dir, "light", results, config.storage.mode)
        return {"phase": "light", "ok": True, **results}
    except Exception as exc:
        logger.exception("Light dreaming phase failed")
        return {"phase": "light", "ok": False, "error": str(exc)}


async def run_deep_phase(
    config: DreamingConfig,
    agent_id: str = "main",
    memory_manager: Any = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run the deep dreaming phase.

    Deep phase: long-term memory synthesis with recovery.
    Mirrors TS runDeepDreaming() in dreaming.ts.
    """
    phase_cfg = config.phases.deep
    if not phase_cfg.enabled:
        return {"phase": "deep", "skipped": True, "reason": "disabled"}

    logger.info(
        "Starting deep dreaming phase for agent '%s' (limit=%d, minScore=%.2f)",
        agent_id,
        phase_cfg.limit,
        phase_cfg.min_score,
    )

    try:
        results = await _run_phase_consolidation(
            phase=DreamPhase.DEEP,
            agent_id=agent_id,
            sources=phase_cfg.sources,
            limit=phase_cfg.limit,
            execution=phase_cfg.execution or config.execution,
            memory_manager=memory_manager,
            min_score=phase_cfg.min_score,
            min_recall_count=phase_cfg.min_recall_count,
        )

        # Recovery sub-phase
        if phase_cfg.recovery.enabled:
            recovery_results = await _run_deep_recovery(
                phase_cfg=phase_cfg,
                agent_id=agent_id,
                memory_manager=memory_manager,
            )
            results["recovery"] = recovery_results

        logger.info("Deep dreaming phase completed: %d items processed", results.get("processed", 0))
        _emit_dream_completed_event(workspace_dir, "deep", results, config.storage.mode)
        return {"phase": "deep", "ok": True, **results}
    except Exception as exc:
        logger.exception("Deep dreaming phase failed")
        return {"phase": "deep", "ok": False, "error": str(exc)}


async def run_rem_phase(
    config: DreamingConfig,
    agent_id: str = "main",
    memory_manager: Any = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Run the REM dreaming phase.

    REM phase: pattern and relationship discovery.
    Mirrors TS runRemDreaming() in dreaming.ts.
    """
    phase_cfg = config.phases.rem
    if not phase_cfg.enabled:
        return {"phase": "rem", "skipped": True, "reason": "disabled"}

    logger.info(
        "Starting REM dreaming phase for agent '%s' (lookback=%dd, limit=%d)",
        agent_id,
        phase_cfg.lookback_days,
        phase_cfg.limit,
    )

    try:
        results = await _run_phase_consolidation(
            phase=DreamPhase.REM,
            agent_id=agent_id,
            sources=phase_cfg.sources,
            lookback_days=phase_cfg.lookback_days,
            limit=phase_cfg.limit,
            execution=phase_cfg.execution or config.execution,
            memory_manager=memory_manager,
            min_pattern_strength=phase_cfg.min_pattern_strength,
        )
        logger.info("REM dreaming phase completed: %d items processed", results.get("processed", 0))
        _emit_dream_completed_event(workspace_dir, "rem", results, config.storage.mode)
        return {"phase": "rem", "ok": True, **results}
    except Exception as exc:
        logger.exception("REM dreaming phase failed")
        return {"phase": "rem", "ok": False, "error": str(exc)}


async def _run_phase_consolidation(
    phase: DreamPhase,
    agent_id: str,
    sources: list[str],
    execution: Any,
    memory_manager: Any,
    lookback_days: int = 7,
    limit: int = 50,
    min_score: float = 0.0,
    min_recall_count: int = 0,
    min_pattern_strength: float = 0.0,
) -> dict[str, Any]:
    """Core consolidation logic shared across phases.

    When a memory plugin is available, delegates to it.
    Otherwise records the attempt in the dream diary.
    """
    if memory_manager and hasattr(memory_manager, "run_dreaming_phase"):
        return await memory_manager.run_dreaming_phase(
            phase=phase.value,
            agent_id=agent_id,
            sources=sources,
            lookback_days=lookback_days,
            limit=limit,
            execution=execution,
            min_score=min_score,
        )

    # Default: record diary entry
    diary_entry = {
        "phase": phase.value,
        "agent_id": agent_id,
        "sources": sources,
        "processed": 0,
        "note": "no memory plugin available — recorded in diary only",
    }
    return diary_entry


async def _run_deep_recovery(
    phase_cfg: DeepPhaseConfig,
    agent_id: str,
    memory_manager: Any,
) -> dict[str, Any]:
    """Run the deep phase recovery sub-routine."""
    rec = phase_cfg.recovery
    if not rec.enabled:
        return {"skipped": True}

    logger.debug(
        "Running deep recovery for agent '%s' (triggerBelowHealth=%.2f)",
        agent_id,
        rec.trigger_below_health,
    )

    if memory_manager and hasattr(memory_manager, "check_memory_health"):
        health = await memory_manager.check_memory_health(agent_id)
        if health >= rec.trigger_below_health:
            return {"skipped": True, "reason": f"health {health:.2f} >= threshold {rec.trigger_below_health:.2f}"}

    return {"ran": True, "recovered": 0}
