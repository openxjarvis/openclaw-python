"""
Gateway uptime tracker.

Mirrors TS: src/gateway/server/health-state.ts — process.uptime()

Call `record_start_time()` once at gateway startup; afterwards
`get_uptime_seconds()` returns the elapsed wall-clock seconds.
Falls back to `time.monotonic()` from module-import time when the gateway
never explicitly records a start (e.g. during testing).
"""
from __future__ import annotations

import time

# Module-level fallback: treat import time as "start" so the function is
# always usable even if record_start_time() was never called.
_start_time: float = time.monotonic()


def record_start_time() -> None:
    """Record gateway start time. Call once from gateway bootstrap."""
    global _start_time
    _start_time = time.monotonic()


def get_uptime_seconds() -> float:
    """Return seconds since gateway start (or module import as fallback)."""
    return time.monotonic() - _start_time


def get_uptime_ms() -> int:
    """Return milliseconds since gateway start."""
    return int(get_uptime_seconds() * 1000)
