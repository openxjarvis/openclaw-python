"""Heartbeat wake request handling — mirrors src/infra/heartbeat-wake.ts"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Literal, TypedDict


class HeartbeatRunResult(TypedDict, total=False):
    """Result of a heartbeat run"""
    status: Literal["ran", "skipped", "failed"]
    duration_ms: float
    reason: str


HeartbeatWakeHandler = Callable[..., Any]

WakeTimerKind = Literal["normal", "retry"]


class PendingWakeReason(TypedDict, total=False):
    """Pending wake request"""
    reason: str
    priority: int
    requested_at: float
    agent_id: str
    session_key: str


# Module state
_handler: HeartbeatWakeHandler | None = None
_handler_generation = 0
_pending_wakes: dict[str, PendingWakeReason] = {}
_scheduled = False
_running = False
_timer_task: asyncio.Task | None = None
_timer_due_at: float | None = None
_timer_kind: WakeTimerKind | None = None

DEFAULT_COALESCE_MS = 250
DEFAULT_RETRY_MS = 1000

REASON_PRIORITY = {
    "RETRY": 0,
    "INTERVAL": 1,
    "DEFAULT": 2,
    "ACTION": 3,
}


def _resolve_reason_priority(reason: str) -> int:
    """Resolve priority for a wake reason"""
    reason_upper = reason.upper()
    if "RETRY" in reason_upper:
        return REASON_PRIORITY["RETRY"]
    if "INTERVAL" in reason_upper:
        return REASON_PRIORITY["INTERVAL"]
    if "ACTION" in reason_upper:
        return REASON_PRIORITY["ACTION"]
    return REASON_PRIORITY["DEFAULT"]


def _normalize_wake_reason(reason: str | None = None) -> str:
    """Normalize wake reason"""
    return (reason or "default").strip().lower()


def _normalize_wake_target(value: str | None = None) -> str | None:
    """Normalize wake target (agent_id or session_key)"""
    trimmed = value.strip() if isinstance(value, str) else ""
    return trimmed or None


def _get_wake_target_key(agent_id: str | None = None, session_key: str | None = None) -> str:
    """Generate unique key for wake target"""
    agent_id_norm = _normalize_wake_target(agent_id)
    session_key_norm = _normalize_wake_target(session_key)
    return f"{agent_id_norm or ''}::{session_key_norm or ''}"


def _queue_pending_wake_reason(
    reason: str | None = None,
    requested_at: float | None = None,
    agent_id: str | None = None,
    session_key: str | None = None,
) -> None:
    """Queue a pending wake reason"""
    import time
    
    requested_at = requested_at if requested_at is not None else time.time() * 1000
    normalized_reason = _normalize_wake_reason(reason)
    normalized_agent_id = _normalize_wake_target(agent_id)
    normalized_session_key = _normalize_wake_target(session_key)
    
    wake_target_key = _get_wake_target_key(
        agent_id=normalized_agent_id,
        session_key=normalized_session_key,
    )
    
    next_wake: PendingWakeReason = {
        "reason": normalized_reason,
        "priority": _resolve_reason_priority(normalized_reason),
        "requested_at": requested_at,
    }
    
    if normalized_agent_id:
        next_wake["agent_id"] = normalized_agent_id
    if normalized_session_key:
        next_wake["session_key"] = normalized_session_key
    
    previous = _pending_wakes.get(wake_target_key)
    
    if not previous:
        _pending_wakes[wake_target_key] = next_wake
        return
    
    if next_wake["priority"] > previous["priority"]:
        _pending_wakes[wake_target_key] = next_wake
        return
    
    if (
        next_wake["priority"] == previous["priority"]
        and next_wake["requested_at"] >= previous["requested_at"]
    ):
        _pending_wakes[wake_target_key] = next_wake


async def _schedule(coalesce_ms: float, kind: WakeTimerKind = "normal") -> None:
    """Schedule heartbeat wake execution"""
    global _timer_task, _timer_due_at, _timer_kind, _scheduled, _running
    
    import time
    
    delay = max(0, coalesce_ms) if coalesce_ms is not None else DEFAULT_COALESCE_MS
    delay_sec = delay / 1000.0
    due_at = time.time() * 1000 + delay
    
    # Check if we should keep existing timer
    if _timer_task:
        if _timer_kind == "retry":
            return
        if _timer_due_at is not None and _timer_due_at <= due_at:
            return
        
        # Cancel and reschedule
        _timer_task.cancel()
        _timer_task = None
        _timer_due_at = None
        _timer_kind = None
    
    _timer_due_at = due_at
    _timer_kind = kind
    
    async def _execute_wakes():
        global _timer_task, _timer_due_at, _timer_kind, _scheduled, _running
        
        await asyncio.sleep(delay_sec)
        
        _timer_task = None
        _timer_due_at = None
        _timer_kind = None
        _scheduled = False
        
        active = _handler
        if not active:
            return
        
        if _running:
            _scheduled = True
            await _schedule(delay, kind)
            return
        
        pending_batch = list(_pending_wakes.values())
        _pending_wakes.clear()
        _running = True
        
        try:
            for pending_wake in pending_batch:
                wake_opts = {
                    "reason": pending_wake.get("reason"),
                }
                if "agent_id" in pending_wake:
                    wake_opts["agent_id"] = pending_wake["agent_id"]
                if "session_key" in pending_wake:
                    wake_opts["session_key"] = pending_wake["session_key"]
                
                res = await active(**wake_opts)
                
                if (
                    isinstance(res, dict)
                    and res.get("status") == "skipped"
                    and res.get("reason") == "requests-in-flight"
                ):
                    # Retry this wake target
                    _queue_pending_wake_reason(
                        reason=pending_wake.get("reason", "retry"),
                        agent_id=pending_wake.get("agent_id"),
                        session_key=pending_wake.get("session_key"),
                    )
                    await _schedule(DEFAULT_RETRY_MS, "retry")
        except Exception:
            # Error already logged; schedule retry
            for pending_wake in pending_batch:
                _queue_pending_wake_reason(
                    reason=pending_wake.get("reason", "retry"),
                    agent_id=pending_wake.get("agent_id"),
                    session_key=pending_wake.get("session_key"),
                )
            await _schedule(DEFAULT_RETRY_MS, "retry")
        finally:
            _running = False
            if _pending_wakes or _scheduled:
                await _schedule(delay, "normal")
    
    _timer_task = asyncio.create_task(_execute_wakes())


def set_heartbeat_wake_handler(next_handler: HeartbeatWakeHandler | None) -> Callable[[], None]:
    """
    Register (or clear) the heartbeat wake handler.
    
    Returns:
        Disposer function that clears this specific registration
    """
    global _handler, _handler_generation, _timer_task, _timer_due_at, _timer_kind, _running, _scheduled
    
    _handler_generation += 1
    generation = _handler_generation
    _handler = next_handler
    
    if next_handler:
        # New lifecycle starting
        if _timer_task:
            _timer_task.cancel()
        _timer_task = None
        _timer_due_at = None
        _timer_kind = None
        _running = False
        _scheduled = False
    
    if _handler and _pending_wakes:
        asyncio.create_task(_schedule(DEFAULT_COALESCE_MS, "normal"))
    
    def disposer():
        global _handler, _handler_generation
        
        if _handler_generation != generation:
            return
        if _handler is not next_handler:
            return
        
        _handler_generation += 1
        _handler = None
    
    return disposer


async def request_heartbeat_now(
    reason: str | None = None,
    coalesce_ms: float | None = None,
    agent_id: str | None = None,
    session_key: str | None = None,
) -> None:
    """
    Request immediate heartbeat execution.
    
    Args:
        reason: Wake reason
        coalesce_ms: Coalesce delay in milliseconds
        agent_id: Target agent ID
        session_key: Target session key
    """
    _queue_pending_wake_reason(
        reason=reason,
        agent_id=agent_id,
        session_key=session_key,
    )
    await _schedule(coalesce_ms if coalesce_ms is not None else DEFAULT_COALESCE_MS, "normal")


def has_heartbeat_wake_handler() -> bool:
    """Check if a heartbeat wake handler is registered"""
    return _handler is not None


def has_pending_heartbeat_wake() -> bool:
    """Check if there are pending heartbeat wake requests"""
    return bool(_pending_wakes) or _timer_task is not None or _scheduled


def reset_heartbeat_wake_state_for_tests() -> None:
    """Reset module state for testing"""
    global _handler, _handler_generation, _pending_wakes, _scheduled, _running
    global _timer_task, _timer_due_at, _timer_kind
    
    if _timer_task:
        _timer_task.cancel()
    
    _timer_task = None
    _timer_due_at = None
    _timer_kind = None
    _pending_wakes.clear()
    _scheduled = False
    _running = False
    _handler_generation += 1
    _handler = None
