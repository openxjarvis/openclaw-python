"""
Inbound message deduplication

Matches TypeScript src/auto-reply/reply/inbound-dedupe.ts

Prevents processing duplicate inbound messages based on a compound key
and TTL-based cache.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from openclaw.infra.dedupe import DedupeCache

if TYPE_CHECKING:
    from openclaw.auto_reply.types import MsgContext

# Global dedupe cache (matches TS INBOUND_DEDUPE_CACHE)
# TTL: 20 minutes, max size: 5000
INBOUND_DEDUPE_CACHE = DedupeCache(
    ttl_ms=20 * 60 * 1000,  # 20 minutes
    max_size=5000
)


def build_inbound_dedupe_key(ctx: "MsgContext") -> str:
    """
    Build dedupe key from message context.
    
    Matches TS buildInboundDedupeKey() from src/auto-reply/reply/inbound-dedupe.ts.
    
    Key format: provider|accountId|sessionKey|peerId|threadId|messageId
    
    Args:
        ctx: Message context
    
    Returns:
        Dedupe key string
    """
    # Support both snake_case and PascalCase attributes
    provider = getattr(ctx, 'Provider', None) or getattr(ctx, 'provider', '')
    account_id = getattr(ctx, 'AccountId', None) or getattr(ctx, 'account_id', '')
    session_key = getattr(ctx, 'SessionKey', None) or getattr(ctx, 'session_key', '')
    peer_id = getattr(ctx, 'PeerIdFull', None) or getattr(ctx, 'peer_id', '')
    thread_id = getattr(ctx, 'MessageThreadId', None) or getattr(ctx, 'thread_id', '')
    message_id = getattr(ctx, 'MessageId', None) or getattr(ctx, 'message_id', '')
    
    parts = [
        provider or "",
        account_id or "",
        session_key or "",
        peer_id or "",
        str(thread_id or ""),
        message_id or "",
    ]
    return "|".join(parts)


def should_skip_duplicate_inbound(ctx: "MsgContext") -> bool:
    """
    Check if inbound message should be skipped as duplicate.
    
    Matches TS shouldSkipDuplicateInbound() concept.
    
    Args:
        ctx: Message context
    
    Returns:
        True if message should be skipped (is duplicate), False otherwise
    """
    key = build_inbound_dedupe_key(ctx)
    is_new = INBOUND_DEDUPE_CACHE.check_and_set(key)
    return not is_new  # Skip if not new (i.e., is duplicate)


__all__ = [
    "INBOUND_DEDUPE_CACHE",
    "build_inbound_dedupe_key",
    "should_skip_duplicate_inbound",
]
