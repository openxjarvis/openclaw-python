"""Message preprocess hook emitter.

Mirrors TypeScript src/auto-reply/reply/message-preprocess-hooks.ts

Fires internal hook events for message preprocessing:
- message:transcribed  (when audio transcript is available)
- message:preprocessed (always, before agent runs)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fire_and_forget(coro, error_label: str = "fire-and-forget hook") -> None:
    """Schedule a coroutine without waiting for its result."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        task.add_done_callback(
            lambda t: logger.debug("%s failed: %s", error_label, t.exception())
            if t.exception() else None
        )
    except RuntimeError:
        logger.debug("%s skipped: no running event loop", error_label)


def emit_pre_agent_message_hooks(
    ctx: Any,
    cfg: Any,
    is_fast_test_env: bool = False,
) -> None:
    """Emit message:transcribed and message:preprocessed internal hooks.

    Args:
        ctx: Finalized message context (FinalizedMsgContext or dict-like
             with SessionKey, Transcript, Body, Channel, etc.).
        cfg: OpenClawConfig instance.
        is_fast_test_env: Skip hooks in fast-test mode.
    """
    if is_fast_test_env:
        return

    session_key = ""
    if hasattr(ctx, "SessionKey"):
        session_key = (ctx.SessionKey or "").strip()
    elif isinstance(ctx, dict):
        session_key = (ctx.get("SessionKey") or ctx.get("session_key") or "").strip()
    if not session_key:
        return

    try:
        from openclaw.hooks.internal_hooks import (
            create_internal_hook_event,
            trigger_internal_hook,
        )
    except ImportError:
        logger.debug("internal_hooks not available; skipping message preprocess hooks")
        return

    transcript = ""
    if hasattr(ctx, "Transcript"):
        transcript = ctx.Transcript or ""
    elif isinstance(ctx, dict):
        transcript = ctx.get("Transcript") or ctx.get("transcript") or ""

    canonical = _derive_inbound_context(ctx)

    if transcript:
        _fire_and_forget(
            trigger_internal_hook(
                create_internal_hook_event(
                    "message",
                    "transcribed",
                    session_key,
                    {**canonical, "transcript": transcript},
                )
            ),
            "get-reply: message:transcribed internal hook failed",
        )

    _fire_and_forget(
        trigger_internal_hook(
            create_internal_hook_event(
                "message",
                "preprocessed",
                session_key,
                canonical,
            )
        ),
        "get-reply: message:preprocessed internal hook failed",
    )


def _derive_inbound_context(ctx: Any) -> dict[str, Any]:
    """Extract canonical inbound message fields from context."""
    result: dict[str, Any] = {}
    field_names = [
        "SessionKey", "Channel", "Body", "From", "To",
        "GroupId", "GroupName", "IsGroup", "Transcript",
        "ReplyToId", "MediaType", "MediaUrl",
    ]
    for name in field_names:
        val = getattr(ctx, name, None) if not isinstance(ctx, dict) else ctx.get(name)
        if val is not None:
            result[name] = val
    return result
