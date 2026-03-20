"""Telegram draft streaming with throttling and edits.

Two transport modes (mirrors TS draft-stream.ts):
  - "draft"  (DMs):    bot.send_message_draft (PTB v22+, Bot API 9.5)
                       Live streaming bubble — no visible message until final sendMessage.
  - "message" (groups): sendMessage (first call) + editMessageText (subsequent)
                        Editable preview message.

Mode selection (mirrors TS "auto" previewTransport):
  - is_dm=True  + PTB has send_message_draft → draft transport
  - otherwise                                → message transport

draft_id: integer, globally incrementing per-process (mirrors TS allocateTelegramDraftId).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

TELEGRAM_STREAM_MAX_CHARS = 4096
DEFAULT_THROTTLE_MS = 1000
# Minimum characters before first preview push (debounce push-notification quality)
DRAFT_MIN_INITIAL_CHARS = 30


# ---------------------------------------------------------------------------
# Integer draft_id allocator — mirrors TS allocateTelegramDraftId()
# ---------------------------------------------------------------------------

_draft_id_counter: int = 0
_DRAFT_ID_MAX: int = 2_147_483_647  # int32 max


def _allocate_draft_id() -> int:
    """Return a monotonically increasing integer draft_id (wraps at int32 max).

    Mirrors TS allocateTelegramDraftId() in src/telegram/draft-stream.ts.
    draft_id MUST be an integer — Telegram Bot API rejects string values.
    """
    global _draft_id_counter
    _draft_id_counter = (_draft_id_counter % _DRAFT_ID_MAX) + 1
    return _draft_id_counter


def _resolve_draft_api(bot: Any) -> bool:
    """Check if the PTB bot has send_message_draft support (Bot API 9.5, PTB v22+).

    Mirrors TS resolveSendMessageDraftApi() — resolves once at stream creation.
    Returns True if the method exists; callers can set transport accordingly.
    """
    return callable(getattr(bot, "send_message_draft", None))


# ---------------------------------------------------------------------------
# TelegramDraftStream
# ---------------------------------------------------------------------------

class TelegramDraftStream:
    """Draft stream for Telegram with throttling.

    Mirrors TS createTelegramDraftStream() in src/telegram/draft-stream.ts.

    Transports:
      draft   — bot.send_message_draft(chat_id, draft_id, text, ...)
                DMs only; creates a native streaming bubble.
      message — bot.send_message() then bot.edit_message_text()
                Groups/channels or fallback when send_message_draft unavailable.
    """

    def __init__(
        self,
        bot_api: Any,
        chat_id: int | str,
        max_chars: int | None = None,
        thread_params: dict | None = None,
        reply_to_message_id: int | None = None,
        throttle_ms: int | None = None,
        min_initial_chars: int | None = None,
        is_dm: bool = False,
    ):
        self._api = bot_api
        self._chat_id = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        self._max_chars = min(max_chars or TELEGRAM_STREAM_MAX_CHARS, TELEGRAM_STREAM_MAX_CHARS)
        self._thread_params = thread_params or {}
        self._reply_to_message_id = reply_to_message_id
        self._throttle_ms = max(250, throttle_ms or DEFAULT_THROTTLE_MS)
        self._min_initial_chars = (
            min_initial_chars if min_initial_chars is not None
            else (DRAFT_MIN_INITIAL_CHARS if is_dm else None)
        )

        # Resolve transport at creation time (mirrors TS resolveSendMessageDraftApi).
        # Draft transport: DMs + PTB v22 send_message_draft available.
        self._use_draft_transport: bool = is_dm and _resolve_draft_api(bot_api)
        self._draft_id: int = _allocate_draft_id()

        # Message transport state
        self._stream_message_id: int | None = None

        # Shared state
        self._last_sent_text = ""
        self._last_delivered_text = ""  # Mirrors TS lastDeliveredText
        self._preview_revision = 0  # Mirrors TS previewRevision counter
        self._stopped = False
        self._is_final = False

        self._pending_text = ""
        self._last_sent_at = 0.0
        self._timer: asyncio.Task | None = None
        self._in_flight: asyncio.Task | None = None

        logger.debug(
            "Telegram draft stream created (chat=%s, transport=%s, throttle=%dms)",
            self._chat_id,
            "draft" if self._use_draft_transport else "message",
            self._throttle_ms,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, text: str) -> None:
        """Queue a text update for streaming (synchronous, fires async task)."""
        if self._stopped or self._is_final:
            return

        self._pending_text = text

        if self._in_flight:
            self._schedule()
            return

        now = time.time()
        elapsed_ms = (now - self._last_sent_at) * 1000
        if not self._timer and elapsed_ms >= self._throttle_ms:
            asyncio.create_task(self._flush_internal())
            return

        self._schedule()

    async def stop(self) -> None:
        """Flush the final pending content and mark stream as done.
        Mirrors TS draft-stream-controls.ts stop().
        """
        self._is_final = True
        await self._flush_internal()

    async def clear(self) -> None:
        """Stop streaming and clean up the preview.

        Draft transport (DMs): no deletion needed — the draft bubble disappears
        automatically when the final bot.send_message() is called.
        Message transport (groups): delete the preview message.
        Mirrors TS draft-stream-controls.ts stopForClear().
        """
        self._stopped = True

        if self._timer:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

        if self._in_flight:
            try:
                await self._in_flight
            except Exception:
                pass

        # Draft transport: nothing to delete
        if self._use_draft_transport:
            return

        msg_id = self._stream_message_id
        self._stream_message_id = None
        if msg_id is None:
            return

        try:
            await self._api.delete_message(
                chat_id=self._chat_id,
                message_id=msg_id,
            )
        except Exception as exc:
            logger.debug("Telegram stream preview cleanup failed: %s", exc)

    async def flush(self) -> None:
        """Force-flush any pending update immediately."""
        await self._flush_internal()

    def message_id(self) -> int | None:
        """Preview message ID (message transport only; None for draft transport)."""
        return self._stream_message_id

    def is_draft_transport(self) -> bool:
        """True when using sendMessageDraft (DM native streaming bubble)."""
        return self._use_draft_transport

    def force_new_message(self) -> None:
        """Reset so next update starts a fresh draft / new preview message.
        Mirrors TS TelegramDraftStream.forceNewMessage().
        """
        self._stream_message_id = None
        self._draft_id = _allocate_draft_id()
        self._last_sent_text = ""
        self._last_delivered_text = ""
        self._pending_text = ""
    
    def preview_revision(self) -> int:
        """Return the number of successful preview updates.
        Mirrors TS TelegramDraftStream.previewRevision().
        """
        return self._preview_revision
    
    def last_delivered_text(self) -> str:
        """Return the last successfully delivered text.
        Mirrors TS TelegramDraftStream.lastDeliveredText().
        """
        return self._last_delivered_text

    # ------------------------------------------------------------------
    # Internal throttle + loop
    # ------------------------------------------------------------------

    def _schedule(self) -> None:
        """Schedule a delayed flush at the end of the current throttle window."""
        if self._timer:
            return
        now = time.time()
        elapsed_ms = (now - self._last_sent_at) * 1000
        delay_ms = max(0.0, self._throttle_ms - elapsed_ms)

        async def _delayed_flush() -> None:
            await asyncio.sleep(delay_ms / 1000)
            await self._flush_internal()

        self._timer = asyncio.create_task(_delayed_flush())

    async def _flush_internal(self) -> None:
        """Drain pending text: wait for in-flight → send → repeat if more pending."""
        if self._timer:
            try:
                self._timer.cancel()
            except Exception:
                pass
            self._timer = None

        while not self._stopped or self._is_final:
            if self._in_flight:
                await self._in_flight
                continue

            text = self._pending_text
            if not text.strip():
                self._pending_text = ""
                return

            self._pending_text = ""
            _captured = text  # prevent closure rebind

            async def _do_send(_t: str = _captured) -> bool | None:
                return await self._send_or_edit_stream_message(_t)

            self._in_flight = asyncio.create_task(_do_send())
            try:
                result = await self._in_flight
            finally:
                self._in_flight = None

            if result is False:
                # Hard failure — re-queue text so caller can inspect, then abort
                self._pending_text = text
                return
            
            # Increment revision counter on successful send
            # Mirrors TS: previewRevision += 1 in sendOrEditStreamMessage
            if result is True:
                self._preview_revision += 1
                self._last_delivered_text = text.rstrip()

            self._last_sent_at = time.time()
            if not self._pending_text:
                return

    async def _send_or_edit_stream_message(self, text: str) -> bool | None:
        """Send or update the streaming preview.

        Returns:
          True  — success
          None  — skipped (no change / debounce)
          False — hard failure (stream stops)
        """
        if self._stopped and not self._is_final:
            return False

        trimmed = text.rstrip()
        if not trimmed:
            return None

        if len(trimmed) > self._max_chars:
            self._stopped = True
            logger.warning(
                "Telegram stream preview stopped: text too long (%d > %d chars)",
                len(trimmed), self._max_chars,
            )
            return False

        if trimmed == self._last_sent_text:
            return None

        # Debounce: wait for minimum initial content before first push
        if self._min_initial_chars is not None and not self._is_final:
            is_first = (
                self._last_sent_text == ""
                and (self._use_draft_transport or self._stream_message_id is None)
            )
            if is_first and len(trimmed) < self._min_initial_chars:
                return None

        self._last_sent_text = trimmed

        # Convert markdown → Telegram HTML with file-reference TLD protection.
        # Mirrors TS renderTelegramHtmlText + wrapFileReferencesInHtml() in format.ts.
        from openclaw.channels.telegram.formatter import markdown_to_html, wrap_file_references_in_html
        html_text = wrap_file_references_in_html(markdown_to_html(trimmed))

        try:
            if self._use_draft_transport:
                return await self._send_draft(html_text)
            else:
                return await self._send_or_edit_message(html_text)

        except Exception as exc:
            _err = str(exc).lower() if exc else "unknown error"
            if "chat not found" in _err:
                logger.warning(
                    "Telegram stream preview stopped: chat not found (chat_id=%s). "
                    "Likely: bot not started in DM, bot removed, group migrated, wrong token.",
                    self._chat_id,
                )
            else:
                logger.warning("Telegram stream preview error: %s", exc, exc_info=True)
            self._stopped = True
            return False

    async def _send_draft(self, html_text: str) -> bool:
        """sendMessageDraft transport — Bot API 9.5, PTB v22+.

        draft_id is an INTEGER (monotonically increasing per-process).
        Mirrors TS sendDraftTransportPreview() in draft-stream.ts.
        """
        thread_id: int | None = self._thread_params.get("message_thread_id")
        await self._api.send_message_draft(
            chat_id=self._chat_id,
            draft_id=self._draft_id,
            text=html_text,
            parse_mode="HTML",
            message_thread_id=thread_id,
        )
        return True

    async def _send_or_edit_message(self, html_text: str) -> bool:
        """Message transport — sendMessage (first) + editMessageText (subsequent).

        Mirrors TS sendMessageTransportPreview() in draft-stream.ts.
        """
        import re as _re

        if self._stream_message_id is not None:
            # Edit existing preview message
            try:
                await self._api.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._stream_message_id,
                    text=html_text,
                    parse_mode="HTML",
                )
            except Exception as _html_err:
                _e = str(_html_err).lower()
                if "chat not found" in _e or "message to edit not found" in _e:
                    raise
                # HTML parse error — retry as plain text
                plain = _re.sub(r"<[^>]+>", "", html_text)
                await self._api.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._stream_message_id,
                    text=plain,
                )
            return True

        # First send — create the preview message
        reply_params: dict[str, Any] = {}
        if self._reply_to_message_id is not None:
            reply_params["reply_to_message_id"] = self._reply_to_message_id
        reply_params.update(self._thread_params)

        try:
            sent = await self._api.send_message(
                chat_id=self._chat_id,
                text=html_text,
                parse_mode="HTML",
                **reply_params,
            )
        except Exception as _html_err:
            _e = str(_html_err).lower()
            if "chat not found" in _e:
                raise
            plain = _re.sub(r"<[^>]+>", "", html_text)
            sent = await self._api.send_message(
                chat_id=self._chat_id,
                text=plain,
                **reply_params,
            )

        if not sent or not hasattr(sent, "message_id"):
            self._stopped = True
            logger.warning("Telegram stream preview: send_message returned no message_id")
            return False

        self._stream_message_id = sent.message_id
        return True


def create_telegram_draft_stream(
    bot_api: Any,
    chat_id: int | str,
    max_chars: int | None = None,
    thread_params: dict | None = None,
    reply_to_message_id: int | None = None,
    throttle_ms: int | None = None,
    min_initial_chars: int | None = None,
    is_dm: bool = False,
) -> TelegramDraftStream:
    """Create a TelegramDraftStream.

    is_dm=True enables sendMessageDraft transport for DM chats (Bot API 9.5).
    Requires PTB v22+ with send_message_draft support; falls back to message transport.
    Mirrors TS createTelegramDraftStream().
    """
    return TelegramDraftStream(
        bot_api=bot_api,
        chat_id=chat_id,
        max_chars=max_chars,
        thread_params=thread_params,
        reply_to_message_id=reply_to_message_id,
        throttle_ms=throttle_ms,
        min_initial_chars=min_initial_chars,
        is_dm=is_dm,
    )
