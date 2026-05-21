"""Harness tool result middleware.

Mirrors TypeScript src/agents/harness/tool-result-middleware.ts

Allows plugins to intercept and transform tool results before they are
included in the conversation history.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Global middleware chain — populated by plugin registrations
_TOOL_RESULT_MIDDLEWARE: list[Callable] = []


def register_tool_result_middleware(middleware: Callable) -> None:
    """Register a tool result middleware function.

    Middleware signature: (tool_result: dict, context: dict) -> dict
    """
    _TOOL_RESULT_MIDDLEWARE.append(middleware)


def clear_tool_result_middleware() -> None:
    """Clear all middleware (for tests)."""
    _TOOL_RESULT_MIDDLEWARE.clear()


async def apply_tool_result_middleware(
    tool_result: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Apply all registered middleware to a tool result.

    Mirrors TS applyAgentToolResultMiddleware().
    Each middleware receives the (possibly transformed) result and returns a new one.
    """
    result = tool_result
    for middleware in _TOOL_RESULT_MIDDLEWARE:
        try:
            transformed = middleware(result, context)
            if hasattr(transformed, "__await__"):
                transformed = await transformed
            if transformed is not None:
                result = transformed
        except Exception:
            logger.exception("Error in tool result middleware")
    return result
