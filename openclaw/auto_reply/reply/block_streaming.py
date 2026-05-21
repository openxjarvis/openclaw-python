"""Block streaming — accumulate streaming text into delivery blocks.

Port of TypeScript:
  openclaw/src/auto-reply/reply/block-streaming.ts      (165 lines)
  openclaw/src/auto-reply/reply/block-reply-coalescer.ts
  openclaw/src/auto-reply/reply/block-reply-pipeline.ts

Accumulates incoming text chunks into blocks of a configured size,
then flushes them as `on_block_reply` payloads. Ensures:
  - Min-chars threshold before flush (to avoid tiny messages)
  - Max-chars hard limit (split large blocks)
  - Paragraph-boundary preference when possible
  - Idle-timeout coalescing
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypedDict

from openclaw.auto_reply.chunk import (
    _get_provider_section,
    _normalize_account_id,
    _resolve_account_entry,
    resolve_chunk_mode,
    resolve_text_chunk_limit,
)

from openclaw.markdown.fences import parse_fence_spans, is_safe_fence_break

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BLOCK_STREAM_MIN = 800
DEFAULT_BLOCK_STREAM_MAX = 1200
DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS = 1000
BLOCK_REPLY_SEND_TIMEOUT_MS = 15000


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BlockStreamingChunkConfig:
    """Configuration for block chunking.
    
    Mirrors TS BlockStreamingChunkConfig.
    """
    min_chars: int = DEFAULT_BLOCK_STREAM_MIN
    max_chars: int = DEFAULT_BLOCK_STREAM_MAX
    break_preference: str = "paragraph"  # "paragraph" | "newline" | "sentence"


@dataclass
class BlockStreamingCoalesceConfig:
    """Configuration for block coalescing.
    
    Mirrors TS BlockStreamingCoalesceConfig.
    """
    min_chars: int = DEFAULT_BLOCK_STREAM_MIN
    max_chars: int = DEFAULT_BLOCK_STREAM_MAX
    idle_ms: int = DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS
    joiner: str = "\n"
    flush_on_enqueue: bool = False


class BlockStreamingCoalescing(TypedDict, total=False):
    min_chars: int
    max_chars: int
    idle_ms: int
    joiner: str
    flush_on_enqueue: bool


class BlockStreamingChunking(TypedDict, total=False):
    min_chars: int
    max_chars: int
    break_preference: Literal["paragraph", "newline", "sentence"]
    flush_on_paragraph: bool


@dataclass
class BlockStreamingConfig:
    """Complete block streaming configuration.
    
    Mirrors TS resolvedBlockStreaming logic from get-reply-directives.ts.
    """
    enabled: bool = False
    break_preference: str = "text_end"  # "text_end" | "message_end"
    chunk_config: BlockStreamingChunkConfig | None = None
    coalesce_config: BlockStreamingCoalesceConfig | None = None




# ---------------------------------------------------------------------------
# Block coalescer — merges small chunks into delivery-sized blocks
# ---------------------------------------------------------------------------

class BlockReplyCoalescer:
    """
    Accumulates text chunks and flushes them when min/max thresholds
    are reached or when the idle timer fires.

    Mirrors TS block-reply-coalescer.ts.
    """

    def __init__(
        self,
        config: BlockStreamingCoalesceConfig,
        on_flush: Callable[[str], Awaitable[None]],
    ) -> None:
        self._cfg = config
        self._on_flush = on_flush
        self._buffer = ""
        self._joiner = config.joiner
        self._idle_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def push(self, text: str) -> None:
        """Push a text chunk into the accumulation buffer."""
        async with self._lock:
            self._cancel_idle()
            if self._buffer and self._joiner:
                self._buffer += self._joiner + text
            else:
                self._buffer += text

            # Max-chars hard flush
            while len(self._buffer) >= self._cfg.max_chars:
                chunk, self._buffer = self._split_at(self._buffer, self._cfg.max_chars)
                await self._on_flush(chunk)

            # Flush-on-enqueue (paragraph-boundary mode)
            if self._cfg.flush_on_enqueue and len(self._buffer) >= self._cfg.min_chars:
                # Try to find paragraph boundary near min_chars
                flush_point = self._find_break_point(self._buffer, self._cfg.min_chars)
                if flush_point > 0:
                    chunk = self._buffer[:flush_point]
                    self._buffer = self._buffer[flush_point:].lstrip("\n")
                    await self._on_flush(chunk)
                    return

            # Arm idle timer for coalescing
            if self._buffer and self._cfg.idle_ms > 0:
                self._idle_task = asyncio.create_task(self._idle_flush())

    async def flush_final(self) -> None:
        """Flush any remaining buffer at end of stream."""
        async with self._lock:
            self._cancel_idle()
            if self._buffer:
                await self._on_flush(self._buffer)
                self._buffer = ""

    async def _idle_flush(self) -> None:
        await asyncio.sleep(self._cfg.idle_ms / 1000)
        async with self._lock:
            if self._buffer:
                await self._on_flush(self._buffer)
                self._buffer = ""

    def _cancel_idle(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    @staticmethod
    def _split_at(text: str, max_chars: int) -> tuple[str, str]:
        return text[:max_chars], text[max_chars:]

    @staticmethod
    def _find_break_point(text: str, near: int) -> int:
        """Find a good break point near ``near`` chars, respecting code fences.

        Strategy:
        1. Try double-newline (paragraph break) — skip if inside a code fence.
        2. Try single newline — skip if inside a code fence.
        3. Hard-cut at *near* — if inside a fence, close+reopen it so Markdown remains valid.
        """
        if len(text) <= near:
            return len(text)

        spans = parse_fence_spans(text)

        # Prefer double-newline (paragraph break)
        pos = text.rfind("\n\n", 0, near + 200)
        if pos > near // 2 and is_safe_fence_break(spans, pos):
            return pos + 2

        # Single newline
        pos = text.rfind("\n", 0, near + 100)
        if pos > near // 2 and is_safe_fence_break(spans, pos):
            return pos + 1

        # Hard cut — safe outside fences
        if is_safe_fence_break(spans, near):
            return near

        # Inside a fence: find next newline after near that is fence-safe
        lookahead = text.find("\n", near)
        if lookahead != -1 and is_safe_fence_break(spans, lookahead):
            return lookahead + 1

        # Last resort: hard cut (caller handles fence repair if needed)
        return near


# ---------------------------------------------------------------------------
# Block streaming pipeline
# ---------------------------------------------------------------------------

class BlockStreamingPipeline:
    """
    Full pipeline: receives raw text events → coalesces → delivers via callback.

    Mirrors TS block-reply-pipeline.ts.
    """

    def __init__(
        self,
        cfg: BlockStreamingConfig,
        on_block: Callable[[str], Awaitable[None]],
    ) -> None:
        self._aborted: bool = False
        self._did_stream: bool = False

        timeout_s = BLOCK_REPLY_SEND_TIMEOUT_MS / 1000

        async def _guarded_send(text: str) -> None:
            if self._aborted:
                return
            try:
                await asyncio.wait_for(on_block(text), timeout=timeout_s)
                self._did_stream = True
            except asyncio.TimeoutError:
                logger.warning("Block send timed out after %dms — aborting pipeline",
                               BLOCK_REPLY_SEND_TIMEOUT_MS)
                self._aborted = True

        # Use provided coalesce_config or create from chunk_config
        if cfg.coalesce_config:
            coalesce_cfg = cfg.coalesce_config
        elif cfg.chunk_config:
            joiner = {
                "sentence": " ",
                "newline": "\n",
                "paragraph": "\n\n",
            }.get(cfg.chunk_config.break_preference, "\n")
            coalesce_cfg = BlockStreamingCoalesceConfig(
                min_chars=cfg.chunk_config.min_chars,
                max_chars=cfg.chunk_config.max_chars,
                idle_ms=DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS,
                joiner=joiner,
                flush_on_enqueue=cfg.chunk_config.break_preference == "newline",
            )
        else:
            coalesce_cfg = BlockStreamingCoalesceConfig()

        self._coalescer = BlockReplyCoalescer(coalesce_cfg, _guarded_send)
        self._enabled = cfg.enabled
        self._block_count = 0

    @property
    def block_count(self) -> int:
        return self._block_count

    @property
    def is_aborted(self) -> bool:
        return self._aborted

    @property
    def did_stream(self) -> bool:
        return self._did_stream

    async def push(self, text: str) -> None:
        if not self._enabled or not text or self._aborted:
            return
        self._block_count += 1
        await self._coalescer.push(text)

    async def finish(self) -> None:
        if not self._enabled or self._aborted:
            return
        await self._coalescer.flush_final()


# ---------------------------------------------------------------------------
# Config resolution helpers
# ---------------------------------------------------------------------------

def clamp_positive_integer(
    value: Any,
    fallback: int,
    *,
    min_value: int,
    max_value: int,
) -> int:
    """Clamp a numeric value to integer bounds. Mirrors TS clampPositiveInteger."""
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return fallback
    rounded = round(value)
    if rounded < min_value:
        return min_value
    if rounded > max_value:
        return max_value
    return int(rounded)


def _as_object_record(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _resolve_channel_streaming_block_coalesce(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve blockStreamingCoalesce from streaming.block.coalesce or legacy keys."""
    if not entry:
        return None
    streaming = _as_object_record(entry.get("streaming"))
    if streaming:
        block = _as_object_record(streaming.get("block"))
        if block:
            coalesce = _as_object_record(block.get("coalesce"))
            if coalesce:
                return coalesce
    legacy = entry.get("blockStreamingCoalesce") or entry.get("block_streaming_coalesce")
    return legacy if isinstance(legacy, dict) else None


