"""
Advanced context compaction strategies
"""

import logging
from typing import Any

from .analyzer import TokenAnalyzer
from .strategy import CompactionManager, CompactionStrategy
from .functions import (
    BASE_CHUNK_RATIO,
    MIN_CHUNK_RATIO,
    SAFETY_MARGIN,
    estimate_messages_tokens,
    split_messages_by_token_share,
    chunk_messages_by_max_tokens,
    compute_adaptive_chunk_ratio,
    is_oversized_for_summary,
    summarize_with_fallback,
    summarize_in_stages,
    prune_history_for_context_share,
    resolve_context_window_tokens,
)

_log = logging.getLogger(__name__)


async def compact_session(
    session_key: str,
    *,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Compact a session's conversation history.

    This is a stub — full implementation is pending.
    Callers (e.g. /compact command) import this symbol from the package.
    """
    _log.warning(
        "compact_session called but not yet implemented (session_key=%s)",
        session_key,
    )
    return {"tokens_before": 0, "tokens_after": 0, "skipped": True}


__all__ = [
    "TokenAnalyzer",
    "CompactionManager",
    "CompactionStrategy",
    "compact_session",
    # TS-aligned functional API
    "BASE_CHUNK_RATIO",
    "MIN_CHUNK_RATIO",
    "SAFETY_MARGIN",
    "estimate_messages_tokens",
    "split_messages_by_token_share",
    "chunk_messages_by_max_tokens",
    "compute_adaptive_chunk_ratio",
    "is_oversized_for_summary",
    "summarize_with_fallback",
    "summarize_in_stages",
    "prune_history_for_context_share",
    "resolve_context_window_tokens",
]
