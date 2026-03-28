"""Session write lock

File-based lock tied to session file path to serialize concurrent writes.
Matches TypeScript acquireSessionWriteLock() in openclaw/src/agents/session-lock.ts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_HOLD_MS = 60_000  # 1 minute


class SessionWriteLockError(Exception):
    """Raised when a session write lock cannot be acquired in time."""


class SessionWriteLock:
    """
    File-based async write lock for a session JSONL file.

    Usage::

        lock = SessionWriteLock(session_file)
        async with lock.acquire(max_hold_ms=30_000):
            # safe to write
            ...

    If the lock is already held and ``max_hold_ms`` is exceeded the context
    manager raises ``SessionWriteLockError``.
    """

    def __init__(self, session_file: str | Path, max_hold_ms: int = _DEFAULT_MAX_HOLD_MS) -> None:
        self._session_file = Path(session_file)
        self._lock_file = self._session_file.with_suffix(".lock")
        self._asyncio_lock = asyncio.Lock()
        self._max_hold_ms = max_hold_ms
        # Stale threshold: 2x max_hold to be conservative (align with TS 30min default)
        self._stale_threshold_s = (max_hold_ms / 1000) * 2

    @property
    def lock_file(self) -> Path:
        return self._lock_file

    def acquire(self, max_hold_ms: int = _DEFAULT_MAX_HOLD_MS) -> "AcquireContext":
        """Return an async context manager that acquires/releases the lock."""
        return AcquireContext(self, max_hold_ms)

    async def __aenter__(self) -> "SessionWriteLock":
        return self

    async def __aexit__(self, *_: object) -> None:
        self._release()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _acquire_inner(self, max_hold_ms: int) -> None:
        """Spin-wait until we can write the lock file or timeout."""
        deadline = time.monotonic() + max_hold_ms / 1000.0
        while True:
            if self._try_lock():
                return
            if time.monotonic() >= deadline:
                raise SessionWriteLockError(
                    f"Could not acquire session write lock for {self._session_file} "
                    f"within {max_hold_ms}ms"
                )
            await asyncio.sleep(0.05)

    def _try_lock(self) -> bool:
        """Try to atomically create the lock file (O_EXCL)."""
        try:
            self._lock_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Check for stale lock - based on max_hold_ms (dynamic, not fixed 300s)
            try:
                mtime = self._lock_file.stat().st_mtime
                age_s = time.time() - mtime
                
                # Only consider stale if age > threshold (2x max_hold_ms by default)
                if age_s > self._stale_threshold_s:
                    # Check if PID is still alive before removing lock
                    if self._is_lock_owner_alive():
                        logger.debug(f"Lock holder still alive (age={age_s:.0f}s), waiting...")
                        return False
                    
                    logger.warning(f"Removing stale lock file (age={age_s:.0f}s): {self._lock_file}")
                    self._lock_file.unlink(missing_ok=True)
                    return self._try_lock()
            except Exception as e:
                logger.debug(f"Failed to check stale lock: {e}")
                pass
            return False
        except Exception as exc:
            logger.debug(f"Lock attempt failed: {exc}")
            return False
    
    def _is_lock_owner_alive(self) -> bool:
        """Check if the PID in the lock file is still running.
        
        Returns True if the process is alive, False otherwise or if check fails.
        Mirrors TS session-write-lock.ts PID existence check.
        """
        try:
            pid_bytes = self._lock_file.read_bytes()
            pid = int(pid_bytes.decode().strip())
            
            # Check if process exists using psutil (already in dependencies)
            import psutil
            return psutil.pid_exists(pid)
        except Exception:
            # If we can't determine, assume not alive (conservative for stale cleanup)
            return False

    def _release(self) -> None:
        try:
            self._lock_file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"Failed to release lock: {exc}")


class AcquireContext:
    """Returned by SessionWriteLock.acquire() for use as async ctx manager."""

    def __init__(self, lock: "SessionWriteLock", max_hold_ms: int) -> None:
        self._lock = lock
        self._max_hold_ms = max_hold_ms

    async def __aenter__(self) -> "SessionWriteLock":
        await self._lock._acquire_inner(self._max_hold_ms)
        return self._lock

    async def __aexit__(self, *_: object) -> None:
        self._lock._release()


_lock_registry: dict[str, SessionWriteLock] = {}
_registry_lock = asyncio.Lock()


async def _get_or_create_lock(session_file: str | Path, max_hold_ms: int = _DEFAULT_MAX_HOLD_MS) -> SessionWriteLock:
    """Return (or create) the canonical lock for *session_file*."""
    key = str(Path(session_file).resolve())
    async with _registry_lock:
        if key not in _lock_registry:
            _lock_registry[key] = SessionWriteLock(session_file, max_hold_ms=max_hold_ms)
        return _lock_registry[key]


def acquire_session_write_lock(
    session_file: str | Path,
    max_hold_ms: int = _DEFAULT_MAX_HOLD_MS,
) -> "AcquireContext":
    """
    Acquire a write lock for the given session file.

    Matches TypeScript::

        const release = await acquireSessionWriteLock({ sessionFile, maxHoldMs });
        try { ... } finally { release(); }

    Usage::

        async with acquire_session_write_lock(session_file, max_hold_ms=30_000):
            ...
    """
    lock = SessionWriteLock(session_file, max_hold_ms=max_hold_ms)
    return AcquireContext(lock, max_hold_ms)


def acquire_session_write_lock_cached(
    session_file: str | Path,
    max_hold_ms: int = _DEFAULT_MAX_HOLD_MS,
) -> "_CachedAcquireContext":
    """Like :func:`acquire_session_write_lock` but reuses locks per path.

    This avoids creating a new lock object on every call and matches the TS
    pattern of keeping a per-session lock map.
    """
    return _CachedAcquireContext(session_file, max_hold_ms)


class _CachedAcquireContext:
    def __init__(self, session_file: str | Path, max_hold_ms: int) -> None:
        self._session_file = session_file
        self._max_hold_ms = max_hold_ms
        self._lock: Optional[SessionWriteLock] = None

    async def __aenter__(self) -> SessionWriteLock:
        self._lock = await _get_or_create_lock(self._session_file, self._max_hold_ms)
        await self._lock._acquire_inner(self._max_hold_ms)
        return self._lock

    async def __aexit__(self, *_: object) -> None:
        if self._lock is not None:
            self._lock._release()
