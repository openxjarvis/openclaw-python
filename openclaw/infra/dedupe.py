"""
Dedupe cache implementation

Matches TypeScript src/infra/dedupe.ts

Provides time-based deduplication with TTL and max size constraints.
"""
from __future__ import annotations

import time
from typing import Optional


class DedupeCache:
    """
    Time-based dedupe cache with TTL and max size.
    
    Matches TS createDedupeCache() from src/infra/dedupe.ts.
    
    Usage:
        cache = DedupeCache(ttl_ms=20*60*1000, max_size=5000)
        
        # Check if key is new
        if cache.check_and_set("my-key"):
            # First time seeing this key
            process_message()
        else:
            # Duplicate, skip
            pass
    """
    
    def __init__(self, ttl_ms: int, max_size: int):
        """
        Initialize dedupe cache.
        
        Args:
            ttl_ms: Time-to-live in milliseconds
            max_size: Maximum number of entries to keep
        """
        self.ttl_ms = ttl_ms
        self.max_size = max_size
        self.cache: dict[str, int] = {}  # key -> timestamp (ms)
    
    def check_and_set(self, key: str) -> bool:
        """
        Check if key is new and add it to cache.
        
        Args:
            key: Key to check
        
        Returns:
            True if key is new (not a duplicate), False if duplicate
        """
        now = int(time.time() * 1000)
        
        # Prune expired entries
        self._prune(now)
        
        # Check if key exists
        if key in self.cache:
            return False  # Duplicate
        
        # Evict oldest if at capacity
        if len(self.cache) >= self.max_size:
            self._evict_oldest()
        
        # Add new key
        self.cache[key] = now
        return True  # New key
    
    def _prune(self, now: int):
        """Remove expired entries"""
        cutoff = now - self.ttl_ms
        self.cache = {k: v for k, v in self.cache.items() if v >= cutoff}
    
    def _evict_oldest(self):
        """Remove oldest entry"""
        if self.cache:
            oldest_key = min(self.cache.items(), key=lambda x: x[1])[0]
            del self.cache[oldest_key]
    
    def clear(self):
        """Clear all entries"""
        self.cache.clear()
    
    def size(self) -> int:
        """Get current cache size"""
        return len(self.cache)


__all__ = ["DedupeCache"]
