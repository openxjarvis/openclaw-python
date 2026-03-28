"""Stream mode configuration and decision logic.

Mirrors TypeScript src/telegram/bot-message-dispatch.ts lines 171-313
"""
from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

StreamMode = Literal["off", "partial", "full", "block"]
ReasoningLevel = Literal["off", "on", "stream"]


def resolve_reasoning_level(
    cfg: dict[str, Any],
    session_key: str | None,
    agent_id: str,
) -> ReasoningLevel:
    """Resolve reasoning level from session config.
    
    Mirrors TS resolveTelegramReasoningLevel() in bot-message-dispatch.ts L108-129.
    
    Args:
        cfg: OpenClaw configuration
        session_key: Session key for looking up session-level config
        agent_id: Agent ID for resolving session store path
    
    Returns:
        "off" | "on" | "stream"
    """
    if not session_key:
        return "off"
    
    try:
        from openclaw.config.sessions import load_session_store, resolve_store_path
        
        store_path = resolve_store_path(
            cfg.get("session", {}).get("store"),
            agent_id=agent_id
        )
        store = load_session_store(store_path, skip_cache=True)
        entry = store.get(session_key.lower()) or store.get(session_key)
        
        level = entry.get("reasoningLevel") if entry else None
        if level in ("on", "stream"):
            return level
    except Exception:
        pass
    
    return "off"


_VALID_STREAM_MODES = {"off", "partial", "full"}


def resolve_telegram_preview_stream_mode(
    telegram_cfg: dict[str, Any],
) -> StreamMode:
    """Resolve stream mode from Telegram account config.

    Mirrors TS resolveTelegramPreviewStreamMode() logic:
      1. Check ``streaming`` field first:
         - string "progress" → "partial"
         - valid mode string → use as-is
         - boolean True → "partial", False → "off"
      2. Fall back to legacy ``streamMode`` field with same rules.
      3. Default: "partial"
    """
    for key in ("streaming", "streamMode"):
        val = telegram_cfg.get(key)
        if val is None:
            continue
        if isinstance(val, str):
            if val == "progress":
                return "partial"
            if val in _VALID_STREAM_MODES:
                return val  # type: ignore[return-value]
        if isinstance(val, bool):
            return "partial" if val else "off"
    return "partial"


def resolve_stream_mode_config(
    cfg: dict[str, Any],
    telegram_cfg: dict[str, Any],
    session_key: str | None,
    agent_id: str,
    is_dm: bool,
    stream_mode: StreamMode = "partial",
) -> dict[str, Any]:
    """Resolve complete stream mode configuration.
    
    Mirrors TS bot-message-dispatch.ts L171-313 logic.
    
    Args:
        cfg: OpenClaw configuration
        telegram_cfg: Telegram-specific account config
        session_key: Session key for looking up session-level config
        agent_id: Agent ID for resolving session store path
        is_dm: True if direct message (DM) chat
        stream_mode: Base stream mode (default "partial")
    
    Returns:
        {
            "stream_mode": "off" | "partial" | "block",
            "reasoning_level": "off" | "on" | "stream",
            "can_stream_answer_draft": bool,
            "can_stream_reasoning_draft": bool,
            "disable_block_streaming": bool | None,
            "is_dm": bool,
            "force_block_streaming_for_reasoning": bool,
            "stream_reasoning_draft": bool,
            "account_block_streaming_enabled": bool,
            "preview_streaming_enabled": bool,
        }
    """
    # Resolve reasoning level from session
    reasoning_level = resolve_reasoning_level(cfg, session_key, agent_id)
    
    # Check account-level block streaming config
    # Mirrors TS L171-174
    _bs = telegram_cfg.get("blockStreaming")
    if isinstance(_bs, bool):
        account_block_streaming_enabled = _bs
    else:
        account_block_streaming_enabled = cfg.get("agents", {}).get("defaults", {}).get("blockStreamingDefault") == "on"
    
    # Force block streaming for reasoning=on
    # Mirrors TS L180
    force_block_streaming_for_reasoning = (reasoning_level == "on")
    
    # Stream reasoning draft when reasoning=stream
    # Mirrors TS L181
    stream_reasoning_draft = (reasoning_level == "stream")
    
    # Preview streaming enabled unless stream_mode=off
    # Mirrors TS L182
    preview_streaming_enabled = (stream_mode != "off")
    
    # ✅ KEY LOGIC: Draft优先，避免与block冲突
    # Mirrors TS L183-184
    can_stream_answer_draft = (
        preview_streaming_enabled
        and not account_block_streaming_enabled
        and not force_block_streaming_for_reasoning
    )
    
    # Reasoning draft可用: answer draft可用 OR reasoning=stream
    # Mirrors TS L185
    can_stream_reasoning_draft = can_stream_answer_draft or stream_reasoning_draft
    
    # ✅ KEY LOGIC: disableBlockStreaming决策
    # Mirrors TS L305-313
    disable_block_streaming: bool | None = None
    
    if not preview_streaming_enabled:
        # stream_mode=off → 禁用block streaming
        disable_block_streaming = True
    elif force_block_streaming_for_reasoning:
        # reasoning=on → 强制启用block streaming
        disable_block_streaming = False
    elif isinstance(telegram_cfg.get("blockStreaming"), bool):
        # Telegram account config明确设置
        disable_block_streaming = not telegram_cfg["blockStreaming"]
    elif can_stream_answer_draft:
        # ✅ Draft优先: 可用draft streaming时禁用block streaming
        disable_block_streaming = True
    else:
        # Use default from resolve_block_streaming_config
        disable_block_streaming = None
    
    logger.debug(
        f"Stream mode resolved: reasoning={reasoning_level}, "
        f"can_draft_answer={can_stream_answer_draft}, "
        f"can_draft_reasoning={can_stream_reasoning_draft}, "
        f"disable_block={disable_block_streaming}"
    )
    
    return {
        "stream_mode": stream_mode,
        "reasoning_level": reasoning_level,
        "can_stream_answer_draft": can_stream_answer_draft,
        "can_stream_reasoning_draft": can_stream_reasoning_draft,
        "disable_block_streaming": disable_block_streaming,
        "is_dm": is_dm,
        "force_block_streaming_for_reasoning": force_block_streaming_for_reasoning,
        "stream_reasoning_draft": stream_reasoning_draft,
        "account_block_streaming_enabled": account_block_streaming_enabled,
        "preview_streaming_enabled": preview_streaming_enabled,
    }


__all__ = [
    "StreamMode",
    "ReasoningLevel",
    "resolve_reasoning_level",
    "resolve_telegram_preview_stream_mode",
    "resolve_stream_mode_config",
]
