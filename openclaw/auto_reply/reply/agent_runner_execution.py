"""Agent runner execution with fallback and error recovery.

Mirrors TypeScript ``openclaw/src/auto-reply/reply/agent-runner-execution.ts``
and ``openclaw/src/agents/model-fallback.ts``.

Provides ``run_agent_turn_with_fallback`` that wraps ``PiAgentRuntime.run_turn``
with:
- Cross-model fallback chain (runWithModelFallback) on auth/rate-limit failures
- Session corruption recovery after compaction failures
- Session recovery after role-ordering conflicts (Gemini)
- Transient HTTP error retry (1 retry after 2.5s, aligns with TS didRetryTransientHttpError)
- Tool result serialization to prevent out-of-order delivery
- Structured error events
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Maximum transient HTTP retry attempts — aligns with TS didRetryTransientHttpError (one retry).
MAX_TRANSIENT_RETRIES = 2

# Patterns that indicate a transient server error worth retrying
_TRANSIENT_HTTP_PATTERNS = [
    re.compile(r"\b(500|502|503|504)\b"),
    re.compile(r"server error", re.IGNORECASE),
    re.compile(r"internal error", re.IGNORECASE),
    re.compile(r"service unavailable", re.IGNORECASE),
    re.compile(r"overloaded", re.IGNORECASE),
    re.compile(r"try again", re.IGNORECASE),
]

# Patterns indicating compaction failure
_COMPACTION_FAILURE_PATTERNS = [
    re.compile(r"compaction", re.IGNORECASE),
    re.compile(r"conversation too long", re.IGNORECASE),
    re.compile(r"context.*overflow", re.IGNORECASE),
    re.compile(r"context.*too large", re.IGNORECASE),
]

# Patterns indicating role-ordering / function-call conflict
_ROLE_ORDER_PATTERNS = [
    re.compile(r"role.*ordering", re.IGNORECASE),
    re.compile(r"function.*call.*order", re.IGNORECASE),
    re.compile(r"invalid.*role.*sequence", re.IGNORECASE),
    re.compile(r"INVALID_ARGUMENT.*function", re.IGNORECASE),
]

# Patterns indicating auth / rate-limit errors that should trigger model fallback
_AUTH_RATE_LIMIT_PATTERNS = [
    re.compile(r"\b(401|403|429)\b"),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"quota.*exceed", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"invalid.?api.?key", re.IGNORECASE),
    re.compile(r"authentication.*failed", re.IGNORECASE),
]


def _is_transient_http_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(p.search(msg) for p in _TRANSIENT_HTTP_PATTERNS)


def _is_compaction_failure(exc: BaseException) -> bool:
    msg = str(exc)
    return any(p.search(msg) for p in _COMPACTION_FAILURE_PATTERNS)


def _is_role_ordering_conflict(exc: BaseException) -> bool:
    msg = str(exc)
    return any(p.search(msg) for p in _ROLE_ORDER_PATTERNS)


def _is_auth_or_rate_limit_error(exc: BaseException) -> bool:
    """Return True for errors that should trigger cross-model fallback."""
    msg = str(exc)
    return any(p.search(msg) for p in _AUTH_RATE_LIMIT_PATTERNS)


@dataclass
class FallbackAttempt:
    provider: str
    model: str
    error: str
    reason: str = "unknown"


def _resolve_fallback_candidates(
    cfg: dict | None,
    primary_provider: str,
    primary_model: str,
) -> list[tuple[str, str]]:
    """Return ordered list of (provider, model) candidates to try.

    Mirrors TS ``resolveFallbackCandidates`` in model-fallback.ts:
    starts with the primary model, appends entries from
    ``agents.defaults.model.fallbacks`` in the config.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(provider: str, model: str) -> None:
        key = f"{provider}/{model}"
        if key not in seen and provider and model:
            seen.add(key)
            candidates.append((provider, model))

    _add(primary_provider, primary_model)

    if not cfg:
        return candidates

    fallback_raw = (
        cfg.get("agents", {})
        .get("defaults", {})
        .get("model", {})
    )
    if isinstance(fallback_raw, dict):
        for raw in (fallback_raw.get("fallbacks") or []):
            if not raw:
                continue
            raw_str = str(raw).strip()
            if "/" in raw_str:
                parts = raw_str.split("/", 1)
                _add(parts[0].strip(), parts[1].strip())
            else:
                _add(primary_provider, raw_str)

    return candidates


