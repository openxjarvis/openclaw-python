"""Remote embedding provider

Mirrors openclaw/src/memory/embeddings-remote.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RemoteEmbeddingProvider:
    """Remote embedding provider via HTTP.
    
    Allows using remote embedding services.
    """
    
    def __init__(self, endpoint: str, api_key: str | None = None):
        self.endpoint = endpoint
        self.api_key = api_key
    
    async def embed_text(self, text: str) -> list[float]:
        """Embed single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        import aiohttp
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.endpoint,
                json={"text": text},
                headers=headers,
            ) as response:
                result = await response.json()
                return result.get("embedding", [])
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed batch of texts.
        
        Args:
            texts: Texts to embed
            
        Returns:
            List of embedding vectors
        """
        import aiohttp
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.endpoint,
                json={"texts": texts},
                headers=headers,
            ) as response:
                result = await response.json()
                return result.get("embeddings", [])


__all__ = ["RemoteEmbeddingProvider"]
