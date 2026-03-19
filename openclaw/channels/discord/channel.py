"""
Discord channel — main ChannelPlugin implementation.
Mirrors extensions/discord/src/channel.ts and the plugin capabilities declaration.

Orchestrates:
  - Config parsing and account resolution
  - DiscordMonitor lifecycle (on_start / on_stop)
  - send_text / send_media outbound adapter
  - check_health / get_guild_info / get_invite_url
  - Streaming reply delivery via DiscordStreamingSession
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..base import ChannelCapabilities, ChannelPlugin, InboundMessage
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


class DiscordChannel(ChannelPlugin):
    """
    Full-featured Discord bot channel.
    Aligns with the TypeScript discordPlugin in extensions/discord/src/channel.ts.

    Capabilities:
      chatTypes: direct, channel, thread
      polls, reactions, threads, media, nativeCommands, blockStreaming
    """

    def __init__(self) -> None:
        super().__init__()
        self.id = "discord"
        self.label = "Discord"
        self.capabilities = ChannelCapabilities(
            chat_types=["direct", "channel", "thread"],
            supports_media=True,
            supports_reactions=True,
            supports_threads=True,
            supports_polls=True,
            native_commands=True,
            block_streaming=True,
            supports_reply=True,
        )

        self._cfg: dict[str, Any] = {}
        self._accounts: list[Any] = []
        self._monitors: list[Any] = []
        self._persist_dir: Path | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_start(self, config: dict[str, Any]) -> None:
        self._cfg = config
        self._stop_event.clear()

        persist_path = config.get("persistDir") or config.get("persist_dir")
        if persist_path:
            self._persist_dir = Path(persist_path)
        else:
            self._persist_dir = resolve_state_dir() / "discord"
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        from .accounts import resolve_discord_accounts
        self._accounts = resolve_discord_accounts(config)

        if not self._accounts:
            logger.warning(
                "[discord] No valid accounts configured. "
                "Set DISCORD_BOT_TOKEN or provide token in config."
            )
            return

        from .monitor import start_discord_monitors
        self._monitors = await start_discord_monitors(
            accounts=self._accounts,
            on_inbound=self._handle_message,
            on_command=self._handle_slash_command,
            persist_dir=self._persist_dir,
        )

        logger.info("[discord] Started %d account(s)", len(self._monitors))
        self._running = True

    async def on_stop(self) -> None:
        self._stop_event.set()
        for monitor in self._monitors:
            try:
                await monitor.stop()
            except Exception as exc:
                logger.debug("[discord] Stop error: %s", exc)
        self._monitors.clear()
        self._running = False
        logger.info("[discord] Stopped")

    # -------------------------------------------------------------------------
    # Outbound — send_text
    # -------------------------------------------------------------------------

    async def send_text(
        self,
        target: str,
        text: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        stream_chunks: list[str] | None = None,
        buttons: list[list[dict]] | None = None,
    ) -> str:
        """Send a text reply.

        If *stream_chunks* is provided, streaming mode is used:
          1. An initial placeholder is posted.
          2. Each chunk is appended via DiscordStreamingSession.
          3. The session is finalized with *text* as the full reply.

        When *stream_chunks* is None the message is sent synchronously via
        the standard chunked send path.

        Mirrors TS discordPlugin outbound.sendText with streaming wiring.
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("Discord channel not connected")

        account = self._accounts[0] if self._accounts else None
        chunk_limit = account.text_chunk_limit if account else 2000
        max_lines = account.max_lines_per_message if account else 17
        chunk_mode = account.chunk_mode if account else "length"
        response_prefix = account.response_prefix if account else None

        # Streaming path — send preview then finalize
        if stream_chunks is not None:
            streaming_mode = getattr(account, "streaming", None) if account else None
            if streaming_mode and streaming_mode != "off":
                from .outbound import resolve_send_target
                from .streaming import DiscordStreamingSession
                from .config import BlockStreamingCoalesceConfig

                coalesce: BlockStreamingCoalesceConfig | None = None
                if account and hasattr(account, "block_streaming_coalesce"):
                    coalesce = account.block_streaming_coalesce

                try:
                    channel_obj = await resolve_send_target(client, target)
                    session = DiscordStreamingSession(
                        channel=channel_obj,
                        mode=streaming_mode,
                        coalesce=coalesce,
                        reply_to_id=int(reply_to) if reply_to else None,
                    )
                    await session.start()
                    for chunk in stream_chunks:
                        await session.append(chunk)
                    final_msg = await session.finish(
                        f"{response_prefix}{text}" if response_prefix else text
                    )
                    return str(final_msg.id) if final_msg else ""
                except Exception as exc:
                    logger.warning("[discord] Streaming send failed, falling back: %s", exc)
                    # Fall through to standard send

        # Buttons path — send message with interactive View (discord.ui.View).
        # Converts [[buttons:...]] directive data into Discord button components
        # and routes clicks back to the agent as synthetic InboundMessages.
        # Mirrors TS: send.components.ts sendDiscordComponentMessage()
        if buttons:
            from .components import build_discord_buttons_view
            from .outbound import resolve_send_target

            async def _on_click(callback_data: str, ctx: dict) -> None:
                """Route button click to agent as synthetic InboundMessage."""
                from ..base import InboundMessage
                import time as _time
                inbound = InboundMessage(
                    channel_id=self.id,
                    message_id=f"btn_{ctx.get('message_id', '')}_{int(_time.time() * 1000)}",
                    sender_id=ctx.get("user_id", ""),
                    sender_name=ctx.get("user_name", ""),
                    chat_id=ctx.get("channel_id", target),
                    chat_type="channel" if ctx.get("guild_id") else "direct",
                    text=callback_data,
                    timestamp=str(int(_time.time())),
                    metadata={
                        "type": "button_interaction",
                        "discord_component": ctx,
                    },
                )
                await self._handle_message(inbound)

            view = build_discord_buttons_view(buttons, on_click=_on_click)
            try:
                channel_obj = await resolve_send_target(client, target)
                reply_ref = None
                if reply_to:
                    import discord
                    try:
                        reply_msg = await channel_obj.fetch_message(int(reply_to))
                        reply_ref = reply_msg
                    except Exception:
                        pass
                full_text = f"{response_prefix}{text}" if response_prefix else text
                msg = await channel_obj.send(full_text, view=view, reference=reply_ref)
                return str(msg.id) if msg else ""
            except Exception as exc:
                logger.warning("[discord] send_text with buttons failed, falling back: %s", exc)
                # Fall through to standard send without buttons

        # Standard synchronous send
        from .outbound import send_discord_text
        msgs = await send_discord_text(
            client=client,
            target=target,
            text=text,
            reply_to=int(reply_to) if reply_to else None,
            chunk_limit=chunk_limit,
            max_lines=max_lines,
            chunk_mode=chunk_mode,
            response_prefix=response_prefix,
        )
        return str(msgs[-1].id) if msgs else ""

    # -------------------------------------------------------------------------
    # Outbound — send_media
    # -------------------------------------------------------------------------

    async def send_media(
        self,
        target: str,
        media_url: str,
        media_type: str,
        caption: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        client = self._get_client()
        if client is None:
            raise RuntimeError("Discord channel not connected")

        from .outbound import send_discord_media
        msg = await send_discord_media(
            client=client,
            target=target,
            media_url_or_path=media_url,
            caption=caption,
            reply_to=int(reply_to) if reply_to else None,
        )
        return str(msg.id) if msg else ""

    # -------------------------------------------------------------------------
    # Extended outbound helpers
    # -------------------------------------------------------------------------

    async def send_poll(
        self,
        target: str,
        question: str,
        answers: list[str],
        duration_hours: int = 24,
    ) -> str:
        client = self._get_client()
        if client is None:
            raise RuntimeError("Discord channel not connected")
        from .outbound import send_discord_poll
        msg = await send_discord_poll(client, target, question, answers, duration_hours)
        return str(msg.id) if msg else ""

    async def send_embed(
        self,
        target: str,
        title: str | None = None,
        description: str | None = None,
        color: str | int | None = None,
        fields: list[dict] | None = None,
        footer: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        client = self._get_client()
        if client is None:
            raise RuntimeError("Discord channel not connected")
        from .outbound import send_discord_embed
        msg = await send_discord_embed(
            client, target, title=title, description=description,
            color=color, fields=fields, footer=footer,
            reply_to=int(reply_to) if reply_to else None,
        )
        return str(msg.id) if msg else ""

    async def react(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> None:
        client = self._get_client()
        if client is None:
            return
        from .reactions import react_message
        await react_message(client, channel_id, message_id, emoji)

    async def set_typing(self, channel_id: str) -> None:
        client = self._get_client()
        if client is None:
            return
        from .typing import send_typing
        await send_typing(client, channel_id)

    async def set_presence(
        self,
        activity: str | None = None,
        activity_type: int = 0,
        status: str | None = None,
        url: str | None = None,
    ) -> None:
        client = self._get_client()
        if client is None:
            return
        from .presence import set_presence
        await set_presence(client, activity, activity_type, status, url)

    # -------------------------------------------------------------------------
    # Info helpers
    # -------------------------------------------------------------------------

    async def get_invite_url(
        self,
        permissions: int = 274877908992,  # standard bot permissions
    ) -> str | None:
        """
        Generate the OAuth2 bot invite URL.
        Equivalent to TS probe.ts application_id lookup.
        """
        client = self._get_client()
        if client is None or not client.application_id:
            return None
        return (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={client.application_id}"
            f"&permissions={permissions}"
            f"&scope=bot%20applications.commands"
        )

    async def get_guild_info(self, guild_id: str) -> dict[str, Any] | None:
        """Return basic info about a guild the bot is in."""
        client = self._get_client()
        if client is None:
            return None
        try:
            guild = client.get_guild(int(guild_id))
            if guild is None:
                guild = await client.fetch_guild(int(guild_id))
            return {
                "id": str(guild.id),
                "name": guild.name,
                "member_count": guild.member_count,
                "owner_id": str(guild.owner_id),
            }
        except Exception as exc:
            logger.warning("[discord] get_guild_info failed: %s", exc)
            return None

    async def check_health(self) -> bool:
        client = self._get_client()
        return client is not None and client.is_ready()

    # -------------------------------------------------------------------------
    # Slash command dispatch
    # -------------------------------------------------------------------------

    async def _handle_slash_command(
        self,
        command_name: str,
        interaction: Any,
        data: dict[str, Any],
    ) -> None:
        """Route slash command events to the inbound handler as structured messages."""
        from ..base import InboundMessage

        user_id = str(interaction.user.id) if interaction.user else ""
        channel_id = str(interaction.channel_id) if interaction.channel_id else ""
        guild_id = str(interaction.guild_id) if interaction.guild_id else None

        inbound = InboundMessage(
            channel_id=self.id,
            message_id=f"slash-{command_name}-{interaction.id}",
            sender_id=user_id,
            sender_name=getattr(interaction.user, "display_name", user_id),
            chat_id=channel_id,
            chat_type="channel" if guild_id else "direct",
            text=f"/{command_name}",
            timestamp="",
            metadata={
                "type": "slash_command",
                "command": command_name,
                "guild_id": guild_id,
                **data,
            },
        )
        await self._handle_message(inbound)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_client(self) -> Any | None:
        for monitor in self._monitors:
            client = monitor.get_client()
            if client and client.is_ready():
                return client
        # Return first client even if not ready (for non-guild lookups)
        for monitor in self._monitors:
            client = monitor.get_client()
            if client:
                return client
        return None

    def is_connected(self) -> bool:
        return any(
            (m.get_client() is not None and m.get_client().is_ready())
            for m in self._monitors
        )
