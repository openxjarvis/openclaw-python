"""Heartbeat event system — mirrors src/infra/heartbeat-events.ts"""
from __future__ import annotations

from typing import Any, Callable, Literal, TypedDict


HeartbeatIndicatorType = Literal["ok", "alert", "error"]


class HeartbeatEventPayload(TypedDict, total=False):
    """Heartbeat event payload"""
    ts: float
    status: Literal["sent", "ok-empty", "ok-token", "skipped", "failed"]
    to: str
    account_id: str
    preview: str
    duration_ms: float
    has_media: bool
    reason: str
    channel: str
    silent: bool
    indicator_type: HeartbeatIndicatorType


def resolve_indicator_type(status: str) -> HeartbeatIndicatorType | None:
    """
    Map heartbeat status to indicator type for UI display.
    
    Args:
        status: Heartbeat status
        
    Returns:
        Indicator type or None for skipped status
    """
    if status in ("ok-empty", "ok-token"):
        return "ok"
    if status == "sent":
        return "alert"
    if status == "failed":
        return "error"
    if status == "skipped":
        return None
    return None


# Module state (singleton pattern)
_last_heartbeat: HeartbeatEventPayload | None = None
_listeners: set[Callable[[HeartbeatEventPayload], None]] = set()


def emit_heartbeat_event(evt: dict[str, Any]) -> None:
    """
    Emit a heartbeat event.
    
    Args:
        evt: Event payload (without ts, which is added automatically)
    """
    import time
    
    global _last_heartbeat
    
    # Enrich with timestamp
    enriched: HeartbeatEventPayload = {
        "ts": time.time() * 1000,  # milliseconds
        **evt,  # type: ignore
    }
    
    _last_heartbeat = enriched
    
    # Notify listeners
    for listener in list(_listeners):
        try:
            listener(enriched)
        except Exception:
            # Ignore listener errors
            pass


def on_heartbeat_event(listener: Callable[[HeartbeatEventPayload], None]) -> Callable[[], None]:
    """
    Register a heartbeat event listener.
    
    Args:
        listener: Callback function
        
    Returns:
        Unsubscribe function
    """
    _listeners.add(listener)
    
    def unsubscribe():
        _listeners.discard(listener)
    
    return unsubscribe


def get_last_heartbeat_event() -> HeartbeatEventPayload | None:
    """
    Get the last heartbeat event.
    
    Returns:
        Last heartbeat event or None
    """
    return _last_heartbeat

