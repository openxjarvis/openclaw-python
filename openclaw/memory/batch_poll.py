"""Batch polling for memory operations

Mirrors openclaw/src/memory/batch-poll.ts
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def poll_batch_completion(
    check_fn: Callable[[], bool],
    timeout_ms: int = 30000,
    interval_ms: int = 100,
) -> bool:
    """Poll for batch operation completion.
    
    Args:
        check_fn: Function to check if operation is complete
        timeout_ms: Timeout in milliseconds
        interval_ms: Polling interval in milliseconds
        
    Returns:
        True if completed, False if timed out
    """
    timeout = timeout_ms / 1000
    interval = interval_ms / 1000
    
    start = asyncio.get_event_loop().time()
    
    while (asyncio.get_event_loop().time() - start) < timeout:
        if check_fn():
            return True
        
        await asyncio.sleep(interval)
    
    return False


__all__ = ["poll_batch_completion"]
