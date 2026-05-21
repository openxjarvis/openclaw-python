"""Voice wake routing resolver — mirrors TS gateway/server-methods/agent.ts lines 627-659."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_voice_wake_route_by_trigger(
    trigger_word: str,
    routing_config: Any,
) -> dict[str, Any] | None:
    """Find a voice wake route entry matching the trigger word.

    Args:
        trigger_word: The trigger word that was spoken.
        routing_config: Voice wake routing config (list of entries or dict).

    Returns:
        Matching route entry dict, or None if not found.
    """
    if not trigger_word or not routing_config:
        return None

    trigger_lower = trigger_word.strip().lower()
    routes: list[Any] = []

    if isinstance(routing_config, list):
        routes = routing_config
    elif isinstance(routing_config, dict):
        routes = routing_config.get("routes") or routing_config.get("entries") or []

    for entry in routes:
        if not isinstance(entry, dict):
            continue
        entry_trigger = (entry.get("trigger") or entry.get("triggerWord") or "").strip().lower()
        if entry_trigger == trigger_lower:
            return entry
        # Also check list of triggers
        triggers = entry.get("triggers") or []
        if isinstance(triggers, list):
            for t in triggers:
                if isinstance(t, str) and t.strip().lower() == trigger_lower:
                    return entry

    return None


def resolve_voice_wake_route_target(
    route_entry: dict[str, Any],
) -> dict[str, str | None]:
    """Extract session_key and agent_id from a route entry.

    Args:
        route_entry: A voice wake route entry dict.

    Returns:
        Dict with session_key and agent_id (either may be None).
    """
    if not route_entry:
        return {"session_key": None, "agent_id": None}

    session_key = (
        route_entry.get("sessionKey")
        or route_entry.get("session_key")
        or None
    )
    agent_id = (
        route_entry.get("agentId")
        or route_entry.get("agent_id")
        or None
    )
    return {"session_key": session_key, "agent_id": agent_id}


def load_voice_wake_routing_config() -> dict[str, Any] | None:
    """Load the voice wake routing config from the openclaw config.

    Returns:
        Voice wake routing config dict, or None if not configured.
    """
    try:
        from openclaw.gateway.config_service import get_config_service
        svc = get_config_service()
        if svc:
            cfg = svc.get_config()
            if isinstance(cfg, dict):
                return cfg.get("voiceWake") or cfg.get("voice_wake")
            voice_wake = getattr(cfg, "voiceWake", None) or getattr(cfg, "voice_wake", None)
            if voice_wake:
                if hasattr(voice_wake, "model_dump"):
                    return voice_wake.model_dump()
                if hasattr(voice_wake, "__dict__"):
                    return vars(voice_wake)
                return dict(voice_wake)
    except Exception as exc:
        logger.debug("Failed to load voice wake routing config: %s", exc)
    return None