def _resolve_provider_chunk_context(
    cfg: dict[str, Any] | None,
    provider: str | None = None,
    account_id: str | None = None,
) -> tuple[str | None, str | None, int]:
    """Return (provider_key, provider_id, text_limit). Mirrors TS resolveProviderChunkContext."""
    from openclaw.utils.message_channel import normalize_message_channel

    provider_key: str | None = None
    if provider:
        provider_key = normalize_message_channel(provider)
    provider_id = provider_key

    provider_chunk_limit: int | None = None
    if provider_id:
        try:
            from openclaw.channels.plugins import get_channel_plugin

            plugin = get_channel_plugin(provider_id)
            outbound = getattr(plugin, "outbound", None) if plugin else None
            limit = getattr(outbound, "text_chunk_limit", None) if outbound else None
            if isinstance(limit, int) and limit > 0:
                provider_chunk_limit = limit
        except Exception:
            provider_chunk_limit = None

    text_limit = resolve_text_chunk_limit(
        cfg,
        provider_key,
        account_id,
        fallback_limit=provider_chunk_limit,
    )
    return provider_key, provider_id, text_limit


def _resolve_provider_block_streaming_coalesce(
    cfg: dict[str, Any] | None,
    provider_key: str | None,
    account_id: str | None,
) -> dict[str, Any] | None:
    """Resolve provider/account blockStreamingCoalesce. Mirrors TS resolveProviderBlockStreamingCoalesce."""
    if not cfg or not provider_key:
        return None
    section = _get_provider_section(cfg, provider_key)
    if not section:
        return None
    normalized_account_id = _normalize_account_id(account_id)
    accounts = section.get("accounts")
    account_cfg: dict[str, Any] | None = None
    if isinstance(accounts, dict) and normalized_account_id:
        account_cfg = _resolve_account_entry(accounts, normalized_account_id)
        if not isinstance(account_cfg, dict):
            account_cfg = None
    account_legacy: dict[str, Any] | None = None
    if isinstance(account_cfg, dict):
        legacy = account_cfg.get("blockStreamingCoalesce") or account_cfg.get("block_streaming_coalesce")
        account_legacy = legacy if isinstance(legacy, dict) else None
    section_legacy_raw = section.get("blockStreamingCoalesce") or section.get("block_streaming_coalesce")
    section_legacy = section_legacy_raw if isinstance(section_legacy_raw, dict) else None
    return (
        _resolve_channel_streaming_block_coalesce(account_cfg)
        or _resolve_channel_streaming_block_coalesce(section)
        or account_legacy
        or section_legacy
    )


