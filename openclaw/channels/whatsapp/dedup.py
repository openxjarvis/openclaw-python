"""Message deduplication for WhatsApp channel.

Two-layer deduplication:
  1. In-memory cache  — fast, synchronous, TTL 24h, max 1000 entries
  2. Persistent JSON  — survives process restarts, TTL 24h, max 10000 entries

Key format: "{accountId}:{remoteJid}:{messageId}"

Mirrors TypeScript: src/web/inbound/dedupe.ts
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

_DEDUP_TTL_SECONDS = 24 * 60 * 60   # 24 hours
_MEMORY_MAX_ENTRIES = 1_000
_PERSIST_MAX_ENTRIES = 10_000


class _MemoryDedup:
    """Fixed-size TTL cache for message IDs."""

    def __init__(self, ttl: float = _DEDUP_TTL_SECONDS, max_size: int = _MEMORY_MAX_ENTRIES) -> None:
        self._ttl = ttl
        self._max = max_size
        self._store: dict[str, float] = {}

    def seen(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        if time.time() > entry:
            del self._store[key]
            return False
        return True

    def record(self, key: str) -> None:
        self._evict_if_needed()
        self._store[key] = time.time() + self._ttl

    def _evict_if_needed(self) -> None:
        if len(self._store) < self._max:
            return
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v]
        for k in expired:
            del self._store[k]
        if len(self._store) >= self._max:
            oldest = sorted(self._store, key=lambda k: self._store[k])
            for k in oldest[: len(self._store) - self._max + 1]:
                del self._store[k]


class _PersistentDedup:
    """JSON file-backed TTL dedup store."""

    def __init__(self, path: Path, ttl: float = _DEDUP_TTL_SECONDS, max_size: int = _PERSIST_MAX_ENTRIES) -> None:
        self._path = path
        self._ttl = ttl
        self._max = max_size
        self._store: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            now = time.time()
            self._store = {k: v for k, v in data.items() if now < v}
        except Exception as e:
            logger.warning("[whatsapp] Failed to load dedup store %s: %s", self._path, e)
            self._store = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._store))
        except Exception as e:
            logger.warning("[whatsapp] Failed to save dedup store %s: %s", self._path, e)

    def seen(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        if time.time() > entry:
            del self._store[key]
            return False
        return True

    def record(self, key: str) -> None:
        self._evict_if_needed()
        self._store[key] = time.time() + self._ttl
        self._save()

    def _evict_if_needed(self) -> None:
        if len(self._store) < self._max:
            return
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v]
        for k in expired:
            del self._store[k]
        if len(self._store) >= self._max:
            oldest = sorted(self._store, key=lambda k: self._store[k])
            for k in oldest[: len(self._store) - self._max + 1]:
                del self._store[k]


class WhatsAppDedup:
    """Combined two-layer dedup for a single WhatsApp account."""

    def __init__(self, account_id: str) -> None:
        self._memory = _MemoryDedup()
        data_dir = resolve_state_dir() / "dedup"
        self._persistent = _PersistentDedup(data_dir / f"{account_id}.json")

    def is_duplicate(self, key: str) -> bool:
        """Return True if this message was already processed."""
        return self._memory.seen(key) or self._persistent.seen(key)

    def record(self, key: str) -> None:
        """Mark message as processed in both layers."""
        self._memory.record(key)
        self._persistent.record(key)

    def try_record(self, key: str) -> bool:
        """
        Check-and-record atomically.
        Returns True if first time (not a duplicate).
        Returns False if already seen.
        """
        if self.is_duplicate(key):
            return False
        self.record(key)
        return True


_dedup_instances: dict[str, WhatsAppDedup] = {}


def get_dedup(account_id: str) -> WhatsAppDedup:
    """Return (or create) the WhatsAppDedup instance for the given account."""
    if account_id not in _dedup_instances:
        _dedup_instances[account_id] = WhatsAppDedup(account_id)
    return _dedup_instances[account_id]