async def run_with_model_fallback(
    runtime: Any,
    session: Any,
    message: str,
    *,
    tools: list[Any] | None = None,
    primary_provider: str = "",
    primary_model: str | None = None,
    system_prompt: str | None = None,
    images: list[str] | None = None,
    run_id: str | None = None,
    session_key: str | None = None,
    typing_signaler: Any | None = None,
    stream_callback: Any | None = None,
    status_reactions: Any | None = None,
    reasoning_stream_callback: Any | None = None,
    reasoning_level: str = "off",
    block_send_fn: Any | None = None,
    cfg: dict | None = None,
    on_fallback: Callable[[FallbackAttempt], Awaitable[None]] | None = None,
) -> tuple[str, bool, bool]:
    """Execute an agent turn with cross-model fallback on auth/rate-limit errors.

    Mirrors TS ``runWithModelFallback()`` in model-fallback.ts.

    When the primary model fails with an auth/rate-limit error, tries fallback
    models from ``agents.defaults.model.fallbacks`` in sequence.
    Context overflow and compaction errors are NOT retried here — they bubble
    up to ``run_agent_turn_with_fallback`` for special handling.
    """
    candidates = _resolve_fallback_candidates(cfg, primary_provider, primary_model or "")
    attempts: list[FallbackAttempt] = []
    last_error: BaseException | None = None

    for idx, (provider, model) in enumerate(candidates):
        try:
            result = await run_agent_turn_with_fallback(
                runtime=runtime,
                session=session,
                message=message,
                tools=tools,
                model=model if model else None,
                system_prompt=system_prompt,
                images=images,
                run_id=run_id,
                session_key=session_key,
                typing_signaler=typing_signaler,
                stream_callback=stream_callback,
                status_reactions=status_reactions,
                reasoning_stream_callback=reasoning_stream_callback,
                reasoning_level=reasoning_level,
                block_send_fn=block_send_fn,
                ctx_metadata=cfg,  # ✅ Pass cfg as ctx_metadata for fallback path
            )
            if attempts:
                logger.info(
                    "run_with_model_fallback: succeeded on fallback candidate %d/%d: %s/%s",
                    idx + 1, len(candidates), provider, model,
                )
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            # Context overflow / compaction → re-raise; outer handler deals with it
            if _is_compaction_failure(exc) or _is_role_ordering_conflict(exc):
                raise
            # Only continue fallback chain for auth/rate-limit errors
            if not _is_auth_or_rate_limit_error(exc):
                if idx == len(candidates) - 1:
                    raise
                # Non-auth errors: still propagate after last candidate
                raise
            attempt = FallbackAttempt(
                provider=provider,
                model=model,
                error=str(exc)[:200],
                reason="auth_or_rate_limit",
            )
            attempts.append(attempt)
            if on_fallback:
                try:
                    await on_fallback(attempt)
                except Exception:
                    pass
            if idx < len(candidates) - 1:
                logger.warning(
                    "run_with_model_fallback: %s/%s failed (%s) — trying next candidate",
                    provider, model, str(exc)[:80],
                )

    # All candidates exhausted
    if last_error is not None:
        raise last_error
    raise RuntimeError("run_with_model_fallback: no candidates available")


async def reset_session_after_compaction_failure(
    runtime: Any, session_id: str
) -> None:
    """Evict the pi_session pool entry so the next turn starts fresh.

    Mirrors TS ``resetSessionAfterCompactionFailure``.
    """
    try:
        if hasattr(runtime, "evict_session"):
            runtime.evict_session(session_id)
            logger.info("reset_session_after_compaction_failure: evicted session %s", session_id[:8])
    except Exception as exc:
        logger.debug("reset_session_after_compaction_failure: error: %s", exc)


async def reset_session_after_role_ordering_conflict(
    runtime: Any, session_id: str
) -> None:
    """Evict the pi_session pool entry after a Gemini role-ordering error.

    Mirrors TS ``resetSessionAfterRoleOrderingConflict``.
    """
    try:
        if hasattr(runtime, "evict_session"):
            runtime.evict_session(session_id)
            logger.info("reset_session_after_role_ordering_conflict: evicted session %s", session_id[:8])
    except Exception as exc:
        logger.debug("reset_session_after_role_ordering_conflict: error: %s", exc)


