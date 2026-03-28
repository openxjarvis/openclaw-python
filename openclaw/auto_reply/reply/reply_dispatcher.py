"""Reply dispatcher for sending messages back to channels.

Mirrors TypeScript ``openclaw/src/auto-reply/reply/reply-dispatcher.ts``.

Handles:
- Serialized tool-result / block / final delivery chain
- ``mark_complete`` to signal no more replies are expected
- Idle detection: only resolves idle when ``mark_complete`` + no pending messages
- Module-level dispatcher registry for gateway-restart coordination
- ``normalize_reply_payload`` — response prefix, heartbeat token strip
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heartbeat token — mirrors TS HEARTBEAT_TOKEN / SILENT_REPLY_TOKEN
# ---------------------------------------------------------------------------

HEARTBEAT_TOKEN = "[[heartbeat]]"
SILENT_REPLY_TOKEN = "[[silent]]"


# ---------------------------------------------------------------------------
# QueuedMessage
# ---------------------------------------------------------------------------

@dataclass
class QueuedMessage:
    """A single outbound message waiting to be delivered."""

    kind: str  # "tool" | "block" | "final"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None


# ---------------------------------------------------------------------------
# Normalize payload — mirrors TS normalizeReplyPayload
# ---------------------------------------------------------------------------

def normalize_reply_payload(
    text: str | None,
    *,
    response_prefix: str | None = None,
    strip_heartbeat: bool = True,
) -> str | None:
    """Normalize a reply payload before delivery.

    - Strips ``HEARTBEAT_TOKEN`` / ``SILENT_REPLY_TOKEN`` from the text.
    - Optionally prepends ``response_prefix``.
    - Returns ``None`` when the resulting text is empty (silent reply).

    Mirrors TS ``normalizeReplyPayload``.
    """
    if not text:
        return None
    result = text
    if strip_heartbeat:
        result = result.replace(HEARTBEAT_TOKEN, "").replace(SILENT_REPLY_TOKEN, "")
    result = result.strip()
    if not result:
        return None
    if response_prefix:
        result = response_prefix + result
    return result


# ---------------------------------------------------------------------------
# ReplyDispatcher
# ---------------------------------------------------------------------------

class ReplyDispatcher:
    """Serialized delivery chain for tool / block / final replies.

    Mirrors TS ``ReplyDispatcher``:
    - All sends go through a single async chain to maintain ordering.
    - ``mark_complete()`` signals that no more messages will be enqueued.
    - ``wait_for_idle()`` resolves only after ``mark_complete`` **and** the
      delivery queue is empty.
    """

    def __init__(
        self,
        channel_send_fn: Callable[..., Awaitable[Any]],
        channel_id: str,
        thread_id: str | None = None,
        response_prefix: str | None = None,
    ) -> None:
        self.channel_send_fn = channel_send_fn
        self.channel_id = channel_id
        self.thread_id = thread_id
        self.response_prefix = response_prefix

        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue()
        self._processor_task: asyncio.Task | None = None
        self._processing = False
        self._completed = False
        self._idle_event = asyncio.Event()
        # Start at 1 (a "reservation") so that _check_idle cannot fire before
        # mark_complete() is called, even if the queue is momentarily empty.
        # Mirrors TS: pending starts at 1, decremented in a microtask inside
        # markComplete().  This prevents premature idle when mark_complete is
        # called before any send_block_reply / send_final_reply has been enqueued.
        self._pending_count = 1

        # Accumulated streaming buffer
        self._current_text = ""
        self._current_meta: dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Public send API
    # -----------------------------------------------------------------------

    async def send_tool_result(self, tool_call_id: str, result: str) -> None:
        """Enqueue a tool-result message.  Mirrors TS ``sendToolResult``."""
        await self._enqueue(QueuedMessage(kind="tool", content=result, tool_call_id=tool_call_id))

    async def send_block_reply(self, payload: "ReplyPayload | str", metadata: dict[str, Any] | None = None) -> None:
        """Send a streaming block reply.
        
        Mirrors TS sendBlockReply. Accepts either:
        - ReplyPayload object (full support for media, reply_to, etc.)
        - str (legacy text-only, for backward compatibility)
        
        Accumulates text and flushes at 500-char threshold.
        """
        # Import here to avoid circular dependency
        from openclaw.auto_reply.reply.get_reply import ReplyPayload
        
        if isinstance(payload, str):
            # Legacy text-only mode
            self._current_text += payload
            if metadata:
                self._current_meta.update(metadata)
        elif isinstance(payload, ReplyPayload):
            # Full ReplyPayload mode
            if payload.text:
                self._current_text += payload.text
            if payload.media_url:
                self._current_meta.setdefault("media_urls", []).append(payload.media_url)
            if payload.media_urls:
                self._current_meta.setdefault("media_urls", []).extend(payload.media_urls)
            if payload.audio_as_voice is not None:
                self._current_meta["audio_as_voice"] = payload.audio_as_voice
            if payload.silent is not None:
                self._current_meta["silent"] = payload.silent
            if payload.reply_to_id:
                self._current_meta["reply_to_id"] = payload.reply_to_id
            if metadata:
                self._current_meta.update(metadata)
        
        if len(self._current_text) >= 500:
            await self._flush(is_final=False)

    async def send_final_reply(self, text: str = "", metadata: dict[str, Any] | None = None) -> None:
        """Flush accumulated text and mark as final.  Mirrors TS ``sendFinalReply``."""
        if text:
            self._current_text += text
        if metadata:
            self._current_meta.update(metadata)
        await self._flush(is_final=True)

    def mark_complete(self) -> None:
        """Signal that no more messages will be enqueued.

        Mirrors TS ``markComplete``.  ``wait_for_idle`` will resolve once
        the queue is drained.

        The reservation decrement is deferred via ``call_soon`` so that any
        synchronous enqueue calls made right before ``mark_complete`` have a
        chance to increment ``_pending_count`` first, preventing a false-idle
        race (mirrors TS microtask scheduling of the pending decrement).
        """
        self._completed = True
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(self._release_reservation)
        except RuntimeError:
            # No running loop — release immediately (e.g. in tests)
            self._release_reservation()

    def _release_reservation(self) -> None:
        """Release the initial reservation and check for idle."""
        self._pending_count = max(0, self._pending_count - 1)
        self._check_idle()

    async def wait_for_idle(self) -> None:
        """Wait until ``mark_complete`` is called and all messages are sent.

        Mirrors TS ``waitForIdle``.
        """
        await self._idle_event.wait()

    def get_queued_counts(self) -> dict[str, int]:
        """Return counts per kind.  Mirrors TS ``getQueuedCounts``."""
        counts: dict[str, int] = {"tool": 0, "block": 0, "final": 0}
        # Iterate current queue snapshot
        items = list(self._queue._queue)  # type: ignore[attr-defined]
        for msg in items:
            counts[msg.kind] = counts.get(msg.kind, 0) + 1
        return counts

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    async def _flush(self, is_final: bool = False) -> None:
        if not self._current_text:
            if is_final:
                self._check_idle()
            return
        normalized = normalize_reply_payload(
            self._current_text,
            response_prefix=self.response_prefix if is_final else None,
        )
        self._current_text = ""
        self._current_meta = {}
        if normalized is None:
            if is_final:
                self._check_idle()
            return
        await self._enqueue(QueuedMessage(kind="final" if is_final else "block", content=normalized))

    async def _enqueue(self, msg: QueuedMessage) -> None:
        self._pending_count += 1
        await self._queue.put(msg)
        self._ensure_processor()

    def _ensure_processor(self) -> None:
        if self._processor_task and not self._processor_task.done():
            return
        self._processing = True
        self._processor_task = asyncio.create_task(self._process_queue())

    async def _process_queue(self) -> None:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if self._queue.empty():
                        break
                    continue
                try:
                    await self._deliver(msg)
                finally:
                    self._queue.task_done()
                    self._pending_count = max(0, self._pending_count - 1)
                    self._check_idle()
        except Exception as exc:
            logger.error("ReplyDispatcher: queue processor error: %s", exc, exc_info=True)
        finally:
            self._processing = False
            self._check_idle()

    async def _deliver(self, msg: QueuedMessage) -> None:
        try:
            params: dict[str, Any] = {"text": msg.content}
            if self.thread_id:
                params["thread_id"] = self.thread_id
            if msg.tool_call_id:
                params["tool_call_id"] = msg.tool_call_id
            params.update(msg.metadata)
            await self.channel_send_fn(self.channel_id, params)
        except Exception as exc:
            logger.error("ReplyDispatcher: delivery error: %s", exc, exc_info=True)

    def _check_idle(self) -> None:
        """Resolve the idle event when complete + nothing pending."""
        if self._completed and self._pending_count == 0 and self._queue.empty():
            self._idle_event.set()


# ---------------------------------------------------------------------------
# Global dispatcher registry — mirrors TS dispatcher-registry.ts
# ---------------------------------------------------------------------------

_DISPATCHER_REGISTRY: dict[str, ReplyDispatcher] = {}


def register_dispatcher(key: str, dispatcher: ReplyDispatcher) -> None:
    """Register *dispatcher* under *key* for gateway-restart coordination."""
    _DISPATCHER_REGISTRY[key] = dispatcher


def unregister_dispatcher(key: str) -> None:
    """Remove the dispatcher for *key*."""
    _DISPATCHER_REGISTRY.pop(key, None)


async def wait_for_all_dispatchers_idle(timeout_ms: int = 30_000) -> None:
    """Wait for all registered dispatchers to reach idle state.

    Used by the gateway restart sentinel before re-execing the process.
    Mirrors TS ``waitForDispatchersIdle``.
    """
    dispatchers = list(_DISPATCHER_REGISTRY.values())
    if not dispatchers:
        return
    tasks = [asyncio.create_task(d.wait_for_idle()) for d in dispatchers]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout_ms / 1000.0)
    except asyncio.TimeoutError:
        logger.warning("wait_for_all_dispatchers_idle: timed out after %dms", timeout_ms)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


__all__ = [
    "QueuedMessage",
    "ReplyDispatcher",
    "normalize_reply_payload",
    "register_dispatcher",
    "unregister_dispatcher",
    "wait_for_all_dispatchers_idle",
    "HEARTBEAT_TOKEN",
    "SILENT_REPLY_TOKEN",
]
