"""Mistral embedding provider

Mirrors openclaw/src/memory/embeddings-mistral.ts
"""
from __future__ import annotations

import httpx
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import EmbeddingProvider

DEFAULT_MISTRAL_EMBEDDING_MODEL = "mistral-embed"
DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"


def normalize_mistral_model(model: str) -> str:
    """Normalize Mistral model name"""
    trimmed = model.strip()
    if not trimmed:
        return DEFAULT_MISTRAL_EMBEDDING_MODEL
    if trimmed.startswith("mistral/"):
        return trimmed[len("mistral/"):]
    return trimmed


class MistralEmbeddingProvider:
    """Mistral embedding provider
    
    Mirrors TypeScript createMistralEmbeddingProvider()
    
    Args:
        model: Model name (default: mistral-embed)
        base_url: Mistral API base URL (default: https://api.mistral.ai/v1)
        api_key: Mistral API key
    """
    
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.id = "mistral"
        self.model = normalize_mistral_model(model or DEFAULT_MISTRAL_EMBEDDING_MODEL)
        self.base_url = (base_url or DEFAULT_MISTRAL_BASE_URL).rstrip("/")
        
        # Build headers
        self.headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        
        self.embed_url = f"{self.base_url}/embeddings"
    
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query text
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
        """
        result = await self.embed_batch([text])
        return result[0]
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts
        
        Mistral API supports batch embedding via /v1/embeddings endpoint
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.embed_url,
                headers=self.headers,
                json={
                    "model": self.model,
                    "input": texts,
                },
                timeout=30.0,
            )
            
            if not response.is_success:
                raise RuntimeError(
                    f"Mistral embeddings HTTP {response.status_code}: {response.text}"
                )
            
            data = response.json()
            embeddings_data = data.get("data", [])
            
            if not isinstance(embeddings_data, list):
                raise RuntimeError("Mistral embeddings response missing data[]")
            
            # Extract embeddings in order
            embeddings = []
            for item in sorted(embeddings_data, key=lambda x: x.get("index", 0)):
                embedding = item.get("embedding")
                if not isinstance(embedding, list):
                    raise RuntimeError("Mistral embeddings item missing embedding[]")
                embeddings.append(embedding)
            
            return embeddings


__all__ = [
    "MistralEmbeddingProvider",
    "DEFAULT_MISTRAL_EMBEDDING_MODEL",
    "DEFAULT_MISTRAL_BASE_URL",
]
