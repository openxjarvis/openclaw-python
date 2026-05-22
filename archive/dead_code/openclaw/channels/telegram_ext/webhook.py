"""Telegram webhook handling."""

from __future__ import annotations

from typing import Optional, Callable, Awaitable, Any
from dataclasses import dataclass


@dataclass
class WebhookConfig:
    """Webhook configuration."""
    
    url: str
    secret_token: Optional[str] = None
    max_connections: int = 40
    allowed_updates: Optional[list[str]] = None


class TelegramWebhookHandler:
    """Handler for Telegram webhooks."""
    
    def __init__(self, bot_token: str, config: WebhookConfig):
        """Initialize webhook handler.
        
        Args:
            bot_token: Telegram bot token
            config: Webhook configuration
        """
        self.bot_token = bot_token
        self.config = config
        self._handlers: dict[str, list[Callable]] = {}
    
    def on(self, update_type: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        """Register handler for update type.
        
        Args:
            update_type: Update type (message, callback_query, etc.)
            handler: Async handler function
        """
        if update_type not in self._handlers:
            self._handlers[update_type] = []
        self._handlers[update_type].append(handler)
    
    async def handle_update(self, update: dict) -> None:
        """Handle incoming update.
        
        Args:
            update: Telegram update dict
        """
        # Determine update type
        if "message" in update:
            await self._dispatch("message", update["message"])
        elif "callback_query" in update:
            await self._dispatch("callback_query", update["callback_query"])
        elif "edited_message" in update:
            await self._dispatch("edited_message", update["edited_message"])
    
    async def _dispatch(self, update_type: str, data: dict) -> None:
        """Dispatch update to handlers.
        
        Args:
            update_type: Update type
            data: Update data
        """
        handlers = self._handlers.get(update_type, [])
        for handler in handlers:
            try:
                await handler(data)
            except Exception:
                pass
    
    async def set_webhook(self) -> bool:
        """Set webhook URL.
        
        Returns:
            True if successful
        """
        # In production, would call Telegram API
        return True
    
    async def delete_webhook(self) -> bool:
        """Delete webhook.
        
        Returns:
            True if successful
        """
        # In production, would call Telegram API
        return True