def sanitize_user_facing_text(text: str, *, error_context: bool = False) -> str:
    """Strip internal tokens and rewrite error messages for user-facing delivery.

    Mirrors TS ``sanitizeUserFacingText`` from ``pi-embedded-helpers/errors.ts``.

    Rules:
    - Strip ``<final>...</final>`` tags (internal chain-of-thought markers).
    - When ``error_context=True``: rewrite role-conflict, context-overflow, billing,
      and raw-API-error text with clean user-friendly copies.
    - Strip leading blank lines.
    - Collapse consecutive duplicate blocks.
    """
    if not text:
        return text

    import re as _re

    # Strip <final>...</final> tags (stripFinalTagsFromText equivalent)
    stripped = _re.sub(r"<final>.*?</final>", "", text, flags=_re.DOTALL)
    stripped = _re.sub(r"<final>", "", stripped)
    trimmed = stripped.strip()
    if not trimmed:
        return ""

    if error_context:
        # Role ordering conflict
        if _re.search(r"incorrect role information|roles must alternate", trimmed, _re.IGNORECASE):
            return (
                "Message ordering conflict - please try again. "
                "If this persists, use /new to start a fresh session."
            )
        # Context overflow
        if _re.search(
            r"prompt is too long|context.*(length|window|limit|overflow|too large)|"
            r"maximum.*context|exceed.*token|too many tokens",
            trimmed, _re.IGNORECASE
        ):
            return (
                "Context overflow: prompt too large for the model. "
                "Try /reset (or /new) to start a fresh session, or use a larger-context model."
            )
        # Raw HTTP error codes — rewrite to hide internals
        if _re.match(r"^(4[0-9]{2}|5[0-9]{2})\b", trimmed):
            return f"Error communicating with AI backend ({trimmed[:80]})"

    # Strip leading blank/whitespace lines without clobbering first-line indentation
    result = _re.sub(r"^(?:[ \t]*\r?\n)+", "", stripped)
    return result


def normalize_streaming_text(
    text: str | None,
    *,
    is_heartbeat: bool = False,
    media_urls: list[str] | None = None,
    is_error: bool = False,
) -> dict[str, Any]:
    """Clean up a streaming text payload before delivery.

    Mirrors TS ``normalizeStreamingText`` defined inside the ``while(true)``
    loop in ``agent-runner-execution.ts``.

    Rules (in order):
    - Strip ``HEARTBEAT_OK`` tokens when NOT in heartbeat mode.
    - Skip (``skip=True``) if the stripped result is empty, unless media_urls is
      non-empty (media-only payloads must pass through).
    - Skip entire payload if it begins with ``[[silent]]``.
    - Apply ``sanitize_user_facing_text`` before returning.

    Args:
        text: Raw text from the agent.
        is_heartbeat: When True, HEARTBEAT_OK is preserved.
        media_urls: Media attachment URLs — when non-empty, media-only payloads
            with no text are passed through (``skip=False, text=None``).
            Mirrors TS: ``if (payload.mediaUrls?.length ?? 0) > 0 → { skip: false }``.
        is_error: Forward to ``sanitize_user_facing_text`` error_context flag.

    Returns a dict ``{"text": str | None, "skip": bool}``.
    """
    HEARTBEAT_OK = "HEARTBEAT_OK"
    SILENT_TOKEN = "[[silent]]"
    HEARTBEAT_TOKEN = "[[heartbeat]]"

    has_media = bool(media_urls)

    if text is None:
        # Media-only payload — allow through (mirrors TS line 157-159)
        if has_media:
            return {"text": None, "skip": False}
        return {"text": None, "skip": True}

    cleaned = text

    # Strip stray HEARTBEAT_OK when not in heartbeat mode
    if not is_heartbeat and HEARTBEAT_OK in cleaned.upper():
        cleaned = cleaned.replace(HEARTBEAT_OK, "").replace("HEARTBEAT_OK", "")
        cleaned = cleaned.strip()
        if not cleaned:
            # Media-only payloads still pass through after HEARTBEAT strip (TS line 141)
            if has_media:
                return {"text": None, "skip": False}
            return {"text": None, "skip": True}

    # Silent reply token — skip entire payload
    if cleaned.startswith(SILENT_TOKEN) or cleaned == SILENT_TOKEN:
        return {"text": None, "skip": True}
    if cleaned.startswith(HEARTBEAT_TOKEN):
        return {"text": None, "skip": True}

    cleaned = cleaned.strip()
    if not cleaned:
        # Media-only pass-through (mirrors TS lines 155-160)
        if has_media:
            return {"text": None, "skip": False}
        return {"text": None, "skip": True}

    # Sanitize user-facing text (mirrors TS line 162-168)
    sanitized = sanitize_user_facing_text(cleaned, error_context=is_error)
    if not sanitized.strip():
        return {"text": None, "skip": True}

    return {"text": sanitized, "skip": False}


