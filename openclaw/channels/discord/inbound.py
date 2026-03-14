"""
Inbound message processing pipeline.
Mirrors src/discord/monitor/message-handler.preflight.ts and
       src/discord/monitor/message-handler.process.ts.

13-step preflight pipeline:
  1.  Drop own-bot messages
  2.  PluralKit identity resolution (webhook proxy detection)
  3.  Drop bots if allowBots=False
  4.  Detect chat type: DM / GroupDM / Guild
  5.  DM policy checks (pairing code reply)
  6.  Group policy / guild allowlist checks
  7.  Channel allowlist + thread parent fallback
  8.  Per-channel users/roles member access checks
  9.  Mention gating (requireMention: explicit mention, regex, implicit reply-to-bot)
 10.  Pre-flight audio transcription (voice notes → transcribe for mention detection)
 11.  Thread binding resolution (bound threads bypass mention gating)
 12.  Empty content drop
 13.  Debouncer (asyncio task per accountId:channelId:authorId, 300ms)
 14.  Build InboundMessage → dispatch
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable, Awaitable

from ..base import InboundMessage
from .config import ResolvedDiscordAccount
from .dedup import DiscordDedup
from .media import download_all_attachments
from .pluralkit import (
    fetch_pluralkit_message_info,
    is_pluralkit_webhook,
    resolve_pluralkit_display,
    resolve_pluralkit_sender_id,
)
from .policy import (
    DmPolicyResult,
    PairingStore,
    check_dm_policy,
    is_channel_allowed,
    is_guild_allowed,
    is_member_allowed,
    resolve_channel_config,
    resolve_guild_config,
)
from .threading import ThreadBindingStore

logger = logging.getLogger(__name__)

_DEBOUNCE_DELAY = 0.3  # seconds — matches TS debounce window

DispatchFn = Callable[[InboundMessage], Awaitable[None]]


class DiscordInboundProcessor:
    """
    Stateful inbound processor for one Discord account.
    Holds the dedup/pairing/thread-binding stores and the debounce state.
    """

    def __init__(
        self,
        account: ResolvedDiscordAccount,
        dispatch: DispatchFn,
        dedup: DiscordDedup,
        pairing_store: PairingStore,
        thread_bindings: ThreadBindingStore,
        send_pairing_dm: Callable[[Any, str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._account = account
        self._dispatch = dispatch
        self._dedup = dedup
        self._pairing_store = pairing_store
        self._thread_bindings = thread_bindings
        self._send_pairing_dm = send_pairing_dm

        # Debounce: "account:channel:author" -> (task, accumulated_text, first_message)
        self._debounce: dict[str, tuple[asyncio.Task, list[str], Any]] = {}

    async def handle_message(self, message: Any, client: Any) -> None:
        """Entry point for new Discord messages from the gateway."""
        import discord

        # ── 1. Drop own-bot messages ──────────────────────────────────────────
        if message.author == client.user:
            return

        # ── 2. PluralKit resolution ───────────────────────────────────────────
        effective_user_id = str(message.author.id)
        effective_username = message.author.display_name or str(message.author.name)
        is_pk_proxy = False

        if self._account.pluralkit.enabled and is_pluralkit_webhook(message):
            # Use message ID for the correct /v2/messages/{msgId} lookup
            pk_config = {"enabled": True}
            if hasattr(self._account.pluralkit, "token"):
                pk_config["token"] = self._account.pluralkit.token
            pk_info = await fetch_pluralkit_message_info(str(message.id), pk_config)
            if pk_info:
                is_pk_proxy = True
                pk_id = resolve_pluralkit_sender_id(pk_info)
                if pk_id:
                    effective_user_id = pk_id  # "pk:<member_id>"
                pk_display = resolve_pluralkit_display(pk_info)
                if pk_display:
                    effective_username = pk_display
            else:
                # Webhook but not PluralKit — treat as bot
                if not self._account.allow_bots:
                    return

        # ── 3. Drop bots (unless PluralKit proxy or allowBots=True) ──────────
        if message.author.bot and not is_pk_proxy:
            if not self._account.allow_bots:
                return

        # ── 4. Detect chat type ───────────────────────────────────────────────
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_group_dm = isinstance(message.channel, discord.GroupChannel)
        is_thread = isinstance(message.channel, discord.Thread)
        is_guild = bool(message.guild)

        chat_type: str
        if is_dm:
            chat_type = "direct"
        elif is_thread:
            chat_type = "thread"
        elif is_guild:
            chat_type = "channel"
        else:
            chat_type = "direct"

        # ── 5. DM policy ─────────────────────────────────────────────────────
        if is_dm:
            result: DmPolicyResult = check_dm_policy(
                self._account,
                effective_user_id,
                effective_username,
                self._pairing_store,
            )
            if not result.allowed:
                if result.pairing_code and self._send_pairing_dm:
                    await self._send_pairing_dm(
                        message.channel,
                        effective_user_id,
                        effective_username,
                        result.pairing_code,
                    )
                return

        # ── 5b. Group DM check ────────────────────────────────────────────────
        if is_group_dm:
            if not self._account.dm.group_enabled:
                return
            # Check channel allowlist if configured
            allowed_channels = self._account.dm.group_channels
            if allowed_channels and str(message.channel.id) not in allowed_channels:
                return

        # ── 6. Guild / group policy ───────────────────────────────────────────
        guild_id: str | None = str(message.guild.id) if message.guild else None
        guild_name: str | None = message.guild.name if message.guild else None
        guild_entry = None

        if is_guild and guild_id:
            if not is_guild_allowed(self._account, guild_id, guild_name):
                return
            guild_entry = resolve_guild_config(self._account, guild_id, guild_name)

        # ── 7. Channel allowlist + thread parent fallback ─────────────────────
        channel_id = str(message.channel.id)
        channel_name = getattr(message.channel, "name", None)
        parent_id: str | None = None
        parent_name: str | None = None

        if is_thread and guild_entry:
            parent = getattr(message.channel, "parent", None)
            if parent:
                parent_id = str(parent.id)
                parent_name = getattr(parent, "name", None)

        channel_cfg = None
        if guild_entry and guild_entry.channels:
            if not is_channel_allowed(
                self._account, guild_entry, channel_id, channel_name, parent_id, parent_name
            ):
                return
            channel_cfg = resolve_channel_config(
                guild_entry, channel_id, channel_name, parent_id, parent_name,
                self._account.dangerously_allow_name_matching,
            )

        # ── 8. Per-channel users/roles member access ──────────────────────────
        if is_guild and message.guild:
            member = message.guild.get_member(int(effective_user_id.removeprefix("pk:")))
            user_roles = [str(r.id) for r in getattr(member, "roles", [])]
            if not is_member_allowed(
                self._account,
                guild_entry,
                channel_cfg,
                effective_user_id,
                effective_username,
                user_roles,
            ):
                return

        # ── 9. Mention gating ─────────────────────────────────────────────────
        require_mention = _resolve_require_mention(
            self._account, guild_entry, channel_cfg, is_guild
        )

        # Check thread binding first (bound threads bypass mention gate)
        thread_binding = None
        if is_thread:
            thread_binding = self._thread_bindings.get_binding(channel_id)

        if require_mention and thread_binding is None:
            bot_user = client.user
            mentioned = _check_mention(message, bot_user, self._account.allow_bots)
            if not mentioned:
                return

        # ── 10. Pre-flight audio transcription (voice notes in guild) ─────────
        content = message.content or ""
        if not content and message.attachments:
            audio_transcript = await _transcribe_audio_attachments(
                message.attachments, self._account.media_max_mb
            )
            if audio_transcript:
                content = audio_transcript
                if require_mention and thread_binding is None:
                    bot_user = client.user
                    if not _check_mention_in_text(audio_transcript, bot_user):
                        return

        # ── 11. Thread binding touch ──────────────────────────────────────────
        if thread_binding:
            self._thread_bindings.touch(channel_id)

        # ── 12. Empty content drop ────────────────────────────────────────────
        if not content.strip() and not message.attachments:
            return

        # ── 12b. Ack reaction — react immediately to signal processing ────────
        from openclaw.channels.ack_reactions import should_ack_reaction
        
        ack_emoji = self._account.ack_reaction
        ack_scope = self._account.ack_reaction_scope
        if ack_emoji:
            # Check if message has mentions (for group-mentions scope)
            was_mentioned = False
            if not is_dm and message.mentions:
                # Check if bot was mentioned
                for mentioned_user in message.mentions:
                    if mentioned_user.id == client.user.id:
                        was_mentioned = True
                        break
            
            # Use helper function to determine if we should ack
            _should_ack = should_ack_reaction(
                scope=ack_scope,
                is_direct=is_dm,
                is_group=is_guild or is_group_dm,
                is_mentionable_group=is_guild or is_group_dm,
                require_mention=True,  # Discord requires explicit mentions
                can_detect_mention=True,  # Discord supports mention detection
                effective_was_mentioned=was_mentioned,
                should_bypass_mention=False,  # No bypass for Discord
            )
            
            if _should_ack:
                asyncio.create_task(
                    _send_ack_reaction(message, client, ack_emoji),
                    name="discord_ack_reaction",
                )

        # ── 13. Debounce ──────────────────────────────────────────────────────
        debounce_key = f"{self._account.account_id}:{channel_id}:{effective_user_id}"
        await self._debounce_and_dispatch(
            debounce_key, message, content, chat_type, guild_id, thread_binding,
            resolved_user_id=effective_user_id, resolved_username=effective_username,
        )

    async def _debounce_and_dispatch(
        self,
        key: str,
        message: Any,
        content: str,
        chat_type: str,
        guild_id: str | None,
        thread_binding: Any,
        *,
        resolved_user_id: str | None = None,
        resolved_username: str | None = None,
    ) -> None:
        """Debounce rapid successive messages (300ms) then dispatch."""
        if key in self._debounce:
            task, texts, first_msg, _ru, _rn = self._debounce[key]
            if not task.done():
                task.cancel()
            texts.append(content)
            self._debounce[key] = (
                asyncio.create_task(
                    self._dispatch_after_delay(
                        key, message, texts, first_msg, chat_type, guild_id, thread_binding,
                        resolved_user_id=resolved_user_id, resolved_username=resolved_username,
                    )
                ),
                texts,
                first_msg,
                resolved_user_id,
                resolved_username,
            )
        else:
            texts = [content]
            task = asyncio.create_task(
                self._dispatch_after_delay(
                    key, message, texts, message, chat_type, guild_id, thread_binding,
                    resolved_user_id=resolved_user_id, resolved_username=resolved_username,
                )
            )
            self._debounce[key] = (task, texts, message, resolved_user_id, resolved_username)

    async def _dispatch_after_delay(
        self,
        key: str,
        last_message: Any,
        texts: list[str],
        first_message: Any,
        chat_type: str,
        guild_id: str | None,
        thread_binding: Any,
        *,
        resolved_user_id: str | None = None,
        resolved_username: str | None = None,
    ) -> None:
        try:
            await asyncio.sleep(_DEBOUNCE_DELAY)
        except asyncio.CancelledError:
            return
        finally:
            self._debounce.pop(key, None)

        combined_text = "\n".join(t for t in texts if t)

        # Build and dispatch InboundMessage
        attachments = await download_all_attachments(
            last_message.attachments, self._account.media_max_mb
        )

        msg_id = str(last_message.id)
        if self._dedup.is_duplicate(msg_id):
            return

        # Preserve PluralKit-resolved identity (don't re-derive from raw author)
        effective_user_id = resolved_user_id or str(last_message.author.id)
        effective_username = (
            resolved_username
            or last_message.author.display_name
            or str(last_message.author.name)
        )

        metadata: dict = {
            "guild_id": guild_id,
            "channel_name": getattr(last_message.channel, "name", None),
            "thread_binding_session": thread_binding.session_key if thread_binding else None,
            "is_thread": isinstance(last_message.channel, __import__("discord").Thread),
        }

        inbound = InboundMessage(
            channel_id="discord",
            message_id=msg_id,
            sender_id=effective_user_id,
            sender_name=effective_username,
            chat_id=str(last_message.channel.id),
            chat_type=chat_type,
            text=combined_text,
            timestamp=last_message.created_at.isoformat(),
            reply_to=str(last_message.reference.message_id) if last_message.reference else None,
            metadata=metadata,
            attachments=attachments,
        )

        await self._dispatch(inbound)

    async def handle_reaction(self, payload: Any, action: str, client: Any) -> None:
        """
        Forward reaction add/remove events based on reactionNotifications config.
        """
        from .reactions import should_forward_reaction

        channel_id = str(payload.channel_id)
        guild_id = str(payload.guild_id) if payload.guild_id else None
        user_id = str(payload.user_id)
        emoji = str(payload.emoji)
        message_id = str(payload.message_id)

        # Resolve guild/channel config to get reactionNotifications setting
        notification_policy = "own"
        if guild_id:
            guild_entry = resolve_guild_config(self._account, guild_id)
            if guild_entry:
                ch_cfg = resolve_channel_config(
                    guild_entry, channel_id, None, None, None,
                    self._account.dangerously_allow_name_matching,
                )
                if ch_cfg:
                    notification_policy = ch_cfg.reaction_notifications
                else:
                    notification_policy = guild_entry.reaction_notifications

        bot_id = str(client.user.id) if client.user else ""

        # Fetch message author to check "own" policy
        message_author_id = ""
        try:
            ch = client.get_channel(int(channel_id))
            if ch:
                msg = await ch.fetch_message(int(message_id))
                message_author_id = str(msg.author.id)
        except Exception:
            pass

        # Resolve allow_from for "allowlist" reaction notifications policy
        allow_from_list: list[str] | None = None
        if guild_id:
            guild_entry_rx = resolve_guild_config(self._account, guild_id)
            if guild_entry_rx:
                allow_from_list = [
                    str(e) for e in (getattr(self._account, "allow_from", None) or [])
                ]

        if not should_forward_reaction(
            notification_policy, bot_id, message_author_id, user_id,
            allow_from=allow_from_list,
        ):
            return

        inbound = InboundMessage(
            channel_id="discord",
            message_id=f"{message_id}-reaction-{action}-{user_id}",
            sender_id=user_id,
            sender_name="",
            chat_id=channel_id,
            chat_type="channel" if guild_id else "direct",
            text="",
            timestamp="",
            metadata={
                "type": "reaction",
                "action": action,
                "emoji": emoji,
                "target_message_id": message_id,
                "guild_id": guild_id,
            },
        )
        await self._dispatch(inbound)


# ---------------------------------------------------------------------------
# Mention detection helpers — mirrors message-handler.preflight.ts
# ---------------------------------------------------------------------------

def _resolve_require_mention(
    account: ResolvedDiscordAccount,
    guild_entry: Any | None,
    channel_cfg: Any | None,
    is_guild: bool,
) -> bool:
    """Resolve the effective requireMention flag (channel > guild > default=False for guilds)."""
    if not is_guild:
        return False
    if channel_cfg and channel_cfg.require_mention is not None:
        return channel_cfg.require_mention
    if guild_entry and guild_entry.require_mention is not None:
        return guild_entry.require_mention
    return False


def _check_mention(message: Any, bot_user: Any, allow_bots: bool) -> bool:
    """
    Return True if the bot is mentioned in the message.
    Checks:
      1. Explicit @mention of the bot user
      2. Implicit reply-to-bot (message is a reply to a bot message)
      3. Bot name regex match in content
    """
    if not bot_user:
        return False

    # Explicit mention
    if bot_user in (message.mentions or []):
        return True

    # Reply to bot's own message
    if message.reference and message.reference.resolved:
        ref_msg = message.reference.resolved
        if hasattr(ref_msg, "author") and ref_msg.author == bot_user:
            return True

    # Name regex (case-insensitive match of bot's display name)
    bot_name = (bot_user.display_name or bot_user.name or "").strip()
    if bot_name and _check_mention_in_text(message.content or "", None, bot_name):
        return True

    return False


def _check_mention_in_text(text: str, bot_user: Any | None, name_override: str | None = None) -> bool:
    bot_name = name_override
    if bot_user and not bot_name:
        bot_name = (getattr(bot_user, "display_name", None) or getattr(bot_user, "name", "") or "").strip()
    if not bot_name:
        return False
    pattern = re.compile(re.escape(bot_name), re.IGNORECASE)
    return bool(pattern.search(text))


async def _send_ack_reaction(message: Any, client: Any, emoji: str) -> None:
    """Send an ack reaction to *message* (fire-and-forget).

    Mirrors TS discordAdapter.setReaction() called from shouldAckReactionGate.
    """
    try:
        await message.add_reaction(emoji)
    except Exception as exc:
        logger.debug("[discord][inbound] Ack reaction failed: %s", exc)


async def _transcribe_audio_attachments(attachments: list[Any], max_mb: int) -> str | None:
    """
    Pre-flight transcription of audio attachments for mention detection.
    Only triggered when message has no text content.
    Mirrors TS pre-flight audio transcription in message-handler.preflight.ts.
    """
    max_bytes = max_mb * 1024 * 1024
    for att in attachments:
        content_type = getattr(att, "content_type", "") or ""
        if not content_type.startswith("audio/"):
            continue
        size = getattr(att, "size", 0) or 0
        if size > max_bytes:
            continue
        try:
            import aiohttp
            import os
            import tempfile
            async with aiohttp.ClientSession() as session:
                async with session.get(att.url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            suffix = ".ogg" if "ogg" in content_type else ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(data)
                tmp_path = f.name
            try:
                from openclaw.voice import transcribe_audio_file
                return await transcribe_audio_file(tmp_path)
            except ImportError:
                return None
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as exc:
            logger.debug("[discord][inbound] Audio preflight transcription failed: %s", exc)
    return None
