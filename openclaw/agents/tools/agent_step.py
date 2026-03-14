"""Agent step utilities

Implements agent-to-agent communication helpers, matching:
  openclaw/src/agents/tools/agent-step.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def read_latest_assistant_reply(
    session_key: str,
    limit: int = 50,
) -> str | None:
    """
    Read the latest assistant reply from session chat history.
    
    Matches TS readLatestAssistantReply() from agent-step.ts.
    
    Args:
        session_key: Session key to read from
        limit: Number of messages to fetch (default: 50)
    
    Returns:
        Latest assistant message text, or None if no assistant message found
    """
    try:
        # Import here to avoid circular dependency
        from openclaw.gateway.rpc_client import GatewayRPCClient
        
        # Call gateway to fetch chat history
        # Matches TS: callGateway({ method: "chat.history", params: {sessionKey, limit} })
        client = GatewayRPCClient()
        history = await client.call(
            method="chat.history",
            params={
                "sessionKey": session_key,
                "limit": limit,
            },
        )
        
        if not isinstance(history, dict):
            logger.warning(f"chat.history returned non-dict: {type(history)}")
            return None
        
        messages = history.get("messages", [])
        if not isinstance(messages, list):
            logger.warning(f"chat.history messages is not list: {type(messages)}")
            return None
        
        # Find latest assistant message (search backwards)
        # Matches TS: for (let i = filtered.length - 1; i >= 0; i -= 1)
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if not isinstance(msg, dict):
                continue
            
            # Check role
            if msg.get("role") != "assistant":
                continue
            
            # Extract text from content
            content = msg.get("content", "")
            text = _extract_text_from_content(content)
            
            if text and text.strip():
                return text
        
        return None
    
    except Exception as e:
        logger.warning(f"Failed to read latest assistant reply: {e}")
        return None


def _extract_text_from_content(content: Any) -> str:
    """
    Extract text from message content.
    
    Handles both string content and array of content blocks.
    Matches TS extractAssistantText() logic.
    
    Args:
        content: Message content (string or array)
    
    Returns:
        Extracted text
    """
    if isinstance(content, str):
        return content.strip()
    
    if isinstance(content, list):
        # Handle array of content blocks
        text_parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            
            # Extract text blocks
            if block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    text_parts.append(text)
        
        return "\n".join(text_parts).strip()
    
    return ""


def strip_tool_messages(messages: list[Any]) -> list[Any]:
    """
    Strip tool-related messages from history.
    
    Matches TS stripToolMessages() from sessions-helpers.ts.
    
    Args:
        messages: List of messages
    
    Returns:
        Filtered messages
    """
    filtered = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        role = msg.get("role")
        # Skip tool_result and tool_use messages
        if role in ("tool_result", "tool_use"):
            continue
        
        # Skip assistant messages with only tool_use content
        if role == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                has_text = any(
                    isinstance(block, dict) and block.get("type") == "text"
                    for block in content
                )
                if not has_text:
                    continue
        
        filtered.append(msg)
    
    return filtered


__all__ = [
    "read_latest_assistant_reply",
    "strip_tool_messages",
]