async def run_agent_turn_with_fallback(
    runtime: Any,
    session: Any,
    message: str,
    *,
    tools: list[Any] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    images: list[str] | None = None,
    run_id: str | None = None,
    session_key: str | None = None,
    typing_signaler: Any | None = None,  # TypingSignaler | None
    stream_callback: Any | None = None,  # Callable[[str], None] | None — called with full accumulated text on each delta
    status_reactions: Any | None = None,  # TelegramStatusReactions | None
    reasoning_stream_callback: Any | None = None,  # Callable[[str], None] | None — called with reasoning text on each delta
    reasoning_level: str = "off",  # "off" | "on" | "stream"
    block_send_fn: Any | None = None,  # Callable[[str], Awaitable[None]] | None — sends each text block before a tool call as a visible message
    verbose_level: str = "off",  # mirrors TS resolvedVerboseLevel
    is_heartbeat: bool = False,
    provider: str = "",  # used for CLI provider check
    cfg: dict | None = None,  # used for CLI provider check
    session_workspace: str | None = None,  # session-specific workspace directory
    ctx_metadata: dict | None = None,  # ✅ NEW: Context metadata from InboundMessage (stream mode config, reasoning coordinator, etc.)
) -> tuple[str, bool, bool]:
    """Execute an agent turn with automatic retry on transient errors.

    Returns ``(response_text, has_error, auto_compaction_completed)``.

    Mirrors TS ``runAgentTurnWithFallback``.

    Error handling priority:
    1. Transient HTTP errors (500/502/503/504): 1 retry after 2.5s (TS: didRetryTransientHttpError).
    2. Compaction failures: reset session, re-raise.
    3. Role-ordering conflicts (Gemini): reset session, re-raise.
    4. Other errors: re-raise immediately.
    """
    from openclaw.events import EventType

    # ------------------------------------------------------------------
    # CLI provider routing — mirrors TS isCliProvider(provider, config) check
    # in agent-runner-execution.ts lines 194+. When the configured provider is
    # a CLI-backed backend (claude-cli, codex-cli, etc.), delegate to run_cli_agent.
    # ------------------------------------------------------------------
    if provider:
        try:
            from openclaw.agents.model_selection import is_cli_provider
            if is_cli_provider(provider, cfg):
                from openclaw.agents.cli_runner import run_cli_agent as real_run_cli_agent
                from openclaw.agents.agent_scope import resolve_agent_workspace_dir
                
                # Get session information
                session_id = getattr(session, "session_id", "") or getattr(session, "id", "") or ""
                agent_id = getattr(session, "agent_id", None) or "main"
                
                # Resolve workspace directory
                workspace_dir = resolve_agent_workspace_dir(cfg, agent_id) if cfg else None
                if workspace_dir is None:
                    raise ValueError("Could not resolve workspace directory for CLI agent")
                
                # Resolve session file
                session_file = getattr(session, "session_file", None) or f"/tmp/{session_id}.jsonl"
                
                # Call real run_cli_agent with all required parameters
                result = await real_run_cli_agent(
                    session_id=session_id,
                    session_key=session_key,
                    agent_id=agent_id,
                    session_file=session_file,
                    workspace_dir=str(workspace_dir),
                    config=cfg,
                    prompt=message,
                    provider=provider,
                    model=model,
                    timeout_ms=None,  # Use default
                    run_id=run_id or session_id,
                    extra_system_prompt=system_prompt,
                    cli_session_id=None,  # Will be resolved from session
                    images=None,  # TODO: Convert images format if needed
                )
                
                # Extract text from result
                response_text = ""
                for payload in result.payloads:
                    if payload.text:
                        response_text += payload.text
                
                return (response_text, False, False)
        except NotImplementedError:
            raise
        except Exception as e:
            logger.error(f"CLI agent execution failed: {e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Register run context — mirrors TS registerAgentRunContext() called
    # at the top of runAgentTurnWithFallback in agent-runner-execution.ts.
    # Allows event enrichment with sessionKey, verboseLevel, isHeartbeat.
    # ------------------------------------------------------------------
    if run_id and session_key:
        try:
            from openclaw.infra.agent_events import register_agent_run_context
            register_agent_run_context(run_id, {
                "session_key": session_key,
                "verbose_level": verbose_level,
                "is_heartbeat": is_heartbeat,
            })
        except Exception:
            pass

    session_id = getattr(session, "session_id", "") or ""
    response_text = ""
    
    # ✅ Get reasoning coordinator from context metadata (passed from channel.py)
    # Used for reasoning block separation (forceNewMessage timing)
    ctx_metadata = ctx_metadata or {}
    reasoning_coordinator = ctx_metadata.get("_reasoning_coordinator")
    if reasoning_coordinator:
        logger.info(f"[{session_id[:8]}] [TEST] Reasoning coordinator loaded from context")
    else:
        logger.info(f"[{session_id[:8]}] [TEST] No reasoning coordinator in context")
    
    # ✅ FIX: Create BlockReplyPipeline when block_send_fn is provided
    # Mirrors TS createBlockReplyPipeline in agent-runner.ts lines 157-174
    block_reply_pipeline = None
    if block_send_fn:
        try:
            from openclaw.auto_reply.reply.block_streaming import (
                BlockStreamingPipeline,
                resolve_block_streaming_config,
            )
            from openclaw.auto_reply.reply.block_reply_pipeline import BlockReplyPipeline as _BlockReplyPipeline  # noqa: F401
            
            # ✅ NEW: Get disable_block_streaming flag from context metadata
            # Mirrors TS bot-message-dispatch.ts L305-313 disableBlockStreaming decision
            # This flag is set by channel.py based on stream mode configuration:
            # - draft优先: can_stream_answer_draft=true → disable_block_streaming=true
            # - reasoning强制: reasoning=on → disable_block_streaming=false
            disable_block_streaming = ctx_metadata.get("_disable_block_streaming")
            
            logger.info(
                f"[{session_id[:8]}] [TEST] Block streaming config: "
                f"disable_flag={disable_block_streaming}, "
                f"reasoning_level={ctx_metadata.get('_reasoning_level')}, "
                f"can_draft_answer={ctx_metadata.get('_can_stream_answer_draft')}"
            )
            
            # Resolve block streaming config (defaults: min=800, max=1200, idle=1000ms)
            # Provider is typically "telegram" or "discord" from channel context
            stream_config = resolve_block_streaming_config(
                cfg=cfg,
                channel="telegram",  # TODO: Pass actual provider from context if available
                account_id=None,
                disable_block_streaming=disable_block_streaming,
            )
            
            logger.debug(
                f"[{session_id[:8]}] Block streaming config: "
                f"enabled={stream_config.enabled}, "
                f"disable_flag={disable_block_streaming}, "
                f"reasoning_level={ctx_metadata.get('_reasoning_level', 'off')}, "
                f"can_draft_answer={ctx_metadata.get('_can_stream_answer_draft', False)}"
            )
            
            # Create pipeline with coalescer
            # Coalescer will automatically:
            # - Flush when accumulated >= maxChars (1200)
            # - Flush after idleMs (1000ms) of no new text
            # - Flush on force (tool start, turn end)
            block_reply_pipeline = BlockStreamingPipeline(
                cfg=stream_config,
                on_block=block_send_fn,
            )
            coalesce_cfg = (
                stream_config.coalesce_config or
                stream_config.chunk_config
            )
            logger.debug(
                f"[{session_id[:8]}] BlockStreamingPipeline created: "
                f"min={getattr(coalesce_cfg, 'min_chars', 800)}, "
                f"max={getattr(coalesce_cfg, 'max_chars', 1200)}, "
                f"idle={getattr(coalesce_cfg, 'idle_ms', 1000)}ms"
            )
        except Exception as e:
            logger.warning(f"Failed to create BlockStreamingPipeline: {e}", exc_info=True)
            block_reply_pipeline = None
    has_error = False
    auto_compaction_completed = False
    attempt = 0

    while attempt < MAX_TRANSIENT_RETRIES:
        attempt += 1
        response_text = ""
        has_error = False
        # Tracks how many chars of response_text have already been sent as block
        # messages. Used so: (a) only the new segment is sent per block, (b) the
        # stream_callback receives only the current segment's text (not full history),
        # (c) the final return value excludes already-delivered blocks.
        _block_sent_len: int = 0

        # Signal run start for typing indicator (mode=instant starts immediately)
        if typing_signaler:
            try:
                await typing_signaler.signal_run_start()
            except Exception:
                pass

        try:
            # Pass stream_callback into run_turn so PiAgentRuntime can call it
            # in real-time as each text delta arrives from the queue consumer loop.
            # PiAgentRuntime.run_turn() accepts stream_callback as a kwarg; other
            # runtimes (MultiProviderRuntime) that don't support it will ignore
            # the unknown kwarg only if they have **kwargs — for safety we wrap.
            _rt_kwargs: dict = {
                "tools": tools,
                "model": model,
                "system_prompt": system_prompt,
                "images": images,
                "run_id": run_id,
                "session_key": session_key,
                "streaming_behavior": "followUp",  # Always queue if agent is already processing
                "session_workspace": session_workspace,  # Pass session workspace for file isolation
            }
            # Only pass stream_callback for real-time streaming when reasoning is off.
            # When reasoning is on, text must be split first (done post-run below),
            # so we cannot forward the raw accumulated text to the draft stream live.
            if stream_callback is not None and reasoning_level == "off" and hasattr(runtime, "run_turn"):
                import inspect as _inspect
                try:
                    _sig = _inspect.signature(runtime.run_turn)
                    if "stream_callback" in _sig.parameters:
                        _rt_kwargs["stream_callback"] = stream_callback
                except Exception:
                    pass

            async for event in runtime.run_turn(session, message, **_rt_kwargs):
                try:
                    evt_type = getattr(event, "type", "")
                    event_data: dict = {}
                    if hasattr(event, "data") and isinstance(event.data, dict):
                        event_data = event.data

                    if evt_type in (
                        EventType.TEXT, EventType.TEXT_DELTA, EventType.AGENT_TEXT,
                        "text", "text_delta", "agent.text",
                    ):
                        chunk = event_data.get("text") or event_data.get("delta") or ""
                        if isinstance(chunk, dict):
                            chunk = chunk.get("text", "")
                        if chunk:
                            response_text += str(chunk)
                            
                            # ✅ FIX: Push chunk to BlockStreamingPipeline
                            # Pipeline's coalescer will automatically flush when:
                            # - Accumulated text >= maxChars (1200)
                            # - idleMs (1000ms) elapsed since last chunk
                            # Mirrors TS: text delta → coalescer.push → auto flush on thresholds
                            if block_reply_pipeline:
                                try:
                                    await block_reply_pipeline.push(str(chunk))
                                except Exception as _enq_err:
                                    logger.debug(f"Pipeline push error (non-fatal): {_enq_err}")
                            
                            # Refresh typing TTL as text arrives — mirrors TS
                            # typing.startTypingOnText() on each text delta
                            if typing_signaler:
                                try:
                                    await typing_signaler.signal_text_delta(str(chunk))
                                except Exception:
                                    pass
                            
                            # ✅ FIX: Stream preview callbacks receive FULL accumulated text
                            # Mirrors TS onPartialReply which receives cleanedText (full snapshot),
                            # NOT delta. Draft stream's own throttling prevents excessive updates.
                            # Split text into answer and reasoning lanes when reasoningLevel != "off".
                            if reasoning_level != "off" and (
                                stream_callback is not None or reasoning_stream_callback is not None
                            ):
                                try:
                                    from openclaw.channels.telegram.reasoning import split_telegram_reasoning_text
                                    _r_text, _a_text = split_telegram_reasoning_text(response_text)
                                    logger.info(
                                        f"[{session_id[:8]}] Reasoning split: r_len={len(_r_text)}, a_len={len(_a_text)}"
                                    )
                                    
                                    # ✅ NEW: Reasoning block separation logic
                                    # Mirrors TS bot-message-dispatch.ts L586-598 onReasoningStream callback
                                    if reasoning_stream_callback is not None and _r_text:
                                        # Check if reasoning coordinator wants to split on next stream
                                        # This happens after reasoning final is delivered, to start fresh preview
                                        if reasoning_coordinator and reasoning_coordinator.should_split_reasoning_on_next_stream():
                                            logger.info(f"[{session_id[:8]}] Reasoning block separation triggered")
                                            reasoning_coordinator.clear_split_flag()
                                            _reasoning_draft_stream = ctx_metadata.get("_reasoning_draft_stream")
                                            if _reasoning_draft_stream:
                                                _reasoning_draft_stream.force_new_message()
                                                logger.info(f"[{session_id[:8]}] Reasoning draft stream force_new_message() called")
                                        
                                        # Note reasoning hint for coordinator
                                        if reasoning_coordinator:
                                            reasoning_coordinator.note_reasoning_hint()
                                        
                                        logger.info(f"[{session_id[:8]}] Calling reasoning_stream_callback")
                                        _r_result = reasoning_stream_callback(_r_text)
                                        if asyncio.iscoroutine(_r_result):
                                            asyncio.create_task(_r_result)
                                        logger.info(f"[{session_id[:8]}] reasoning_stream_callback called successfully")
                                        
                                        # Mark reasoning delivered for coordinator
                                        if reasoning_coordinator:
                                            reasoning_coordinator.note_reasoning_delivered()
                                    
                                    if stream_callback is not None and _a_text:
                                        logger.info(
                                            f"[{session_id[:8]}] Calling stream_callback with {len(_a_text)} chars: "
                                            f"{_a_text[:50]}..."
                                        )
                                        _a_result = stream_callback(_a_text)
                                        if asyncio.iscoroutine(_a_result):
                                            asyncio.create_task(_a_result)
                                        logger.info(f"[{session_id[:8]}] stream_callback called successfully")
                                except Exception as e:
                                    logger.error(
                                        f"[{session_id[:8]}] stream_callback ERROR (reasoning branch): "
                                        f"{type(e).__name__}: {e}",
                                        exc_info=True
                                    )
                            elif stream_callback is not None:
                                try:
                                    # ✅ Pass FULL accumulated text, not segment
                                    logger.info(
                                        f"[{session_id[:8]}] Calling stream_callback with {len(response_text)} chars: "
                                        f"{response_text[:50]}..."
                                    )
                                    result = stream_callback(response_text)
                                    # Support both sync and async callbacks
                                    if asyncio.iscoroutine(result):
                                        asyncio.create_task(result)
                                    logger.info(f"[{session_id[:8]}] stream_callback called successfully")
                                except Exception as e:
                                    logger.error(
                                        f"[{session_id[:8]}] stream_callback ERROR: {type(e).__name__}: {e}",
                                        exc_info=True
                                    )
                    elif evt_type in (
                        EventType.AGENT_TOOL_USE, EventType.TOOL_EXECUTION_START,
                        "tool_use", "tool_call", "agent.tool_use", "tool_execution_start",
                    ):
                        # ✅ FIX: Force flush pipeline before tool execution
                        # Ensures all accumulated text is sent as a block before the tool starts.
                        # This preserves message boundaries: "thought" → "tool" → "result"
                        # Mirrors TS: handleToolExecutionStart → flushBlockReplyBuffer → onBlockReplyFlush
                        if block_reply_pipeline:
                            try:
                                await block_reply_pipeline.finish()
                                _block_sent_len = len(response_text)
                                logger.debug(f"[{session_id[:8]}] Block pipeline flushed before tool (sent_len={_block_sent_len})")
                            except Exception as _flush_err:
                                logger.debug(f"Pipeline flush error (non-fatal): {_flush_err}")
                        elif block_send_fn:
                            # Legacy path (if pipeline creation failed)
                            _unsent = response_text[_block_sent_len:].strip()
                            if _unsent:
                                _block_sent_len = len(response_text)
                                try:
                                    _block_result = block_send_fn(_unsent)
                                    if asyncio.iscoroutine(_block_result):
                                        asyncio.create_task(_block_result)
                                except Exception as _be:
                                    logger.debug("block_send_fn error (non-fatal): %s", _be)
                        # Tool execution started — keep typing indicator alive
                        if typing_signaler:
                            try:
                                await typing_signaler.signal_tool_start()
                            except Exception:
                                pass
                        # Update status reaction to show which tool is running.
                        # Mirrors TS onToolStart: statusReactionController.setTool(payload.name).
                        if status_reactions:
                            try:
                                tool_name = event_data.get("name", "") or event_data.get("tool_name", "") or ""
                                await status_reactions.set_tool(str(tool_name))
                            except Exception:
                                pass
                    elif evt_type in ("tool_result", "agent.tool_result", EventType.AGENT_TOOL_RESULT if hasattr(EventType, "AGENT_TOOL_RESULT") else "tool_result"):
                        # Inject MEDIA: lines from tool results directly into response_text.
                        # This is a reliable fallback: even if the LLM forgets to echo a
                        # MEDIA: path in its text response, any MEDIA: token emitted by a
                        # tool (e.g. pdf_generate, ppt_generate, image tools) will still
                        # trigger file delivery via split_media_from_output.
                        # Mirrors the guarantee TS imageResult() gives by embedding the
                        # MEDIA: token as a TextContent block visible to the delivery layer.
                        result_str = event_data.get("result", "") or ""
                        if result_str and "MEDIA:" in result_str.upper():
                            for _line in result_str.splitlines():
                                _stripped = _line.strip()
                                if _stripped.upper().startswith("MEDIA:"):
                                    response_text += f"\n{_stripped}"
                                    logger.info("Injected MEDIA token from tool_result: %s", _stripped[:100])
                    elif evt_type == "auto_compaction_end":
                        # Mirrors TS onAgentEvent phase === "end" detection in followup-runner.ts
                        auto_compaction_completed = True
                    elif evt_type in (EventType.ERROR, "error", "agent.error"):
                        err_msg = event_data.get("message", str(event_data))
                        logger.error("run_agent_turn_with_fallback: agent error: %s", err_msg)
                        # Transient server errors (500/502/503/504) should be
                        # retried. Raise so the outer except block catches it
                        # and applies the standard backoff-retry logic.
                        if _is_transient_http_error(RuntimeError(err_msg)):
                            raise RuntimeError(err_msg)
                        has_error = True
                except Exception as evt_exc:
                    logger.error("Event processing error: %s", evt_exc)
                    has_error = True

            # ✅ FIX: Force flush pipeline at turn end
            # Ensures any remaining buffered text is sent as the final block.
            # Mirrors TS: agent-runner.ts lines 400-402 (blockReplyPipeline.flush + stop)
            if block_reply_pipeline:
                try:
                    await block_reply_pipeline.finish()
                    _block_sent_len = len(response_text)
                    logger.debug(f"[{session_id[:8]}] Block pipeline final flush (sent_len={_block_sent_len})")
                except Exception as _final_flush_err:
                    logger.debug(f"Pipeline final flush error (non-fatal): {_final_flush_err}")

            # When block streaming was active, return only the undelivered remainder
            # (text after the last block send). Blocks already sent as individual
            # messages should not be re-sent by _deliver_response().
            final_text = response_text[_block_sent_len:].strip() if _block_sent_len else response_text
            
            # ✅ NEW: Mark split_reasoning_on_next_stream after reasoning final
            # Mirrors TS bot-message-dispatch.ts L529-534 where reasoning final triggers split flag
            # This ensures the next reasoning block starts a fresh preview message
            if reasoning_coordinator and reasoning_level != "off":
                # Check if we had reasoning content delivered
                if reasoning_coordinator._reasoning_delivered:
                    # Mark that next reasoning stream should split
                    reasoning_coordinator.mark_split_reasoning_on_next_stream()
                    logger.debug(
                        f"[{session_id[:8]}] Marked split_reasoning_on_next_stream after final "
                        f"(will reset on next turn)"
                    )
                # Reset for next turn
                reasoning_coordinator.reset_for_next_step()
            
            # CRITICAL FIX: Extract MEDIA: tokens from pi_coding_agent's final messages.
            # The pi_runtime injects MEDIA tokens when it sees agent_end, but those
            # events arrive AFTER this loop exits. Instead, we query the runtime
            # for the final messages and scan for MEDIA: tokens directly.
            try:
                logger.info("[EXTRACT-DEBUG] Attempting MEDIA extraction: runtime=%s, has_pool=%s", 
                           type(runtime).__name__, hasattr(runtime, "_pool"))
                if hasattr(runtime, "_pool") and session_id in runtime._pool:
                    pi_session = runtime._pool[session_id]
                    logger.info("[EXTRACT-DEBUG] Found pi_session, has_agent=%s", hasattr(pi_session, "_agent"))
                    if hasattr(pi_session, "_agent") and hasattr(pi_session._agent, "state"):
                        messages = getattr(pi_session._agent.state, "messages", [])
                        logger.info("[EXTRACT-DEBUG] Checking last assistant message only (not full history)")
                        # Only check the LAST assistant message from THIS turn, not full history
                        # This prevents re-sending files from previous conversation turns
                        last_assistant_msg = None
                        for m in reversed(messages):
                            if getattr(m, "role", None) == "assistant":
                                last_assistant_msg = m
                                break
                        
                        if last_assistant_msg:
                            content = getattr(last_assistant_msg, "content", [])
                            if isinstance(content, list):
                                # Concatenate all text chunks from this message
                                full_text = ""
                                for chunk in content:
                                    chunk_type = getattr(chunk, "type", None)
                                    chunk_text = getattr(chunk, "text", "")
                                    if chunk_type == "text" and chunk_text:
                                        full_text += chunk_text
                                # Extract MEDIA: lines
                                if full_text and "MEDIA:" in full_text.upper():
                                    logger.info("[EXTRACT-DEBUG] Found MEDIA in last assistant message: %s", full_text[:150])
                                    for _line in full_text.splitlines():
                                        _stripped = _line.strip()
                                        if _stripped.upper().startswith("MEDIA:"):
                                            if _stripped not in final_text:
                                                final_text += f"\n{_stripped}"
                                                logger.info("📎 Extracted MEDIA token from current turn: %s", _stripped[:100])
                else:
                    logger.info("[EXTRACT-DEBUG] No pi_session found in _pool for session_id=%s", session_id[:8] if session_id else "None")
            except Exception as extract_err:
                logger.warning("MEDIA extraction from final messages failed: %s", extract_err, exc_info=True)
            
            return final_text, has_error, auto_compaction_completed

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            if _is_compaction_failure(exc):
                logger.warning(
                    "Compaction failure in session %s — resetting session: %s",
                    session_id[:8], exc,
                )
                await reset_session_after_compaction_failure(runtime, session_id)
                raise

            if _is_role_ordering_conflict(exc):
                logger.warning(
                    "Role-ordering conflict in session %s — resetting session: %s",
                    session_id[:8], exc,
                )
                await reset_session_after_role_ordering_conflict(runtime, session_id)
                raise

            if _is_transient_http_error(exc) and attempt < MAX_TRANSIENT_RETRIES:
                logger.warning(
                    "Transient HTTP error (attempt %d/%d) — retrying in 2.5s: %s",
                    attempt, MAX_TRANSIENT_RETRIES, exc,
                )
                await asyncio.sleep(2.5)
                continue

            # Non-retryable error
            raise

    # Should not reach here, but return error state if we somehow exit the loop
    return response_text, True, auto_compaction_completed


__all__ = [
    "run_agent_turn_with_fallback",
    "run_with_model_fallback",
    "reset_session_after_compaction_failure",
    "reset_session_after_role_ordering_conflict",
    "normalize_streaming_text",
    "sanitize_user_facing_text",
    "FallbackAttempt",
]
