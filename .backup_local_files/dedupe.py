"""
Deduplication cache for idempotent operations.

This module provides caching for operations with idempotency keys to prevent
duplicate processing of the same request (e.g., chat.send, agent runs).

Matches openclaw/src/gateway/server.impl.ts deduplication logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class DedupeEntry:
    """Cached result of an operation"""
    
    ts: datetime
    ok: bool
    payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DedupeCache:
    """
    Deduplication cache for idempotent operations.
    
    Stores operation results keyed by idempotency key. If the same
    idempotency key is seen within the TTL, the cached result is returned
    instead of re-executing the operation.
    
    Usage:
        cache = DedupeCache(ttl_minutes=60)
        
        # Check cache before operation
        cached = cache.get(f"chat:{idempotency_key}")
        if cached:
            return cached.payload if cached.ok else cached.error
        
        # Execute operation and cache result
        result = await execute_operation(...)
        cache.set(f"chat:{idempotency_key}", ok=True, payload=result)
    """
    
    def __init__(self, ttl_minutes: int = 60):
        """
        Initialize deduplication cache.
        
        Args:
            ttl_minutes: Time-to-live for cache entries in minutes
        """
        self._cache: dict[str, DedupeEntry] = {}
        self._ttl = timedelta(minutes=ttl_minutes)
    
    def get(self, key: str) -> DedupeEntry | None:
        """
        Get cached entry if not expired.
        
        Args:
            key: Cache key (e.g., "chat:{idempotencyKey}", "send:{idempotencyKey}")
            
        Returns:
            DedupeEntry if found and not expired, None otherwise
        """
        entry = self._cache.get(key)
        if entry and datetime.now() - entry.ts < self._ttl:
            return entry
        
        # Entry expired or not found
        if entry:
            del self._cache[key]
        
        return None
    
    def set(
        self, 
        key: str, 
        ok: bool, 
        payload: dict[str, Any] | None = None, 
        error: dict[str, Any] | None = None
    ) -> None:
        """
        Cache operation result.
        
        Args:
            key: Cache key
            ok: Whether operation succeeded
            payload: Operation result if successful
            error: Error information if failed
        """
        self._cache[key] = DedupeEntry(
            ts=datetime.now(),
            ok=ok,
            payload=payload,
            error=error
        )
    
    def cleanup(self) -> int:
        """
        Remove expired entries.
        
        Returns:
            Number of entries removed
        """
        now = datetime.now()
        expired_keys = [
            key for key, entry in self._cache.items()
            if now - entry.ts >= self._ttl
        ]
        
        for key in expired_keys:
            del self._cache[key]
        
        return len(expired_keys)
    
    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()
    
    def size(self) -> int:
        """Get number of cached entries"""
        return len(self._cache)


__all__ = [
    "DedupeEntry",
    "DedupeCache",
]
