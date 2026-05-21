"""Preemptive compaction decision module.

Mirrors TypeScript src/agents/run/preemptive-compaction.ts

Determines whether the agent should compact its context before sending
the next prompt, based on current token count vs context limit.
"""
from __future__ import annotations

from typing import Literal

# Compaction decision values — mirrors TS type
CompactionDecision = Literal[
    "fits",
    "compact_only",
    "truncate_tool_results_only",
    "compact_then_truncate",
]

# Thresholds — mirrors TS constants
_COMPACT_THRESHOLD = 0.80       # >80% of context → compact
_TRUNCATE_THRESHOLD = 0.90      # >90% → truncate tool results
_TRUNCATE_ONLY_BAND = 0.85      # 85-90% → try truncate-only first


def should_preemptively_compact(
    current_tokens: int,
    context_limit: int,
) -> CompactionDecision:
    """Determine compaction action needed before the next prompt.

    Mirrors TS shouldPreemptivelyCompactBeforePrompt().

    Returns:
        "fits"                    — no action needed
        "compact_only"            — compact context, no truncation needed yet
        "truncate_tool_results_only" — only truncate tool result texts
        "compact_then_truncate"   — compact AND truncate tool results
    """
    if context_limit <= 0:
        return "fits"

    ratio = current_tokens / context_limit

    if ratio < _COMPACT_THRESHOLD:
        return "fits"

    if ratio < _TRUNCATE_ONLY_BAND:
        return "compact_only"

    if ratio < _TRUNCATE_THRESHOLD:
        return "truncate_tool_results_only"

    return "compact_then_truncate"
