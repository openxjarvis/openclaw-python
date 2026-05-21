"""ACP Session Interaction Mode.

Mirrors TypeScript src/acp/session-interaction-mode.ts

Session interaction mode determines how the agent interacts with the user:
- interactive: normal conversational mode, expects replies
- background: runs autonomously without expecting replies
- stream: streaming-only mode (no chat history updates)
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class SessionInteractionMode(str, Enum):
    """Session interaction mode values.

    Mirrors TS SessionInteractionMode type.
    """

    INTERACTIVE = "interactive"    # normal chat mode
    BACKGROUND = "background"      # autonomous/batch mode
    STREAM = "stream"              # streaming-only output


def resolve_session_interaction_mode(session_entry: Any) -> SessionInteractionMode:
    """Resolve interaction mode from a session entry.

    Mirrors TS resolveSessionInteractionMode().

    Checks in order:
    1. session_entry.acp["interactionMode"]
    2. session_entry.metadata["interaction_mode"]
    3. Default: interactive
    """
    try:
        acp = getattr(session_entry, "acp", None) or {}
        if isinstance(acp, dict):
            mode = acp.get("interactionMode") or acp.get("interaction_mode")
            if mode:
                return SessionInteractionMode(mode)
    except (ValueError, KeyError):
        pass

    try:
        metadata = getattr(session_entry, "metadata", None) or {}
        if isinstance(metadata, dict):
            mode = metadata.get("interaction_mode")
            if mode:
                return SessionInteractionMode(mode)
    except (ValueError, KeyError):
        pass

    return SessionInteractionMode.INTERACTIVE


def update_session_interaction_mode(
    session_key: str,
    mode: SessionInteractionMode | str,
) -> None:
    """Update the interaction mode for a session.

    Mirrors TS updateSessionInteractionMode().
    Writes into the session entry's ACP blob.
    """
    mode_str = mode.value if isinstance(mode, SessionInteractionMode) else str(mode)
    try:
        from openclaw.agents.session_store import get_session_store
        import asyncio
        store = get_session_store()
        if store and hasattr(store, "get_session_sync"):
            session = store.get_session_sync(session_key)
            if session:
                acp = dict(session.acp or {})
                acp["interactionMode"] = mode_str
                asyncio.ensure_future(store.patch_session(session_key, {"acp": acp}))
    except Exception:
        pass
