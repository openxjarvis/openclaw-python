"""DEPRECATED: This legacy module is not imported by any production code.
Use openclaw.channels.telegram.draft_stream instead.

Draft streaming for Telegram.

Allows real-time updates of messages while the agent is generating responses.
Matches TypeScript src/telegram/draft-stream.ts
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TELEGRAM_DRAFT_MAX_CHARS = 4096
DEFAULT_THROTTLE_MS = 300


class TelegramDraftStream:
    """Real-time draft message streaming for Telegram.
    
    Updates a message while content is being generated.
    """
    
    def __init__(
        self,
        bot,
        chat_id: int,
        draft_id: int,
        max_chars: int = TELEGRAM_DRAFT_MAX_CHARS,
        throttle_ms: int = DEFAULT_THROTTLE_MS,
    ):
        """Initialize draft stream.
        
        Args:
            bot: Telegram bot instance
            chat_id: Chat ID
            draft_id: Draft message ID
            max_chars: Maximum characters (default 4096)
            throttle_ms: Throttle interval in milliseconds (default 300)
        """
        self.bot = bot
        self.chat_id = chat_id
        self.draft_id = draft_id
        self.max_chars = min(max_chars, TELEGRAM_DRAFT_MAX_CHARS)
        self.throttle_ms = max(50, throttle_ms)
        
        self.last_sent_text = ""
        self.last_sent_at = 0
        self.pending_text = ""
        self.stopped = False
        self._task: Optional[asyncio.Task] = None
    
    def update(self, text: str):
        """Update draft with new text.
        
        Args:
            text: New draft text
        """
        if self.stopped:
            return
        
        self.pending_text = text
        
        # Schedule send if not already scheduled
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._throttled_send())
    
    async def _throttled_send(self):
        """Send draft with throttling."""
        await asyncio.sleep(self.throttle_ms / 1000)
        
        if self.stopped:
            return
        
        trimmed = self.pending_text.rstrip()
        if not trimmed:
            return
        
        if len(trimmed) > self.max_chars:
            logger.warning(f"Draft too long ({len(trimmed)} > {self.max_chars}), stopping stream")
            self.stopped = True
            return
        
        if trimmed == self.last_sent_text:
            return
        
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.draft_id,
                text=trimmed
            )
            self.last_sent_text = trimmed
            self.last_sent_at = asyncio.get_running_loop().time()
        except Exception as e:
            logger.error(f"Draft stream failed: {e}")
            self.stopped = True
    
    async def flush(self):
        """Flush pending updates immediately."""
        if self._task and not self._task.done():
            await self._task
        
        await self._throttled_send()
    
    def stop(self):
        """Stop the draft stream."""
        self.stopped = True
        if self._task and not self._task.done():
            self._task.cancel()


def create_telegram_draft_stream(
    bot,
    chat_id: int,
    draft_id: int,
    max_chars: Optional[int] = None,
    throttle_ms: Optional[int] = None,
) -> TelegramDraftStream:
    """Create a draft stream.
    
    Args:
        bot: Bot instance
        chat_id: Chat ID
        draft_id: Draft message ID
        max_chars: Max characters (optional)
        throttle_ms: Throttle ms (optional)
    
    Returns:
        Draft stream instance
    """
    kwargs = {}
    if max_chars is not None:
        kwargs["max_chars"] = max_chars
    if throttle_ms is not None:
        kwargs["throttle_ms"] = throttle_ms
    
    return TelegramDraftStream(bot, chat_id, draft_id, **kwargs)
