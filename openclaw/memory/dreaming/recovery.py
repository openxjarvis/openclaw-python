"""Deep dreaming recovery logic.

Mirrors TypeScript memory-host-sdk/dreaming.ts deep phase recovery sub-routine.

Recovery runs when memory health drops below a threshold, attempting to
re-synthesize degraded or missing long-term memories.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import DeepRecoveryConfig

logger = logging.getLogger(__name__)


async def check_and_run_recovery(
    agent_id: str,
    recovery_cfg: DeepRecoveryConfig,
    memory_manager: Any = None,
) -> dict[str, Any]:
    """Check memory health and run recovery if below threshold.

    Mirrors TS deep phase recovery in dreaming.ts.
    """
    if not recovery_cfg.enabled:
        return {"skipped": True, "reason": "recovery disabled"}

    # Check health
    health = 1.0
    if memory_manager and hasattr(memory_manager, "check_memory_health"):
        try:
            health = float(await memory_manager.check_memory_health(agent_id))
        except Exception:
            logger.exception("Failed to check memory health")

    if health >= recovery_cfg.trigger_below_health:
        return {
            "skipped": True,
            "reason": f"health {health:.2f} >= threshold {recovery_cfg.trigger_below_health:.2f}",
        }

    logger.info(
        "Memory health %.2f below threshold %.2f — running recovery for agent '%s'",
        health,
        recovery_cfg.trigger_below_health,
        agent_id,
    )

    if memory_manager and hasattr(memory_manager, "run_memory_recovery"):
        try:
            result = await memory_manager.run_memory_recovery(
                agent_id=agent_id,
                lookback_days=recovery_cfg.lookback_days,
                max_candidates=recovery_cfg.max_recovered_candidates,
                min_confidence=recovery_cfg.min_recovery_confidence,
                auto_write_min_confidence=recovery_cfg.auto_write_min_confidence,
            )
            return {"ran": True, "health_before": health, **result}
        except Exception as exc:
            logger.exception("Memory recovery failed")
            return {"ran": False, "error": str(exc)}

    return {
        "ran": True,
        "health_before": health,
        "recovered": 0,
        "note": "no memory plugin — recovery skipped",
    }
