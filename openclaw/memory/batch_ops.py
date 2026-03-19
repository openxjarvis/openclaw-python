"""Batch operations for memory

Mirrors openclaw/src/memory/batch-ops.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def batch_embed(
    texts: list[str],
    embedder: Any,
    batch_size: int = 100,
) -> list[list[float] | None]:
    """Batch embed texts with chunking.
    
    Args:
        texts: Texts to embed
        embedder: Embedding provider
        batch_size: Batch size for embedding
        
    Returns:
        List of embeddings (None for failed items)
    """
    results: list[list[float] | None] = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        
        try:
            batch_result = await embedder.embed_batch(batch)
            
            if hasattr(batch_result, 'embeddings'):
                results.extend(batch_result.embeddings)
            else:
                results.extend(batch_result)
        except Exception as e:
            logger.warning(f"Batch embedding failed for batch {i//batch_size}: {e}")
            results.extend([None] * len(batch))
    
    return results


__all__ = ["batch_embed"]
