"""Reply dispatcher for Feishu channel.

Manages the lifecycle of a single reply:
  - Starts typing indicator (Typing emoji reaction)
  - Either uses streaming card (CardKit) or direct message send
  - Sends any media URLs after text
  - Removes typing indicator when done

Key improvements vs previous version:
  - ``thread_reply=True`` disables streaming cards (not supported in topic threads)
  - ``root_id`` passed to streaming session for topic-group routing
  - Media URL sending integrated into the reply lifecycle
  - ``is_active()`` forwarded from streaming session

Mirrors TypeScript: extensions/feishu/src/reply-dispatcher.ts
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .outbound import FeishuOutboundAdapter, resolve_receive_id_type
from .send import send_feishu_message
from .streaming_card import FeishuStreamingSession, StreamingCardHeader
from .typing import FeishuTypingIndicator
from ...config.paths import resolve_state_dir

if TYPE_CHECKING:
    from .config import ResolvedFeishuAccount

logger = logging.getLogger(__name__)


class FeishuReplyDispatcher:
    """
    Orchestrates typing indicator + streaming card for a single reply context.

    Usage pattern (mirrors TS createFeishuReplyDispatcher):
      dispatcher = FeishuReplyDispatcher(client, account, ...)
      await dispatcher.start()
      await dispatcher.send(text)    # or stream via update/finalize
      await dispatcher.send_media_urls(urls)   # optional
      # typing indicator removed automatically after send()
    """

    def __init__(
        self,
        client: Any,
        account: ResolvedFeishuAccount,
        *,
        receive_id: str,
        receive_id_type: str,
        reply_to_message_id: str | None,
        message_timestamp: float,
        reply_in_thread: bool = False,
        thread_reply: bool = False,
        root_id: str | None = None,
        header: StreamingCardHeader | None = None,
    ) -> None:
        self._client = client
        self._account = account
        self._receive_id = receive_id
        self._receive_id_type = receive_id_type
        self._reply_to_message_id = reply_to_message_id
        self._message_timestamp = message_timestamp
        self._reply_in_thread = reply_in_thread
        self._root_id = root_id
        self._header = header

        # Streaming is disabled for topic-thread replies (card streaming misses thread
        # affinity in topic contexts). Mirrors TS: ``!threadReplyMode && streaming !== false``
        self._thread_reply_mode = thread_reply
        self._streaming_enabled = (
            not thread_reply
            and getattr(account, "streaming", True)
            and getattr(account, "render_mode", "auto") != "raw"
        )

        self._typing: FeishuTypingIndicator | None = None
        self._streaming_session: FeishuStreamingSession | None = None
        self._outbound = FeishuOutboundAdapter(client, account)
        self._last_sent_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_typing(self) -> None:
        """Begin the typing indicator if enabled and message is recent."""
        if not self._account.typing_indicator:
            return

        if self._reply_to_message_id:
            self._typing = FeishuTypingIndicator(
                self._client,
                self._reply_to_message_id,
                self._message_timestamp,
                self._account.account_id,
            )
            await self._typing.__aenter__()

    async def stop_typing(self) -> None:
        """Remove typing indicator."""
        if self._typing:
            await self._typing.__aexit__(None, None, None)
            self._typing = None

    # ------------------------------------------------------------------
    # Streaming card flow (incremental output)
    # ------------------------------------------------------------------

    async def start_streaming(self) -> bool:
        """
        Initialize a streaming card session.

        Returns True if streaming card was created successfully.
        Falls back to non-streaming if CardKit fails or streaming is disabled.
        Mirrors TS startStreaming().
        """
        if not self._streaming_enabled:
            return False

        # Pass block_coalesce from account config so that when streamingCoalesce
        # is enabled in the account settings, only the final text is sent (no
        # intermediate streaming updates). Mirrors TS blockStreamingCoalesce option.
        _bsc = getattr(self._account, "block_streaming_coalesce", None)
        _block_coalesce = getattr(_bsc, "enabled", False) if _bsc else False
        session = FeishuStreamingSession(
            self._client, self._account, block_coalesce=_block_coalesce
        )
        ok = await session.start(
            self._receive_id,
            self._receive_id_type,
            reply_to_message_id=self._reply_to_message_id,
            reply_in_thread=self._reply_in_thread,
            root_id=self._root_id,
            header=self._header,
        )
        if ok:
            self._streaming_session = session
        return ok

    async def stream_update(self, text: str) -> None:
        """Push incremental text to the streaming card."""
        if self._streaming_session:
            await self._streaming_session.update(text)

    async def stream_finalize(
        self,
        final_text: str,
        buttons: list[list[dict]] | None = None,
    ) -> str | None:
        """
        Finalize the streaming card and stop typing. Returns message_id.

        When buttons are provided, the streaming card is finalized with the
        text first, then PATCH-ed again with a button card so users can
        interact with the response (e.g. confirm / choose).
        
        CRITICAL: This method also handles MEDIA tokens for Feishu streaming mode.
        Must parse and extract MEDIA before finalizing the card.
        """
        # Parse and extract MEDIA tokens (same logic as send())
        from openclaw.auto_reply.media_parse import split_media_from_output
        
        logger.info(f"[feishu dispatcher] stream_finalize() called with text length: {len(final_text)}")
        if "MEDIA:" in final_text.upper():
            logger.info(f"[feishu dispatcher] Found MEDIA token in final_text, parsing...")
        
        media_result = split_media_from_output(final_text)
        display_text = media_result.text if media_result.text is not None else final_text
        media_urls = []
        if media_result.media_url:
            media_urls.append(media_result.media_url)
        if media_result.media_urls:
            media_urls.extend(media_result.media_urls)
        
        logger.info(f"[feishu dispatcher] Parsed: display_text={len(display_text)} chars, media_urls={len(media_urls)}")
        
        msg_id: str | None = None
        if self._streaming_session:
            # Finalize with display text (MEDIA tokens stripped)
            msg_id = await self._streaming_session.finalize(display_text)
            self._last_sent_id = msg_id

            # If buttons are requested, patch the now-finalized card with a
            # full button card (includes both the markdown text and ActionSet).
            if buttons and msg_id:
                try:
                    from .card_builder import build_button_card
                    from .send import patch_feishu_card
                    button_card = build_button_card(display_text, buttons)
                    await patch_feishu_card(self._client, msg_id, button_card)
                except Exception as _be:
                    logger.debug("[feishu] Failed to patch button card after finalize: %s", _be)

        await self.stop_typing()
        
        # Send media files if any were found
        if media_urls:
            logger.info(f"[feishu dispatcher] Sending {len(media_urls)} media files after finalize")
            await self.send_media_urls(media_urls)
        
        return msg_id

    def is_streaming_active(self) -> bool:
        return self._streaming_session is not None and self._streaming_session.is_active()

    # ------------------------------------------------------------------
    # Block reply (intermediate step message — no typing stop)
    # ------------------------------------------------------------------

    async def send_block(self, text: str) -> str | None:
        """Send an intermediate block reply without stopping the typing indicator.

        Used in thread-reply mode (where CardKit is disabled) to deliver each
        agent reasoning step as a plain message while the typing indicator stays
        active. Mirrors TS sendBlockReply — typing keeps running between blocks.
        """
        try:
            result = await send_feishu_message(
                self._client,
                receive_id=self._receive_id,
                receive_id_type=self._receive_id_type,
                text=text,
                render_mode=self._account.render_mode,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
            )
            return result.message_id if result else None
        except Exception as exc:
            logger.debug("[feishu] send_block error (non-fatal): %s", exc)
            return None

    # ------------------------------------------------------------------
    # Non-streaming single send
    # ------------------------------------------------------------------

    async def send(
        self,
        text: str,
        buttons: list[list[dict]] | None = None,
    ) -> str | None:
        """
        Send a complete text reply (non-streaming).

        When buttons are provided, sends an interactive card with both the
        markdown text and an ActionSet (button card), instead of a plain
        post message. Button clicks are routed back as synthetic agent messages.

        Stops typing indicator when done.
        Returns message_id or None.
        
        CRITICAL: This method handles MEDIA tokens for Feishu, since Feishu uses
        a special dispatcher path that bypasses the standard _deliver_response().
        Mirrors agent_runner.py's _deliver_response() logic for media parsing.
        """
        try:
            # Parse and extract MEDIA tokens from text
            # This is critical for Feishu because it uses a special send path
            from openclaw.auto_reply.media_parse import split_media_from_output
            
            logger.info(f"[feishu dispatcher] send() called with text length: {len(text)}")
            if "MEDIA:" in text.upper():
                logger.info(f"[feishu dispatcher] Found MEDIA token in text, parsing...")
            
            media_result = split_media_from_output(text)
            display_text = media_result.text if media_result.text is not None else text
            media_urls = []
            if media_result.media_url:
                media_urls.append(media_result.media_url)
            if media_result.media_urls:
                media_urls.extend(media_result.media_urls)
            
            logger.info(f"[feishu dispatcher] Parsed: display_text={len(display_text)} chars, media_urls={len(media_urls)}")
            
            # Send text message (with MEDIA tokens stripped)
            card_override = None
            if buttons:
                from .card_builder import build_button_card
                card_override = build_button_card(display_text, buttons)

            result = await send_feishu_message(
                self._client,
                receive_id=self._receive_id,
                receive_id_type=self._receive_id_type,
                text=display_text,
                render_mode=self._account.render_mode,
                reply_to_message_id=self._reply_to_message_id,
                reply_in_thread=self._reply_in_thread,
                card_override=card_override,
            )
            msg_id = result.message_id if result else None
            self._last_sent_id = msg_id
            
            # Send media files if any were found
            if media_urls:
                logger.info(f"[feishu dispatcher] Sending {len(media_urls)} media files")
                await self.send_media_urls(media_urls)
            
            return msg_id
        finally:
            await self.stop_typing()

    # ------------------------------------------------------------------
    # Media sending
    # ------------------------------------------------------------------

    async def send_media_urls(
        self,
        media_urls: list[str],
        *,
        media_type: str = "file",
    ) -> None:
        """
        Download and send one or more media URLs as Feishu file/image messages.

        Called after text send to attach media to the reply.
        Supports both HTTP URLs and local file paths.
        Mirrors TS sendMediaFeishu() calls inside deliver().
        
        IMPORTANT: Deduplicates URLs to prevent sending the same file multiple times.
        """
        import aiohttp
        from pathlib import Path
        
        logger.info(f"[feishu dispatcher] send_media_urls called with {len(media_urls)} URLs")
        
        # Deduplicate media_urls to prevent sending the same file multiple times
        # This can happen when MEDIA tokens accumulate in session history
        seen_urls = set()
        unique_urls = []
        for url in media_urls:
            if url not in seen_urls:
                seen_urls.add(url)
                unique_urls.append(url)
        
        if len(unique_urls) < len(media_urls):
            logger.info(f"[feishu dispatcher] Deduped {len(media_urls)} URLs to {len(unique_urls)} unique URLs")

        for url in unique_urls:
            try:
                # Resolve local paths - try multiple locations
                # Priority: 1) session workspace, 2) common workspace, 3) absolute path
                resolved_url = url
                
                if not url.startswith(("http://", "https://", "/")):
                    # Relative path - try to resolve it
                    candidates = []
                    
                    # Try session-specific workspace first
                    if hasattr(self, '_session_workspace') and self._session_workspace:
                        candidates.append(Path(self._session_workspace) / url.lstrip('./'))
                    
                    # Try common workspace (/Users/long/.openclaw/workspace)
                    common_workspace = resolve_state_dir() / "workspace"
                    candidates.append(common_workspace / url.lstrip('./'))
                    
                    # Find first existing file
                    for candidate in candidates:
                        if candidate.exists():
                            resolved_url = str(candidate)
                            logger.info(f"[feishu dispatcher] Resolved path: {url} -> {resolved_url}")
                            break
                    else:
                        # No file found, use first candidate for error reporting
                        resolved_url = str(candidates[0]) if candidates else url
                        logger.warning(f"[feishu dispatcher] File not found in any location: {url}")
                
                is_local = not url.startswith(("http://", "https://"))
                
                if is_local:
                    # Read local file
                    file_path = Path(resolved_url).expanduser()
                    logger.info(f"[feishu dispatcher] Reading local file: {file_path}")
                    if not file_path.exists():
                        logger.error(f"[feishu dispatcher] Local file not found: {file_path}")
                        continue
                    data = file_path.read_bytes()
                    filename = file_path.name
                else:
                    # Download remote URL
                    logger.info(f"[feishu dispatcher] Downloading remote URL: {resolved_url}")
                    # Set User-Agent to avoid HTTP 403 from some CDNs/sites (e.g., Wikimedia)
                    headers = {
                        "User-Agent": "Mozilla/5.0 (compatible; OpenClaw-Python/1.0; +https://github.com/openxjarvis/openclaw-python)"
                    }
                    async with aiohttp.ClientSession(headers=headers) as session:
                        async with session.get(resolved_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status != 200:
                                logger.warning(
                                    "[feishu dispatcher] Failed to download media from %s: HTTP %s",
                                    resolved_url, resp.status,
                                )
                                continue
                            data = await resp.read()
                            
                            # Extract filename from URL or Content-Type
                            from urllib.parse import urlparse
                            url_path = urlparse(resolved_url).path
                            filename = Path(url_path).name or "attachment"
                            
                            # If no extension, try to infer from Content-Type
                            if '.' not in filename:
                                content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
                                ext_map = {
                                    'image/jpeg': '.jpg',
                                    'image/png': '.png',
                                    'image/gif': '.gif',
                                    'image/webp': '.webp',
                                    'image/bmp': '.bmp',
                                    'video/mp4': '.mp4',
                                    'video/quicktime': '.mov',
                                    'application/pdf': '.pdf',
                                    'application/vnd.ms-powerpoint': '.ppt',
                                    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
                                }
                                ext = ext_map.get(content_type)
                                if ext:
                                    filename = f"{filename}{ext}"
                                    logger.info(f"[feishu dispatcher] Inferred extension from Content-Type: {content_type} -> {ext}")

                
                logger.info(f"[feishu dispatcher] Got {len(data)} bytes, sending to Feishu as {filename}")
                
                # Determine media type from file extension for correct display
                # This ensures images display inline, videos/docs as file cards
                from pathlib import Path
                ext = Path(filename).suffix.lower()
                if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                    detected_media_type = "image"
                elif ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                    detected_media_type = "video"
                elif ext in {".opus", ".ogg", ".mp3", ".wav", ".m4a"}:
                    detected_media_type = "audio"
                else:
                    detected_media_type = "file"
                
                logger.info(f"[feishu dispatcher] Detected media_type={detected_media_type} for {filename}")
                
                await self._outbound.send_media(
                    self._receive_id,
                    data,
                    filename,
                    media_type=detected_media_type,
                    reply_to=self._reply_to_message_id,
                    reply_in_thread=self._reply_in_thread,
                )
                logger.info(f"[feishu dispatcher] Successfully sent media: {filename}")
            except Exception as exc:
                logger.warning("[feishu dispatcher] send_media_urls error for %s: %s", url, exc, exc_info=True)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def last_sent_id(self) -> str | None:
        return self._last_sent_id

    @property
    def streaming_enabled(self) -> bool:
        return self._streaming_enabled


def create_feishu_reply_dispatcher(
    client: Any,
    account: ResolvedFeishuAccount,
    *,
    chat_id: str,
    is_p2p: bool,
    reply_to_message_id: str | None,
    message_timestamp: float,
    reply_in_thread: bool = False,
    thread_reply: bool = False,
    root_id: str | None = None,
    header: StreamingCardHeader | None = None,
    session_workspace: str | None = None,
) -> FeishuReplyDispatcher:
    """
    Factory that creates a FeishuReplyDispatcher with correct receive_id_type.

    Mirrors TS createFeishuReplyDispatcher().
    """
    receive_id, receive_id_type = resolve_receive_id_type(chat_id)
    dispatcher = FeishuReplyDispatcher(
        client,
        account,
        receive_id=receive_id,
        receive_id_type=receive_id_type,
        reply_to_message_id=reply_to_message_id,
        message_timestamp=message_timestamp,
        reply_in_thread=reply_in_thread,
        thread_reply=thread_reply,
        root_id=root_id,
        header=header,
    )
    # Store session_workspace for media resolution
    dispatcher._session_workspace = session_workspace
    return dispatcher
