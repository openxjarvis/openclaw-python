"""Agent Harness global registry.

Mirrors TypeScript src/agents/harness/registry.ts

Uses a module-level singleton (equivalent to TS globalThis symbol key)
to store registered harnesses across the process.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import AgentHarness

logger = logging.getLogger(__name__)

# Module-level singleton registry — mirrors TS globalThis[HARNESS_REGISTRY_KEY]
_HARNESS_REGISTRY: dict[str, "AgentHarness"] = {}

# Track which sessions are pinned to which harness id
# session_key → harness_id
_PINNED_SESSIONS: dict[str, str] = {}


def register_global_agent_harness(harness: "AgentHarness") -> None:
    """Register a harness globally.

    Mirrors TS registerGlobalAgentHarness().
    Raises ValueError on duplicate id.
    """
    hid = harness.id
    if hid in _HARNESS_REGISTRY:
        existing = _HARNESS_REGISTRY[hid]
        owner = getattr(existing, "plugin_id", None) or "built-in"
        raise ValueError(
            f"Agent harness id '{hid}' is already registered by '{owner}'. "
            "Harness ids must be unique."
        )
    _HARNESS_REGISTRY[hid] = harness
    logger.debug("Registered agent harness '%s'", hid)


def get_agent_harness(harness_id: str) -> "AgentHarness | None":
    """Look up a harness by id."""
    return _HARNESS_REGISTRY.get(harness_id)


def list_registered_agent_harnesses() -> list["AgentHarness"]:
    """Return all registered harnesses."""
    return list(_HARNESS_REGISTRY.values())


def pin_session_harness(session_key: str, harness_id: str) -> None:
    """Pin a session to a specific harness id.

    Called when a run starts so subsequent turns use the same harness.
    Mirrors TS session agentHarnessId pin semantics.
    """
    _PINNED_SESSIONS[session_key] = harness_id


def get_pinned_session_harness(session_key: str) -> str | None:
    """Get the pinned harness id for a session, or None."""
    return _PINNED_SESSIONS.get(session_key)


def reset_registered_agent_harness_sessions() -> None:
    """Clear all session pins (used in tests / gateway restart)."""
    _PINNED_SESSIONS.clear()


async def dispose_registered_agent_harnesses() -> None:
    """Call dispose() on all registered harnesses (gateway shutdown)."""
    for harness in list(_HARNESS_REGISTRY.values()):
        dispose = getattr(harness, "dispose", None)
        if callable(dispose):
            try:
                await dispose()
            except Exception:
                logger.exception("Error disposing harness '%s'", harness.id)


def clear_harness_registry() -> None:
    """Clear registry entirely — for tests only."""
    _HARNESS_REGISTRY.clear()
    _PINNED_SESSIONS.clear()