def resolve_block_streaming_chunking(
    cfg: dict[str, Any] | None,
    provider: str | None = None,
    account_id: str | None = None,
) -> BlockStreamingChunking:
    """Resolve block streaming chunking defaults. Mirrors TS resolveBlockStreamingChunking."""
    provider_key, _, text_limit = _resolve_provider_chunk_context(cfg, provider, account_id)
    agents = (cfg or {}).get("agents", {}).get("defaults", {})
    chunk_cfg = agents.get("blockStreamingChunk") or agents.get("block_streaming_chunk") or {}

    chunk_mode = resolve_chunk_mode(cfg, provider_key, account_id)

    max_requested = max(1, math.floor(chunk_cfg.get("maxChars", chunk_cfg.get("max_chars", DEFAULT_BLOCK_STREAM_MAX))))
    max_chars = max(1, min(max_requested, text_limit))
    min_fallback = DEFAULT_BLOCK_STREAM_MIN
    min_requested = max(1, math.floor(chunk_cfg.get("minChars", chunk_cfg.get("min_chars", min_fallback))))
    min_chars = min(min_requested, max_chars)
    break_pref = chunk_cfg.get("breakPreference") or chunk_cfg.get("break_preference")
    break_preference: Literal["paragraph", "newline", "sentence"] = (
        break_pref if break_pref in ("newline", "sentence") else "paragraph"
    )
    return BlockStreamingChunking(
        min_chars=min_chars,
        max_chars=max_chars,
        break_preference=break_preference,
        flush_on_paragraph=chunk_mode == "newline",
    )


