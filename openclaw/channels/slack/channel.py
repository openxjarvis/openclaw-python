"""Slack channel implementation — aligned with TS monitorSlackProvider/sendMessageSlack"""
from __future__ import annotations


import base64
import logging
from datetime import datetime
from typing import Any

from ..base import ChannelCapabilities, ChannelPlugin, ChatAttachment, InboundMessage

logger = logging.getLogger(__name__)

# Default reply-to mode: "thread" sends replies in thread, "channel" in channel
_DEFAULT_REPLY_MODE = "thread"


class SlackChannel(ChannelPlugin):
    """Slack bot channel — fully aligned with TS slackPlugin"""

    def __init__(self):
        super().__init__()
        self.id = "slack"
        self.label = "Slack"
        self.capabilities = ChannelCapabilities(
            chat_types=["direct", "group", "channel"],
            supports_media=True,
            supports_reactions=True,
            supports_threads=True,
            supports_polls=False,
            native_commands=True,
            supports_reply=True,
        )
        self._app: Any | None = None
        self._bot_token: str | None = None
        # TS ResolvedSlackAccount fields
        self._app_token: str | None = None
        self._reply_to_mode: str = _DEFAULT_REPLY_MODE
        self._reply_to_mode_by_chat_type: dict[str, str] = {}
        self._text_chunk_limit: int = 4000
        self._media_max_mb: int = 8
        self._reaction_notifications: bool = True
        # Native AI streaming (chat.startStream/appendStream/stopStream)
        # Mirrors TS nativeStreaming + streaming config fields
        self._native_streaming_enabled: bool = False
        self._team_id: str | None = None
        # HTTP mode support (P1-4)
        self._mode: str = "socket"
        self._webhook_path: str | None = None
        # Block streaming support (P1-5)
        self._block_streaming: bool = True
        self._block_streaming_coalesce: dict[str, Any] | None = None
        # Config storage for ackReaction and other features
        self._config: dict[str, Any] | None = None
        self._cfg: Any = None
        self._account_id: str | None = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self, config: dict[str, Any]) -> None:
        """Start Slack bot — supports both Socket and HTTP modes (P1-4)"""
        # Parse mode
        self._mode = config.get("mode", "socket")  # default: socket
        self._bot_token = config.get("botToken") or config.get("bot_token")
        
        # Parse common config
        self._parse_common_config(config)
        
        # Start based on mode
        if self._mode == "http":
            await self._start_http_mode(config)
        else:
            await self._start_socket_mode(config)
    
    def _parse_common_config(self, config: dict[str, Any]) -> None:
        """Parse common configuration for both socket and HTTP modes"""
        # Store full config for later use (e.g., ackReaction)
        self._config = config
        self._account_id = config.get("accountId") or config.get("account_id") or "default"
        
        # Get global config if available (for resolve functions)
        self._cfg = config.get("_full_config")  # Passed from channel manager
        
        # ResolvedSlackAccount field alignment
        self._reply_to_mode = (
            config.get("replyToMode") or config.get("reply_to_mode") or _DEFAULT_REPLY_MODE
        )
        self._reply_to_mode_by_chat_type = (
            config.get("replyToModeByChatType") or config.get("reply_to_mode_by_chat_type") or {}
        )
        self._text_chunk_limit = int(
            config.get("textChunkLimit") or config.get("text_chunk_limit") or 4000
        )
        self._media_max_mb = int(
            config.get("mediaMaxMb") or config.get("media_max_mb") or 8
        )
        self._reaction_notifications = bool(
            config.get("reactionNotifications", config.get("reaction_notifications", True))
        )

        # Native AI streaming — enabled when both streaming:"partial" and nativeStreaming:true
        # Mirrors TS isSlackStreamingEnabled(): mode === "partial" && nativeStreaming === true
        _streaming_mode = config.get("streaming") or "off"
        _native_streaming = bool(config.get("nativeStreaming") or config.get("native_streaming"))
        self._native_streaming_enabled = _streaming_mode == "partial" and _native_streaming
        
        # Block streaming config (P1-5)
        self._block_streaming = config.get("blockStreaming", config.get("block_streaming", True))
        self._block_streaming_coalesce = config.get(
            "blockStreamingCoalesce",
            config.get("block_streaming_coalesce", {
                "minChars": 1500,
                "maxChars": 2000,
                "idleMs": 1000,
            })
        )

    async def _start_socket_mode(self, config: dict[str, Any]) -> None:
        """Start Slack bot using Socket Mode (existing logic)"""
        signing_secret = config.get("signingSecret") or config.get("signing_secret")
        self._app_token = config.get("appToken") or config.get("app_token")

        if not self._bot_token or not signing_secret:
            raise ValueError("Slack botToken and signingSecret are required")
        if not self._app_token:
            raise ValueError("Slack appToken is required for socket mode")

        logger.info("Starting Slack channel in Socket mode...")

        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp

            self._app = AsyncApp(token=self._bot_token, signing_secret=signing_secret)

            # Register event handlers
            self._register_event_handlers()

            if self._app_token:
                handler = AsyncSocketModeHandler(self._app, self._app_token)
                await handler.start_async()

            # Fetch team_id via auth.test — required for native streaming DM streams
            if self._native_streaming_enabled:
                try:
                    auth_resp = await self._app.client.auth_test()
                    self._team_id = auth_resp.get("team_id")
                    logger.debug("[slack] native streaming enabled, team_id=%s", self._team_id)
                except Exception as _ae:
                    logger.debug("[slack] auth.test failed: %s", _ae)

            self._running = True
            logger.info("Slack channel started in Socket mode")

        except ImportError:
            logger.error("slack-sdk not installed. Install with: pip install slack-sdk slack-bolt")
            raise

    async def _start_http_mode(self, config: dict[str, Any]) -> None:
        """Start Slack bot using HTTP webhook mode (P1-4)"""
        signing_secret = config.get("signingSecret") or config.get("signing_secret")
        self._webhook_path = config.get("webhookPath", "/slack/events")

        if not self._bot_token:
            raise ValueError("Slack botToken is required")
        if not signing_secret:
            raise ValueError("Slack signingSecret is required for HTTP mode")

        logger.info(f"Starting Slack channel in HTTP mode, webhook: {self._webhook_path}")

        try:
            from slack_bolt.async_app import AsyncApp
            
            # Create HTTPReceiver with signing secret
            from openclaw.channels.slack.http_receiver import create_http_receiver
            
            receiver = create_http_receiver(
                signing_secret=signing_secret,
                endpoint=self._webhook_path,
            )

            self._app = AsyncApp(
                token=self._bot_token,
                receiver=receiver,
            )

            # Register event handlers (same as socket mode)
            self._register_event_handlers()

            self._running = True
            logger.info(f"Slack HTTP mode started, listening on {self._webhook_path}")

        except ImportError:
            logger.error("slack-bolt not installed")
            raise
    
    def _register_event_handlers(self) -> None:
        """Register Slack event handlers (common for both socket and HTTP modes)"""
        if not self._app:
            return
        
        @self._app.message("")
        async def handle_message(message, say):
            await self._handle_slack_message(message, say)

        if self._reaction_notifications:
            @self._app.event("reaction_added")
            async def handle_reaction_added(event, say):
                await self._handle_reaction_event(event, "add")

            @self._app.event("reaction_removed")
            async def handle_reaction_removed(event, say):
                await self._handle_reaction_event(event, "remove")
    
    def get_webhook_handler(self):
        """Get HTTP webhook handler for gateway registration (P1-4)"""
        if not self._app or not hasattr(self._app, "receiver"):
            return None

        async def handler(request):
            """Handle incoming Slack webhook request"""
            return await self._app.receiver.handle(request)

        return handler

    async def stop(self) -> None:
        """Stop Slack bot"""
        logger.info("Stopping Slack channel...")
        self._running = False

    # -------------------------------------------------------------------------
    # Inbound handling
    # -------------------------------------------------------------------------

    async def _handle_slack_message(self, message: dict[str, Any], say: Any) -> None:
        """Handle incoming Slack message — mirrors TS monitorSlackProvider"""
        if message.get("bot_id"):
            return

        attachments: list[ChatAttachment] = await self._download_attachments(message)

        chat_type_raw = message.get("channel_type", "")
        chat_type = "direct" if chat_type_raw == "im" else "group"
        is_dm = chat_type == "direct"
        is_group = chat_type == "group"

        # Ack reaction — send reaction while processing (mirrors TS ackReaction/ackReactionScope)
        if self._cfg and self._app:
            from openclaw.agents.identity import resolve_ack_reaction
            from openclaw.channels.ack_reactions import should_ack_reaction
            
            # Resolve agent_id - use "main" as default (most Slack messages route to main agent)
            agent_id = "main"
            
            # Resolve ack reaction emoji using cascading config
            # Priority: account → channel → messages → identity → default "👀"
            ack_emoji = resolve_ack_reaction(
                self._cfg,
                agent_id,
                {"channel": "slack", "accountId": self._account_id}
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
                # Slack mentions appear in message text as <@USER_ID>
                message_text = message.get("text", "")
                if message_text and "<@" in message_text:
                    # Would need bot_user_id to check if it's our bot, but we'll assume any mention
                    was_mentioned = True
                
                # Determine if we should send ack reaction
                should_ack = should_ack_reaction(
                    scope=ack_scope,
                    is_direct=is_dm,
                    is_group=is_group,
                    is_mentionable_group=is_group,  # Slack groups support mentions
                    require_mention=True,  # Slack requires explicit mentions in groups
                    can_detect_mention=True,  # Slack supports mention detection
                    effective_was_mentioned=was_mentioned,
                    should_bypass_mention=False,  # No bypass for Slack
                )
                
                if should_ack:
                    # Send ack reaction via Slack API
                    try:
                        await self._app.client.reactions_add(
                            channel=message.get("channel"),
                            timestamp=message.get("ts"),
                            name=ack_emoji.strip(":")  # Slack wants emoji name without colons
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send ack reaction: {e}")

        inbound = InboundMessage(
            channel_id=self.id,
            message_id=message.get("ts", ""),
            sender_id=message.get("user", ""),
            sender_name=message.get("user", ""),
            chat_id=message.get("channel", ""),
            chat_type=chat_type,
            text=message.get("text", ""),
            timestamp=_slack_ts_to_iso(message.get("ts", "0")),
            reply_to=message.get("thread_ts") if message.get("thread_ts") != message.get("ts") else None,
            metadata={
                "channel_type": chat_type_raw,
                "team": message.get("team"),
                "thread_ts": message.get("thread_ts"),
            },
            attachments=attachments,
        )

        await self._handle_message(inbound)

    async def _handle_reaction_event(self, event: dict[str, Any], action: str) -> None:
        """Handle Slack reaction_added / reaction_removed events"""
        item = event.get("item", {})
        inbound = InboundMessage(
            channel_id=self.id,
            message_id=f"{item.get('ts', '')}-reaction-{action}",
            sender_id=event.get("user", ""),
            sender_name=event.get("user", ""),
            chat_id=item.get("channel", ""),
            chat_type="group",
            text="",
            timestamp=_slack_ts_to_iso(event.get("event_ts", "0")),
            metadata={
                "type": "reaction",
                "action": action,
                "emoji": event.get("reaction", ""),
                "target_message_id": item.get("ts"),
            },
        )
        await self._handle_message(inbound)

    async def _download_attachments(self, message: dict[str, Any]) -> list[ChatAttachment]:
        """Download Slack file attachments as base64 — mirrors TS attachment handling"""
        result: list[ChatAttachment] = []
        files = message.get("files") or []
        for f in files:
            try:
                url = f.get("url_private_download") or f.get("url_private")
                if not url:
                    continue
                size = f.get("size", 0)
                max_bytes = self._media_max_mb * 1024 * 1024
                if size > max_bytes:
                    logger.debug(f"[slack] Skipping large file {f.get('name')} ({size} bytes)")
                    continue
                import aiohttp
                headers = {"Authorization": f"Bearer {self._bot_token}"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as resp:
                        data = await resp.read()
                content_b64 = base64.b64encode(data).decode()
                mime = f.get("mimetype", "")
                result.append(ChatAttachment(
                    type=_mime_to_type(mime),
                    mime_type=mime or None,
                    content=content_b64,
                    filename=f.get("name"),
                    size=size or None,
                ))
            except Exception as e:
                logger.warning(f"[slack] Failed to download attachment: {e}")
        return result

    # -------------------------------------------------------------------------
    # Outbound
    # -------------------------------------------------------------------------

    async def send_text(
        self,
        target: str,
        text: str,
        reply_to: str | None = None,
        thread_ts: str | None = None,
    ) -> str:
        """Send text message with replyToMode support — mirrors TS sendMessageSlack"""
        if not self._app:
            raise RuntimeError("Slack channel not started")

        try:
            effective_thread_ts = thread_ts or reply_to
            if effective_thread_ts:
                mode = self._reply_to_mode_by_chat_type.get("group") or self._reply_to_mode
                if mode == "channel":
                    effective_thread_ts = None

            result = await self._app.client.chat_postMessage(
                channel=target,
                text=text,
                thread_ts=effective_thread_ts,
            )
            return result["ts"]

        except Exception as e:
            logger.error(f"Failed to send Slack message: {e}", exc_info=True)
            raise

    async def send_media(
        self,
        target: str,
        media_url: str,
        media_type: str,
        caption: str | None = None,
        thread_ts: str | None = None,
    ) -> str:
        """Upload media to Slack — mirrors TS Slack file upload"""
        if not self._app:
            raise RuntimeError("Slack channel not started")

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(media_url) as resp:
                    data = await resp.read()

            filename = media_url.split("/")[-1] or "media"
            result = await self._app.client.files_upload_v2(
                channel=target,
                file=data,
                filename=filename,
                initial_comment=caption or "",
                thread_ts=thread_ts,
            )
            return result.get("file", {}).get("id", "")

        except Exception as e:
            logger.error(f"Failed to send Slack media: {e}", exc_info=True)
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slack_ts_to_iso(ts: str) -> str:
    """Convert Slack timestamp (e.g. '1234567890.123456') to ISO format"""
    try:
        epoch = float(ts)
        return datetime.fromtimestamp(epoch).isoformat()
    except (ValueError, OSError):
        return ts


def _mime_to_type(mime: str) -> str:
    """Map MIME type to ChatAttachment type string"""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "file"
