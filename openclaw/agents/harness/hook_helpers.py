"""Harness lifecycle hook helpers.

Mirrors TypeScript src/agents/harness/hook-helpers.ts and lifecycle-hook-helpers.ts
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def run_before_attempt_hooks(
    hooks: list[Callable],
    params: Any,
) -> None:
    """Run all before-attempt hooks in sequence."""
    for hook in hooks:
        try:
            result = hook(params)
            if hasattr(result, "__await__"):
                await result
        except Exception:
            logger.exception("Error in before-attempt hook")


async def run_after_attempt_hooks(
    hooks: list[Callable],
    result: Any,
    params: Any,
) -> None:
    """Run all after-attempt hooks in sequence."""
    for hook in hooks:
        try:
            r = hook(result, params)
            if hasattr(r, "__await__"):
                await r
        except Exception:
            logger.exception("Error in after-attempt hook")


async def run_on_error_hooks(
    hooks: list[Callable],
    error: Exception,
    params: Any,
) -> None:
    """Run error hooks."""
    for hook in hooks:
        try:
            r = hook(error, params)
            if hasattr(r, "__await__"):
                await r
        except Exception:
            logger.exception("Error in harness error hook")
