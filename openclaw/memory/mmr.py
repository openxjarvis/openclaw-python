"""MMR (Maximal Marginal Relevance) re-ranking

Mirrors openclaw/src/memory/mmr.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_mmr(
    results: list[Any],
    query_embedding: list[float],
    lambda_param: float = 0.5,
    top_k: int | None = None,
) -> list[Any]:
    """Apply MMR re-ranking to search results.
    
    Balances relevance and diversity in search results.
    
    Args:
        results: Search results with embeddings
        query_embedding: Query embedding vector
        lambda_param: Balance parameter (0-1, higher = more relevance)
        top_k: Number of results to return
        
    Returns:
        Re-ranked results
    """
    if not results:
        return []
    
    if top_k is None:
        top_k = len(results)
    
    # Extract embeddings
    embeddings = []
    for r in results:
        if hasattr(r, 'embedding'):
            embeddings.append(r.embedding)
        else:
            embeddings.append(None)
    
    # Simple fallback if embeddings not available
    if all(e is None for e in embeddings):
        return results[:top_k]
    
    selected = []
    remaining = list(range(len(results)))
    
    # Select first item (most relevant)
    if remaining:
        selected.append(remaining.pop(0))
    
    # Iteratively select items balancing relevance and diversity
    while remaining and len(selected) < top_k:
        best_score = float('-inf')
        best_idx = None
        
        for idx in remaining:
            if embeddings[idx] is None:
                continue
            
            # Relevance to query
            relevance = _cosine_similarity(query_embedding, embeddings[idx])
            
            # Max similarity to already selected
            max_sim = max(
                _cosine_similarity(embeddings[idx], embeddings[sel])
                for sel in selected
                if embeddings[sel] is not None
            ) if selected else 0.0
            
            # MMR score
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim
            
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx
        
        if best_idx is not None:
            selected.append(best_idx)
            remaining.remove(best_idx)
        else:
            break
    
    return [results[i] for i in selected]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    
    if mag_a == 0 or mag_b == 0:
        return 0.0
    
    return dot / (mag_a * mag_b)


__all__ = ["apply_mmr"]
