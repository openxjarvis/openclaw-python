"""sessions_yield tool — pause current session and wait for subagent.

Mirrors TypeScript:
  src/agents/run/attempt.sessions-yield.ts
  src/agents/tools/sessions-yield-tool.ts

The sessions_yield tool is used by an agent to pause its own run and
wait for a subagent (spawned session) to complete. This enables
coordinated multi-agent workflows where the parent delegates a task
and then resumes after the delegate finishes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

TOOL_NAME = "sessions_yield"
TOOL_DESCRIPTION = (
    "Pause the current session and wait for a spawned subagent session to complete. "
    "Use this after spawning a subagent to collect its result before continuing. "
    "The session will resume automatically when the subagent finishes."
)


async def run_sessions_yield(
    session_key: str,
    *,
    target_session_key: str | None = None,
    timeout_ms: int = 300_000,
) -> dict[str, Any]:
    """Wait for target_session_key's subagent run to complete.

    Mirrors TS runSessionsYield() in attempt.sessions-yield.ts.

    Args:
        session_key: The parent session that is yielding.
        target_session_key: The subagent session to wait for.
        timeout_ms: Maximum wait time in milliseconds (default 5 min).

    Returns:
        {"ok": True, "result": ..., "timedOut": bool}
    """
    if not target_session_key:
        return {"ok": False, "error": "target_session_key is required"}

    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    poll_interval = 0.5  # 500ms poll

    while asyncio.get_event_loop().time() < deadline:
        try:
            from openclaw.agents.subagent_registry import get_global_registry
            registry = get_global_registry()
            if hasattr(registry, "is_session_done") and registry.is_session_done(target_session_key):
                result = None
                if hasattr(registry, "get_session_result"):
                    result = registry.get_session_result(target_session_key)
                return {"ok": True, "result": result, "timedOut": False}
        except Exception:
            pass
        await asyncio.sleep(poll_interval)

    return {"ok": True, "result": None, "timedOut": True}


class SessionsYieldTool:
    """sessions_yield tool implementation.

    Follows the openclaw AgentToolBase interface pattern.
    """

    name = TOOL_NAME
    description = TOOL_DESCRIPTION

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target_session_key": {
                    "type": "string",
                    "description": "The session key of the subagent to wait for.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Maximum wait time in milliseconds (default 300000).",
                    "default": 300_000,
                },
            },
            "required": ["target_session_key"],
        }

    async def execute(self, params: dict[str, Any], context: Any = None) -> dict[str, Any]:
        session_key = (
            getattr(context, "session_key", None)
            or getattr(context, "SessionKey", None)
            or ""
        )
        return await run_sessions_yield(
            session_key=session_key,
            target_session_key=params.get("target_session_key"),
            timeout_ms=int(params.get("timeout_ms", 300_000)),
        )
