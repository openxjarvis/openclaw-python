"""Session lifecycle hook payload builders.

Mirrors TypeScript src/auto-reply/reply/session-hooks.ts

Builds hook payloads for session start/end events that are emitted
through the internal hooks system.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionHookContext:
    """Context for session lifecycle hooks."""
    session_id: str
    session_key: str
    agent_id: str


def _resolve_agent_id(session_key: str, cfg: Any) -> str:
    """Resolve agent ID for a session key."""
    try:
        from openclaw.agents.agent_scope import resolve_session_agent_id
        return resolve_session_agent_id(session_key=session_key, config=cfg)
    except Exception:
        return "default"


def build_session_hook_context(
    session_id: str,
    session_key: str,
    cfg: Any,
) -> SessionHookContext:
    return SessionHookContext(
        session_id=session_id,
        session_key=session_key,
        agent_id=_resolve_agent_id(session_key, cfg),
    )


def build_session_start_hook_payload(
    session_id: str,
    session_key: str,
    cfg: Any,
    resumed_from: str | None = None,
) -> dict[str, Any]:
    """Build payload for session:start hook event."""
    ctx = build_session_hook_context(session_id, session_key, cfg)
    event: dict[str, Any] = {
        "sessionId": session_id,
        "sessionKey": session_key,
    }
    if resumed_from:
        event["resumedFrom"] = resumed_from
    return {"event": event, "context": ctx.__dict__}


def build_session_end_hook_payload(
    session_id: str,
    session_key: str,
    cfg: Any,
    message_count: int = 0,
) -> dict[str, Any]:
    """Build payload for session:end hook event."""
    ctx = build_session_hook_context(session_id, session_key, cfg)
    return {
        "event": {
            "sessionId": session_id,
            "sessionKey": session_key,
            "messageCount": message_count,
        },
        "context": ctx.__dict__,
    }
