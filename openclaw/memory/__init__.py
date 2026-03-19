"""Memory management system"""
from .types import (
    MemorySource,
    MemorySearchResult,
    MemoryProviderStatus,
    MemoryEmbeddingProbeResult,
    MemorySearchManager,
)
from .embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from .builtin_manager import BuiltinMemoryManager
from .hybrid import apply_mmr, merge_hybrid_results, normalize_scores
from .chunking import chunk_file_by_tokens, chunk_text_by_tokens, DEFAULT_CHUNK_TOKENS, DEFAULT_CHUNK_OVERLAP
from .advanced_search import extract_keywords, apply_temporal_decay, normalize_han_bm25_query, EmbeddingCache
from .qmd_manager import QmdMemoryManager, QmdCollection, create_qmd_memory_manager
from .sqlite_utils import ensure_parent_dir, connect_sqlite, vacuum_sqlite
from .batch_ops import batch_embed
from .batch_poll import poll_batch_completion
from .embeddings_remote import RemoteEmbeddingProvider
from .mmr import apply_mmr as mmr_rerank
from .session_files import resolve_session_files
from .post_json import post_json
from .remote_http import RemoteHttpMemoryManager
from .fs_utils import hash_file, resolve_memory_files
from .backend_config import MemoryBackend, MemoryBackendConfig, resolve_memory_backend_config
from .status_format import format_memory_status
from .memory_schema import MemorySearchOptions, MemoryStats

__all__ = [
    # Core types
    "MemorySource",
    "MemorySearchResult",
    "MemoryProviderStatus",
    "MemoryEmbeddingProbeResult",
    "MemorySearchManager",
    # Embeddings
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "RemoteEmbeddingProvider",
    # Managers
    "BuiltinMemoryManager",
    "QmdMemoryManager",
    "RemoteHttpMemoryManager",
    # Search and hybrid
    "apply_mmr",
    "mmr_rerank",
    "merge_hybrid_results",
    "normalize_scores",
    # Chunking
    "chunk_file_by_tokens",
    "chunk_text_by_tokens",
    "DEFAULT_CHUNK_TOKENS",
    "DEFAULT_CHUNK_OVERLAP",
    # Advanced search
    "extract_keywords",
    "apply_temporal_decay",
    "normalize_han_bm25_query",
    "EmbeddingCache",
    # QMD
    "QmdCollection",
    "create_qmd_memory_manager",
    # SQLite utils
    "ensure_parent_dir",
    "connect_sqlite",
    "vacuum_sqlite",
    # Batch operations
    "batch_embed",
    "poll_batch_completion",
    # Session and files
    "resolve_session_files",
    "post_json",
    "hash_file",
    "resolve_memory_files",
    # Configuration
    "MemoryBackend",
    "MemoryBackendConfig",
    "resolve_memory_backend_config",
    # Status and schema
    "format_memory_status",
    "MemorySearchOptions",
    "MemoryStats",
]
