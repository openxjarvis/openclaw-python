"""OpenResponses HTTP utilities

Mirrors openclaw/src/gateway/openresponses-http.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def send_openresponse_http(
    url: str,
    data: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Send OpenResponse via HTTP.
    
    Args:
        url: Target URL
        data: Response data
        headers: Optional HTTP headers
        timeout: Timeout in seconds
        
    Returns:
        Response result
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
            return {
                "status": response.status,
                "data": await response.json() if response.status == 200 else None,
            }


__all__ = ["send_openresponse_http"]