def resolve_block_streaming_coalescing(
    cfg: dict[str, Any] | None,
    provider: str | None = None,
    account_id: str | None = None,
    chunking: BlockStreamingChunking | None = None,
) -> BlockStreamingCoalescing | None:
    """Resolve block streaming coalescing defaults. Mirrors TS resolveBlockStreamingCoalescing."""
    provider_key, provider_id, text_limit = _resolve_provider_chunk_context(cfg, provider, account_id)

    provider_defaults: dict[str, Any] | None = None
    if provider_id:
        try:
            from openclaw.channels.plugins import get_channel_plugin

            plugin = get_channel_plugin(provider_id)
            streaming = getattr(plugin, "streaming", None) if plugin else None
            defaults = (
                getattr(streaming, "block_streaming_coalesce_defaults", None)
                or getattr(streaming, "blockStreamingCoalesceDefaults", None)
                if streaming
                else None
            )
            if isinstance(defaults, dict):
                provider_defaults = defaults
        except Exception:
            provider_defaults = None

    provider_cfg = _resolve_provider_block_streaming_coalesce(cfg, provider_key, account_id)
    agents = (cfg or {}).get("agents", {}).get("defaults", {})
    coalesce_cfg = provider_cfg or agents.get("blockStreamingCoalesce") or agents.get("block_streaming_coalesce")

    min_requested = max(
        1,
        math.floor(
            (coalesce_cfg or {}).get("minChars")
            or (coalesce_cfg or {}).get("min_chars")
            or (provider_defaults or {}).get("minChars")
            or (provider_defaults or {}).get("min_chars")
            or (chunking or {}).get("min_chars")
            or DEFAULT_BLOCK_STREAM_MIN
        ),
    )
    max_requested = max(
        1,
        math.floor((coalesce_cfg or {}).get("maxChars") or (coalesce_cfg or {}).get("max_chars") or text_limit),
    )
    max_chars = max(1, min(max_requested, text_limit))
    min_chars = min(min_requested, max_chars)
    idle_ms = max(
        0,
        math.floor(
            (coalesce_cfg or {}).get("idleMs")
            or (coalesce_cfg or {}).get("idle_ms")
            or (provider_defaults or {}).get("idleMs")
            or (provider_defaults or {}).get("idle_ms")
            or DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS
        ),
    )
    preference = (chunking or {}).get("break_preference", "paragraph")
    joiner = " " if preference == "sentence" else "\n" if preference == "newline" else "\n\n"
    return BlockStreamingCoalescing(
        min_chars=min_chars,
        max_chars=max_chars,
        idle_ms=idle_ms,
        joiner=joiner,
    )


MAX_CHUNK_CHARS_CAP = 4000
MAX_COALESCE_IDLE_MS_CAP = 10_000


def resolve_effective_block_streaming_config(
    config: dict | None,
    session_config: dict | None = None,
) -> dict:
    """Merge chunking + coalescing settings with cap values.

    Mirrors TS resolveEffectiveBlockStreamingConfig.

    Args:
        config: Global OpenClaw config dict.
        session_config: Per-session config overrides (optional).

    Returns:
        Dict with: enabled, maxChunkChars, minChunkChars, coalesceIdleMs,
        breakPreference, joiner.
    """
    cfg = config or {}
    s_cfg = session_config or {}

    agents = cfg.get("agents", {}).get("defaults", {})
    enabled = agents.get("blockStreamingDefault") == "on"

    chunk_raw = s_cfg.get("blockStreamingChunk") or agents.get("blockStreamingChunk") or {}
    coalesce_raw = s_cfg.get("blockStreamingCoalesce") or agents.get("blockStreamingCoalesce") or {}

    max_chunk = int(chunk_raw.get("maxChars", chunk_raw.get("max_chars", DEFAULT_BLOCK_STREAM_MAX)))
    max_chunk = max(1, min(max_chunk, MAX_CHUNK_CHARS_CAP))

    min_chunk = int(chunk_raw.get("minChars", chunk_raw.get("min_chars", DEFAULT_BLOCK_STREAM_MIN)))
    min_chunk = max(1, min(min_chunk, max_chunk))

    idle_ms = int(coalesce_raw.get("idleMs", coalesce_raw.get("idle_ms", DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS)))
    idle_ms = max(0, min(idle_ms, MAX_COALESCE_IDLE_MS_CAP))

    break_pref = chunk_raw.get("breakPreference") or chunk_raw.get("break_preference") or "paragraph"
    if break_pref not in ("paragraph", "newline", "sentence"):
        break_pref = "paragraph"

    joiner_map = {"sentence": " ", "newline": "\n", "paragraph": "\n\n"}
    joiner = coalesce_raw.get("joiner") or joiner_map.get(break_pref, "\n\n")

    return {
        "enabled": bool(s_cfg.get("blockStreamingDefault", enabled) if "blockStreamingDefault" in s_cfg else enabled),
        "maxChunkChars": max_chunk,
        "minChunkChars": min_chunk,
        "coalesceIdleMs": idle_ms,
        "breakPreference": break_pref,
        "joiner": joiner,
    }


