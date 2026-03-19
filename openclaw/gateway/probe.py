"""Gateway probe utilities

Mirrors openclaw/src/gateway/probe.ts
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """Probe result"""
    
    success: bool
    """Whether probe succeeded"""
    
    latency_ms: float | None = None
    """Latency in milliseconds"""
    
    error: str | None = None
    """Error message if failed"""


async def probe_http_endpoint(
    url: str,
    timeout: float = 5.0,
) -> ProbeResult:
    """Probe HTTP endpoint.
    
    Args:
        url: Endpoint URL
        timeout: Timeout in seconds
        
    Returns:
        Probe result
    """
    import aiohttp
    import time
    
    start = time.time()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                latency = (time.time() - start) * 1000
                
                if response.status == 200:
                    return ProbeResult(
                        success=True,
                        latency_ms=latency,
                    )
                else:
                    return ProbeResult(
                        success=False,
                        latency_ms=latency,
                        error=f"HTTP {response.status}",
                    )
    except asyncio.TimeoutError:
        return ProbeResult(
            success=False,
            error="Timeout",
        )
    except Exception as e:
        return ProbeResult(
            success=False,
            error=str(e),
        )


async def probe_websocket_endpoint(
    url: str,
    timeout: float = 5.0,
) -> ProbeResult:
    """Probe WebSocket endpoint.
    
    Args:
        url: WebSocket URL
        timeout: Timeout in seconds
        
    Returns:
        Probe result
    """
    import aiohttp
    import time
    
    start = time.time()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as ws:
                latency = (time.time() - start) * 1000
                await ws.close()
                
                return ProbeResult(
                    success=True,
                    latency_ms=latency,
                )
    except asyncio.TimeoutError:
        return ProbeResult(
            success=False,
            error="Timeout",
        )
    except Exception as e:
        return ProbeResult(
            success=False,
            error=str(e),
        )


__all__ = [
    "ProbeResult",
    "probe_http_endpoint",
    "probe_websocket_endpoint",
]
