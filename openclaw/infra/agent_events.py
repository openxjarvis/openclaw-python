"""Global synchronous agent event pub-sub.

Mirrors TypeScript ``src/infra/agent-events.ts``.

Used for:
- Subagent lifecycle events (start, error, end)
- Fallback transition events
- Diagnostic events
- Memory flush triggers

Usage::

    from openclaw.infra.agent_events import emit_agent_event, on_agent_event

    # Subscribe (returns an unsubscribe callable)
    unsub = on_agent_event(lambda evt: print(evt))

    # Emit
    emit_agent_event({"type": "agent_lifecycle", "phase": "start", "runId": "..."})

    # Unsubscribe
    unsub()
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

# Stream type constants — mirrors TS AgentEventStream
AGENT_STREAM_LIFECYCLE: Literal["lifecycle"] = "lifecycle"
AGENT_STREAM_TOOL: Literal["tool"] = "tool"
AGENT_STREAM_ASSISTANT: Literal["assistant"] = "assistant"
AGENT_STREAM_ERROR: Literal["error"] = "error"

AgentEventStream = Literal["lifecycle", "tool", "assistant", "error"] | str

# Module-level listener registry — synchronous callbacks only (mirrors TS)
_listeners: set[Callable[[dict[str, Any]], None]] = set()

# Per-run sequence counters — mirrors TS seqByRun Map<string, number>
_seq_by_run: dict[str, int] = {}

# Per-run context registry — mirrors TS runContextById Map<string, AgentRunContext>
_run_context_by_id: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Run context registry — mirrors TS registerAgentRunContext / getAgentRunContext
# ---------------------------------------------------------------------------

def register_agent_run_context(run_id: str, context: dict[str, Any]) -> None:
    """Register context for a specific run.

    Mirrors TS ``registerAgentRunContext(runId, context)`` from
    ``src/infra/agent-events.ts``.  Stores ``sessionKey``, ``verboseLevel``,
    and ``isHeartbeat`` for later event enrichment.

    Args:
        run_id: Unique run identifier (UUID).
        context: Dict with optional keys: ``session_key``, ``verbose_level``,
                 ``is_heartbeat``.
    """
    if not run_id:
        return
    existing = _run_context_by_id.get(run_id)
    if existing is None:
        _run_context_by_id[run_id] = dict(context)
        return
    # Merge — only overwrite if new value is truthy
    if context.get("session_key") and existing.get("session_key") != context["session_key"]:
        existing["session_key"] = context["session_key"]
    if context.get("verbose_level") and existing.get("verbose_level") != context["verbose_level"]:
        existing["verbose_level"] = context["verbose_level"]
    if context.get("is_heartbeat") is not None and existing.get("is_heartbeat") != context["is_heartbeat"]:
        existing["is_heartbeat"] = context["is_heartbeat"]


def get_agent_run_context(run_id: str) -> dict[str, Any] | None:
    """Return the context registered for *run_id*, or ``None``.

    Mirrors TS ``getAgentRunContext``.
    """
    return _run_context_by_id.get(run_id)


def clear_agent_run_context(run_id: str) -> None:
    """Remove the context registered for *run_id*.

    Mirrors TS ``clearAgentRunContext``.  Call after the run completes to
    prevent unbounded growth of the registry.
    
    Note: This does NOT clear _seq_by_run, matching TS behavior where
    seqByRun is not cleared. If the same runId is reused, seq will continue
    from the previous count.
    """
    _run_context_by_id.pop(run_id, None)


def reset_agent_run_context_for_test() -> None:
    """Clear all run context entries (useful for tests)."""
    _run_context_by_id.clear()
    _seq_by_run.clear()


# ---------------------------------------------------------------------------
# Event emission — mirrors TS emitAgentEvent
# ---------------------------------------------------------------------------

def emit_agent_event(event: dict[str, Any]) -> None:
    """Emit an agent event to all registered listeners.

    Synchronous and non-blocking — mirrors TS ``emitAgentEvent``.
    Automatically enriches the event with ``seq``, ``ts``, and
    ``sessionKey`` from the registered run context (if available).

    Listener exceptions are caught and logged so one bad listener cannot
    break the entire event pipeline.

    Args:
        event: Agent event payload dict with TS-compatible fields:
               - ``runId`` (required): Unique run identifier
               - ``stream`` (required): "lifecycle" | "tool" | "assistant" | "error"
               - ``data`` (required): Arbitrary event data
               - ``sessionKey`` (optional): Session key (enriched from context if missing)
               
               For backward compatibility, also accepts ``run_id`` (converted to ``runId``).
    
    Returns:
        None. Event is enriched with ``seq`` and ``ts`` and broadcast to listeners.
    """
    # Normalize field names: convert snake_case to camelCase for TS compatibility
    run_id = event.get("runId") or event.get("run_id") or ""
    
    # Ensure runId is in the event (prefer camelCase for TS compatibility)
    if run_id and "runId" not in event:
        event = {**event, "runId": run_id}
    
    if run_id:
        next_seq = _seq_by_run.get(run_id, 0) + 1
        _seq_by_run[run_id] = next_seq
        context = _run_context_by_id.get(run_id)
        
        # Enrich with sessionKey from context if not already set
        # Use sessionKey (TS style) not session_key
        if context and not event.get("sessionKey") and not event.get("session_key"):
            session_key = context.get("session_key")
            if session_key:
                event = {**event, "sessionKey": session_key, "seq": next_seq, "ts": int(time.time() * 1000)}
            else:
                event = {**event, "seq": next_seq, "ts": int(time.time() * 1000)}
        else:
            # If sessionKey or session_key already present, ensure we use sessionKey
            if "session_key" in event and "sessionKey" not in event:
                event = {**event, "sessionKey": event["session_key"], "seq": next_seq, "ts": int(time.time() * 1000)}
            else:
                event = {**event, "seq": next_seq, "ts": int(time.time() * 1000)}

    for listener in list(_listeners):
        try:
            listener(event)
        except Exception as exc:
            logger.warning("agent_events: listener error: %s", exc)


def on_agent_event(listener: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Register *listener* to receive all future agent events.

    Mirrors TS ``onAgentEvent``.

    Args:
        listener: Synchronous callable accepting a single event dict.

    Returns:
        An unsubscribe callable — call it to remove the listener.
    """
    _listeners.add(listener)

    def _unsub() -> None:
        _listeners.discard(listener)

    return _unsub


def listener_count() -> int:
    """Return the number of currently registered listeners (useful for testing)."""
    return len(_listeners)


def clear_all_listeners() -> None:
    """Remove every registered listener (useful for tests)."""
    _listeners.clear()


__all__ = [
    "AGENT_STREAM_LIFECYCLE",
    "AGENT_STREAM_TOOL",
    "AGENT_STREAM_ASSISTANT",
    "AGENT_STREAM_ERROR",
    "AgentEventStream",
    "emit_agent_event",
    "on_agent_event",
    "listener_count",
    "clear_all_listeners",
    "register_agent_run_context",
    "get_agent_run_context",
    "clear_agent_run_context",
    "reset_agent_run_context_for_test",
]
