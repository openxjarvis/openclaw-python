"""Advanced memory search features.

Provides query expansion, temporal decay, and other advanced search capabilities
to align with TypeScript memory implementation.
"""
from typing import List, Dict, Any
import re
import logging

logger = logging.getLogger(__name__)


def extract_keywords(query: str) -> List[str]:
    """
    Extract keywords from natural language query.
    
    Mirrors TS extractKeywords() from memory search logic.
    Converts conversational queries into search keywords.
    
    Args:
        query: Natural language query
        
    Returns:
        List of extracted keywords
    """
    # Remove common filler words
    filler_words = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during',
        'what', 'where', 'when', 'how', 'why', 'who', 'which', 'is', 'are', 'was',
        'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
        'can', 'could', 'should', 'would', 'may', 'might', 'must', 'shall', 'will',
        'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them',
        'my', 'your', 'his', 'her', 'its', 'our', 'their',
    }
    
    # Split query into words and filter
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in filler_words and len(w) > 2]
    
    # Remove duplicates while preserving order
    seen = set()
    unique_keywords = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)
    
    return unique_keywords


def apply_temporal_decay(
    results: List[Dict[str, Any]],
    decay_factor: float = 0.1,
    current_time_ms: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Apply temporal decay to search results (reduce score for older items).
    
    Mirrors TS applyTemporalDecayToHybridResults().
    
    Args:
        results: Search results with 'score' and 'updated_at' fields
        decay_factor: Decay rate (0-1, higher = faster decay)
        current_time_ms: Current time in milliseconds
        
    Returns:
        Results with adjusted scores
    """
    if not results or decay_factor <= 0:
        return results
    
    if current_time_ms is None:
        import time
        current_time_ms = int(time.time() * 1000)
    
    # Calculate time-based decay for each result
    for result in results:
        updated_at = result.get('updated_at') or result.get('updatedAt')
        if updated_at:
            # Age in days
            age_ms = current_time_ms - updated_at
            age_days = age_ms / (24 * 60 * 60 * 1000)
            
            # Exponential decay: score * exp(-decay_factor * age_days)
            import math
            decay_multiplier = math.exp(-decay_factor * age_days)
            
            # Adjust score
            original_score = result.get('score', 0.0)
            result['score'] = original_score * decay_multiplier
            result['temporal_decay'] = decay_multiplier
    
    return results


def normalize_han_bm25_query(query: str) -> str:
    """
    Normalize Chinese (Han) characters for BM25 search.
    
    Mirrors TS normalizeHanBm25Query() to prevent single-character
    over-matching in Chinese text.
    
    Args:
        query: Search query
        
    Returns:
        Normalized query
    """
    # For Chinese text, wrap single characters in quotes to avoid over-matching
    # This is a simplified version; full implementation would need proper CJK detection
    
    # Pattern for single CJK characters
    cjk_pattern = re.compile(r'[\u4e00-\u9fff]')
    
    tokens = query.split()
    normalized_tokens = []
    
    for token in tokens:
        # If token is a single CJK character, wrap in quotes
        if len(token) == 1 and cjk_pattern.match(token):
            normalized_tokens.append(f'"{token}"')
        else:
            normalized_tokens.append(token)
    
    return ' '.join(normalized_tokens)


# Embedding cache would be implemented in the database layer
# This is a placeholder for the interface

class EmbeddingCache:
    """
    Embedding cache to avoid recomputing embeddings for same text.
    
    Mirrors TS embedding_cache table functionality.
    
    TODO: Full implementation with database-backed cache
    """
    
    def __init__(self, db_connection):
        self.db = db_connection
        self._init_cache_table()
    
    def _init_cache_table(self):
        """Initialize cache table if it doesn't exist."""
        try:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    text_hash TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            self.db.commit()
        except Exception as e:
            logger.debug(f"Failed to init embedding cache table: {e}")
    
    def get(self, text: str, model: str) -> List[float] | None:
        """Get cached embedding for text."""
        import hashlib
        text_hash = hashlib.sha256(f"{text}:{model}".encode()).hexdigest()
        
        try:
            cursor = self.db.execute(
                "SELECT embedding FROM embedding_cache WHERE text_hash = ? AND model = ?",
                (text_hash, model)
            )
            row = cursor.fetchone()
            if row:
                # Deserialize embedding
                import struct
                import pickle
                try:
                    return pickle.loads(row[0])
                except:
                    return None
        except Exception as e:
            logger.debug(f"Failed to get from embedding cache: {e}")
        
        return None
    
    def set(self, text: str, model: str, embedding: List[float]) -> None:
        """Cache embedding for text."""
        import hashlib
        import time
        import pickle
        
        text_hash = hashlib.sha256(f"{text}:{model}".encode()).hexdigest()
        embedding_blob = pickle.dumps(embedding)
        current_ms = int(time.time() * 1000)
        
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO embedding_cache (text_hash, model, embedding, created_at) VALUES (?, ?, ?, ?)",
                (text_hash, model, embedding_blob, current_ms)
            )
            self.db.commit()
        except Exception as e:
            logger.debug(f"Failed to set embedding cache: {e}")


__all__ = [
    'extract_keywords',
    'apply_temporal_decay',
    'normalize_han_bm25_query',
    'EmbeddingCache',
]
