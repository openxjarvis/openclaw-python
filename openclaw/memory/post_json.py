"""POST JSON utilities for memory

Mirrors openclaw/src/memory/post-json.ts
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def post_json(
    url: str,
    data: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """POST JSON data to URL.
    
    Args:
        url: Target URL
        data: JSON data to post
        headers: Optional HTTP headers
        timeout: Timeout in seconds
        
    Returns:
        Response JSON
    """
    import aiohttp
    
    headers = headers or {}
    headers.setdefault("Content-Type", "application/json")
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            return await response.json()


__all__ = ["post_json"]
