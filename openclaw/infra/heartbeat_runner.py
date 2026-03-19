"""Heartbeat runner (matches TypeScript infra/heartbeat-runner.ts)

Periodically sends heartbeat messages to agents to trigger proactive behavior.
Supports:
- Multi-agent with per-agent intervals
- Active hours (HH:MM + IANA timezone-aware, mirrors TS heartbeat-active-hours.ts)
- Queue-aware (skips if requests in flight)
- Delivery target resolution
"""

import asyncio
import logging
from typing import Any, Callable

from .heartbeat_events import (
    HeartbeatEventPayload,
    emit_heartbeat_event,
)

logger = logging.getLogger(__name__)

# Global enable/disable flag (mirrors TS heartbeatsEnabled)
_heartbeats_enabled = True
_running_tasks: dict[str, asyncio.Task] = {}


def set_heartbeats_enabled(enabled: bool) -> None:
    """Globally enable/disable heartbeats"""
    global _heartbeats_enabled
    _heartbeats_enabled = enabled
    logger.info(f"Heartbeats {'enabled' if enabled else 'disabled'}")


def is_heartbeat_enabled_for_agent(
    agent_config: dict[str, Any],
) -> bool:
    """Check if heartbeat is enabled for a specific agent"""
    if not _heartbeats_enabled:
        return False

    heartbeat_config = agent_config.get("heartbeat", {})
    return heartbeat_config.get("enabled", False)


def resolve_heartbeat_interval_ms(
    agent_config: dict[str, Any],
) -> int:
    """Resolve heartbeat interval in milliseconds"""
    heartbeat_config = agent_config.get("heartbeat", {})
    interval = heartbeat_config.get("interval", "1h")

    if isinstance(interval, (int, float)):
        return int(interval)

    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for suffix, mult in multipliers.items():
        if interval.endswith(suffix):
            try:
                return int(float(interval[:-1]) * mult)
            except ValueError:
                pass

    return 3_600_000  # Default 1 hour


def resolve_heartbeat_prompt(
    agent_config: dict[str, Any],
) -> str:
    """Resolve heartbeat prompt text"""
    heartbeat_config = agent_config.get("heartbeat", {})
    return heartbeat_config.get(
        "prompt",
        "This is a scheduled heartbeat. Check for any pending tasks, reminders, "
        "or proactive actions you should take. If nothing needs attention, respond briefly."
    )


def _is_within_active_hours(
    agent_config: dict[str, Any],
) -> bool:
    """Check if current time is within active hours.

    Supports HH:MM strings and IANA timezone names, mirroring
    TS heartbeat-active-hours.ts / HeartbeatManager._is_active_hours().
    """
    heartbeat_config = agent_config.get("heartbeat", {})
    active_hours = heartbeat_config.get("activeHours") or heartbeat_config.get("active_hours")

    if not active_hours:
        return True  # No restriction

    # Delegate to the shared helper in gateway/heartbeat.py
    try:
        from ..gateway.heartbeat import ActiveHoursConfig, _in_active_hours

        if isinstance(active_hours, dict):
            cfg = ActiveHoursConfig(
                start=active_hours.get("start", "00:00"),
                end=active_hours.get("end", "24:00"),
                timezone=active_hours.get("timezone"),
            )
            return _in_active_hours(cfg)
    except Exception:
        pass

    # Fallback: basic integer-hour comparison (no tz)
    from datetime import datetime
    now = datetime.now()
    if isinstance(active_hours, dict):
        start_raw = active_hours.get("start", 0)
        end_raw = active_hours.get("end", 24)
        try:
            start_hour = int(str(start_raw).split(":")[0])
            end_hour = int(str(end_raw).split(":")[0])
        except (ValueError, IndexError):
            return True
    else:
        return True

    current_hour = now.hour
    if start_hour <= end_hour:
        return start_hour <= current_hour < end_hour
    # Overnight range (e.g. 22-6)
    return current_hour >= start_hour or current_hour < end_hour


async def run_heartbeat_once(
    agent_id: str,
    agent_config: dict[str, Any],
    execute_fn: Callable[[str, str], Any],
    is_busy_fn: Callable[[str], bool] | None = None,
) -> None:
    """Execute a single heartbeat for an agent"""

    if not is_heartbeat_enabled_for_agent(agent_config):
        emit_heartbeat_event({
            "status": "skipped",
            "reason": "disabled",
        })
        return

    if not _is_within_active_hours(agent_config):
        emit_heartbeat_event({
            "status": "skipped",
            "reason": "outside_active_hours",
        })
        return

    if is_busy_fn and is_busy_fn(agent_id):
        emit_heartbeat_event({
            "status": "skipped",
            "reason": "agent_busy",
        })
        return

    prompt = resolve_heartbeat_prompt(agent_config)

    try:
        emit_heartbeat_event({
            "status": "sent",
        })

        result = await execute_fn(agent_id, prompt)

        if result:
            emit_heartbeat_event({
                "status": "ok-token",
            })
        else:
            emit_heartbeat_event({
                "status": "ok-empty",
            })

    except Exception as e:
        logger.error(f"Heartbeat failed for agent {agent_id}: {e}")
        emit_heartbeat_event({
            "status": "failed",
            "reason": str(e),
        })


async def _heartbeat_loop(
    agent_id: str,
    agent_config: dict[str, Any],
    execute_fn: Callable[[str, str], Any],
    is_busy_fn: Callable[[str], bool] | None = None,
) -> None:
    """Internal heartbeat loop for an agent"""
    interval_ms = resolve_heartbeat_interval_ms(agent_config)
    interval_sec = interval_ms / 1000.0

    logger.info(f"Heartbeat loop started for agent {agent_id} (interval: {interval_sec}s)")

    while True:
        try:
            await asyncio.sleep(interval_sec)

            if not _heartbeats_enabled:
                continue

            await run_heartbeat_once(agent_id, agent_config, execute_fn, is_busy_fn)

        except asyncio.CancelledError:
            logger.info(f"Heartbeat loop cancelled for agent {agent_id}")
            break
        except Exception as e:
            logger.error(f"Heartbeat loop error for agent {agent_id}: {e}")
            await asyncio.sleep(60)  # Wait before retry


def start_heartbeat_runner(
    agents: dict[str, dict[str, Any]],
    execute_fn: Callable[[str, str], Any],
    is_busy_fn: Callable[[str], bool] | None = None,
) -> Callable:
    """
    Start heartbeat runners for all configured agents.

    Args:
        agents: Dict of agent_id -> agent_config
        execute_fn: Async function(agent_id, prompt) to execute heartbeat
        is_busy_fn: Function(agent_id) -> bool to check if agent is busy

    Returns:
        stop function to cancel all heartbeat tasks
    """
    for agent_id, agent_config in agents.items():
        if is_heartbeat_enabled_for_agent(agent_config):
            task = asyncio.create_task(
                _heartbeat_loop(agent_id, agent_config, execute_fn, is_busy_fn)
            )
            _running_tasks[agent_id] = task
            logger.info(f"Started heartbeat for agent: {agent_id}")

    def stop():
        for agent_id, task in _running_tasks.items():
            task.cancel()
            logger.info(f"Stopped heartbeat for agent: {agent_id}")
        _running_tasks.clear()

    return stop
