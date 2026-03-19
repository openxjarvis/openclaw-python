"""Remote HTTP memory backend

Mirrors openclaw/src/memory/remote-http.ts
"""
from __future__ import annotations

import logging
from typing import Any

from .post_json import post_json

logger = logging.getLogger(__name__)


class RemoteHttpMemoryManager:
    """Remote HTTP memory backend.
    
    Delegates memory operations to a remote HTTP service.
    """
    
    def __init__(self, endpoint: str, api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key
    
    async def search(
        self,
        query: str,
        opts: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search via remote HTTP endpoint.
        
        Args:
            query: Search query
            opts: Search options
            
        Returns:
            Search results
        """
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        data = {
            "query": query,
            "options": opts or {},
        }
        
        result = await post_json(self.endpoint + "/search", data, headers)
        return result.get("results", [])


__all__ = ["RemoteHttpMemoryManager"]
