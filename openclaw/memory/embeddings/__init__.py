"""Embedding providers for memory vector search.

Available providers (matches TypeScript memory/embeddings/):
- OpenAI    – text-embedding-3-small/large (default)
- Gemini    – embedding-001
- Voyage    – voyage-3 / voyage-code-3 (best for code retrieval)
- Ollama    – nomic-embed-text (local)
- Mistral   – mistral-embed
- Local     – sentence-transformers (offline)
"""
from __future__ import annotations

from .base import EmbeddingProvider, EmbeddingBatch
from .openai_provider import OpenAIEmbeddingProvider
from .gemini_provider import GeminiEmbeddingProvider
from .local_provider import LocalEmbeddingProvider
from .voyage_provider import VoyageEmbeddingProvider
from .ollama_provider import OllamaEmbeddingProvider
from .mistral_provider import MistralEmbeddingProvider


def create_embedding_provider(
    provider_name: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
) -> EmbeddingProvider:
    """
    Factory for embedding providers.

    Matches TypeScript createEmbeddingProvider().

    Args:
        provider_name: "openai" | "gemini" | "voyage" | "ollama" | "mistral" | "local"
        model: Model override (provider-specific defaults used if None).
        api_key: API key override.

    Returns:
        An EmbeddingProvider instance.
    """
    name = provider_name.lower()

    if name in ("openai",):
        return OpenAIEmbeddingProvider(
            model=model or "text-embedding-3-small",
            api_key=api_key,
        )

    if name in ("gemini", "google"):
        return GeminiEmbeddingProvider(
            model=model or "embedding-001",
            api_key=api_key,
        )

    if name in ("voyage", "voyageai"):
        return VoyageEmbeddingProvider(
            model=model or "voyage-3",
            api_key=api_key,
        )

    if name in ("ollama",):
        return OllamaEmbeddingProvider(
            model=model or "nomic-embed-text",
            api_key=api_key,
        )

    if name in ("mistral",):
        return MistralEmbeddingProvider(
            model=model or "mistral-embed",
            api_key=api_key,
        )

    if name in ("local", "sentence-transformers"):
        return LocalEmbeddingProvider(model=model or "all-MiniLM-L6-v2")

    raise ValueError(
        f"Unknown embedding provider: {provider_name!r}. "
        f"Choose from: openai, gemini, voyage, ollama, mistral, local"
    )


__all__ = [
    "EmbeddingProvider",
    "EmbeddingBatch",
    "OpenAIEmbeddingProvider",
    "GeminiEmbeddingProvider",
    "LocalEmbeddingProvider",
    "VoyageEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "MistralEmbeddingProvider",
    "create_embedding_provider",
]
