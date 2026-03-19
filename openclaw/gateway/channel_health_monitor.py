"""Channel health monitoring

Mirrors openclaw/src/gateway/channel-health-monitor.ts
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChannelHealthStatus:
    """Channel health status"""
    
    channel_id: str
    """Channel identifier"""
    
    healthy: bool
    """Whether channel is healthy"""
    
    last_check_ms: int
    """Last health check timestamp (ms)"""
    
    error_count: int = 0
    """Error count"""
    
    last_error: str | None = None
    """Last error message"""


class ChannelHealthMonitor:
    """Monitor channel health.
    
    Tracks channel connectivity and errors.
    """
    
    def __init__(self, check_interval_ms: int = 30000):
        self.check_interval_ms = check_interval_ms
        self.statuses: dict[str, ChannelHealthStatus] = {}
        self._running = False
        self._task: asyncio.Task | None = None
    
    async def start(self) -> None:
        """Start health monitoring."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Channel health monitor started")
    
    async def stop(self) -> None:
        """Stop health monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Channel health monitor stopped")
    
    async def _monitor_loop(self) -> None:
        """Health monitoring loop."""
        while self._running:
            try:
                await self.check_all_channels()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            
            await asyncio.sleep(self.check_interval_ms / 1000)
    
    async def check_all_channels(self) -> None:
        """Check health of all channels."""
        # Placeholder - would check actual channels
        pass
    
    def record_error(self, channel_id: str, error: str) -> None:
        """Record channel error.
        
        Args:
            channel_id: Channel identifier
            error: Error message
        """
        if channel_id not in self.statuses:
            self.statuses[channel_id] = ChannelHealthStatus(
                channel_id=channel_id,
                healthy=False,
                last_check_ms=int(time.time() * 1000),
            )
        
        status = self.statuses[channel_id]
        status.error_count += 1
        status.last_error = error
        status.healthy = False
        status.last_check_ms = int(time.time() * 1000)
    
    def record_success(self, channel_id: str) -> None:
        """Record channel success.
        
        Args:
            channel_id: Channel identifier
        """
        if channel_id not in self.statuses:
            self.statuses[channel_id] = ChannelHealthStatus(
                channel_id=channel_id,
                healthy=True,
                last_check_ms=int(time.time() * 1000),
            )
        else:
            status = self.statuses[channel_id]
            status.healthy = True
            status.error_count = 0
            status.last_error = None
            status.last_check_ms = int(time.time() * 1000)


__all__ = ["ChannelHealthMonitor", "ChannelHealthStatus"]
