"""Enhanced Telegram channel implementation.

Extends basic Telegram with webhook, reactions, buttons, etc.
"""

from __future__ import annotations

from .webhook import TelegramWebhookHandler
from .reactions import add_reaction, remove_reaction
from .inline_buttons import create_inline_keyboard, InlineButton
from .media_upload import upload_media, MediaUploadResult

__all__ = [
    "TelegramWebhookHandler",
    "add_reaction",
    "remove_reaction",
    "create_inline_keyboard",
    "InlineButton",
    "upload_media",
    "MediaUploadResult",
]
