"""Base channel plugin interface"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from .connection import (
    ConnectionManager,
    ConnectionMetrics,
    ConnectionState,
    HealthChecker,
    ReconnectConfig,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Channel Meta (mirrors TS ChannelMeta)
# =============================================================================

class ChannelMeta(BaseModel):
    """Channel metadata — mirrors TS ChannelMeta."""
    id: str
    label: str
    description: str | None = None
    docs_path: str | None = None
    icon_url: str | None = None


# =============================================================================
# Channel Account Snapshot (mirrors TS ChannelAccountSnapshot)
# =============================================================================

class ChannelAccountSnapshot(BaseModel):
    """Runtime status for a channel account. Mirrors TS ChannelAccountSnapshot."""
    account_id: str
    enabled: bool = True
    configured: bool = True
    running: bool = False
    last_error: str | None = None
    last_start_at: int | None = None
    last_stop_at: int | None = None
    reconnect_attempts: int = 0


# =============================================================================
# Channel Adapter Protocols (mirrors TS types.adapters.ts)
# =============================================================================

@runtime_checkable
class ChannelConfigAdapter(Protocol):
    """Adapter for channel account configuration. Mirrors TS ChannelConfigAdapter."""

    def list_account_ids(self, cfg: dict[str, Any]) -> list[str]:
        """Return list of configured account IDs for this channel."""
        ...

    def resolve_account(self, cfg: dict[str, Any], account_id: str) -> dict[str, Any] | None:
        """Resolve full account config for a given account ID."""
        ...

    def is_enabled(self, account: dict[str, Any] | None, cfg: dict[str, Any]) -> bool:
        """Return True if account is enabled."""
        ...

    def is_configured(self, account: dict[str, Any] | None, cfg: dict[str, Any]) -> bool:
        """Return True if account is fully configured (has required fields)."""
        ...

    def disabled_reason(self, account: dict[str, Any] | None, cfg: dict[str, Any]) -> str | None:
        """Return human-readable reason why account is disabled, or None."""
        ...

    def unconfigured_reason(self, account: dict[str, Any] | None, cfg: dict[str, Any]) -> str | None:
        """Return human-readable reason why account is not configured, or None."""
        ...


@runtime_checkable
class ChannelGatewayAdapter(Protocol):
    """Per-account gateway lifecycle adapter. Mirrors TS ChannelGatewayAdapter."""

    async def start_account(
        self,
        *,
        cfg: dict[str, Any],
        account_id: str,
        account: dict[str, Any] | None,
        runtime: Any,  # RuntimeEnv
        abort_signal: Any,
        get_status: Callable[[], ChannelAccountSnapshot],
        set_status: Callable[[ChannelAccountSnapshot], None],
        log: Any,
        channel_runtime: Any | None = None,  # PluginRuntime["channel"]
    ) -> None:
        """
        Start a single account. Runs until stopped or error. Mirrors TS startAccount().
        
        Args:
            cfg: Full OpenClaw config
            account_id: Account ID  
            account: Resolved account config
            runtime: Runtime environment
            abort_signal: AbortSignal for cancellation
            get_status: Function to get current status snapshot
            set_status: Function to update status snapshot
            log: Channel log sink
            channel_runtime: Optional PluginRuntime["channel"] for external plugins
        """
        ...

    async def stop_account(
        self,
        *,
        account_id: str,
        account: dict[str, Any] | None,
    ) -> None:
        """Stop a specific account gracefully. Mirrors TS stopAccount()."""
        ...


class ChannelCapabilities(BaseModel):
    """Channel capabilities — mirrors TS ChannelCapabilities"""

    chat_types: list[str] = ["direct", "group"]  # Supported chat types
    supports_media: bool = False
    supports_reactions: bool = False
    supports_threads: bool = False
    supports_polls: bool = False
    # TS-aligned fields
    block_streaming: bool = False       # TS: blockStreaming — send full message, no streaming
    native_commands: bool = False       # TS: nativeCommands — platform slash/native commands
    supports_edit: bool = False         # TS: edit — can edit sent messages
    supports_unsend: bool = False       # TS: unsend — can delete/unsend messages
    supports_reply: bool = True         # TS: reply — supports threaded replies
    group_management: bool = False      # TS: groupManagement — can manage groups
    text_chunk_limit: int | None = None  # TS: textChunkLimit — max chars per message


class ChatAttachment(BaseModel):
    """File/media attachment — mirrors TS ChatAttachment exactly"""

    type: str                       # "image" | "audio" | "video" | "file" | "sticker"
    mime_type: str | None = None    # TS: mimeType
    content: str | None = None      # base64-encoded content
    url: str | None = None          # remote URL (alternative to content)
    filename: str | None = None
    size: int | None = None         # bytes


class InboundMessage(BaseModel):
    """Normalized inbound message — mirrors TS InboundMessage"""

    channel_id: str
    message_id: str
    sender_id: str
    sender_name: str
    chat_id: str
    chat_type: str  # "direct", "group", "channel"
    text: str
    timestamp: str
    reply_to: str | None = None
    account_id: str | None = None  # channel account ID (e.g. Telegram bot alias, Feishu appId)
    metadata: dict[str, Any] = {}
    attachments: list[ChatAttachment] = []


class OutboundMessage(BaseModel):
    """Message to send"""

    channel_id: str
    target: str  # Chat/user ID
    text: str
    reply_to: str | None = None
    metadata: dict[str, Any] = {}


# Type alias for message handler
MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class ChannelPlugin(ABC):
    """Base class for channel plugins with enhanced connection management"""

    def __init__(self):
        self.id: str = ""
        self.label: str = ""
        self.capabilities: ChannelCapabilities = ChannelCapabilities()
        self._message_handler: MessageHandler | None = None
        self._running: bool = False

        # Connection management
        self._connection_manager: ConnectionManager | None = None
        self._health_checker: HealthChecker | None = None
        self._config: dict[str, Any] = {}

    def _setup_connection_manager(self, reconnect_config: ReconnectConfig | None = None) -> None:
        """
        Setup connection manager for automatic reconnection

        Call this in subclass __init__ or start() if you want
        automatic reconnection support.
        """
        self._connection_manager = ConnectionManager(
            channel_id=self.id,
            connect_fn=self._do_connect,
            disconnect_fn=self._do_disconnect,
            reconnect_config=reconnect_config or ReconnectConfig(),
        )

    def _setup_health_checker(self, interval: float = 30.0, timeout: float = 10.0) -> None:
        """
        Setup health checker

        Call this in subclass start() after connection is established.
        """
        self._health_checker = HealthChecker(
            channel_id=self.id, check_fn=self._health_check, interval=interval, timeout=timeout
        )
        self._health_checker.set_unhealthy_callback(self._on_unhealthy)

    async def _do_connect(self) -> None:
        """
        Internal connection implementation

        Override this in subclass instead of start() if using
        connection manager.
        """
        raise NotImplementedError("Subclass must implement _do_connect")

    async def _do_disconnect(self) -> None:
        """
        Internal disconnection implementation

        Override this in subclass instead of stop() if using
        connection manager.
        """
        raise NotImplementedError("Subclass must implement _do_disconnect")

    async def _health_check(self) -> bool:
        """
        Perform health check

        Override in subclass to implement actual health check.
        Default implementation just checks if running.
        """
        return self._running

    async def _on_unhealthy(self) -> None:
        """
        Called when channel becomes unhealthy

        Override in subclass to handle unhealthy state.
        Default triggers reconnection if connection manager is set.
        """
        logger.warning(f"[{self.id}] Channel unhealthy, attempting reconnection")
        if self._connection_manager:
            self._connection_manager.handle_connection_error(Exception("Health check failed"))

    # =========================================================================
    # Lifecycle Hooks (Template Method Pattern)
    # =========================================================================

    async def on_init(self) -> None:
        """
        Called before start() - initialize resources

        Override this for custom initialization logic.
        Default: no-op
        """
        pass

    async def on_start(self, config: dict[str, Any]) -> None:
        """
        Called during start() - connect to platform

        Override this to implement platform connection logic.
        This is where you should connect to Telegram API, Discord Gateway, etc.

        Args:
            config: Channel configuration
        """
        pass

    async def on_ready(self) -> None:
        """
        Called after start() completes - channel is ready

        Override this for post-connection setup (e.g., register commands).
        Default: no-op
        """
        pass

    async def on_stop(self) -> None:
        """
        Called during stop() - disconnect from platform

        Override this to implement platform disconnection logic.
        Default: no-op
        """
        pass

    async def on_destroy(self) -> None:
        """
        Called after stop() - cleanup resources

        Override this for final cleanup (e.g., close files, connections).
        Default: no-op
        """
        pass

    # =========================================================================
    # Message Hooks
    # =========================================================================

    async def on_message_received(self, message: InboundMessage) -> InboundMessage | None:
        """
        Called before processing inbound message - can filter/modify

        Override this to filter or modify messages before they're processed.
        Return None to skip processing this message.

        Args:
            message: Inbound message

        Returns:
            Modified message or None to skip

        Default: returns message unchanged
        """
        return message

    async def on_message_sent(self, message: OutboundMessage, message_id: str) -> None:
        """
        Called after sending outbound message

        Override this for post-send actions (e.g., logging, analytics).

        Args:
            message: Outbound message that was sent
            message_id: Platform message ID

        Default: no-op
        """
        pass

    # =========================================================================
    # Error Hooks
    # =========================================================================

    async def on_error(self, error: Exception) -> None:
        """
        Called when an error occurs

        Override this for custom error handling.

        Args:
            error: Exception that occurred

        Default: logs the error
        """
        logger.error(f"[{self.id}] Error: {error}", exc_info=True)

    async def on_connection_lost(self) -> None:
        """
        Called when connection is lost

        Override this to handle connection loss (e.g., trigger reconnection).
        Default: logs warning
        """
        logger.warning(f"[{self.id}] Connection lost")

    # =========================================================================
    # Health Check
    # =========================================================================

    async def check_health(self) -> tuple[bool, str]:
        """
        Perform health check

        Override this for custom health check logic.

        Returns:
            (is_healthy, reason)

        Default: checks if running
        """
        if not self._running:
            return False, "Channel not running"

        if not self.is_connected():
            return False, "Not connected"

        return True, "OK"

    # =========================================================================
    # Template Methods (final - don't override these)
    # =========================================================================

    async def start(self, config: dict[str, Any]) -> None:
        """
        Start the channel (Template Method)

        This calls the lifecycle hooks in order:
        1. on_init()
        2. on_start(config)
        3. set running flag
        4. on_ready()

        Don't override this - override the hooks instead.
        """
        if self._running:
            logger.warning(
                "[%s] Channel.start() called while already running — ignoring duplicate start",
                self.id,
            )
            return
        try:
            await self.on_init()
            await self.on_start(config)
            self._running = True
            await self.on_ready()
            logger.info(f"[{self.id}] Channel started successfully")
        except Exception as e:
            await self.on_error(e)
            raise

    async def stop(self) -> None:
        """
        Stop the channel (Template Method)

        This calls the lifecycle hooks in order:
        1. on_stop()
        2. clear running flag
        3. on_destroy()

        Don't override this - override the hooks instead.
        """
        try:
            await self.on_stop()
            self._running = False
            await self.on_destroy()
            logger.info(f"[{self.id}] Channel stopped successfully")
        except Exception as e:
            await self.on_error(e)
            raise

    # =========================================================================
    # Abstract Methods (must be implemented by subclasses)
    # =========================================================================

    @abstractmethod
    async def send_text(self, target: str, text: str, reply_to: str | None = None) -> str:
        """Send text message. Returns message ID."""
        pass

    async def send_media(
        self,
        target: str,
        media_url: str,
        media_type: str,
        caption: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        """Send media message. Returns message ID."""
        raise NotImplementedError("Media not supported by this channel")

    def set_message_handler(self, handler: MessageHandler) -> None:
        """Set handler for inbound messages"""
        self._message_handler = handler

    async def _handle_message(self, message: InboundMessage) -> None:
        """Internal message handler with metrics tracking"""
        if self._connection_manager:
            self._connection_manager.metrics.record_message_received()

        if self._message_handler:
            try:
                await self._message_handler(message)
            except Exception as e:
                logger.error(f"[{self.id}] Message handler error: {e}")
                if self._connection_manager:
                    self._connection_manager.metrics.record_error(str(e))

    async def _track_send(self) -> None:
        """Track sent message in metrics"""
        if self._connection_manager:
            self._connection_manager.metrics.record_message_sent()

    def is_running(self) -> bool:
        """Check if channel is running"""
        return self._running

    def is_connected(self) -> bool:
        """Check if channel is connected"""
        if self._connection_manager:
            return self._connection_manager.is_connected
        return self._running

    def is_healthy(self) -> bool:
        """Check if channel is healthy"""
        if self._health_checker:
            return self._health_checker.is_healthy
        return self._running

    def get_connection_state(self) -> ConnectionState:
        """Get current connection state"""
        if self._connection_manager:
            return self._connection_manager.state
        return ConnectionState.CONNECTED if self._running else ConnectionState.DISCONNECTED

    def get_metrics(self) -> ConnectionMetrics | None:
        """Get connection metrics"""
        if self._connection_manager:
            return self._connection_manager.metrics
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with enhanced status"""
        result = {
            "id": self.id,
            "label": self.label,
            "capabilities": self.capabilities.model_dump(),
            "running": self._running,
            "connected": self.is_connected(),
            "healthy": self.is_healthy(),
            "state": self.get_connection_state().value,
        }

        # Add metrics if available
        if self._connection_manager:
            result["metrics"] = self._connection_manager.metrics.to_dict()

        # Add health info if available
        if self._health_checker:
            result["health"] = self._health_checker.to_dict()

        return result
