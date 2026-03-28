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
from dataclasses import dataclass
from typing import Callable, Awaitable

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
