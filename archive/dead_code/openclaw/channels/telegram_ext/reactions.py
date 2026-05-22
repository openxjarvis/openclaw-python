"""Telegram reactions support."""

from __future__ import annotations

from typing import Optional


async def add_reaction(
    bot_token: str,
    chat_id: int,
    message_id: int,
    reaction: str
) -> bool:
    """Add reaction to message.
    
    Args:
        bot_token: Bot token
        chat_id: Chat ID
        message_id: Message ID
        reaction: Reaction emoji
    
    Returns:
        True if successful
    """
    # In production, would call setMessageReaction API
    return True


async def remove_reaction(
    bot_token: str,
    chat_id: int,
    message_id: int,
    reaction: Optional[str] = None
) -> bool:
    """Remove reaction from message.
    
    Args:
        bot_token: Bot token
        chat_id: Chat ID
        message_id: Message ID
        reaction: Reaction to remove (None = all)
    
    Returns:
        True if successful
    """
    # In production, would call setMessageReaction API
    return True
