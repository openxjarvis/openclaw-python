"""Telegram channel implementation"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timezone
from typing import Any, Optional

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

from ..base import ChannelCapabilities, ChannelPlugin, InboundMessage
from ..chat_commands import ChatCommandExecutor, ChatCommandParser
from .commands_extended import (
    register_extended_commands,  # noqa: F401 – kept for backward compat, not used directly
)
from .i18n_support import register_lang_handlers
from .sent_message_cache import record_sent_message, was_sent_by_bot
from .sticker_cache import (
    CachedSticker,
    cache_sticker,
    describe_sticker_image,
    get_cached_sticker,
)
from .update_dedupe import TelegramUpdateDedupe, callback_key, message_key, update_key
from .update_offset_store import (
    read_telegram_update_offset,
    write_telegram_update_offset,
)

logger = logging.getLogger(__name__)

# Reconnect policy — mirrors TS TELEGRAM_POLL_RESTART_POLICY
_POLL_BACKOFF_INITIAL = 2.0       # seconds (TS initialMs: 2000)
_POLL_BACKOFF_MAX = 30.0          # seconds (TS maxMs: 30000)
_POLL_BACKOFF_FACTOR = 1.8        # TS factor: 1.8
_POLL_JITTER = 0.25               # TS jitter: 0.25
_MAX_RETRY_TIME_S = 60 * 60       # 1 hour (TS maxRetryTime: 60 min)
_POLL_TIMEOUT_S = 30              # matches TS grammY fetch.timeout: 30
_HEALTH_CHECK_INTERVAL_S = 60     # health check interval
_HEALTH_CHECK_TIMEOUT_S = 15      # get_me() timeout
_HEALTH_MAX_FAILURES = 3          # consecutive failures before forced restart

# Backwards-compat aliases used in existing code
_CONFLICT_BACKOFF_INITIAL = _POLL_BACKOFF_INITIAL
_CONFLICT_BACKOFF_MAX = _POLL_BACKOFF_MAX
_CONFLICT_BACKOFF_FACTOR = _POLL_BACKOFF_FACTOR
_CONFLICT_MAX_RETRY_TIME = 5 * 60


# ---------------------------------------------------------------------------
# Network error classification — mirrors TS isRecoverableTelegramNetworkError
# ---------------------------------------------------------------------------

_RECOVERABLE_ERROR_CODES = frozenset({
    "ECONNRESET", "ECONNREFUSED", "EPIPE", "ETIMEDOUT",
    "ESOCKETTIMEDOUT", "ENETUNREACH", "EHOSTUNREACH",
    "ENOTFOUND", "ECONNABORTED",
})

_RECOVERABLE_ERROR_NAMES = frozenset({
    "AbortError", "TimeoutError", "ConnectTimeoutError",
    "RequestError", "FetchError",
})


# ---------------------------------------------------------------------------
# Safe reply_to converter — mirrors TS cbq_ guard
# ---------------------------------------------------------------------------

def _safe_reply_to(reply_to: str | int | None) -> int | None:
    """Convert reply_to to int, returning None for non-numeric values like 'cbq_...' IDs.

    Callback queries produce synthetic IDs prefixed with 'cbq_' which are not
    valid Telegram message IDs and must not be passed to reply_to_message_id.
    Mirrors TS cbq_ handling in bot-handlers.ts.
    """
    if reply_to is None:
        return None
    if isinstance(reply_to, int):
        return reply_to
    try:
        return int(reply_to)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# sendChatAction 401 circuit breaker — mirrors TS sendchataction-401-backoff.ts
# ---------------------------------------------------------------------------

_SEND_CHAT_ACTION_401_MAX_FAILURES = 10  # suspend after N consecutive 401s
_SEND_CHAT_ACTION_401_BACKOFF_INITIAL = 1.0   # seconds
_SEND_CHAT_ACTION_401_BACKOFF_MAX = 300.0     # 5 minutes
_SEND_CHAT_ACTION_401_BACKOFF_FACTOR = 2.0
_SEND_CHAT_ACTION_401_JITTER = 0.10           # ±10%

# Per-account state: {account_key: {"failures": int, "suspended_until": float}}
_send_chat_action_backoff: dict[str, dict] = {}

_RECOVERABLE_MSG_FRAGMENTS = (
    "network error", "socket hang up", "timeout", "econnreset",
    "undici", "fetch failed", "ETIMEDOUT", "ECONNREFUSED",
    "ENOTFOUND", "read ECONNRESET", "write EPIPE",
)


def _is_recoverable_network_error(exc: BaseException) -> bool:
    """Return True when *exc* looks like a transient network error.

    Mirrors TS ``isRecoverableTelegramNetworkError``.
    """
    import telegram.error as tg_err

    if isinstance(exc, (tg_err.NetworkError, tg_err.TimedOut)):
        return True

    msg = str(exc).lower()
    name = type(exc).__name__

    if name in _RECOVERABLE_ERROR_NAMES:
        return True
    if any(frag.lower() in msg for frag in _RECOVERABLE_MSG_FRAGMENTS):
        return True

    # Recurse into cause chain (mirrors TS recursive inspection)
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None and cause is not exc:
        return _is_recoverable_network_error(cause)

    return False


class TelegramChannel(ChannelPlugin):
    """Telegram bot channel"""

    def __init__(self, bot_token: str | None = None):
        super().__init__()
        self.id = "telegram"
        self.label = "Telegram"
        self.capabilities = ChannelCapabilities(
            chat_types=["direct", "group", "channel"],
            supports_media=True,
            supports_reactions=True,
            supports_threads=False,
            supports_polls=True,
            block_streaming=True,
            native_commands=True,
            supports_edit=True,
            supports_unsend=True,
            supports_reply=True,
        )
        self._app: Application | None = None
        self._bot_token: str | None = None
        self._command_parser: ChatCommandParser | None = None
        self._command_executor: ChatCommandExecutor | None = None
        self._owner_id: str | None = None
        self._config: dict | None = None
        self._cfg: dict[str, Any] = {}
        self._account_id: str | None = None
        self._agent_runtime: Any = None
        self._session_manager: Any = None
        self._dedupe = TelegramUpdateDedupe()
        self._conflict_backoff = _CONFLICT_BACKOFF_INITIAL
        self._conflict_retry_task: asyncio.Task | None = None
        self._conflict_recovery_in_progress: bool = False
        # Monotonic timestamp of the most recent Conflict error — used by the
        # health monitor to guard against resetting the backoff while conflicts
        # are still occurring.  None means no conflict yet in this run.
        self._last_conflict_at: float | None = None

        # Media group buffering (albums)
        self._media_group_buffer: dict[str, dict] = {}
        self._media_group_processing: asyncio.Task | None = None

        # Text fragment buffering (long messages split by Telegram)
        self._text_fragment_buffer: dict[str, dict] = {}
        self._text_fragment_processing: asyncio.Task | None = None

        # Channel watchdog: reset on each message, wakes heartbeat on silence
        self._heartbeat_monitor = None

        if bot_token is not None:
            if not bot_token:
                raise ValueError("bot_token cannot be an empty string")
            self._bot_token = bot_token

    @property
    def bot_token(self) -> str | None:
        """Return the configured bot token."""
        return self._bot_token

    async def _make_api_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Make a raw Telegram Bot API call.

        Args:
            method: Telegram Bot API method (e.g. "sendMessage")
            params: Method parameters

        Returns:
            Parsed API response dict
        """
        import aiohttp
        if not self._bot_token:
            raise ValueError("Bot token not configured")
        url = f"https://api.telegram.org/bot{self._bot_token}/{method}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params or {}) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    raise Exception(f"Telegram API error: {data.get('description', 'Unknown error')}")
                return data.get("result", {})

    async def send_message(self, chat_id: str, text: str, **kwargs) -> dict[str, Any]:
        """
        Send a text message to a chat.

        Args:
            chat_id: Telegram chat ID
            text: Message text

        Returns:
            Sent message dict with at least ``message_id``
        """
        return await self._make_api_call("sendMessage", {"chat_id": chat_id, "text": text, **kwargs})

    async def send_photo(self, chat_id: str, photo: str, **kwargs) -> dict[str, Any]:
        """
        Send a photo to a chat.

        Args:
            chat_id: Telegram chat ID
            photo: Photo URL or file_id

        Returns:
            Sent message dict
        """
        return await self._make_api_call("sendPhoto", {"chat_id": chat_id, "photo": photo, **kwargs})

    def parse_message(self, telegram_message: dict[str, Any]) -> dict[str, Any]:
        """
        Parse a raw Telegram message dict into a normalised format.

        Args:
            telegram_message: Raw Telegram message object

        Returns:
            Normalised message dict with ``text``, ``user_id``, ``chat_id``,
            ``message_id``, ``is_command`` fields.
        """
        from_user = telegram_message.get("from", {})
        chat = telegram_message.get("chat", {})
        text = telegram_message.get("text", "")
        entities = telegram_message.get("entities", [])

        is_command = any(e.get("type") == "bot_command" for e in entities)

        return {
            "message_id": str(telegram_message.get("message_id", "")),
            "user_id": str(from_user.get("id", "")),
            "chat_id": str(chat.get("id", "")),
            "text": text,
            "is_command": is_command,
            "date": telegram_message.get("date"),
            "from": from_user,
        }

    async def start(self, config: dict[str, Any]) -> None:
        """Start Telegram bot"""
        if self._running and self._app is not None:
            logger.warning(
                "Telegram channel already running — ignoring duplicate start() call "
                "(likely both plugin and built-in paths tried to start the same instance)"
            )
            return

        self._bot_token = config.get("botToken") or config.get("bot_token")

        if not self._bot_token:
            raise ValueError("Telegram bot token not provided")

        # Get owner ID for command permissions
        self._owner_id = config.get("ownerId") or config.get("owner_id")
        self._config = config
        self._cfg = config  # Alias for compatibility

        logger.info("Starting Telegram channel...")

        # Initialize chat command system
        self._command_parser = ChatCommandParser()

        # Store runtime config values for send_text path
        self._text_chunk_limit: int = config.get("textChunkLimit") or 4096
        self._chunk_mode: str = config.get("chunkMode") or "smart"
        self._response_prefix: str = config.get("responsePrefix") or ""
        self._link_preview: bool = bool(config.get("linkPreview", True))
        self._media_max_mb: int = config.get("mediaMaxMb") or 50
        self._history_limit: int = config.get("historyLimit") or 100
        self._dm_history_limit: int = config.get("dmHistoryLimit") or 50
        self._streaming_enabled: bool = bool(config.get("streaming", False))
        self._reply_to_mode: str = config.get("replyToMode") or "first"

        # Build Application — wire proxy and network timeouts
        builder = Application.builder().token(self._bot_token)
        from .network_config import resolve_telegram_proxy
        proxy_url = resolve_telegram_proxy(config)
        if proxy_url:
            try:
                from telegram.request import HTTPXRequest
                _req = HTTPXRequest(proxy=proxy_url)
                builder = builder.request(_req).get_updates_request(_req)
                logger.info("Telegram proxy configured: %s", proxy_url)
            except Exception as proxy_err:
                logger.warning("Failed to configure Telegram proxy (%s): %s", proxy_url, proxy_err)

        network_cfg = config.get("network") or {}
        connect_timeout = float(network_cfg.get("connectTimeout") or 10)
        read_timeout = float(network_cfg.get("readTimeout") or 30)
        write_timeout = float(network_cfg.get("writeTimeout") or 30)
        pool_timeout = float(network_cfg.get("poolTimeout") or 10)
        try:
            builder = (
                builder
                .connect_timeout(connect_timeout)
                .read_timeout(read_timeout)
                .write_timeout(write_timeout)
                .pool_timeout(pool_timeout)
            )
        except Exception:
            pass  # Older PTB versions may not support all timeout setters

        self._app = builder.build()

        # Register i18n language switching handlers
        register_lang_handlers(self._app)

        # NOTE: register_extended_commands() is intentionally NOT called here.
        # All slash-command handlers are registered later in _register_dynamic_command_handlers()
        # via the proper command pipeline (command_pipeline.py).  The old extended-command
        # handlers required bot_data["agent_runtime"] which is never populated, producing the
        # "Command cannot be executed" error.

        # Add callback query handler for inline keyboards
        self._app.add_handler(CallbackQueryHandler(self._handle_callback_query))

        # Add message handlers for all types (text and media)
        # Handle text messages
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_telegram_message)
        )
        # Handle photo messages
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self._handle_telegram_media)
        )
        # Handle video messages
        self._app.add_handler(
            MessageHandler(filters.VIDEO, self._handle_telegram_media)
        )
        # Handle audio messages
        self._app.add_handler(
            MessageHandler(filters.AUDIO | filters.VOICE, self._handle_telegram_media)
        )
        # Handle document messages
        self._app.add_handler(
            MessageHandler(filters.Document.ALL, self._handle_telegram_media)
        )

        # Handle message reactions
        self._app.add_handler(
            MessageReactionHandler(self._handle_reaction_update)
        )

        # Start bot
        await self._app.initialize()
        await self._app.start()

        # Get bot info after initialization
        bot_info = await self._app.bot.get_me()
        # Resolve account_id: use configured accountId if present, else "default" (matches TS resolveAccountId())
        cfg_account_id = (
            (config or {}).get("accountId")
            or (config or {}).get("account_id")
            or ""
        )
        account_id = str(cfg_account_id).strip() if cfg_account_id else ""
        logger.info(f"Bot initialized: @{bot_info.username} (account_id: {account_id})")

        # Create a minimal config dict for command handler
        cmd_config = {
            "channels": {
                "telegram": {
                    "accounts": {
                        account_id: {
                            "allowFrom": []  # Allow all for now
                        }
                    }
                }
            },
            "agents": {
                "defaults": {
                    "model": config.get("model", "google/gemini-3-pro-preview")
                }
            }
        }

        self._account_id = account_id

        # Register conflict/error handler for update-processing errors
        # (Updater polling errors are handled via error_callback in start_polling below)
        self._app.add_error_handler(self._handle_application_error)

        # Delete any existing webhook to ensure clean state when switching
        # from webhook to polling mode.
        await self._app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Cleared webhook and pending updates")

        # Restore persisted update offset so we don't reprocess old updates.
        # Read BEFORE the pre-start probe so the probe can use the correct offset.
        saved_offset = read_telegram_update_offset(account_id)
        if saved_offset is not None:
            logger.info("Resuming from persisted update offset %d", saved_offset)

        # Invalidate any lingering getUpdates long-poll from a previous process.
        # When the old gateway is killed (even with SIGKILL), its TCP connection
        # to Telegram stays alive server-side for up to 30 seconds (the long-poll
        # timeout).  delete_webhook() does NOT affect getUpdates connections —
        # they are a completely separate API path.  The ONLY way to break the
        # stale server-side long-poll is to send our OWN getUpdates request,
        # which causes Telegram to terminate the old one with 409 Conflict.
        await self._invalidate_stale_polling()

        # Suppress PTB Updater's own ERROR-level logging for 409 Conflict errors.
        import logging as _logging
        _logging.getLogger("telegram.ext.Updater").setLevel(_logging.WARNING)

        # Register dynamic command handlers (all native commands from registry)
        # Must be after bot initialization so we have account_id and cfg
        await self._register_dynamic_command_handlers()

        # Register bot commands with Telegram
        await self._register_bot_commands()

        # Set bot menu button (optional)
        await self._setup_menu_button()

        # Webhook vs polling mode (mirrors TS: webhookUrl → start_webhook)
        webhook_url = config.get("webhookUrl") or config.get("webhook_url")
        webhook_port = int(config.get("webhookPort") or config.get("webhook_port") or 8443)
        self._webhook_mode = bool(webhook_url)

        if webhook_url:
            logger.info("Telegram channel starting in WEBHOOK mode: %s", webhook_url)
            try:
                await self._app.updater.start_webhook(
                    listen="0.0.0.0",
                    port=webhook_port,
                    url_path=self._bot_token,
                    webhook_url=f"{webhook_url.rstrip('/')}/{self._bot_token}",
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
            except Exception as wh_err:
                logger.error("Failed to start webhook, falling back to polling: %s", wh_err)
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    error_callback=self._handle_updater_poll_error,
                )
        else:
            await self._app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,  # We already dropped via delete_webhook
                error_callback=self._handle_updater_poll_error,
            )

        self._running = True
        self._conflict_backoff = _POLL_BACKOFF_INITIAL
        # Launch background health monitor
        self._health_monitor_task: asyncio.Task | None = asyncio.create_task(
            self._run_health_monitor()
        )
        # Channel silence watchdog — mirrors TS DEFAULT_STALE_EVENT_THRESHOLD_MS (30 min)
        try:
            from openclaw.auto_reply.heartbeat_monitor import HeartbeatMonitor
            from openclaw.infra.heartbeat_wake import request_heartbeat_now

            async def _on_silence(channel_id: str) -> None:
                logger.info("Telegram channel silence detected — requesting heartbeat wake")
                await request_heartbeat_now(reason="wake", agent_id=None, session_key=None)

            self._heartbeat_monitor = HeartbeatMonitor(
                channel_id=self.id,
                timeout_seconds=30 * 60,
                health_check_callback=_on_silence,
            )
            await self._heartbeat_monitor.start()
        except Exception as _exc:
            logger.debug("Heartbeat monitor init failed: %s", _exc)
            self._heartbeat_monitor = None
        logger.info("Telegram channel started")

    async def stop(self) -> None:
        """Stop Telegram bot"""
        self._running = False
        self._conflict_recovery_in_progress = False
        if self._conflict_retry_task and not self._conflict_retry_task.done():
            self._conflict_retry_task.cancel()
        health_task = getattr(self, "_health_monitor_task", None)
        if health_task and not health_task.done():
            health_task.cancel()
        if self._heartbeat_monitor and getattr(self._heartbeat_monitor, "is_running", lambda: False)():
            try:
                await self._heartbeat_monitor.stop()
            except Exception:
                pass
        if self._app:
            logger.info("Stopping Telegram channel...")
            try:
                await self._app.updater.stop()
            except Exception:
                pass
            await self._app.stop()
            await self._app.shutdown()
            self._running = False
            logger.info("Telegram channel stopped")

    async def _handle_application_error(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Application-level PTB error handler for update processing errors."""
        self._handle_telegram_error(getattr(context, "error", None))

    def _handle_updater_poll_error(self, exc: Exception) -> None:
        """Non-async PTB polling callback for getUpdates errors.

        PTB's `Updater.start_polling(error_callback=...)` requires a regular
        function, not a coroutine. This is the path used for long-polling 409
        Conflict errors from `getUpdates`.
        """
        self._handle_telegram_error(exc)

    def _handle_telegram_error(self, exc: Exception | None) -> None:
        """Handle Telegram updater/application errors.

        Mirrors TS monitor.ts error handling for Conflict (409), recoverable
        network errors, and unknown failures.

        For Conflict errors we do NOT stop/reinitialize/restart the PTB Updater.
        PTB's own ``network_retry_loop`` already retries ``getUpdates`` with
        exponential backoff (0 → 1 → 1.5 → 2.25 → … → 30 s).  Stopping and
        restarting the Updater is counter-productive because PTB's ``stop()``
        sends a cleanup ``getUpdates(timeout=0)`` that creates a NEW server-side
        connection, which then conflicts with the NEXT ``start_polling()`` call
        — perpetuating the conflict forever.

        Instead we just log and let PTB's retry loop keep trying.  The old
        server-side long-poll dies within ~30 s; PTB will succeed on the next
        retry after that.
        """
        import telegram.error as tg_err

        if exc is None:
            return

        if isinstance(exc, tg_err.Conflict):
            import time as _time
            self._last_conflict_at = _time.monotonic()
            logger.warning(
                "Telegram Conflict (another bot instance polling) — "
                "PTB retry loop will recover automatically: %s",
                exc,
            )

        elif _is_recoverable_network_error(exc):
            # PTB's network_retry_loop already handles NetworkError/TimedOut by
            # retrying internally.  Scheduling a restart here would stop an
            # actively-retrying updater and trigger the initialize() lifecycle,
            # causing the "Updater was not initialized" error during outages.
            # Mirror TS behaviour: log and let PTB's built-in retry loop recover.
            logger.debug("Telegram recoverable network error (PTB retry loop active): %s", exc)

        elif isinstance(exc, tg_err.TimedOut):
            logger.debug("Telegram request timed out (auto-retry by PTB): %s", exc)

        elif isinstance(exc, tg_err.InvalidToken):
            logger.critical("Telegram InvalidToken — will not retry: %s", exc)

        else:
            logger.error("Unhandled Telegram error: %s", exc, exc_info=True)

    def _schedule_polling_restart(self, reason: str = "unknown") -> None:
        """Schedule a polling restart, cancelling any in-progress restart.

        Sets _conflict_recovery_in_progress=True synchronously (before
        create_task) so that rapid back-to-back Conflict errors cannot cancel
        and recreate the task before it gets a chance to start.
        """
        if self._conflict_recovery_in_progress:
            return
        # Set flag synchronously so the next error handler call sees it
        # immediately (before the task coroutine body starts executing).
        self._conflict_recovery_in_progress = True
        if self._conflict_retry_task and not self._conflict_retry_task.done():
            self._conflict_retry_task.cancel()
        self._conflict_retry_task = asyncio.create_task(
            self._restart_polling_after_conflict(reason=reason)
        )

    async def _restart_polling_after_conflict(self, reason: str = "unknown") -> None:
        """Stop polling, invalidate server-side connections, and resume.

        Used only by the health monitor for hard failures.  Conflict errors
        from getUpdates are handled by PTB's built-in retry loop and do NOT
        trigger this restart (see _handle_telegram_error).
        """
        try:
            import random
            wait = self._conflict_backoff
            jitter = wait * _POLL_JITTER * (2 * random.random() - 1)
            wait_with_jitter = max(0.5, wait + jitter)

            self._conflict_backoff = min(
                self._conflict_backoff * _POLL_BACKOFF_FACTOR,
                _POLL_BACKOFF_MAX,
            )
            logger.info(
                "Polling restart (%s): pausing %.1fs before retry (next backoff=%.1fs)",
                reason,
                wait_with_jitter,
                self._conflict_backoff,
            )
            await asyncio.sleep(wait_with_jitter)

            if not self._running or self._app is None:
                return

            # Persist current update offset before stopping
            if self._account_id and self._app.updater:
                try:
                    current_offset = getattr(self._app.updater, "_last_update_id", None)
                    if current_offset is not None and current_offset > 0:
                        write_telegram_update_offset(self._account_id, current_offset)
                except Exception:
                    pass

            try:
                await self._app.updater.stop()
            except Exception as stop_exc:
                logger.debug("Updater stop during restart (%s): %s", reason, stop_exc)

            # After stop(), PTB's cleanup get_updates(timeout=0) may have left
            # a brief server-side connection.  Invalidate it with our own probe
            # + sleep before restarting, to avoid a self-inflicted 409 loop.
            await self._invalidate_stale_polling()

            try:
                await self._app.updater.initialize()
                await self._app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                    error_callback=self._handle_updater_poll_error,
                )
                logger.info("Polling restarted after %s", reason)
            except Exception as start_exc:
                logger.error("Failed to restart polling (%s): %s", reason, start_exc)
                self._conflict_recovery_in_progress = False
                self._schedule_polling_restart(reason=reason)
        finally:
            self._conflict_recovery_in_progress = False

    async def _invalidate_stale_polling(self) -> None:
        """Send a short getUpdates probe to break any lingering server-side
        long-poll, then sleep to let Telegram fully release the slot.

        Called both during initial startup and before restart.
        """
        try:
            saved_offset = None
            if self._account_id:
                saved_offset = read_telegram_update_offset(self._account_id)
            _offset = (saved_offset + 1) if saved_offset is not None else None
            await self._app.bot.get_updates(
                offset=_offset,
                timeout=0,
                allowed_updates=Update.ALL_TYPES,
            )
            logger.debug("Polling slot claimed — old long-poll invalidated")
        except Exception as exc:
            logger.debug("Polling slot probe: %s (expected during transition)", exc)
        await asyncio.sleep(1.5)

    async def _run_health_monitor(self) -> None:
        """Periodically check that the bot is reachable.

        Mirrors TS EnhancedTelegramChannel health check: calls ``get_me()``
        every ``_HEALTH_CHECK_INTERVAL_S`` seconds.  After
        ``_HEALTH_MAX_FAILURES`` consecutive failures, forces a polling
        restart.
        """
        failures = 0
        while self._running and self._app is not None:
            try:
                await asyncio.sleep(_HEALTH_CHECK_INTERVAL_S)
                if not self._running or self._app is None:
                    break
                await asyncio.wait_for(
                    self._app.bot.get_me(),
                    timeout=_HEALTH_CHECK_TIMEOUT_S,
                )
                failures = 0  # reset on success
                # Only reset the conflict backoff if no Conflict error has
                # been seen recently.  get_me() succeeds even while conflicts
                # are occurring (it's a different API endpoint), so we guard
                # with _last_conflict_at to avoid resetting the backoff while
                # the restart cycle is still active.
                if self._conflict_backoff > _CONFLICT_BACKOFF_INITIAL:
                    import time as _time
                    _conflict_free_window = _HEALTH_CHECK_INTERVAL_S * 2  # 120s
                    _since_last = (
                        _time.monotonic() - self._last_conflict_at
                        if self._last_conflict_at is not None
                        else float("inf")
                    )
                    if _since_last >= _conflict_free_window:
                        self._conflict_backoff = _CONFLICT_BACKOFF_INITIAL
                        logger.info(
                            "Conflict resolved (no conflict for %.0fs) — polling backoff reset",
                            _since_last,
                        )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if _is_recoverable_network_error(exc):
                    # Network outage: PTB's retry loop is already handling this.
                    # Don't count against the failure threshold — health restarts
                    # are only for cases where the bot is stuck despite connectivity.
                    logger.debug(
                        "Telegram health check: network unavailable (PTB retry loop active): %s",
                        exc,
                    )
                else:
                    failures += 1
                    logger.warning(
                        "Telegram health check failed (%d/%d): %s",
                        failures,
                        _HEALTH_MAX_FAILURES,
                        exc,
                    )
                    if failures >= _HEALTH_MAX_FAILURES:
                        logger.error(
                            "Telegram health check failed %d times — forcing polling restart",
                            failures,
                        )
                        failures = 0
                        self._schedule_polling_restart(reason="health-check-failure")

    async def send_typing(
        self,
        target: str,
        message_id: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        """Send a 'typing…' chat action to show the bot is processing.

        Implements a 401 circuit breaker — mirrors TS sendchataction-401-backoff.ts.
        After 10 consecutive 401 Unauthorized responses the action is suspended to
        prevent Telegram from deleting the bot for repeated bad-auth requests.
        The counter resets on each successful call.
        """
        if not self._app:
            return

        import math
        import random
        import time

        account_key = self._account_id or "default"
        state = _send_chat_action_backoff.setdefault(account_key, {"failures": 0, "suspended_until": 0.0})

        # Circuit breaker: check suspension window
        now = time.monotonic()
        if state["failures"] >= _SEND_CHAT_ACTION_401_MAX_FAILURES:
            if now < state["suspended_until"]:
                return  # silently skip — bot is suspended
            # Suspension window expired; try again cautiously
            logger.info("send_typing 401 suspension window expired for account %s, retrying", account_key)

        try:
            chat_id = int(target) if str(target).lstrip("-").isdigit() else target
            _typing_kwargs: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
            if message_thread_id is not None:
                _typing_kwargs["message_thread_id"] = message_thread_id
            await self._app.bot.send_chat_action(**_typing_kwargs)
            # Success — reset circuit breaker
            if state["failures"] > 0:
                logger.info("send_typing recovered for account %s (was %d failures)", account_key, state["failures"])
            state["failures"] = 0
            state["suspended_until"] = 0.0
        except Exception as exc:
            exc_str = str(exc)
            is_401 = "401" in exc_str or "Unauthorized" in exc_str.lower()
            if is_401:
                state["failures"] += 1
                n = state["failures"]
                # Exponential backoff with jitter
                delay = min(
                    _SEND_CHAT_ACTION_401_BACKOFF_INITIAL * (_SEND_CHAT_ACTION_401_BACKOFF_FACTOR ** (n - 1)),
                    _SEND_CHAT_ACTION_401_BACKOFF_MAX,
                )
                jitter = delay * _SEND_CHAT_ACTION_401_JITTER * (2 * random.random() - 1)
                state["suspended_until"] = time.monotonic() + delay + jitter
                if n >= _SEND_CHAT_ACTION_401_MAX_FAILURES:
                    logger.critical(
                        "send_typing: %d consecutive 401 Unauthorized errors for account %s. "
                        "sendChatAction suspended — check bot token validity. "
                        "Mirrors TS sendchataction-401-backoff.ts suspension logic.",
                        n, account_key,
                    )
                else:
                    logger.warning(
                        "send_typing 401 for account %s (failure %d/%d), backoff %.1fs",
                        account_key, n, _SEND_CHAT_ACTION_401_MAX_FAILURES, delay,
                    )
            else:
                logger.debug("send_typing failed for %s: %s", target, exc)

    def _fire_message_sent_hook(
        self,
        to: str,
        content: str,
        success: bool,
        message_id: str | None = None,
        error: str | None = None,
        session_key: str | None = None,
    ) -> None:
        """Fire internal hook for message:sent (fire-and-forget)."""
        import asyncio

        if not session_key:
            return

        try:
            from openclaw.hooks.internal_hooks import (
                create_internal_hook_event,
                trigger_internal_hook,
            )

            context = {
                "to": to,
                "content": content,
                "success": success,
                "channelId": "telegram",
                "channel_id": "telegram",
                "accountId": None,
                "account_id": None,
                "conversationId": to,
                "conversation_id": to,
            }

            if message_id:
                context["messageId"] = message_id
                context["message_id"] = message_id

            if error:
                context["error"] = error

            hook_event = create_internal_hook_event(
                "message",
                "sent",
                session_key,
                context
            )

            async def _trigger():
                await trigger_internal_hook(hook_event)

            try:
                asyncio.ensure_future(_trigger())
            except Exception:
                pass
        except Exception:
            pass

    async def send_text(
        self,
        target: str,
        text: str,
        reply_to: str | None = None,
        session_key: str | None = None,
        buttons: list[list[dict]] | None = None,
        message_thread_id: int | None = None,
    ) -> str:
        """Send text message with Markdown→HTML conversion.

        buttons: optional 2D list of inline keyboard buttons.  Each entry is a dict
        with at least "text" and "callback_data" keys.  The buttons are only attached
        when the configured inlineButtonsScope allows it for the target chat.
        message_thread_id: Telegram forum topic thread ID (for supergroup forum replies).
          Mirrors TS buildTelegramThreadReplyParams().
        """
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        success = False
        error_msg = None
        message_id = None

        # Build InlineKeyboardMarkup when buttons are requested and allowed.
        reply_markup = None
        if buttons:
            try:
                from openclaw.telegram.send import build_inline_keyboard
                from openclaw.channels.telegram.inline_buttons import (
                    resolve_telegram_inline_buttons_scope,
                    resolve_telegram_target_chat_type,
                    validate_inline_buttons_for_target,
                )
                scope = resolve_telegram_inline_buttons_scope(
                    self._config or {}, self._account_id
                )
                chat_type = resolve_telegram_target_chat_type(target)
                if validate_inline_buttons_for_target(scope, chat_type):
                    reply_markup = build_inline_keyboard(buttons)
            except Exception as _btn_err:
                logger.debug("Failed to build inline keyboard: %s", _btn_err)
                reply_markup = None

        try:
            # str() guard: safe even if target is already int (mirrors TS chatId handling)
            chat_id = int(target) if str(target).lstrip("-").isdigit() else target

            # Build thread params for Telegram forum topics.
            # Mirrors TS buildTelegramThreadReplyParams() in bot/helpers.ts.
            thread_kwargs: dict[str, Any] = {}
            if message_thread_id is not None:
                thread_kwargs["message_thread_id"] = message_thread_id

            # Convert markdown → Telegram HTML, then send with parse_mode="HTML".
            # Mirrors TS: markdownToTelegramHtml + parse_mode: "HTML" in send.ts.
            # On HTML parse error, fall back to plain text (strip tags).
            from openclaw.channels.telegram.formatter import markdown_to_html
            html_text = markdown_to_html(text)

            # Use modern link_preview_options (Bot API 7.0+) instead of deprecated
            # disable_web_page_preview — mirrors TS send.ts link_preview_options usage.
            from telegram import LinkPreviewOptions as _LPO
            _link_preview_opts = _LPO(is_disabled=not self._link_preview)

            async def _send_msg(extra_kwargs: dict) -> Any:
                """Inner helper — retried with/without thread_id on 'thread not found'."""
                return await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=extra_kwargs.pop("text"),
                    reply_to_message_id=_safe_reply_to(reply_to),
                    parse_mode=extra_kwargs.pop("parse_mode", None),
                    reply_markup=reply_markup,
                    link_preview_options=_link_preview_opts,
                    **extra_kwargs,
                )

            try:
                message = await _send_msg({
                    "text": html_text,
                    "parse_mode": "HTML",
                    **thread_kwargs,
                })
            except Exception as html_error:
                _err_str = str(html_error).lower()
                # Re-raise immediately on "chat not found" — retrying with plain text
                # won't help and masks the root cause diagnostic.
                if "chat not found" in _err_str:
                    raise
                # Thread-not-found: retry without message_thread_id — mirrors TS
                # withTelegramThreadFallback() in send.ts.
                if "message thread not found" in _err_str and thread_kwargs:
                    logger.debug("send_text: message thread not found, retrying without thread_id")
                    try:
                        message = await _send_msg({"text": html_text, "parse_mode": "HTML"})
                    except Exception:
                        import re as _re
                        plain_text = _re.sub(r"<[^>]+>", "", html_text)
                        message = await _send_msg({"text": plain_text})
                else:
                    logger.debug(f"HTML parse error, retrying as plain text: {html_error}")
                    import re as _re
                    plain_text = _re.sub(r"<[^>]+>", "", html_text)
                    try:
                        message = await _send_msg({
                            "text": plain_text,
                            **thread_kwargs,
                        })
                    except Exception as plain_err:
                        if "message thread not found" in str(plain_err).lower() and thread_kwargs:
                            logger.debug("send_text: thread not found on plain retry, dropping thread_id")
                            message = await _send_msg({"text": plain_text})
                        else:
                            raise

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            message_id = str(message.message_id)
            success = True

            # Trigger message:sent hook
            self._fire_message_sent_hook(
                to=target,
                content=text,
                success=True,
                message_id=message_id,
                session_key=session_key,
            )

            return message_id

        except Exception as e:
            error_msg = str(e)
            # Enrich "Chat not found" with actionable diagnostics.
            # Mirrors TS wrapTelegramChatNotFoundError() in send.ts.
            if "chat not found" in error_msg.lower():
                logger.error(
                    "Telegram send failed: chat not found (chat_id=%s). "
                    "Likely causes: bot not started in DM, bot removed from group/channel, "
                    "group migrated to supergroup (new -100… ID), or wrong bot token.",
                    target,
                )
            else:
                logger.error(f"Failed to send Telegram message: {e}", exc_info=True)

            # Trigger message:sent hook for failure
            self._fire_message_sent_hook(
                to=target,
                content=text,
                success=False,
                error=error_msg,
                session_key=session_key,
            )

            raise

    async def send_photo(
        self,
        target: str | None = None,
        photo: Any = None,
        caption: str | None = None,
        reply_to: str | None = None,
        keyboard: Any = None,
        chat_id: str | None = None,
        **kwargs,
    ) -> Any:
        """Send photo message.

        Supports two calling styles:
        - Legacy: send_photo(target, photo, caption, ...)
        - API-style: send_photo(chat_id="...", photo="...", ...)
        """
        # New-style call: chat_id keyword provided (uses _make_api_call)
        if chat_id is not None:
            return await self._make_api_call(
                "sendPhoto", {"chat_id": chat_id, "photo": photo, **kwargs}
            )

        # Legacy style: requires a running bot application
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        resolved_chat_id = int(target) if target and str(target).lstrip("-").isdigit() else target

        try:
            from openclaw.channels.telegram.formatter import markdown_to_html
            html_caption = markdown_to_html(caption) if caption else None
            message = await self._app.bot.send_photo(
                chat_id=resolved_chat_id,
                photo=photo,
                caption=html_caption,
                parse_mode="HTML" if html_caption else None,
                reply_to_message_id=_safe_reply_to(reply_to),
                reply_markup=keyboard,
            )

            # Record sent message for reaction tracking
            record_sent_message(resolved_chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send photo: {e}")
            raise

    async def send_video(
        self, target: str, video, caption: str | None = None,
        reply_to: str | None = None, keyboard=None
    ) -> str:
        """Send video message"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            from openclaw.channels.telegram.formatter import markdown_to_html
            html_caption = markdown_to_html(caption) if caption else None
            message = await self._app.bot.send_video(
                chat_id=chat_id,
                video=video,
                caption=html_caption,
                parse_mode="HTML" if html_caption else None,
                reply_to_message_id=_safe_reply_to(reply_to),
                reply_markup=keyboard
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send video: {e}")
            raise

    async def send_document(
        self, target: str, document, caption: str | None = None,
        reply_to: str | None = None, keyboard=None
    ) -> str:
        """Send document/file message"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            from openclaw.channels.telegram.formatter import markdown_to_html
            html_caption = markdown_to_html(caption) if caption else None
            message = await self._app.bot.send_document(
                chat_id=chat_id,
                document=document,
                caption=html_caption,
                parse_mode="HTML" if html_caption else None,
                reply_to_message_id=_safe_reply_to(reply_to),
                reply_markup=keyboard
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send document: {e}")
            raise

    async def send_audio(
        self, target: str, audio, caption: str | None = None,
        reply_to: str | None = None
    ) -> str:
        """Send audio message"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            from openclaw.channels.telegram.formatter import markdown_to_html
            html_caption = markdown_to_html(caption) if caption else None
            message = await self._app.bot.send_audio(
                chat_id=chat_id,
                audio=audio,
                caption=html_caption,
                parse_mode="HTML" if html_caption else None,
                reply_to_message_id=_safe_reply_to(reply_to)
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send audio: {e}")
            raise

    async def send_location(
        self, target: str, latitude: float, longitude: float,
        reply_to: str | None = None
    ) -> str:
        """Send location message"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            message = await self._app.bot.send_location(
                chat_id=chat_id,
                latitude=latitude,
                longitude=longitude,
                reply_to_message_id=_safe_reply_to(reply_to)
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send location: {e}")
            raise

    async def send_poll(
        self, target: str, question: str, options: list[str],
        is_anonymous: bool = True, reply_to: str | None = None
    ) -> str:
        """Send poll message"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            message = await self._app.bot.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                is_anonymous=is_anonymous,
                reply_to_message_id=_safe_reply_to(reply_to)
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send poll: {e}")
            raise

    async def send_dice(
        self, target: str, emoji: str = "🎲",
        reply_to: str | None = None
    ) -> str:
        """Send dice/animation message (🎲🎯🏀⚽🎳🎰)"""
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target

        try:
            message = await self._app.bot.send_dice(
                chat_id=chat_id,
                emoji=emoji,
                reply_to_message_id=_safe_reply_to(reply_to)
            )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, message.message_id)

            return str(message.message_id)
        except Exception as e:
            logger.error(f"Failed to send dice: {e}")
            raise

    async def send_media(
        self,
        target: str,
        media_url: str,
        media_type: str,
        caption: str | None = None,
        reply_to: str | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
        buttons: Any | None = None,
    ) -> str:
        """Send media message — mirrors TS delivery.ts deliverReplies().

        Supports: photo, video, animation (GIF), audio, voice, document, video_note.
        Caption longer than 1024 chars: media sent captionless, full text follows as a
        separate message (mirrors TS splitTelegramCaption behavior).
        Accepts both local file paths and HTTP URLs.
        """
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        from pathlib import Path

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target
        reply_id = _safe_reply_to(reply_to)

        # Thread kwargs for forum supergroups — mirrors TS buildTelegramThreadReplyParams()
        thread_kwargs: dict[str, Any] = {}
        if message_thread_id is not None:
            thread_kwargs["message_thread_id"] = message_thread_id

        # Convert caption to HTML — mirrors TS always-HTML approach in send.ts.
        from openclaw.channels.telegram.formatter import markdown_to_html as _md2html
        html_caption = _md2html(caption) if caption else None

        # Caption splitting — mirrors TS splitTelegramCaption():
        # if caption > 1024 chars, send media captionless and route full text
        # as a follow-up message (avoids mid-sentence HTML truncation).
        from openclaw.telegram.caption import split_telegram_caption
        _cap_split = split_telegram_caption(html_caption)
        html_caption = _cap_split["caption"]         # None when too long
        overflow_text: str | None = _cap_split["follow_up_text"]  # full text as follow-up

        # Build inline keyboard markup for media (e.g. buttons on follow-up text)
        reply_markup = None
        if buttons:
            from openclaw.channels.telegram.keyboard import build_inline_keyboard
            reply_markup = build_inline_keyboard(buttons)

        # Telegram Bot API hard limit for uploads via the public API
        _TELEGRAM_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
        # Write timeout scales with file size: at least 60s, +1s per MB
        _BASE_WRITE_TIMEOUT = 60.0

        media_source = media_url
        is_local_file = False
        write_timeout = _BASE_WRITE_TIMEOUT

        # Detect local file (no URL scheme)
        if not media_url.startswith(("http://", "https://", "file://")):
            file_path = Path(media_url).expanduser()
            if file_path.exists() and file_path.is_file():
                file_size = file_path.stat().st_size
                if file_size > _TELEGRAM_MAX_UPLOAD_BYTES:
                    size_mb = file_size / (1024 * 1024)
                    raise ValueError(
                        f"File too large for Telegram Bot API ({size_mb:.1f} MB, limit 50 MB): {file_path.name}. "
                        "Consider compressing the file or sharing it via a URL."
                    )
                # Scale write timeout by file size (1 second per MB, min 60s)
                write_timeout = max(_BASE_WRITE_TIMEOUT, file_size / (1024 * 1024))
                media_source = open(file_path, "rb")  # noqa: WPS515 — closed in finally
                is_local_file = True
                logger.info("Sending local file: %s (%.1f MB)", file_path, file_size / (1024 * 1024))
            else:
                raise FileNotFoundError(
                    f"Media file not found: {media_url!r}. "
                    "Ensure the agent outputs an absolute path or that the file "
                    "is resolved against the session workspace before calling send_media."
                )

        # Common kwargs shared across all media sub-calls
        _common: dict[str, Any] = {
            "chat_id": chat_id,
            "reply_to_message_id": reply_id,
            "disable_notification": silent,
            "write_timeout": write_timeout,
            **thread_kwargs,
        }

        try:
            if media_type == "photo":
                msg = await self._app.bot.send_photo(
                    photo=media_source,
                    caption=html_caption,
                    parse_mode="HTML" if html_caption else None,
                    **_common,
                )
            elif media_type == "video":
                msg = await self._app.bot.send_video(
                    video=media_source,
                    caption=html_caption,
                    parse_mode="HTML" if html_caption else None,
                    **_common,
                )
            elif media_type in ("animation", "gif"):
                msg = await self._app.bot.send_animation(
                    animation=media_source,
                    caption=html_caption,
                    parse_mode="HTML" if html_caption else None,
                    **_common,
                )
            elif media_type == "video_note":
                # Circular video note — no caption support in Telegram API
                msg = await self._app.bot.send_video_note(
                    video_note=media_source,
                    **_common,
                )
            elif media_type == "voice":
                try:
                    msg = await self._app.bot.send_voice(
                        voice=media_source,
                        caption=html_caption,
                        parse_mode="HTML" if html_caption else None,
                        **_common,
                    )
                except Exception as voice_err:
                    logger.warning("send_voice failed (%s), falling back to document", voice_err)
                    # Re-open if file was closed by failed send
                    if is_local_file:
                        file_path_obj = Path(media_url).expanduser()
                        media_source = open(file_path_obj, "rb")  # noqa: WPS515
                    msg = await self._app.bot.send_document(
                        document=media_source,
                        caption=html_caption,
                        parse_mode="HTML" if html_caption else None,
                        **_common,
                    )
            elif media_type == "audio":
                msg = await self._app.bot.send_audio(
                    audio=media_source,
                    caption=html_caption,
                    parse_mode="HTML" if html_caption else None,
                    **_common,
                )
            else:
                # Default: send as document (covers pptx, pdf, zip, etc.)
                msg = await self._app.bot.send_document(
                    document=media_source,
                    caption=html_caption,
                    parse_mode="HTML" if html_caption else None,
                    **_common,
                )

            # Record sent message for reaction tracking
            record_sent_message(chat_id, msg.message_id)

            # Send overflow text as follow-up — mirrors TS sendPendingFollowUpText():
            # route reply_markup (buttons) to this message, not the media, so the
            # keyboard is actionable. Also forwards thread_id and reply context.
            if overflow_text:
                try:
                    overflow_msg = await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=overflow_text,
                        parse_mode="HTML",
                        reply_to_message_id=reply_id,
                        reply_markup=reply_markup,
                        disable_notification=silent,
                        **thread_kwargs,
                    )
                    record_sent_message(chat_id, overflow_msg.message_id)
                except Exception as ov_err:
                    logger.warning("Failed to send overflow text after media: %s", ov_err)

            return str(msg.message_id)
        finally:
            if is_local_file and hasattr(media_source, "close"):
                media_source.close()

    async def send_media_group(
        self,
        target: str,
        media_urls: list[str],
        caption: str | None = None,
        reply_to: str | None = None,
        message_thread_id: int | None = None,
        silent: bool = False,
    ) -> list[str]:
        """Send an album (2–10 photos/videos/docs) as a single grouped Telegram message.

        Wraps telegram_media.send_media_group() which handles InputMedia construction,
        MIME detection, and the sendMediaGroup API call.
        Returns list of sent message IDs (empty list on failure).
        """
        if not self._app:
            raise RuntimeError("Telegram channel not started")

        from openclaw.channels.telegram.telegram_media import send_media_group as _send_mg

        chat_id = int(target) if str(target).lstrip("-").isdigit() else target
        result = await _send_mg(
            bot=self._app.bot,
            chat_id=chat_id,
            media_urls=media_urls,
            caption=caption,
            thread_id=message_thread_id,
        )
        msg_ids: list[str] = []
        if isinstance(result, dict):
            for mid in result.get("message_ids", []):
                msg_ids.append(str(mid))
                try:
                    record_sent_message(chat_id, int(mid))
                except Exception:
                    pass
        return msg_ids

    def set_command_executor(self, session_manager, agent_runtime) -> None:
        """Set up command executor with session manager and agent runtime"""
        self._command_executor = ChatCommandExecutor(session_manager, agent_runtime)

    async def _handle_telegram_media(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming media messages — mirrors TS bot-handlers.ts resolveMedia().

        Downloads the file from Telegram, encodes it as a base64 data URL, and
        passes it to the agent as a structured attachment (same shape as ChatAttachment
        in the TypeScript version) so the LLM can actually process the content.
        """
        if not update.message:
            return

        message = update.message

        # Deduplication — mirrors TS bot-updates.ts createTelegramUpdateDedupe
        dedup_key = message_key(message.chat_id, message.message_id)
        if self._dedupe.should_skip(dedup_key):
            logger.debug("Skipping duplicate media message %s", message.message_id)
            return

        # Media group handling (albums) - buffer multi-image messages
        media_group_id = getattr(message, "media_group_id", None)
        if media_group_id:
            await self._buffer_media_group(message, media_group_id, update, context)
            return

        chat = message.chat
        sender = message.from_user

        # Determine media type and collect file info
        media_type: str | None = None
        file_id: str | None = None
        file_name: str | None = None
        mime_type: str | None = None
        caption = message.caption or ""

        if message.photo:
            media_type = "photo"
            file_id = message.photo[-1].file_id
            file_name = f"photo_{message.message_id}.jpg"
            mime_type = "image/jpeg"
        elif message.video:
            media_type = "video"
            file_id = message.video.file_id
            file_name = message.video.file_name or f"video_{message.message_id}.mp4"
            mime_type = message.video.mime_type or "video/mp4"
        elif message.audio:
            media_type = "audio"
            file_id = message.audio.file_id
            file_name = message.audio.file_name or f"audio_{message.message_id}.mp3"
            mime_type = message.audio.mime_type or "audio/mpeg"
        elif message.voice:
            media_type = "voice"
            file_id = message.voice.file_id
            file_name = f"voice_{message.message_id}.ogg"
            mime_type = message.voice.mime_type or "audio/ogg"
        elif message.document:
            media_type = "document"
            file_id = message.document.file_id
            file_name = message.document.file_name or f"document_{message.message_id}"
            mime_type = message.document.mime_type or "application/octet-stream"
        elif getattr(message, "video_note", None):
            # video_note = circular video message (Telegram Video Messages feature)
            media_type = "video_note"
            file_id = message.video_note.file_id
            file_name = f"video_note_{message.message_id}.mp4"
            mime_type = "video/mp4"
        elif message.sticker:
            # Skip animated/video stickers; accept static WebP only
            if not (message.sticker.is_animated or message.sticker.is_video):
                media_type = "photo"
                file_id = message.sticker.file_id
                file_name = f"sticker_{message.message_id}.webp"
                mime_type = "image/webp"

                # Store sticker metadata for potential caching
                sticker_metadata = {
                    "file_id": message.sticker.file_id,
                    "file_unique_id": message.sticker.file_unique_id,
                    "emoji": message.sticker.emoji,
                    "set_name": message.sticker.set_name,
                }

        if not file_id:
            logger.warning("No file_id found for media message %s", message.message_id)
            return

        # Persist update offset
        if update.update_id and self._account_id:
            write_telegram_update_offset(self._account_id, update.update_id)

        try:
            # Download file from Telegram with retry — mirrors TS resolveTelegramFileWithRetry.
            # 3 attempts, 1-4s backoff; non-retryable for file-too-big (>20 MB).
            import asyncio as _asyncio
            import base64

            _GETFILE_MAX_ATTEMPTS = 3
            _GETFILE_RETRY_BASE = 1.0
            _GETFILE_RETRY_MAX = 4.0
            _GETFILE_TOO_BIG_HINT = "file is too big"   # Telegram API message

            file_bytes: bytearray | None = None
            _last_getfile_err: Exception | None = None
            for _attempt in range(_GETFILE_MAX_ATTEMPTS):
                try:
                    tg_file = await context.bot.get_file(file_id)
                    file_bytes = await tg_file.download_as_bytearray()
                    _last_getfile_err = None
                    break
                except Exception as _gfe:
                    _last_getfile_err = _gfe
                    _gfe_str = str(_gfe).lower()
                    if _GETFILE_TOO_BIG_HINT in _gfe_str or "file_too_large" in _gfe_str:
                        logger.warning(
                            "get_file: file too big for Telegram Bot API (%s) — not retrying",
                            file_name,
                        )
                        break  # non-retryable
                    if _attempt < _GETFILE_MAX_ATTEMPTS - 1:
                        _delay = min(_GETFILE_RETRY_BASE * (2 ** _attempt), _GETFILE_RETRY_MAX)
                        logger.warning(
                            "get_file attempt %d/%d failed (%s), retrying in %.1fs",
                            _attempt + 1, _GETFILE_MAX_ATTEMPTS, _gfe, _delay,
                        )
                        await _asyncio.sleep(_delay)

            if file_bytes is None:
                # All retries exhausted — deliver placeholder text so agent still gets the event
                logger.error(
                    "get_file failed after %d attempts for %s: %s — sending placeholder",
                    _GETFILE_MAX_ATTEMPTS, file_name, _last_getfile_err,
                )
                _placeholder_text = (
                    caption
                    if caption
                    else f"[User sent a {media_type}: {file_name} — file download failed: {_last_getfile_err}]"
                )
                _inbound_placeholder = InboundMessage(
                    channel_id=self.id,
                    message_id=str(message.message_id),
                    sender_id=str(sender.id) if sender else "unknown",
                    sender_name=(
                        " ".join(filter(None, [
                            getattr(sender, "first_name", ""),
                            getattr(sender, "last_name", ""),
                        ])).strip() or str(getattr(sender, "id", "unknown"))
                    ) if sender else "unknown",
                    chat_id=str(chat.id),
                    chat_type=(
                        "group" if chat.type in ("group", "supergroup") else
                        "channel" if chat.type == "channel" else "direct"
                    ),
                    text=_placeholder_text,
                    timestamp=message.date.isoformat() if message.date else None,
                )
                if self._message_handler:
                    await self._message_handler(_inbound_placeholder)
                return

            file_size = len(file_bytes)
            b64_content = base64.b64encode(bytes(file_bytes)).decode()

            # Determine attachment type bucket
            is_image = (mime_type or "").startswith("image/")
            is_audio = media_type in ("voice", "audio") or (mime_type or "").startswith("audio/")
            if is_image:
                attach_type = "image"
            elif is_audio:
                attach_type = media_type or "audio"  # "voice" or "audio"
            else:
                attach_type = "file"

            attachment: dict = {
                "type": attach_type,
                "mimeType": mime_type or "application/octet-stream",
                "content": b64_content,
                "filename": file_name,
                "size": file_size,
            }

            logger.info(
                "Received %s: %s (%d bytes) from user %s",
                media_type, file_name, file_size, sender.id,
            )

            # Cache sticker if this is a static sticker
            if message.sticker and "sticker_metadata" in locals():
                await self._cache_sticker_if_needed(
                    sticker_metadata=sticker_metadata,
                    file_bytes=bytes(file_bytes),
                    sender_username=sender.username,
                )

            # Fetch reply-target media — mirrors TS resolveReplyMedia()
            reply_attachments: list[dict] = []
            if message.reply_to_message:
                reply_attachments = await self._fetch_reply_target_media(
                    message.reply_to_message, context
                )

            # Determine chat type
            chat_type = "direct"
            if chat.type in ("group", "supergroup"):
                chat_type = "group"
            elif chat.type == "channel":
                chat_type = "channel"

            # Human-readable text description (fallback for models that don't handle attachments)
            text = caption if caption else f"[User sent a {media_type}: {file_name}]"

            inbound = InboundMessage(
                channel_id=self.id,
                message_id=str(message.message_id),
                sender_id=str(sender.id),
                sender_name=sender.full_name or sender.username or str(sender.id),
                chat_id=str(chat.id),
                chat_type=chat_type,
                text=text,
                timestamp=message.date.isoformat() if message.date else datetime.now(UTC).isoformat(),
                reply_to=str(message.reply_to_message.message_id) if message.reply_to_message else None,
                account_id=self._account_id or None,
                metadata={
                    "username": sender.username,
                    "chat_title": chat.title,
                    "chat_username": chat.username,
                    "media_type": media_type,
                    "file_id": file_id,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "caption": caption,
                },
                attachments=[attachment] + reply_attachments,
            )

            await self._handle_message(inbound)

        except Exception as e:
            logger.error("Error handling media message: %s", e, exc_info=True)
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=f"Sorry, I had trouble processing that {media_type}.",
                    reply_to_message_id=message.message_id,
                )
            except Exception:
                pass

    async def _handle_telegram_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming Telegram text message"""
        if not update.message or not update.message.text:
            return

        message = update.message

        # Reset the silence watchdog — channel is alive
        if self._heartbeat_monitor:
            self._heartbeat_monitor.reset()

        # Deduplication — mirrors TS bot-updates.ts
        dedup_key = message_key(message.chat_id, message.message_id)
        if self._dedupe.should_skip(dedup_key):
            logger.debug("Skipping duplicate text message %s", message.message_id)
            return

        # Text fragment handling - buffer long messages split by Telegram
        text_fragment_id = self._detect_text_fragment(message)
        if text_fragment_id:
            await self._buffer_text_fragment(message, text_fragment_id, update, context)
            return

        # Persist update offset
        if update.update_id and self._account_id:
            write_telegram_update_offset(self._account_id, update.update_id)

        chat = message.chat
        sender = message.from_user

        # Determine chat type first
        is_group = chat.type in ["group", "supergroup"]
        is_dm = not is_group

        # DM / group access control is handled inside _process_normal_text_message
        # Process as normal message
        await self._process_normal_text_message(message, update, context)

    async def _register_dynamic_command_handlers(self) -> None:
        """Register all native command handlers dynamically (mirrors TS bot-native-commands.ts:438-647)."""
        try:
            from openclaw.auto_reply.commands_registry_data import (
                list_native_command_specs_for_config,
            )
            from openclaw.auto_reply.skill_commands import list_skill_commands_for_agents
            from openclaw.channels.telegram.command_pipeline import handle_native_command
            from openclaw.channels.telegram.commands import (
                TELEGRAM_COMMAND_NAME_PATTERN,
                normalize_telegram_command_name,
            )

            # Get skill commands if enabled
            skill_commands = []
            try:
                skill_commands = list_skill_commands_for_agents(self._cfg)
                logger.info(f"Loaded {len(skill_commands)} skill commands for registration")
            except Exception as exc:
                logger.warning(f"Failed to load skill commands: {exc}")

            # Get all native commands from registry
            native_specs = list_native_command_specs_for_config(
                self._cfg,
                skill_commands,
                provider="telegram"
            )

            logger.info(f"Registering {len(native_specs)} native command handlers dynamically")

            # Register handler for each command
            registered_count = 0
            for spec in native_specs:
                name = spec.name or spec.native_name
                if not name:
                    continue

                normalized = normalize_telegram_command_name(name)
                if not TELEGRAM_COMMAND_NAME_PATTERN.match(normalized):
                    logger.warning(f"Skipping invalid command name: {normalized}")
                    continue

                # Create handler closure that captures the spec
                async def create_command_handler(command_spec):
                    async def command_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
                        await handle_native_command(
                            update=update,
                            context=ctx,
                            command_spec=command_spec,
                            bot=self._app.bot,
                            cfg=self._cfg,
                            account_id=self._account_id,
                            message_handler=self._message_handler,
                            channel_id=self.id,
                        )
                    return command_handler

                handler = await create_command_handler(spec)
                self._app.add_handler(CommandHandler(normalized, handler))
                registered_count += 1
                logger.debug(f"Registered command handler: /{normalized}")

            logger.info(f"Successfully registered {registered_count} command handlers")

        except Exception as exc:
            logger.error(f"Failed to register dynamic command handlers: {exc}")
            # Fallback to minimal hardcoded handlers
            logger.info("Falling back to hardcoded command handlers")
            self._app.add_handler(CommandHandler("start", self._handle_start_command))
            self._app.add_handler(CommandHandler("help", self._handle_help_command))
            self._app.add_handler(CommandHandler("model", self._handle_model_command))
            self._app.add_handler(CommandHandler("status", self._handle_status_command))

    async def _register_bot_commands(self):
        """Register bot commands with Telegram API using dynamic registration."""
        try:
            from openclaw.channels.telegram.command_handler import register_telegram_native_commands

            # Load the FULL openclaw config so skill commands (from agents.list)
            # and custom commands are included. self._config is only the telegram
            # channel sub-config (botToken, dmPolicy…), not the root config.
            try:
                from openclaw.config.loader import load_config
                full_cfg_obj = load_config()
                if full_cfg_obj and hasattr(full_cfg_obj, "model_dump"):
                    full_cfg: dict = full_cfg_obj.model_dump(by_alias=True, exclude_none=True)
                elif isinstance(full_cfg_obj, dict):
                    full_cfg = full_cfg_obj
                else:
                    full_cfg = self._config or {}
            except Exception:
                full_cfg = self._config or {}

            # Use dynamic registration from command registry
            await register_telegram_native_commands(
                bot=self._app.bot,
                cfg=full_cfg,
                account_id=self._account_id or "",
                native_enabled=True,
                native_skills_enabled=True,
            )
        except Exception as e:
            logger.error(f"Failed to register commands with Telegram API: {e}")

            # Fallback to minimal hardcoded commands
            try:
                from telegram import BotCommand

                minimal_commands = [
                    BotCommand("start", "Start using the bot"),
                    BotCommand("help", "View help information"),
                    BotCommand("status", "View status"),
                ]

                await self._app.bot.set_my_commands(minimal_commands)
                logger.info("Registered minimal fallback commands")
            except Exception as fallback_err:
                logger.error(f"Failed to register fallback commands: {fallback_err}")

    async def _setup_menu_button(self):
        """Setup bot menu button"""
        try:
            # Set menu button (shows in bottom left of chat)
            logger.info("Menu button setup completed")
        except Exception as e:
            logger.debug(f"Menu button setup failed: {e}")

    def _get_quick_reply_keyboard(self):
        """Get quick reply keyboard with common commands"""
        keyboard = [
            [KeyboardButton("💬 New Chat"), KeyboardButton("📊 Status")],
            [KeyboardButton("❓ Help"), KeyboardButton("🤖 Switch Model")],
        ]
        return ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True,
            one_time_keyboard=False
        )

    async def _handle_start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "👋 *Welcome to OpenClaw AI Assistant!*\n\n"
            "I am a powerful AI assistant that can help you:\n"
            "• 💬 Intelligent conversation\n"
            "• 📝 Process documents and files\n"
            "• 🔍 Search and query information\n"
            "• 🛠️ Execute various tasks\n\n"
            "Send any message to start a conversation, or use /help to see more commands."
        )

        # Send welcome message with quick reply keyboard
        await update.message.reply_text(
            welcome_message,
            parse_mode="Markdown",
            reply_markup=self._get_quick_reply_keyboard()
        )

    async def _handle_help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "📋 *Available Commands*\n\n"
            "/start - Show welcome message\n"
            "/help - Show this help information\n"
            "/new - Start new conversation (clear history)\n"
            "/status - View bot status\n"
            "/model - Switch AI model\n\n"
            "*💡 Tips*\n"
            "• Send messages directly to start conversation\n"
            "• Supports images, files, etc.\n"
            "• Multi-turn conversation supported\n\n"
            "_Need help? Visit documentation or contact support team._"
        )

        await update.message.reply_text(
            help_message,
            parse_mode="Markdown"
        )

    async def _handle_new_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /new command — aligned with TS bot-native-commands.ts.

        Resets the session and dispatches BARE_SESSION_RESET_PROMPT so the agent
        greets the user fresh in its configured persona (identical to typing /new
        as a plain text message through the gateway path).
        """
        await self._do_session_reset(update, reason="new")

    async def _handle_reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reset command — same behavior as /new."""
        await self._do_session_reset(update, reason="reset")

    async def _do_session_reset(self, update: Update, reason: str = "new") -> None:
        """Shared implementation for /new and /reset native commands.

        Mirrors the TS flow:
        1. sessions.reset  → new session ID, archive transcript, systemSent=False
        2. Send ✅ notice  → mirrors sendResetSessionNotice / buildResetSessionNoticeText
        3. Dispatch BARE_SESSION_RESET_PROMPT as synthetic InboundMessage so the agent
           runs a greeting turn (same as the handlers.py / channel_manager gateway path).
        """
        from openclaw.gateway.api.sessions_methods import SessionsResetMethod
        from openclaw.gateway.handlers import BARE_SESSION_RESET_PROMPT

        chat = update.effective_chat
        sender = update.effective_user
        if not chat or not sender:
            return

        chat_id = chat.id
        chat_type_raw = chat.type

        # Resolve canonical session key (mirrors _handle_reset_command logic)
        if chat_type_raw == "private":
            session_key = f"telegram:{self.id}:dm:main:{chat_id}"
            inbound_chat_type = "direct"
        else:
            session_key = f"telegram:{self.id}:group:{chat_id}"
            inbound_chat_type = "group"

        logger.info("[%s] %s command from %s, session_key=%s", self.id, reason, sender.id, session_key)

        try:
            reset_method = SessionsResetMethod()
            result = await reset_method.execute(
                connection=None,
                params={"key": session_key, "reason": reason, "archiveTranscript": True},
            )
        except Exception as exc:
            logger.error("[%s] Session reset failed: %s", self.id, exc)
            if update.message:
                await update.message.reply_text(
                    f"❌ Reset failed: {str(exc)[:120]}",
                    parse_mode=None,
                )
            return

        if not result.get("ok"):
            logger.warning("[%s] sessions.reset returned non-ok for %s", self.id, session_key)

        # Build ✅ notice — mirrors buildResetSessionNoticeText in get-reply-run.ts
        model_label = (self._config or {}).get("model", "unknown") if self._config else "unknown"
        notice_text = f"✅ New session started · model: {model_label}"
        if update.message:
            await update.message.reply_text(notice_text, parse_mode=None)

        # Dispatch BARE_SESSION_RESET_PROMPT as a synthetic message so the agent
        # reads workspace files and greets the user in its configured persona.
        now_iso = datetime.now(UTC).isoformat()
        inbound = InboundMessage(
            channel_id=self.id,
            message_id=f"reset_{reason}_{chat_id}_{int(datetime.now(UTC).timestamp() * 1000)}",
            sender_id=str(sender.id),
            sender_name=sender.full_name or sender.username or str(sender.id),
            chat_id=str(chat_id),
            chat_type=inbound_chat_type,
            text=BARE_SESSION_RESET_PROMPT,
            timestamp=now_iso,
            account_id=self._account_id or None,
            metadata={
                "username": sender.username,
                "chat_title": chat.title,
                "is_reset": True,
                "reset_reason": reason,
            },
        )
        await self._handle_message(inbound)

    async def _handle_status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        # Get current model from config
        current_model = self._config.get("model", "google/gemini-3-pro-preview") if self._config else "unknown"

        status_message = (
            "📊 *Bot Status*\n\n"
            f"🤖 Current Model: `{current_model}`\n"
            f"✅ Status: Running\n"
            f"💬 Session: Active\n"
            f"📡 Connection: Normal\n\n"
            "_System running normally_"
        )

        await update.message.reply_text(
            status_message,
            parse_mode="Markdown"
        )

    async def _handle_model_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /model command — paginated model picker."""
        keyboard, header = self._build_model_picker_keyboard(page=0)
        await update.message.reply_text(
            header,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries from inline keyboards.

        Mirrors TS bot-handlers.ts handleCallbackQuery():
        - Well-known data prefixes handled directly
        - Unknown callbacks passed as synthetic text messages to the agent
        """
        from telegram.error import BadRequest

        query = update.callback_query
        await query.answer()

        data = query.data or ""
        logger.info("Callback query: %s", data)

        if data == "new_confirm":
            try:
                await query.edit_message_text(
                    "✅ *New Conversation Started*\n\n"
                    "Conversation history cleared. Send a message to start a new conversation!",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise

        elif data == "new_cancel":
            try:
                await query.edit_message_text(
                    "❌ *Cancelled*\n\nContinuing current conversation.",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise

        elif data.startswith("model_select:"):
            # Paginated model selection — model_select:<model_id>
            model_id = data[len("model_select:"):]
            if self._config:
                self._config["model"] = model_id
            try:
                await query.edit_message_text(
                    f"✅ *Model Switched*\n\nNow using: `{model_id}`\n\n_New messages will use this model_",
                    parse_mode="Markdown"
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise

        elif data.startswith("model_page:"):
            # Pagination for model picker — model_page:<page_number>
            try:
                page = int(data[len("model_page:"):])
            except ValueError:
                page = 0
            keyboard, header = self._build_model_picker_keyboard(page=page)
            try:
                await query.edit_message_text(
                    header,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    raise

        elif data.startswith("model_"):
            # Legacy: model_<short_key>
            model_name = data[len("model_"):]
            model_map = {
                "gemini": ("google/gemini-3-pro-preview", "Gemini Pro"),
                "claude": ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet"),
                "gpt4": ("gpt-4", "GPT-4"),
                "gpt4turbo": ("gpt-4-turbo", "GPT-4 Turbo"),
            }
            if model_name in model_map:
                model_id, display_name = model_map[model_name]
                if self._config:
                    self._config["model"] = model_id
                try:
                    await query.edit_message_text(
                        f"✅ *Model Switched*\n\nNow using: {display_name}\nModel ID: `{model_id}`\n\n_New messages will use this model_",
                        parse_mode="Markdown",
                    )
                except BadRequest as e:
                    if "Message is not modified" not in str(e):
                        raise

        else:
            # Unknown callback_data — pass as synthetic text message to the agent.
            # Mirrors TS: any unhandled callbackData forwarded as user message.
            if query.message and query.from_user:
                chat = query.message.chat
                sender = query.from_user
                is_group = chat.type in ("group", "supergroup")
                chat_type = "group" if is_group else "direct"

                # Clear the inline keyboard on the original message so the user
                # cannot double-press while the agent is processing the action.
                # Mirrors TS bot-handlers.ts: answerCallbackQuery + edit markup.
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception as _markup_err:
                    # Non-fatal: message may have already been edited or deleted
                    logger.debug(
                        "Could not clear inline keyboard after callback: %s", _markup_err
                    )

                inbound = InboundMessage(
                    channel_id=self.id,
                    message_id=f"cbq_{query.id}",
                    sender_id=str(sender.id),
                    sender_name=sender.full_name or sender.username or str(sender.id),
                    chat_id=str(chat.id),
                    chat_type=chat_type,
                    text=data,
                    timestamp=datetime.now(UTC).isoformat(),
                    account_id=self._account_id or None,
                    metadata={
                        "username": sender.username,
                        "event_type": "callback_query",
                        "callback_query_id": query.id,
                        "inline_message_id": getattr(query, "inline_message_id", None),
                    },
                )
                await self._handle_message(inbound)

    def _build_model_picker_keyboard(self, page: int = 0) -> tuple[list, str]:
        """Build paginated model picker inline keyboard.

        Returns (keyboard_rows, header_text).
        Mirrors TS buildModelPickerKeyboard().
        """
        _PAGE_SIZE = 5

        available_models: list[tuple[str, str]] = []
        try:
            from openclaw.agents.registry import list_available_models  # type: ignore
            raw = list_available_models()
            available_models = [(m.get("id", ""), m.get("label", m.get("id", ""))) for m in raw if m.get("id")]
        except Exception:
            pass

        if not available_models:
            # Fallback to hardcoded list
            available_models = [
                ("google/gemini-3-pro-preview", "Gemini Pro"),
                ("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet"),
                ("gpt-4", "GPT-4"),
                ("gpt-4-turbo", "GPT-4 Turbo"),
                ("gpt-4o", "GPT-4o"),
            ]

        total = len(available_models)
        total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * _PAGE_SIZE
        page_models = available_models[start : start + _PAGE_SIZE]

        current_model = (self._config or {}).get("model", "")
        keyboard: list[list[InlineKeyboardButton]] = []
        for model_id, label in page_models:
            marker = "✅ " if model_id == current_model else ""
            keyboard.append([InlineKeyboardButton(f"{marker}{label}", callback_data=f"model_select:{model_id}")])

        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"model_page:{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"model_page:{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)

        header = f"🤖 *Select AI Model* (page {page + 1}/{total_pages})\n\nCurrent: `{current_model}`"
        return keyboard, header

    async def _check_sender_allowed(
        self,
        sender_id: str,
        username: str | None,
        dm_policy: str,
        allow_from_override: list | None = None,
    ) -> bool:
        """Check if sender is allowed based on dm_policy and allowFrom.

        Mirrors TS checkTelegramSenderAllowed():
        - strips telegram:/tg: prefixes via normalizer before comparing
        - merges config allowFrom with pairing-store approved senders

        Args:
            sender_id: Telegram user ID
            username: Telegram username (without @)
            dm_policy: DM policy (pairing, allowlist, open)
            allow_from_override: Optional list override (e.g. per-group allowFrom)

        Returns:
            True if sender is allowed
        """
        from .allow_from import (
            is_numeric_telegram_user_id,
            normalize_telegram_allow_from_entry,
        )

        if allow_from_override is not None:
            raw_list = allow_from_override
        else:
            # For open policy with wildcard, allow all
            if dm_policy == "open":
                allow_from = self._config.get("allowFrom") or self._config.get("allow_from") or []
                normalized = [normalize_telegram_allow_from_entry(e) for e in allow_from]
                if "*" in normalized:
                    return True

            # Get allowFrom from config
            allow_from_config = self._config.get("allowFrom") or self._config.get("allow_from") or []

            # Get allowFrom from pairing store
            try:
                from ...pairing.pairing_store import read_channel_allow_from_store
                allow_from_store = read_channel_allow_from_store("telegram", self._account_id)
            except Exception as e:
                logger.warning("Failed to read pairing store: %s", e)
                allow_from_store = []

            raw_list = allow_from_config + allow_from_store

        # Normalize all entries (strips telegram:/tg: prefixes)
        normalized = [normalize_telegram_allow_from_entry(e) for e in raw_list]

        # Check wildcard
        if "*" in normalized:
            return True

        # Check if empty and not in pairing mode
        if not normalized and dm_policy == "allowlist":
            return False

        # Check sender ID match (numeric IDs match directly)
        if sender_id in normalized:
            return True

        # Check username match (case-insensitive, with/without @ prefix)
        if username:
            username_lower = username.lower()
            for entry in normalized:
                entry_lower = entry.lower()
                if entry_lower == username_lower or entry_lower == f"@{username_lower}":
                    return True

        return False

    def _resolve_group_config(self, chat_id: str) -> dict:
        """Return per-group config override block (may be empty dict).

        Mirrors TS resolveGroupConfig(): looks up config.groups[chatId].
        """
        if not self._config:
            return {}
        groups_config = self._config.get("groups") or {}
        return groups_config.get(str(chat_id)) or groups_config.get(chat_id) or {}

    def _message_mentions_bot(self, message: Any) -> bool:
        """Return True if the message @-mentions the bot.

        Mirrors TS requiresMentionCheck().
        """
        if not self._app:
            return False
        try:
            bot_username = self._app.bot.username or ""
        except Exception:
            bot_username = ""

        text = message.text or getattr(message, "caption", None) or ""
        if bot_username and f"@{bot_username}" in text:
            return True

        entities = list(getattr(message, "entities", None) or []) + list(
            getattr(message, "caption_entities", None) or []
        )
        for entity in entities:
            if entity.type == "mention":
                mention = text[entity.offset : entity.offset + entity.length]
                if mention.lstrip("@").lower() == bot_username.lower():
                    return True
            elif entity.type == "text_mention":
                user = getattr(entity, "user", None)
                if user:
                    try:
                        if user.id == self._app.bot.id:
                            return True
                    except Exception:
                        pass
        return False

    async def _check_group_message_allowed(
        self, message: Any, chat: Any, sender: Any
    ) -> bool:
        """Apply group-level access-control logic.

        Mirrors TS groupPolicy / requireMention / groupAllowFrom gating.
        Returns True when the message should be forwarded to the agent.
        """
        if not self._config:
            return True

        chat_id = str(chat.id)
        group_cfg = self._resolve_group_config(chat_id)

        # Resolve effective policy (per-group override → account default → "allowlist")
        group_policy = (
            group_cfg.get("groupPolicy")
            or self._config.get("groupPolicy")
            or "allowlist"
        )

        if group_policy == "disabled":
            logger.debug("Group %s blocked by groupPolicy=disabled", chat_id)
            return False

        if group_policy == "allowlist":
            # Chat itself must be in groupAllowFrom
            group_allow_from_raw = self._config.get("groupAllowFrom") or []
            from .allow_from import normalize_telegram_allow_from_entry
            group_allow_from = [normalize_telegram_allow_from_entry(e) for e in group_allow_from_raw]
            if "*" not in group_allow_from and chat_id not in group_allow_from:
                logger.debug("Group %s not in groupAllowFrom allowlist", chat_id)
                return False

        # Per-group sender allowFrom (overrides account-level allowFrom for groups)
        group_sender_allow_from = group_cfg.get("allowFrom")
        if group_sender_allow_from is not None:
            sender_ok = await self._check_sender_allowed(
                sender_id=str(sender.id),
                username=getattr(sender, "username", None),
                dm_policy="allowlist",
                allow_from_override=group_sender_allow_from,
            )
            if not sender_ok:
                logger.debug(
                    "Group %s: sender %s blocked by group allowFrom", chat_id, sender.id
                )
                return False

        # requireMention (per-group overrides account level)
        require_mention = group_cfg.get(
            "requireMention",
            self._config.get("requireMention", False),
        )
        if require_mention:
            if not self._message_mentions_bot(message):
                logger.debug(
                    "Group %s: ignored — bot not mentioned (requireMention=true)", chat_id
                )
                return False

        return True

    async def _send_ack_reaction(self, chat_id: int | str, message_id: int, emoji: str = "👀") -> None:
        """Send an ack reaction to indicate the bot is processing.

        Mirrors TS sendAckReaction().
        """
        if not self._app:
            return
        try:
            from telegram import ReactionTypeEmoji
            await self._app.bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception as exc:
            logger.debug("Failed to send ack reaction: %s", exc)

    async def _handle_pairing_request(
        self,
        sender: Any,
        chat: Any,
        context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle pairing request for unauthorized DM.
        
        Args:
            sender: Telegram User object
            chat: Telegram Chat object
            context: Telegram context
        """
        try:
            from ...pairing.messages import format_pairing_request_message
            from ...pairing.pairing_store import upsert_channel_pairing_request

            # Create or update pairing request (with account_id for multi-account support)
            result = upsert_channel_pairing_request(
                channel="telegram",
                sender_id=str(sender.id),
                account_id=self._account_id,
                meta={
                    "username": sender.username or "",
                    "first_name": sender.first_name or "",
                    "last_name": sender.last_name or "",
                    "full_name": sender.full_name or "",
                }
            )

            pairing_code = result["code"]
            is_new_request = result["created"]

            # Only send message for new requests
            if is_new_request:
                logger.info(f"Created pairing request for telegram:{sender.id}, code={pairing_code}")

                # Format pairing message
                message_text = format_pairing_request_message(
                    code=pairing_code,
                    channel="telegram",
                    id_label=f"Telegram ID ({sender.id})"
                )

                # Add user info
                user_info = "\n📱 **Your Info**\n"
                user_info += f"- Telegram ID: `{sender.id}`\n"
                if sender.username:
                    user_info += f"- Username: @{sender.username}\n"
                user_info += f"- Name: {sender.full_name}\n"

                message_text = message_text.replace(
                    "This code expires in 1 hour.",
                    user_info + "\nThis code expires in 1 hour."
                )

                # Send to user
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=message_text,
                    parse_mode="Markdown"
                )
            else:
                logger.debug(f"Pairing request already exists for telegram:{sender.id}")

        except Exception as e:
            logger.error(f"Failed to handle pairing request: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat.id,
                text="⚠️ Access not configured. Please contact the bot owner.",
            )

    async def _cache_sticker_if_needed(
        self,
        sticker_metadata: dict[str, Any],
        file_bytes: bytes,
        sender_username: str | None,
    ) -> None:
        """
        Cache a sticker with vision-based description.
        
        Args:
            sticker_metadata: Sticker metadata from Telegram
            file_bytes: Downloaded sticker file bytes
            sender_username: Username of sender (for receivedFrom)
        """
        file_unique_id = sticker_metadata.get("file_unique_id")
        if not file_unique_id:
            return

        # Check if already cached
        existing = get_cached_sticker(file_unique_id)
        if existing:
            logger.debug("Sticker %s already cached", file_unique_id)
            return

        try:
            import tempfile

            # Save to temp file for vision analysis
            with tempfile.NamedTemporaryFile(suffix=".webp", delete=False) as tmp:
                tmp.write(file_bytes)
                temp_path = tmp.name

            # Describe sticker using vision API
            description = await describe_sticker_image(
                image_path=temp_path,
                config=self._config,
                agent_id=None,
            )

            # Clean up temp file
            import os
            try:
                os.unlink(temp_path)
            except Exception:
                pass

            if not description:
                description = "Sticker"

            # Cache the sticker
            cached = CachedSticker(
                file_id=sticker_metadata.get("file_id", ""),
                file_unique_id=file_unique_id,
                emoji=sticker_metadata.get("emoji"),
                set_name=sticker_metadata.get("set_name"),
                description=description,
                cached_at=datetime.now(UTC).isoformat(),
                received_from=sender_username,
            )

            cache_sticker(cached)
            logger.info("Cached sticker %s: %s", file_unique_id, description)

        except Exception as exc:
            logger.warning("Failed to cache sticker: %s", exc)

    async def _handle_reaction_update(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming message reaction updates"""
        if not update.message_reaction:
            return

        reaction = update.message_reaction
        chat = reaction.chat
        message_id = reaction.message_id
        user = reaction.user

        # Resolve reaction notification mode (default: "own")
        reaction_mode = (
            self._config.get("reactionNotifications")
            or self._config.get("reaction_notifications")
            or "own"
        )

        if reaction_mode == "off":
            return

        if user and user.is_bot:
            return

        # Filter based on mode
        if reaction_mode == "own" and not was_sent_by_bot(chat.id, message_id):
            return

        # Detect added reactions (compare old vs new)
        old_emojis = set()
        if reaction.old_reaction:
            for r in reaction.old_reaction:
                if hasattr(r, "type") and r.type == "emoji" and hasattr(r, "emoji"):
                    old_emojis.add(r.emoji)

        added_reactions = []
        if reaction.new_reaction:
            for r in reaction.new_reaction:
                if hasattr(r, "type") and r.type == "emoji" and hasattr(r, "emoji"):
                    if r.emoji not in old_emojis:
                        added_reactions.append(r.emoji)

        if not added_reactions:
            return

        # Build sender label
        sender_label = "unknown"
        if user:
            name_parts = [user.first_name or "", user.last_name or ""]
            sender_name = " ".join(p for p in name_parts if p).strip()
            sender_username = f"@{user.username}" if user.username else None

            if sender_name and sender_username:
                sender_label = f"{sender_name} ({sender_username})"
            elif sender_name:
                sender_label = sender_name
            elif sender_username:
                sender_label = sender_username
            elif user.id:
                sender_label = f"id:{user.id}"

        # Determine session routing
        is_group = chat.type in ["group", "supergroup"]
        is_forum = getattr(chat, "is_forum", False)

        # Build session key for reaction (chat-level, no thread ID available)
        if is_group:
            # For groups, route to chat-level session
            session_key = f"agent:main:telegram:group:{chat.id}"
        else:
            # For DMs
            session_key = f"agent:main:telegram:{chat.id}"

        # Enqueue system event for each added reaction
        for emoji in added_reactions:
            text = f"Telegram reaction added: {emoji} by {sender_label} on msg {message_id}"

            # Create system event
            try:
                if self._message_handler:
                    inbound = InboundMessage(
                        channel_id=self.id,
                        message_id=str(message_id),
                        sender_id=str(user.id) if user else "unknown",
                        sender_name=sender_label,
                        chat_id=str(chat.id),
                        chat_type="group" if is_group else "direct",
                        text=text,
                        timestamp=datetime.now(UTC).isoformat(),
                        account_id=self._account_id or None,
                        metadata={
                            "event_type": "reaction",
                            "emoji": emoji,
                            "username": user.username if user else None,
                            "chat_title": chat.title if hasattr(chat, "title") else None,
                            "session_key": session_key,
                        },
                    )

                    await self._message_handler(inbound)
                    logger.debug("Reaction event enqueued: %s", text)

            except Exception as exc:
                logger.error("Failed to handle reaction event: %s", exc, exc_info=True)

    async def _fetch_reply_target_media(
        self,
        reply_msg: Any,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> list[dict]:
        """Download media from a replied-to message and return as attachments.

        Mirrors TS resolveReplyMedia() in bot-handlers.ts.
        Returns a list (possibly empty) of attachment dicts.
        """
        import base64

        def _make_attachment(file_bytes: bytes, mime_type: str, file_name: str) -> dict:
            b64 = base64.b64encode(file_bytes).decode()
            is_image = mime_type.startswith("image/")
            attach_type = "image" if is_image else "file"
            return {
                "type": attach_type,
                "mimeType": mime_type,
                "content": b64,
                "filename": file_name,
                "size": len(file_bytes),
                "isReplyMedia": True,
            }

        try:
            file_id: str | None = None
            mime_type = "application/octet-stream"
            file_name = "reply_media"

            if reply_msg.photo:
                file_id = reply_msg.photo[-1].file_id
                mime_type = "image/jpeg"
                file_name = f"reply_photo_{reply_msg.message_id}.jpg"
            elif reply_msg.video:
                file_id = reply_msg.video.file_id
                mime_type = reply_msg.video.mime_type or "video/mp4"
                file_name = reply_msg.video.file_name or f"reply_video_{reply_msg.message_id}.mp4"
            elif reply_msg.audio:
                file_id = reply_msg.audio.file_id
                mime_type = reply_msg.audio.mime_type or "audio/mpeg"
                file_name = reply_msg.audio.file_name or f"reply_audio_{reply_msg.message_id}.mp3"
            elif reply_msg.voice:
                file_id = reply_msg.voice.file_id
                mime_type = reply_msg.voice.mime_type or "audio/ogg"
                file_name = f"reply_voice_{reply_msg.message_id}.ogg"
            elif reply_msg.document:
                file_id = reply_msg.document.file_id
                mime_type = reply_msg.document.mime_type or "application/octet-stream"
                file_name = reply_msg.document.file_name or f"reply_doc_{reply_msg.message_id}"

            if not file_id:
                return []

            tg_file = await context.bot.get_file(file_id)
            data = await tg_file.download_as_bytearray()
            return [_make_attachment(bytes(data), mime_type, file_name)]
        except Exception as exc:
            logger.debug("Failed to fetch reply-target media: %s", exc)
            return []

    async def _buffer_media_group(
        self,
        message: Any,
        media_group_id: str,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Buffer messages from a media group (album).
        
        Combines multiple media messages with same media_group_id into a single
        InboundMessage with multiple attachments.
        
        Args:
            message: Telegram Message object
            media_group_id: Media group ID
            update: Telegram Update object
            context: Telegram context
        """
        MEDIA_GROUP_TIMEOUT_MS = 500

        # Get or create buffer entry
        if media_group_id in self._media_group_buffer:
            entry = self._media_group_buffer[media_group_id]

            # Cancel existing timer
            if "timer" in entry:
                entry["timer"].cancel()

            # Add message to buffer
            entry["messages"].append({
                "message": message,
                "update": update,
                "context": context,
            })
        else:
            entry = {
                "messages": [{
                    "message": message,
                    "update": update,
                    "context": context,
                }],
            }
            self._media_group_buffer[media_group_id] = entry

        # Schedule flush
        async def flush_group():
            await asyncio.sleep(MEDIA_GROUP_TIMEOUT_MS / 1000)
            if media_group_id in self._media_group_buffer:
                buffered = self._media_group_buffer.pop(media_group_id)
                await self._process_media_group(buffered)

        entry["timer"] = asyncio.create_task(flush_group())

    async def _process_media_group(self, entry: dict) -> None:
        """
        Process a buffered media group.
        
        Downloads all media, combines captions, and sends as single InboundMessage
        with multiple attachments.
        
        Args:
            entry: Media group buffer entry
        """
        try:
            messages = entry["messages"]
            if not messages:
                return

            # Sort by message_id
            messages.sort(key=lambda m: m["message"].message_id)

            # Find message with caption (prefer first one with caption)
            caption_msg = next(
                (m for m in messages if m["message"].caption or m["message"].text),
                messages[0]
            )

            primary_message = caption_msg["message"]
            primary_context = caption_msg["context"]

            chat = primary_message.chat
            sender = primary_message.from_user

            # Download all media
            attachments = []
            for msg_entry in messages:
                msg = msg_entry["message"]
                ctx = msg_entry["context"]

                # Determine media type and file info
                file_id = None
                file_name = None
                mime_type = None

                if msg.photo:
                    file_id = msg.photo[-1].file_id
                    file_name = f"photo_{msg.message_id}.jpg"
                    mime_type = "image/jpeg"
                elif msg.video:
                    file_id = msg.video.file_id
                    file_name = msg.video.file_name or f"video_{msg.message_id}.mp4"
                    mime_type = msg.video.mime_type or "video/mp4"
                elif msg.document:
                    file_id = msg.document.file_id
                    file_name = msg.document.file_name or f"document_{msg.message_id}"
                    mime_type = msg.document.mime_type or "application/octet-stream"

                if not file_id:
                    continue

                try:
                    # Download file
                    tg_file = await ctx.bot.get_file(file_id)
                    file_bytes = await tg_file.download_as_bytearray()
                    file_size = len(file_bytes)

                    import base64
                    b64_content = base64.b64encode(bytes(file_bytes)).decode()

                    # Determine attachment type
                    is_image = (mime_type or "").startswith("image/")
                    attach_type = "image" if is_image else "file"

                    attachment = {
                        "type": attach_type,
                        "mimeType": mime_type or "application/octet-stream",
                        "content": b64_content,
                        "filename": file_name,
                        "size": file_size,
                    }

                    attachments.append(attachment)

                except Exception as exc:
                    logger.warning("Failed to download media from group: %s", exc)

            if not attachments:
                logger.warning("No attachments in media group")
                return

            # Combine captions from all messages
            captions = [
                m["message"].caption or m["message"].text
                for m in messages
                if m["message"].caption or m["message"].text
            ]
            combined_caption = "\n".join(c for c in captions if c)

            # Determine chat type
            chat_type = "direct"
            if chat.type in ("group", "supergroup"):
                chat_type = "group"
            elif chat.type == "channel":
                chat_type = "channel"

            # Human-readable text description
            text = combined_caption if combined_caption else f"[User sent {len(attachments)} media items]"

            # Build InboundMessage
            inbound = InboundMessage(
                channel_id=self.id,
                message_id=str(primary_message.message_id),
                sender_id=str(sender.id),
                sender_name=sender.full_name or sender.username or str(sender.id),
                chat_id=str(chat.id),
                chat_type=chat_type,
                text=text,
                timestamp=primary_message.date.isoformat() if primary_message.date else datetime.now(UTC).isoformat(),
                reply_to=str(primary_message.reply_to_message.message_id) if primary_message.reply_to_message else None,
                account_id=self._account_id or None,
                metadata={
                    "username": sender.username,
                    "chat_title": chat.title,
                    "chat_username": chat.username,
                    "media_type": "album",
                    "caption": combined_caption,
                    "media_count": len(attachments),
                },
                attachments=attachments,
            )

            await self._handle_message(inbound)

        except Exception as exc:
            logger.error("Error processing media group: %s", exc, exc_info=True)

    def _detect_text_fragment(self, message: Any) -> str | None:
        """
        Detect if message is part of a split text fragment.
        
        Telegram splits messages >4096 chars. We detect fragments by checking:
        - Message length is near 4096 char limit
        - Sender/chat match previous fragment
        - Time delta is < 2s from last fragment
        
        Returns:
            Fragment ID (sender_chat composite) or None
        """
        TEXT_FRAGMENT_MIN_LENGTH = 3900

        if not message.text:
            return None

        # Only buffer messages close to the limit
        if len(message.text) < TEXT_FRAGMENT_MIN_LENGTH:
            return None

        # Generate fragment key (sender + chat)
        sender_id = message.from_user.id if message.from_user else None
        if not sender_id:
            return None

        return f"{message.chat_id}:{sender_id}"

    async def _buffer_text_fragment(
        self,
        message: Any,
        fragment_id: str,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Buffer text fragments from split messages.
        
        Combines consecutive messages from same sender that are split by Telegram.
        
        Args:
            message: Telegram Message object
            fragment_id: Fragment buffer key
            update: Telegram Update object
            context: Telegram context
        """
        TEXT_FRAGMENT_TIMEOUT_MS = 2000

        # Get or create buffer entry
        if fragment_id in self._text_fragment_buffer:
            entry = self._text_fragment_buffer[fragment_id]

            # Cancel existing timer
            if "timer" in entry:
                entry["timer"].cancel()

            # Add message to buffer
            entry["messages"].append({
                "message": message,
                "update": update,
                "context": context,
            })
        else:
            entry = {
                "messages": [{
                    "message": message,
                    "update": update,
                    "context": context,
                }],
            }
            self._text_fragment_buffer[fragment_id] = entry

        # Schedule flush
        async def flush_fragments():
            await asyncio.sleep(TEXT_FRAGMENT_TIMEOUT_MS / 1000)
            if fragment_id in self._text_fragment_buffer:
                buffered = self._text_fragment_buffer.pop(fragment_id)
                await self._process_text_fragments(buffered)

        entry["timer"] = asyncio.create_task(flush_fragments())

    async def _process_text_fragments(self, entry: dict) -> None:
        """
        Process buffered text fragments.
        
        Combines consecutive split messages into a single InboundMessage.
        
        Args:
            entry: Text fragment buffer entry
        """
        try:
            messages = entry["messages"]
            if not messages:
                return

            # Sort by message_id
            messages.sort(key=lambda m: m["message"].message_id)

            # Combine all text
            combined_text = "".join(m["message"].text or "" for m in messages)

            # Use first message as primary
            primary_message = messages[0]["message"]
            primary_update = messages[0]["update"]
            primary_context = messages[0]["context"]

            # Create new Update with combined text for normal processing
            # We'll modify the message text temporarily
            original_text = primary_message.text
            primary_message.text = combined_text

            try:
                # Now process as normal text message (skip fragment detection)
                await self._process_normal_text_message(
                    primary_message,
                    primary_update,
                    primary_context,
                )
            finally:
                primary_message.text = original_text

        except Exception as exc:
            logger.error("Error processing text fragments: %s", exc, exc_info=True)

    async def _process_normal_text_message(
        self,
        message: Any,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Process a normal text message (non-fragment).
        
        This is the core text message handling logic extracted from _handle_telegram_message.
        """
        # Persist update offset
        if update.update_id and self._account_id:
            write_telegram_update_offset(self._account_id, update.update_id)

        chat = message.chat
        sender = message.from_user

        # Determine chat type first
        is_group = chat.type in ["group", "supergroup"]
        is_dm = not is_group

        # DM Access Control - Check dm_policy for direct messages
        if is_dm and self._config:
            dm_policy = self._config.get("dmPolicy") or self._config.get("dm_policy") or "pairing"

            # Handle disabled DM
            if dm_policy == "disabled":
                logger.info(f"DM from {sender.id} blocked by dm_policy=disabled")
                return

            # Handle pairing and allowlist modes
            if dm_policy in ["pairing", "allowlist"]:
                # Check if sender is allowed
                is_allowed = await self._check_sender_allowed(
                    sender_id=str(sender.id),
                    username=sender.username,
                    dm_policy=dm_policy
                )

                if not is_allowed:
                    # For pairing mode, create pairing request
                    if dm_policy == "pairing":
                        await self._handle_pairing_request(sender, chat, context)
                    else:
                        # For allowlist mode, just ignore
                        logger.info(f"DM from {sender.id} blocked by dm_policy={dm_policy}")
                    return

        # Group Access Control — mirrors TS groupPolicy gating
        if is_group and self._config:
            if not await self._check_group_message_allowed(message, chat, sender):
                return

        # Ack reaction — send 👀 while processing (mirrors TS ackReaction/ackReactionScope)
        if self._cfg:
            from openclaw.agents.identity import resolve_ack_reaction
            from openclaw.channels.ack_reactions import should_ack_reaction
            
            # Resolve agent_id - use "main" as default (most Telegram messages route to main agent)
            agent_id = "main"
            
            # Resolve ack reaction emoji using cascading config
            # Priority: account → channel → messages → identity → default "👀"
            ack_emoji = resolve_ack_reaction(
                self._cfg,
                agent_id,
                {"channel": "telegram", "accountId": self._account_id}
            )
            
            if ack_emoji:
                # Resolve ack reaction scope
                # Priority: account → channel → messages → default "group-mentions"
                ack_scope = "group-mentions"  # default
                
                # Check config layers
                try:
                    # Global messages config
                    if hasattr(self._cfg, "messages") and self._cfg.messages:
                        if hasattr(self._cfg.messages, "ack_reaction_scope"):
                            scope_val = self._cfg.messages.ack_reaction_scope
                            if scope_val:
                                ack_scope = scope_val
                    
                    # Channel-level config (if available)
                    if self._config and "ackReactionScope" in self._config:
                        ack_scope = self._config["ackReactionScope"]
                except Exception:
                    pass
                
                # Check if message has mentions (for group-mentions scope)
                was_mentioned = False
                if message.entities:
                    # Check if bot was mentioned
                    for entity in message.entities:
                        if entity.type == "mention":
                            # Extract mention text
                            mention_text = message.text[entity.offset:entity.offset + entity.length]
                            # Check if it's our bot (would need bot username to verify fully)
                            was_mentioned = True
                            break
                
                # Determine if we should send ack reaction
                should_ack = should_ack_reaction(
                    scope=ack_scope,
                    is_direct=is_dm,
                    is_group=is_group,
                    is_mentionable_group=is_group,  # Telegram groups support mentions
                    require_mention=True,  # Telegram requires explicit mentions in groups
                    can_detect_mention=True,  # Telegram supports mention detection
                    effective_was_mentioned=was_mentioned,
                    should_bypass_mention=False,  # No bypass for Telegram
                )
                
                if should_ack:
                    asyncio.create_task(self._send_ack_reaction(chat.id, message.message_id, ack_emoji))

        # Check for chat commands
        if self._command_parser:
            command = self._command_parser.parse(message.text)
            if command and self._command_executor:
                session_id = f"telegram:{chat.id}"
                user_id = str(sender.id)
                is_owner = self._owner_id and user_id == self._owner_id
                try:
                    await self._command_executor.execute(
                        command=command,
                        session_id=session_id,
                        is_owner=is_owner,
                        channel=self,
                        context={"chat_id": chat.id}
                    )
                except Exception as cmd_exc:
                    logger.error(
                        "Failed to execute command %s: %s",
                        command.command,
                        cmd_exc,
                        exc_info=True
                    )
                return

        # Normal message processing
        chat_type = "direct"
        if chat.type in ("group", "supergroup"):
            chat_type = "group"
        elif chat.type == "channel":
            chat_type = "channel"

        # ✅ Resolve stream mode configuration for draft vs block decision
        # Mirrors TS bot-message-dispatch.ts L171-313
        from openclaw.channels.telegram.stream_mode import resolve_stream_mode_config
        from openclaw.channels.telegram.reasoning_coordinator import ReasoningCoordinator
        
        # Resolve session key for config lookup
        session_key = f"telegram:{chat.id}"
        
        # Resolve stream mode configuration
        stream_mode_cfg = resolve_stream_mode_config(
            cfg=self._cfg,
            telegram_cfg=self._config or {},
            session_key=session_key,
            agent_id="main",  # Telegram typically routes to main agent
            is_dm=is_dm,
        )
        
        # Log stream mode decision for testing/debugging
        logger.info(
            f"[TEST] Stream mode resolved: reasoning={stream_mode_cfg['reasoning_level']}, "
            f"can_draft_answer={stream_mode_cfg['can_stream_answer_draft']}, "
            f"disable_block={stream_mode_cfg['disable_block_streaming']}"
        )
        
        # Create archived preview collectors
        archived_answer_previews: list[dict] = []
        archived_reasoning_preview_ids: list[int] = []
        
        # Create reasoning coordinator
        reasoning_coordinator = ReasoningCoordinator()
        
        # Define superseded preview callbacks
        def on_superseded_answer_preview(preview: dict) -> None:
            archived_answer_previews.append({
                "message_id": preview["message_id"],
                "text_snapshot": preview["text_snapshot"],
            })
            logger.debug(f"Archived answer preview: msg_id={preview['message_id']}")
        
        def on_superseded_reasoning_preview(preview: dict) -> None:
            msg_id = preview["message_id"]
            if msg_id not in archived_reasoning_preview_ids:
                archived_reasoning_preview_ids.append(msg_id)
                logger.debug(f"Archived reasoning preview: msg_id={msg_id}")

        inbound = InboundMessage(
            channel_id=self.id,
            message_id=str(message.message_id),
            sender_id=str(sender.id),
            sender_name=sender.full_name or sender.username or str(sender.id),
            chat_id=str(chat.id),
            chat_type=chat_type,
            text=message.text,
            timestamp=message.date.isoformat() if message.date else datetime.now(UTC).isoformat(),
            reply_to=str(message.reply_to_message.message_id) if message.reply_to_message else None,
            account_id=self._account_id or None,
            metadata={
                "username": sender.username,
                "chat_title": chat.title,
                "chat_username": chat.username,
                **({"message_thread_id": message.message_thread_id}
                   if getattr(message, "message_thread_id", None) else {}),
                
                # ✅ NEW: Stream mode configuration (draft vs block decision)
                # Passed to agent_runner_execution for block streaming control
                "_stream_mode_config": stream_mode_cfg,
                "_disable_block_streaming": stream_mode_cfg["disable_block_streaming"],
                "_reasoning_level": stream_mode_cfg["reasoning_level"],
                "_can_stream_answer_draft": stream_mode_cfg["can_stream_answer_draft"],
                "_can_stream_reasoning_draft": stream_mode_cfg["can_stream_reasoning_draft"],
                "_is_dm": is_dm,
                
                # ✅ NEW: Draft stream callbacks for superseded preview handling
                "_on_superseded_answer_preview": on_superseded_answer_preview,
                "_on_superseded_reasoning_preview": on_superseded_reasoning_preview,
                "_archived_answer_previews": archived_answer_previews,
                "_archived_reasoning_preview_ids": archived_reasoning_preview_ids,
                
                # ✅ NEW: Reasoning coordinator for block separation
                "_reasoning_coordinator": reasoning_coordinator,
            },
        )

        await self._handle_message(inbound)