def resolve_block_streaming_config(
    cfg: dict | None,
    channel: str | None = None,
    account_id: str | None = None,
    disable_block_streaming: bool | None = None,
) -> BlockStreamingConfig:
    """Resolve BlockStreamingConfig from the OpenClaw config dict.
    
    Mirrors TS get-reply-directives.ts L358-372 logic:
    - opts?.disableBlockStreaming takes precedence
    - Then agentCfg?.blockStreamingDefault
    - Channel-specific telegram.blockStreaming overrides for Telegram
    """
    cfg = cfg or {}
    agents = cfg.get("agents", {}).get("defaults", {})
    
    # Step 1: Determine if block streaming is enabled
    # Mirrors TS resolvedBlockStreaming logic
    if disable_block_streaming is True:
        return BlockStreamingConfig(enabled=False)
    
    if disable_block_streaming is False:
        enabled = True
    else:
        # Check agents.defaults.blockStreamingDefault
        enabled = agents.get("blockStreamingDefault") == "on"
    
    # Step 2: For Telegram, check channel-specific blockStreaming override
    # Mirrors TS bot-message-dispatch.ts L171-174
    if channel and channel.lower() == "telegram":
        channel_cfg = cfg.get("telegram", {})
        if account_id:
            # Find account-specific config
            accounts = channel_cfg.get("accounts", [])
            for acc in accounts:
                if acc.get("accountId") == account_id:
                    # telegram.blockStreaming: boolean | undefined
                    acc_bs = acc.get("blockStreaming")
                    if isinstance(acc_bs, bool):
                        enabled = acc_bs
                    break
    
    if not enabled:
        return BlockStreamingConfig(enabled=False)
    
    # Step 3: Resolve chunk and coalesce configs
    # blockStreamingBreak: "text_end" | "message_end"
    break_preference = agents.get("blockStreamingBreak", "text_end")
    
    # blockStreamingChunk
    chunk_raw = agents.get("blockStreamingChunk")
    chunk_config = None
    if chunk_raw:
        chunk_config = BlockStreamingChunkConfig(
            min_chars=int(chunk_raw.get("minChars", DEFAULT_BLOCK_STREAM_MIN)),
            max_chars=int(chunk_raw.get("maxChars", DEFAULT_BLOCK_STREAM_MAX)),
            break_preference=chunk_raw.get("breakPreference", "paragraph"),
        )
    
    # blockStreamingCoalesce — check channel-specific first, then global
    coalesce_raw = None
    if channel and channel.lower() == "telegram":
        channel_cfg = cfg.get("telegram", {})
        if account_id:
            accounts = channel_cfg.get("accounts", [])
            for acc in accounts:
                if acc.get("accountId") == account_id:
                    coalesce_raw = acc.get("blockStreamingCoalesce")
                    break
        if not coalesce_raw:
            coalesce_raw = channel_cfg.get("blockStreamingCoalesce")
    
    if not coalesce_raw:
        coalesce_raw = agents.get("blockStreamingCoalesce")
    
    coalesce_config = None
    if coalesce_raw:
        idle_ms = int(coalesce_raw.get("idleMs", DEFAULT_BLOCK_STREAM_COALESCE_IDLE_MS))
        idle_ms = min(idle_ms, 5000)
        coalesce_config = BlockStreamingCoalesceConfig(
            min_chars=int(coalesce_raw.get("minChars", DEFAULT_BLOCK_STREAM_MIN)),
            max_chars=int(coalesce_raw.get("maxChars", DEFAULT_BLOCK_STREAM_MAX)),
            idle_ms=idle_ms,
            joiner=coalesce_raw.get("joiner", "\n"),
            flush_on_enqueue=bool(coalesce_raw.get("flushOnEnqueue", False)),
        )
    
    return BlockStreamingConfig(
        enabled=True,
        break_preference=break_preference,
        chunk_config=chunk_config,
        coalesce_config=coalesce_config,
    )
