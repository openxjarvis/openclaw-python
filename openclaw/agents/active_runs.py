"""
Active runs management for embedded Pi agent

Matches TypeScript src/agents/pi-embedded-runner/runs.ts

Tracks active agent runs for steering, abort, and status queries.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

# Global registry of active runs (matches TS ACTIVE_EMBEDDED_RUNS)
ACTIVE_EMBEDDED_RUNS: dict[str, "EmbeddedPiQueueHandle"] = {}

# Global registry of run waiters (matches TS EMBEDDED_RUN_WAITERS)
EMBEDDED_RUN_WAITERS: dict[str, set] = {}


@dataclass
class EmbeddedPiQueueHandle:
    """
    Handle for an active embedded Pi agent run.
    
    Matches TS EmbeddedPiQueueHandle type lines 7-12.
    
    Provides methods to:
    - queue_message: Inject steering messages
    - is_streaming: Check if agent is streaming
    - is_compacting: Check if agent is compacting
    - abort: Abort the run
    """
    queue_message: Callable[[str], Awaitable[None]]
    is_streaming: Callable[[], bool]
    is_compacting: Callable[[], bool]
    abort: Callable[[], None]


def set_active_embedded_run(
    session_id: str,
    handle: EmbeddedPiQueueHandle,
    session_key: Optional[str] = None,
) -> None:
    """
    Register an active embedded run.
    
    Matches TS setActiveEmbeddedRun() lines 117-149.
    
    Args:
        session_id: Session ID
        handle: Queue handle for the run
        session_key: Optional session key for logging
    """
    was_active = session_id in ACTIVE_EMBEDDED_RUNS
    ACTIVE_EMBEDDED_RUNS[session_id] = handle
    
    # Log state change
    if not was_active:
        # Optional: add logging here
        pass


def clear_active_embedded_run(
    session_id: str,
    handle: EmbeddedPiQueueHandle,
    session_key: Optional[str] = None,
) -> None:
    """
    Clear an active embedded run.
    
    Matches TS clearActiveEmbeddedRun() lines 117-149.
    
    Args:
        session_id: Session ID
        handle: Queue handle to clear (must match registered handle)
        session_key: Optional session key for logging
    """
    if ACTIVE_EMBEDDED_RUNS.get(session_id) == handle:
        del ACTIVE_EMBEDDED_RUNS[session_id]
        _notify_embedded_run_ended(session_id)


def _notify_embedded_run_ended(session_id: str) -> None:
    """
    Notify waiters that a run has ended.
    
    Internal helper for clearActiveEmbeddedRun.
    """
    waiters = EMBEDDED_RUN_WAITERS.pop(session_id, set())
    for waiter in waiters:
        if hasattr(waiter, 'set') and callable(waiter.set):
            waiter.set()


def is_embedded_pi_run_active(session_id: str) -> bool:
    """
    Check if session has an active embedded run.
    
    Matches TS isEmbeddedPiRunActive().
    
    Args:
        session_id: Session ID to check
    
    Returns:
        True if active, False otherwise
    """
    return session_id in ACTIVE_EMBEDDED_RUNS


def is_embedded_pi_run_streaming(session_id: str) -> bool:
    """
    Check if session's active run is streaming.
    
    Matches TS isEmbeddedPiRunStreaming().
    
    Args:
        session_id: Session ID to check
    
    Returns:
        True if streaming, False otherwise
    """
    handle = ACTIVE_EMBEDDED_RUNS.get(session_id)
    return handle.is_streaming() if handle else False


def queue_embedded_pi_message(session_id: str, text: str) -> bool:
    """
    Queue a steering message to an active run.
    
    Matches TS queueEmbeddedPiMessage() lines 21-37.
    
    Args:
        session_id: Session ID
        text: Message text to inject
    
    Returns:
        True if message was queued, False if failed
    """
    handle = ACTIVE_EMBEDDED_RUNS.get(session_id)
    
    if not handle:
        # No active run
        return False
    
    if not handle.is_streaming():
        # Not streaming, can't inject
        return False
    
    if handle.is_compacting():
        # Compacting, can't inject
        return False
    
    # Queue the message
    asyncio.create_task(handle.queue_message(text))
    return True


def abort_embedded_pi_run(session_id: str) -> bool:
    """
    Abort an active embedded run.
    
    Args:
        session_id: Session ID to abort
    
    Returns:
        True if run was aborted, False if no active run
    """
    handle = ACTIVE_EMBEDDED_RUNS.get(session_id)
    
    if not handle:
        return False
    
    handle.abort()
    return True


async def wait_for_embedded_run_end(
    session_id: str,
    timeout_seconds: Optional[float] = None,
) -> bool:
    """
    Wait for an embedded run to end.
    
    Args:
        session_id: Session ID to wait for
        timeout_seconds: Optional timeout in seconds
    
    Returns:
        True if run ended, False if timeout
    """
    if not is_embedded_pi_run_active(session_id):
        return True
    
    # Create event for this waiter
    event = asyncio.Event()
    
    # Register waiter
    if session_id not in EMBEDDED_RUN_WAITERS:
        EMBEDDED_RUN_WAITERS[session_id] = set()
    EMBEDDED_RUN_WAITERS[session_id].add(event)
    
    try:
        if timeout_seconds:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
        else:
            await event.wait()
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        # Clean up waiter
        if session_id in EMBEDDED_RUN_WAITERS:
            EMBEDDED_RUN_WAITERS[session_id].discard(event)


__all__ = [
    "EmbeddedPiQueueHandle",
    "ACTIVE_EMBEDDED_RUNS",
    "set_active_embedded_run",
    "clear_active_embedded_run",
    "is_embedded_pi_run_active",
    "is_embedded_pi_run_streaming",
    "queue_embedded_pi_message",
    "abort_embedded_pi_run",
    "wait_for_embedded_run_end",
]
