"""Channel adapters

Mirrors openclaw/src/channels/plugins/adapters.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Awaitable, Protocol

logger = logging.getLogger(__name__)


class ChannelAdapter(Protocol):
    """Channel adapter protocol"""
    
    async def send_message(self, message: dict[str, Any]) -> bool:
        """Send message through channel"""
        ...
    
    async def receive_message(self) -> dict[str, Any] | None:
        """Receive message from channel"""
        ...


@dataclass
class AdapterRegistration:
    """Adapter registration"""
    
    channel_id: str
    """Channel identifier"""
    
    adapter: ChannelAdapter
    """Adapter instance"""
    
    metadata: dict[str, Any] | None = None
    """Optional metadata"""


class ChannelAdapterRegistry:
    """Registry for channel adapters."""
    
    def __init__(self):
        self.adapters: dict[str, AdapterRegistration] = {}
    
    def register_adapter(
        self,
        channel_id: str,
        adapter: ChannelAdapter,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register channel adapter.
        
        Args:
            channel_id: Channel identifier
            adapter: Adapter instance
            metadata: Optional metadata
        """
        registration = AdapterRegistration(
            channel_id=channel_id,
            adapter=adapter,
            metadata=metadata,
        )
        self.adapters[channel_id] = registration
        logger.info(f"Registered adapter for channel: {channel_id}")
    
    def get_adapter(self, channel_id: str) -> ChannelAdapter | None:
        """Get adapter for channel.
        
        Args:
            channel_id: Channel identifier
            
        Returns:
            Adapter or None
        """
        registration = self.adapters.get(channel_id)
        return registration.adapter if registration else None
    
    def unregister_adapter(self, channel_id: str) -> None:
        """Unregister adapter.
        
        Args:
            channel_id: Channel identifier
        """
        if channel_id in self.adapters:
            del self.adapters[channel_id]
            logger.info(f"Unregistered adapter for channel: {channel_id}")
    
    def list_adapters(self) -> list[str]:
        """List registered channel IDs.
        
        Returns:
            List of channel IDs
        """
        return list(self.adapters.keys())


__all__ = [
    "ChannelAdapter",
    "AdapterRegistration",
    "ChannelAdapterRegistry",
]
