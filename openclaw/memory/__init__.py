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
from .batch_ops import batch_embed, run_embedding_batch_groups, run_remote_batch_embeddings
from .batch_poll import poll_batch_completion
from .batch_utils import BatchHttpClientConfig, split_batch_requests, build_batch_headers
from .batch_output import EmbeddingBatchOutputLine, parse_batch_output_jsonl
from .batch_error_utils import extract_batch_error_message, format_unavailable_batch_error
from .batch_status import is_terminal_failure_state, resolve_batch_completion_from_status, poll_batch_until_complete
from .embeddings_remote import RemoteEmbeddingProvider
from .mmr import apply_mmr as mmr_rerank
from .session_files import resolve_session_files
from .post_json import post_json
from .remote_http import RemoteHttpMemoryManager
from .fs_utils import hash_file, resolve_memory_files
from .backend_config import MemoryBackend, MemoryBackendConfig, resolve_memory_backend_config
from .status_format import format_memory_status
from .memory_schema import MemorySearchOptions, MemoryStats
from .events import (
    MemoryRecallRecordedEvent,
    MemoryPromotionAppliedEvent,
    MemoryDreamCompletedEvent,
    append_memory_host_event,
    read_memory_host_events,
    resolve_memory_host_event_log_path,
)
from .multimodal import (
    MemoryMultimodalSettings,
    normalize_memory_multimodal_settings,
    is_memory_multimodal_enabled,
    classify_memory_multimodal_path,
    build_memory_multimodal_label,
    get_memory_multimodal_extensions,
    MEMORY_MULTIMODAL_MODALITIES,
)

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
    "run_embedding_batch_groups",
    "run_remote_batch_embeddings",
    "poll_batch_completion",
    "BatchHttpClientConfig",
    "split_batch_requests",
    "build_batch_headers",
    "EmbeddingBatchOutputLine",
    "parse_batch_output_jsonl",
    "extract_batch_error_message",
    "format_unavailable_batch_error",
    "is_terminal_failure_state",
    "resolve_batch_completion_from_status",
    "poll_batch_until_complete",
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
    # Events
    "MemoryRecallRecordedEvent",
    "MemoryPromotionAppliedEvent",
    "MemoryDreamCompletedEvent",
    "append_memory_host_event",
    "read_memory_host_events",
    "resolve_memory_host_event_log_path",
    # Multimodal
    "MemoryMultimodalSettings",
    "normalize_memory_multimodal_settings",
    "is_memory_multimodal_enabled",
    "classify_memory_multimodal_path",
    "build_memory_multimodal_label",
    "get_memory_multimodal_extensions",
    "MEMORY_MULTIMODAL_MODALITIES",
]
