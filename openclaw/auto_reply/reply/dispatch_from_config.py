"""Dispatch reply from config — main message processing entry point.

Port of TypeScript:
  openclaw/src/auto-reply/reply/dispatch-from-config.ts

Responsibilities (per TS source):
  1. Duplicate-message detection (shouldSkipDuplicateInbound)
  2. Plugin & internal hook firing (message_received)
  3. Cross-channel routing setup (OriginatingChannel ≠ current surface)
  4. Fast-abort detection (/stop, abort, etc.)
  5. Tool-result and block-reply callbacks (with TTS support stub)
  6. Final reply TTS accumulation
  7. Route to originating channel OR send via dispatcher
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from ..inbound_context import MsgContext
from .inbound_dedupe import should_skip_duplicate_inbound
from .get_reply import (
    ReplyPayload,
    get_reply_from_config,
    try_fast_abort,
    format_abort_reply_text,
)
from .reply_dispatcher import ReplyDispatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dedupe module wrapper (for backward compatibility with tests)
# ---------------------------------------------------------------------------

class _DedupeModule:
    """Wrapper for dedupe functionality to match test expectations."""
    def __init__(self):
        # Import the actual cache from inbound_dedupe
        from .inbound_dedupe import INBOUND_DEDUPE_CACHE
        self._cache = INBOUND_DEDUPE_CACHE

_dedupe = _DedupeModule()

# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    queued_final: bool = False
    counts: dict[str, int] = field(default_factory=lambda: {"tool": 0, "block": 0, "final": 0})
    skipped: bool = False
    skip_reason: str | None = None


# ---------------------------------------------------------------------------
# Audio-context detection (mirrors TS isInboundAudioContext)
# ---------------------------------------------------------------------------

_AUDIO_PLACEHOLDER_RE = __import__("re").compile(
    r"^<media:audio>(\s*\([^)]*\))?$", __import__("re").IGNORECASE
)
_AUDIO_HEADER_RE = __import__("re").compile(r"^\[Audio\b", __import__("re").IGNORECASE)


def _is_inbound_audio(ctx: MsgContext) -> bool:
    media_type = getattr(ctx, "MediaType", None)
    media_types = getattr(ctx, "MediaTypes", None) or []
    raw_types = [t for t in ([media_type] + list(media_types)) if isinstance(t, str)]
    normalized = [t.split(";")[0].strip().lower() for t in raw_types]
    if any(t == "audio" or t.startswith("audio/") for t in normalized):
        return True
    body = (
        getattr(ctx, "BodyForCommands", None)
        or getattr(ctx, "CommandBody", None)
        or getattr(ctx, "RawBody", None)
        or getattr(ctx, "Body", "")
        or ""
    )
    trimmed = body.strip()
    if not trimmed:
        return False
    return bool(_AUDIO_PLACEHOLDER_RE.match(trimmed)) or bool(_AUDIO_HEADER_RE.match(trimmed))


# ---------------------------------------------------------------------------
# Channel routing helpers
# ---------------------------------------------------------------------------

_ROUTABLE_CHANNELS = {"telegram", "slack", "discord", "sms", "whatsapp", "twilio", "teams"}


def _is_routable_channel(channel: str | None) -> bool:
    if not channel:
        return False
    return channel.lower() in _ROUTABLE_CHANNELS


async def _route_reply_to_channel(
    payload: ReplyPayload,
    channel: str,
    to: Any,
    session_key: str | None,
    cfg: dict[str, Any],
) -> bool:
    """Send a payload to a different channel (cross-provider routing)."""
    try:
        from openclaw.gateway.route_reply import route_reply
        result = await route_reply(
            payload=payload,
            channel=channel,
            to=to,
            session_key=session_key,
            cfg=cfg,
        )
        return result.get("ok", False)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning(f"dispatch_from_config: route_reply failed: {exc}")
    return False


# ---------------------------------------------------------------------------
# Hook firing helpers
# ---------------------------------------------------------------------------

def _fire_message_received_hooks(ctx: MsgContext, cfg: dict[str, Any]) -> None:
    """Fire plugin hooks and internal hooks for message_received (fire-and-forget)."""
    import asyncio
    
    # Fire plugin hooks (existing)
    try:
        from openclaw.plugins.hook_runner import get_global_hook_runner
        runner = get_global_hook_runner()
        if runner and hasattr(runner, "has_hooks") and runner.has_hooks("message_received"):
            content = (
                getattr(ctx, "BodyForCommands", None)
                or getattr(ctx, "RawBody", None)
                or getattr(ctx, "Body", "")
                or ""
            )
            coro = runner.run_message_received(
                {
                    "from": getattr(ctx, "From", "") or "",
                    "content": content,
                    "timestamp": getattr(ctx, "Timestamp", None),
                    "metadata": {
                        "to": getattr(ctx, "To", None),
                        "provider": getattr(ctx, "Provider", None),
                        "surface": getattr(ctx, "Surface", None),
                        "messageId": (
                            getattr(ctx, "MessageSidFull", None)
                            or getattr(ctx, "MessageSid", None)
                        ),
                        "senderId": getattr(ctx, "SenderId", None),
                        "senderName": getattr(ctx, "SenderName", None),
                    },
                },
                {
                    "channelId": (
                        getattr(ctx, "OriginatingChannel", None)
                        or getattr(ctx, "Surface", None)
                        or getattr(ctx, "Provider", None)
                        or ""
                    ).lower(),
                    "accountId": getattr(ctx, "AccountId", None),
                    "conversationId": (
                        getattr(ctx, "OriginatingTo", None)
                        or getattr(ctx, "To", None)
                        or getattr(ctx, "From", None)
                    ),
                },
            )
            try:
                asyncio.ensure_future(coro)
            except Exception:
                pass
    except Exception:
        pass
    
    # Fire internal hooks (HOOK.md discovery system)
    try:
        from openclaw.hooks.internal_hooks import create_internal_hook_event, trigger_internal_hook
        
        session_key = getattr(ctx, "SessionKey", None) or ""
        if session_key:
            content = (
                getattr(ctx, "BodyForCommands", None)
                or getattr(ctx, "RawBody", None)
                or getattr(ctx, "Body", "")
                or ""
            )
            timestamp = getattr(ctx, "Timestamp", None)
            channel_id = (
                getattr(ctx, "OriginatingChannel", None)
                or getattr(ctx, "Surface", None)
                or getattr(ctx, "Provider", None)
                or ""
            ).lower()
            conversation_id = (
                getattr(ctx, "OriginatingTo", None)
                or getattr(ctx, "To", None)
                or getattr(ctx, "From", None)
            )
            message_id_for_hook = (
                getattr(ctx, "MessageSidFull", None)
                or getattr(ctx, "MessageSid", None)
            )
            
            hook_event = create_internal_hook_event(
                "message",
                "received",
                session_key,
                {
                    "from": getattr(ctx, "From", "") or "",
                    "content": content,
                    "timestamp": timestamp,
                    "channelId": channel_id,
                    "channel_id": channel_id,
                    "accountId": getattr(ctx, "AccountId", None),
                    "account_id": getattr(ctx, "AccountId", None),
                    "conversationId": conversation_id,
                    "conversation_id": conversation_id,
                    "messageId": message_id_for_hook,
                    "message_id": message_id_for_hook,
                    "metadata": {
                        "to": getattr(ctx, "To", None),
                        "provider": getattr(ctx, "Provider", None),
                        "surface": getattr(ctx, "Surface", None),
                        "threadId": getattr(ctx, "MessageThreadId", None),
                        "senderId": getattr(ctx, "SenderId", None),
                        "senderName": getattr(ctx, "SenderName", None),
                        "senderUsername": getattr(ctx, "SenderUsername", None),
                        "senderE164": getattr(ctx, "SenderE164", None),
                    }
                }
            )
            
            # Fire and forget
            async def _trigger():
                await trigger_internal_hook(hook_event)
            
            try:
                asyncio.ensure_future(_trigger())
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# TTS helpers (stub — full implementation in AR-3)
# ---------------------------------------------------------------------------

async def _maybe_apply_tts(
    payload: ReplyPayload,
    cfg: dict[str, Any],
    channel: str | None,
    kind: str,
    inbound_audio: bool,
    tts_auto: str | None,
) -> ReplyPayload:
    """Apply TTS to payload if configured. Stub — passes through for now."""
    try:
        from openclaw.tts.tts import maybe_apply_tts_to_payload
        return await maybe_apply_tts_to_payload(
            payload=payload, cfg=cfg, channel=channel, kind=kind,
            inbound_audio=inbound_audio, tts_auto=tts_auto,
        )
    except Exception:
        pass
    return payload


def _resolve_session_tts_auto(ctx: MsgContext, cfg: dict[str, Any]) -> str | None:
    try:
        from openclaw.tts.tts import normalize_tts_auto_mode
        from openclaw.config.sessions import load_session_store, resolve_store_path
        session_key = (getattr(ctx, "SessionKey", "") or "").strip()
        if not session_key:
            return None
        store_path = resolve_store_path(cfg.get("session", {}).get("store"), {})
        store = load_session_store(store_path)
        entry = store.get(session_key.lower()) or store.get(session_key)
        return normalize_tts_auto_mode(entry.get("ttsAuto") if entry else None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main dispatch function
# ---------------------------------------------------------------------------

async def dispatch_reply_from_config(
    ctx: MsgContext,
    cfg: dict[str, Any],
    dispatcher: ReplyDispatcher,
    *,
    runtime: Any = None,
    reply_options: dict[str, Any] | None = None,
    channel_send_fn: Callable | None = None,
) -> DispatchResult:
    """
    Main message dispatch entry point.

    Mirrors TS dispatchReplyFromConfig().

    Args:
        ctx: Finalized message context
        cfg: OpenClaw config dict
        dispatcher: Reply dispatcher (handles tool/block/final reply)
        runtime: Agent runtime (MultiProviderRuntime or similar)
        reply_options: Extra options forwarded to get_reply_from_config
        channel_send_fn: Explicit channel send function (overrides dispatcher when cross-routing)

    Returns:
        DispatchResult with queued_final and counts
    """
    reply_options = reply_options or {}
    start_time = time.monotonic()

    channel = str(
        getattr(ctx, "Surface", None) or getattr(ctx, "Provider", None) or "unknown"
    ).lower()
    chat_id = getattr(ctx, "To", None) or getattr(ctx, "From", None)
    session_key = getattr(ctx, "SessionKey", None) or ""

    result = DispatchResult()

    # ------------------------------------------------------------------
    # 1. Duplicate check (mirrors shouldSkipDuplicateInbound)
    # ------------------------------------------------------------------
    # Matches TS dispatch-from-config.ts line 162
    if should_skip_duplicate_inbound(ctx):
        logger.debug(f"dispatch_from_config: skipping duplicate message")
        result.skipped = True
        result.skip_reason = "duplicate"
        return result

    # ------------------------------------------------------------------
    # 2. Hook firing (fire-and-forget)
    # ------------------------------------------------------------------
    _fire_message_received_hooks(ctx, cfg)

    # ------------------------------------------------------------------
    # 3. Cross-channel routing setup
    # ------------------------------------------------------------------
    originating_channel = getattr(ctx, "OriginatingChannel", None)
    originating_to = getattr(ctx, "OriginatingTo", None)
    current_surface = channel
    should_route_to_originating = (
        _is_routable_channel(originating_channel)
        and originating_to is not None
        and originating_channel != current_surface
    )
    tts_channel = originating_channel if should_route_to_originating else current_surface

    # ------------------------------------------------------------------
    # 4. Audio / TTS context
    # ------------------------------------------------------------------
    inbound_audio = _is_inbound_audio(ctx)
    session_tts_auto = _resolve_session_tts_auto(ctx, cfg)

    # ------------------------------------------------------------------
    # 5. Fast abort detection
    # ------------------------------------------------------------------
    fast_abort = try_fast_abort(ctx)
    if fast_abort.get("handled"):
        abort_text = format_abort_reply_text(fast_abort.get("stopped_subagents", 0))
        abort_payload = ReplyPayload(text=abort_text)
        tts_payload = await _maybe_apply_tts(abort_payload, cfg, tts_channel, "final", inbound_audio, session_tts_auto)
        if should_route_to_originating and originating_channel and originating_to:
            ok = await _route_reply_to_channel(tts_payload, originating_channel, originating_to, session_key, cfg)
            result.queued_final = ok
            result.counts["final"] += 1 if ok else 0
        else:
            await dispatcher.send_final_reply(tts_payload.text or abort_text)
            result.queued_final = True
            result.counts["final"] += 1
        logger.debug(f"dispatch_from_config: fast_abort handled [{channel}]")
        return result

    # ------------------------------------------------------------------
    # 6. Block-reply and tool-result callbacks
    # ------------------------------------------------------------------
    should_send_tool_summaries = (
        getattr(ctx, "ChatType", "") != "group"
        and getattr(ctx, "CommandSource", "") != "native"
    )

    accumulated_block_text = ""
    block_count = 0

    async def on_block_reply(payload: ReplyPayload) -> None:
        nonlocal accumulated_block_text, block_count
        if payload.text:
            if accumulated_block_text:
                accumulated_block_text += "\n"
            accumulated_block_text += payload.text
            block_count += 1
        tts_p = await _maybe_apply_tts(payload, cfg, tts_channel, "block", inbound_audio, session_tts_auto)
        if should_route_to_originating and originating_channel and originating_to:
            await _route_reply_to_channel(tts_p, originating_channel, originating_to, session_key, cfg)
        else:
            result.counts["block"] += 1
            await dispatcher.send_block_reply(tts_p)

    async def on_tool_result(payload: ReplyPayload) -> None:
        if not should_send_tool_summaries:
            has_media = bool(payload.media_url) or bool(payload.media_urls)
            if not has_media:
                return
            payload = ReplyPayload(media_url=payload.media_url, media_urls=payload.media_urls)
        tts_p = await _maybe_apply_tts(payload, cfg, tts_channel, "tool", inbound_audio, session_tts_auto)
        if should_route_to_originating and originating_channel and originating_to:
            await _route_reply_to_channel(tts_p, originating_channel, originating_to, session_key, cfg)
        else:
            result.counts["tool"] += 1
            if tts_p.text:
                await dispatcher.send_tool_result("", tts_p.text)

    # ------------------------------------------------------------------
    # 7. Run reply generation
    # ------------------------------------------------------------------
    try:
        reply_result = await get_reply_from_config(
            ctx,
            opts=reply_options,
            cfg=cfg,
            runtime=runtime,
            on_block_reply=on_block_reply,
            on_tool_result=on_tool_result,
        )
    except Exception as exc:
        logger.error(f"dispatch_from_config: get_reply_from_config failed: {exc}", exc_info=True)
        raise

    replies: list[ReplyPayload] = (
        reply_result if isinstance(reply_result, list)
        else [reply_result] if reply_result is not None
        else []
    )

    # ------------------------------------------------------------------
    # 8. Send final replies (with TTS)
    # ------------------------------------------------------------------
    for reply in replies:
        tts_reply = await _maybe_apply_tts(reply, cfg, tts_channel, "final", inbound_audio, session_tts_auto)
        if should_route_to_originating and originating_channel and originating_to:
            ok = await _route_reply_to_channel(tts_reply, originating_channel, originating_to, session_key, cfg)
            if ok:
                result.counts["final"] += 1
                result.queued_final = True
        else:
            result.counts["final"] += 1
            metadata: dict = {}
            if tts_reply.media_url:
                metadata["media_url"] = tts_reply.media_url
            if tts_reply.reply_to_id:
                metadata["reply_to_id"] = tts_reply.reply_to_id
            await dispatcher.send_final_reply(tts_reply.text or "", metadata or None)
            result.queued_final = True

    # ------------------------------------------------------------------
    # 9. TTS-only payload for block-streamed content (no final reply)
    # ------------------------------------------------------------------
    if not replies and block_count > 0 and accumulated_block_text.strip():
        try:
            tts_synthetic = await _maybe_apply_tts(
                ReplyPayload(text=accumulated_block_text),
                cfg, tts_channel, "final", inbound_audio, session_tts_auto,
            )
            if tts_synthetic.media_url:
                tts_only = ReplyPayload(
                    media_url=tts_synthetic.media_url,
                    audio_as_voice=tts_synthetic.audio_as_voice,
                )
                if should_route_to_originating and originating_channel and originating_to:
                    ok = await _route_reply_to_channel(tts_only, originating_channel, originating_to, session_key, cfg)
                    if ok:
                        result.queued_final = True
                        result.counts["final"] += 1
                else:
                    await dispatcher.send_final_reply("", {"media_url": tts_only.media_url})
                    result.queued_final = True
                    result.counts["final"] += 1
        except Exception as exc:
            logger.warning(f"dispatch_from_config: accumulated block TTS failed: {exc}")

    elapsed = (time.monotonic() - start_time) * 1000
    logger.debug(
        f"dispatch_from_config: done [{channel}] queued_final={result.queued_final} "
        f"counts={result.counts} elapsed={elapsed:.0f}ms"
    )
    return result


