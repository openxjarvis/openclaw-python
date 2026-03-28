"""Active runs management for embedded Pi agent.

This module re-exports from ``pi_embedded`` to ensure a single registry.
Previously this was a duplicate registry that got out of sync with the
one used by ``pi_runtime``.  Now both ``agent_runner`` and ``pi_runtime``
share the same ``ACTIVE_EMBEDDED_RUNS`` map.
"""
from __future__ import annotations

# Re-export everything from the canonical registry in pi_embedded
from openclaw.agents.pi_embedded import (  # noqa: F401
    ACTIVE_EMBEDDED_RUNS,
    EmbeddedPiRunHandle as EmbeddedPiQueueHandle,  # alias for backwards compat
    set_active_embedded_run,
    clear_active_embedded_run,
    get_active_embedded_run,
    is_embedded_pi_run_active,
    is_embedded_pi_run_streaming,
    queue_embedded_pi_message,
    abort_embedded_pi_run,
    wait_for_embedded_pi_run_end as wait_for_embedded_run_end,
)

__all__ = [
    "EmbeddedPiQueueHandle",
    "ACTIVE_EMBEDDED_RUNS",
    "set_active_embedded_run",
    "clear_active_embedded_run",
    "get_active_embedded_run",
    "is_embedded_pi_run_active",
    "is_embedded_pi_run_streaming",
    "queue_embedded_pi_message",
    "abort_embedded_pi_run",
    "wait_for_embedded_run_end",
]
