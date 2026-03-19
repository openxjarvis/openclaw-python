"""Ollama embedding provider

Mirrors openclaw/src/memory/embeddings-ollama.ts
"""
from __future__ import annotations

import math
import httpx
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import EmbeddingProvider

DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"


def sanitize_and_normalize_embedding(vec: list[float]) -> list[float]:
    """Sanitize and L2-normalize embedding vector"""
    # Replace non-finite values with 0
    sanitized = [v if math.isfinite(v) else 0.0 for v in vec]
    
    # L2 normalization
    magnitude = math.sqrt(sum(v * v for v in sanitized))
    if magnitude < 1e-10:
        return sanitized
    
    return [v / magnitude for v in sanitized]


def normalize_ollama_model(model: str) -> str:
    """Normalize Ollama model name"""
    trimmed = model.strip()
    if not trimmed:
        return DEFAULT_OLLAMA_EMBEDDING_MODEL
    if trimmed.startswith("ollama/"):
        return trimmed[len("ollama/"):]
    return trimmed


def resolve_ollama_api_base(configured_base_url: str | None = None) -> str:
    """Resolve Ollama API base URL"""
    if not configured_base_url:
        return DEFAULT_OLLAMA_BASE_URL
    
    trimmed = configured_base_url.rstrip("/")
    # Remove /v1 suffix if present
    if trimmed.lower().endswith("/v1"):
        return trimmed[:-3]
    return trimmed


class OllamaEmbeddingProvider:
    """Ollama embedding provider
    
    Mirrors TypeScript createOllamaEmbeddingProvider()
    
    Args:
        model: Model name (default: nomic-embed-text)
        base_url: Ollama API base URL (default: http://127.0.0.1:11434)
        api_key: Optional API key
    """
    
    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        self.id = "ollama"
        self.model = normalize_ollama_model(model or DEFAULT_OLLAMA_EMBEDDING_MODEL)
        self.base_url = resolve_ollama_api_base(base_url)
        
        # Build headers
        self.headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        
        self.embed_url = f"{self.base_url.rstrip('/')}/api/embeddings"
    
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query text
        
        Args:
            text: Text to embed
            
        Returns:
            Normalized embedding vector
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.embed_url,
                headers=self.headers,
                json={"model": self.model, "prompt": text},
                timeout=30.0,
            )
            
            if not response.is_success:
                raise RuntimeError(
                    f"Ollama embeddings HTTP {response.status_code}: {response.text}"
                )
            
            data = response.json()
            embedding = data.get("embedding")
            
            if not isinstance(embedding, list):
                raise RuntimeError("Ollama embeddings response missing embedding[]")
            
            return sanitize_and_normalize_embedding(embedding)
    
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts
        
        Ollama /api/embeddings accepts one prompt per request,
        so we process them in parallel.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        import asyncio
        
        # Process all texts in parallel
        tasks = [self.embed_query(text) for text in texts]
        return await asyncio.gather(*tasks)


__all__ = [
    "OllamaEmbeddingProvider",
    "DEFAULT_OLLAMA_EMBEDDING_MODEL",
    "DEFAULT_OLLAMA_BASE_URL",
]
