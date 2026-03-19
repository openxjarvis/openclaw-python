"""Memory schema definitions

Mirrors openclaw/src/memory/memory-schema.ts
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MemorySource = Literal["memory", "sessions"]


@dataclass
class MemorySearchOptions:
    """Memory search options"""
    
    max_results: int = 6
    """Maximum number of results"""
    
    sources: list[MemorySource] | None = None
    """Source filters"""
    
    vector_weight: float = 0.7
    """Vector search weight in hybrid search"""


@dataclass
class MemoryStats:
    """Memory index statistics"""
    
    total_files: int = 0
    """Total indexed files"""
    
    total_chunks: int = 0
    """Total indexed chunks"""
    
    indexed_bytes: int = 0
    """Total indexed bytes"""
    
    files_added: int = 0
    """Files added in last sync"""
    
    files_updated: int = 0
    """Files updated in last sync"""
    
    files_removed: int = 0
    """Files removed in last sync"""
    
    chunks_created: int = 0
    """Chunks created in last sync"""


__all__ = [
    "MemorySource",
    "MemorySearchOptions",
    "MemoryStats",
]
