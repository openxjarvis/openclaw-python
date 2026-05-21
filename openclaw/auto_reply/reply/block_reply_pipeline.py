"""Block reply pipeline — manages payload coalescing and delivery.

Port of TypeScript:
  openclaw/src/auto-reply/reply/block-reply-pipeline.ts (245 lines)
  openclaw/src/auto-reply/reply/block-reply-coalescer.ts (150 lines)

The pipeline receives ReplyPayload blocks, coalesces text into delivery-sized
chunks, deduplicates payloads, handles buffering, and delivers via callback.
Ensures:
  - No duplicate deliveries (payload key tracking)
  - Coalescing of text blocks with min/max char thresholds
  - Media payloads flush immediately
  - Timeout handling and abortion
  - Buffer management for special payload types (e.g., audioAsVoice)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from openclaw.auto_reply.reply.get_reply import ReplyPayload

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration types
# ---------------------------------------------------------------------------

@dataclass
class BlockStreamingCoalesceConfig:
    """Configuration for block coalescing.
    
    Mirrors TS BlockStreamingCoalescing.
    """
    min_chars: int = 800
    max_chars: int = 1200
    idle_ms: int = 1000
    joiner: str = "\n"
    flush_on_enqueue: bool = False


# ---------------------------------------------------------------------------
# Helper: Create payload key for deduplication
# ---------------------------------------------------------------------------

def create_block_reply_payload_key(payload: ReplyPayload) -> str:
    """Create a unique key for a payload to track duplicates.
    
    Mirrors TS createBlockReplyPayloadKey.
    """
    text = (payload.text or "").strip()
    media_list = payload.media_urls if payload.media_urls else (
        [payload.media_url] if payload.media_url else []
    )
    key_data = {
        "text": text,
        "mediaList": media_list,
        "replyToId": payload.reply_to_id,
    }
    # Use JSON + hash for consistent keys
    key_str = json.dumps(key_data, sort_keys=True)
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Block Reply Coalescer
# ---------------------------------------------------------------------------

class BlockReplyCoalescer:
    """Accumulates ReplyPayload blocks and flushes when thresholds are met.
    
    Mirrors TS createBlockReplyCoalescer / block-reply-coalescer.ts.
    """
    
    def __init__(
        self,
        config: BlockStreamingCoalesceConfig,
        should_abort: Callable[[], bool],
        on_flush: Callable[[ReplyPayload], Awaitable[None]],
    ) -> None:
        self._cfg = config
        self._should_abort = should_abort
        self._on_flush = on_flush
        
        self._buffer_text = ""
        self._buffer_reply_to_id: str | None = None
        self._buffer_audio_as_voice: bool | None = None
        self._idle_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
    
    def enqueue(self, payload: ReplyPayload) -> None:
        """Enqueue a payload for coalescing."""
        asyncio.create_task(self._enqueue_async(payload))
    
    async def _enqueue_async(self, payload: ReplyPayload) -> None:
        """Internal async enqueue implementation."""
        async with self._lock:
            if self._should_abort():
                return
            
            has_media = bool(payload.media_url) or len(payload.media_urls or []) > 0
            text = payload.text or ""
            has_text = text.strip() != ""
            
            # Media payloads flush immediately
            if has_media:
                await self._flush_internal(force=True)
                await self._on_flush(payload)
                return
            
            if not has_text:
                return
            
            # flushOnEnqueue mode: treat each payload as separate paragraph
            if self._cfg.flush_on_enqueue:
                if self._buffer_text:
                    await self._flush_internal(force=True)
                self._buffer_reply_to_id = payload.reply_to_id
                self._buffer_audio_as_voice = payload.audio_as_voice
                self._buffer_text = text
                await self._flush_internal(force=True)
                return
            
            # Conflict detection: replyToId or audioAsVoice mismatch
            reply_to_conflict = bool(
                self._buffer_text
                and payload.reply_to_id
                and (not self._buffer_reply_to_id or self._buffer_reply_to_id != payload.reply_to_id)
            )
            if self._buffer_text and (
                reply_to_conflict or self._buffer_audio_as_voice != payload.audio_as_voice
            ):
                await self._flush_internal(force=True)
            
            if not self._buffer_text:
                self._buffer_reply_to_id = payload.reply_to_id
                self._buffer_audio_as_voice = payload.audio_as_voice
            
            # Accumulate text
            next_text = (
                f"{self._buffer_text}{self._cfg.joiner}{text}"
                if self._buffer_text
                else text
            )
            
            if len(next_text) > self._cfg.max_chars:
                if self._buffer_text:
                    await self._flush_internal(force=True)
                    self._buffer_reply_to_id = payload.reply_to_id
                    self._buffer_audio_as_voice = payload.audio_as_voice
                    if len(text) >= self._cfg.max_chars:
                        await self._on_flush(payload)
                        return
                    self._buffer_text = text
                    self._schedule_idle_flush()
                    return
                await self._on_flush(payload)
                return
            
            self._buffer_text = next_text
            if len(self._buffer_text) >= self._cfg.max_chars:
                await self._flush_internal(force=True)
                return
            
            self._schedule_idle_flush()
    
    async def flush(self, force: bool = False) -> None:
        """Flush buffered payload."""
        async with self._lock:
            await self._flush_internal(force=force)
    
    async def _flush_internal(self, force: bool = False) -> None:
        """Internal flush implementation (must be called with lock held)."""
        self._clear_idle_timer()
        
        if self._should_abort():
            self._reset_buffer()
            return
        
        if not self._buffer_text:
            return
        
        if not force and not self._cfg.flush_on_enqueue and len(self._buffer_text) < self._cfg.min_chars:
            self._schedule_idle_flush()
            return
        
        payload = ReplyPayload(
            text=self._buffer_text,
            reply_to_id=self._buffer_reply_to_id,
            audio_as_voice=self._buffer_audio_as_voice,
        )
        self._reset_buffer()
        await self._on_flush(payload)
    
    def _schedule_idle_flush(self) -> None:
        """Schedule idle flush timer."""
        if self._cfg.idle_ms <= 0:
            return
        self._clear_idle_timer()
        self._idle_task = asyncio.create_task(self._idle_flush())
    
    async def _idle_flush(self) -> None:
        """Idle flush callback."""
        await asyncio.sleep(self._cfg.idle_ms / 1000)
        async with self._lock:
            await self._flush_internal(force=False)
    
    def _clear_idle_timer(self) -> None:
        """Clear idle timer."""
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None
    
    def _reset_buffer(self) -> None:
        """Reset buffer state."""
        self._buffer_text = ""
        self._buffer_reply_to_id = None
        self._buffer_audio_as_voice = None
    
    def has_buffered(self) -> bool:
        """Check if coalescer has buffered content."""
        return bool(self._buffer_text)
    
    def stop(self) -> None:
        """Stop coalescer and clear timers."""
        self._clear_idle_timer()


# ---------------------------------------------------------------------------
# Block Reply Buffer (for special payload types)
# ---------------------------------------------------------------------------

class BlockReplyBuffer:
    """Optional buffer for special payload types (e.g., audioAsVoice).
    
    Mirrors TS BlockReplyBuffer interface.
    """
    
    def should_buffer(self, payload: ReplyPayload) -> bool:
        """Check if this payload should be buffered."""
        return False
    
    def on_enqueue(self, payload: ReplyPayload) -> None:
        """Called when a payload is enqueued."""
        pass
    
    def finalize(self, payload: ReplyPayload) -> ReplyPayload:
        """Finalize buffered payload before sending."""
        return payload


# ---------------------------------------------------------------------------
# Block Reply Pipeline
# ---------------------------------------------------------------------------

class BlockReplyPipeline:
    """Full pipeline for block reply delivery with coalescing and deduplication.
    
    Mirrors TS createBlockReplyPipeline / block-reply-pipeline.ts.
    """
    
    def __init__(
        self,
        on_block_reply: Callable[[ReplyPayload, dict[str, Any] | None], Awaitable[None]],
        timeout_ms: int = 15000,  # Aligns with TS: BLOCK_REPLY_SEND_TIMEOUT_MS = 15_000
        coalescing: BlockStreamingCoalesceConfig | None = None,
        buffer: BlockReplyBuffer | None = None,
    ) -> None:
        self._on_block_reply = on_block_reply
        self._timeout_ms = timeout_ms
        self._buffer = buffer
        
        self._sent_keys: set[str] = set()
        self._pending_keys: set[str] = set()
        self._seen_keys: set[str] = set()
        self._buffered_keys: set[str] = set()
        self._buffered_payload_keys: set[str] = set()
        self._buffered_payloads: list[ReplyPayload] = []
        self._send_chain: asyncio.Task = asyncio.create_task(asyncio.sleep(0))
        self._aborted = False
        self._did_stream = False
        self._did_log_timeout = False
        self._lock = asyncio.Lock()
        
        self._coalescer: BlockReplyCoalescer | None = None
        if coalescing:
            self._coalescer = BlockReplyCoalescer(
                config=coalescing,
                should_abort=lambda: self._aborted,
                on_flush=lambda p: self._send_payload(p, bypass_seen_check=True),
            )
    
    def enqueue(self, payload: ReplyPayload) -> None:
        """Enqueue a payload for delivery."""
        asyncio.create_task(self._enqueue_async(payload))

    async def push(self, text: str) -> None:
        """Push a text chunk — convenience wrapper used by agent_runner_execution.

        Mirrors TS BlockReplyPipeline.sendBlock(text) behavior.
        """
        if text and not self._aborted:
            await self._enqueue_async(ReplyPayload(text=text))

    async def finish(self) -> None:
        """Flush coalesced content and buffered payloads — call at end of turn.

        Mirrors TS blockReplyPipeline.flush() / finalize() at turn end.
        """
        if self._coalescer:
            await self._coalescer.flush(force=True)
        await self._flush_buffered()
    
    async def _enqueue_async(self, payload: ReplyPayload) -> None:
        """Internal async enqueue implementation."""
        if self._aborted:
            return
        
        # Buffer special payload types
        if self._buffer_payload(payload):
            return
        
        has_media = bool(payload.media_url) or len(payload.media_urls or []) > 0
        if has_media:
            if self._coalescer:
                await self._coalescer.flush(force=True)
            await self._send_payload(payload, bypass_seen_check=False)
            return
        
        if self._coalescer:
            payload_key = create_block_reply_payload_key(payload)
            if (
                payload_key in self._seen_keys
                or payload_key in self._pending_keys
                or payload_key in self._buffered_keys
            ):
                return
            self._seen_keys.add(payload_key)
            self._buffered_keys.add(payload_key)
            self._coalescer.enqueue(payload)
            return
        
        await self._send_payload(payload, bypass_seen_check=False)
    
    def _buffer_payload(self, payload: ReplyPayload) -> bool:
        """Buffer special payload types if needed."""
        if not self._buffer:
            return False
        
        self._buffer.on_enqueue(payload)
        if not self._buffer.should_buffer(payload):
            return False
        
        payload_key = create_block_reply_payload_key(payload)
        if (
            payload_key in self._seen_keys
            or payload_key in self._sent_keys
            or payload_key in self._pending_keys
            or payload_key in self._buffered_payload_keys
        ):
            return True
        
        self._seen_keys.add(payload_key)
        self._buffered_payload_keys.add(payload_key)
        self._buffered_payloads.append(payload)
        return True
    
    async def _flush_buffered(self) -> None:
        """Flush buffered payloads."""
        if not self._buffered_payloads:
            return
        
        for payload in self._buffered_payloads:
            final_payload = (
                self._buffer.finalize(payload) if self._buffer else payload
            )
            await self._send_payload(final_payload, bypass_seen_check=True)
        
        self._buffered_payloads.clear()
        self._buffered_payload_keys.clear()
    
    async def _send_payload(self, payload: ReplyPayload, bypass_seen_check: bool = False) -> None:
        """Send a payload via on_block_reply callback."""
        if self._aborted:
            return
        
        payload_key = create_block_reply_payload_key(payload)
        
        if not bypass_seen_check:
            if payload_key in self._seen_keys:
                return
            self._seen_keys.add(payload_key)
        
        if payload_key in self._sent_keys or payload_key in self._pending_keys:
            return
        
        self._pending_keys.add(payload_key)
        
        async def send_task():
            try:
                if self._aborted:
                    return False
                
                # Call on_block_reply with timeout
                try:
                    await asyncio.wait_for(
                        self._on_block_reply(payload, None),
                        timeout=self._timeout_ms / 1000,
                    )
                    return True
                except asyncio.TimeoutError:
                    self._aborted = True
                    if not self._did_log_timeout:
                        self._did_log_timeout = True
                        logger.warning(
                            f"block reply delivery timed out after {self._timeout_ms}ms; "
                            "skipping remaining block replies to preserve ordering"
                        )
                    return False
            except Exception as err:
                logger.warning(f"block reply delivery failed: {err}")
                return False
        
        # Chain send tasks to preserve ordering
        prev_chain = self._send_chain
        
        async def chained():
            await prev_chain
            did_send = await send_task()
            if did_send:
                self._sent_keys.add(payload_key)
                self._did_stream = True
            self._pending_keys.discard(payload_key)
        
        self._send_chain = asyncio.create_task(chained())
    
    async def flush(self, force: bool = False) -> None:
        """Flush all buffered content."""
        if self._coalescer:
            await self._coalescer.flush(force=force)
        await self._flush_buffered()
        await self._send_chain
    
    def stop(self) -> None:
        """Stop the pipeline."""
        if self._coalescer:
            self._coalescer.stop()
    
    def has_buffered(self) -> bool:
        """Check if pipeline has buffered content."""
        return (
            (self._coalescer and self._coalescer.has_buffered())
            or len(self._buffered_payloads) > 0
        )
    
    def did_stream(self) -> bool:
        """Check if any payloads were streamed."""
        return self._did_stream
    
    def is_aborted(self) -> bool:
        """Check if pipeline is aborted."""
        return self._aborted
    
    def has_sent_payload(self, payload: ReplyPayload) -> bool:
        """Check if a specific payload has been sent."""
        payload_key = create_block_reply_payload_key(payload)
        return payload_key in self._sent_keys
