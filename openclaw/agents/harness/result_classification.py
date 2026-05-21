"""Harness result classification.

Mirrors TypeScript src/agents/harness/result-classification.ts

Classifies agent run results to detect degenerate outputs:
- empty: no meaningful content
- reasoning-only: only thinking/reasoning blocks, no actual reply
- planning-only: only planning text, no actionable output
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import AgentHarnessResult, HarnessResultClassification


def classify_harness_result(result: "AgentHarnessResult") -> "HarnessResultClassification | None":
    """Classify a harness result.

    Returns None if the result is normal/valid.
    Returns a classification string for degenerate results.

    Mirrors TS result-classification.ts classify().
    """
    if result.error:
        return None

    content = result.content or ""
    tool_calls = result.tool_calls or []

    # Has tool calls → not degenerate
    if tool_calls:
        return None

    stripped = content.strip()

    # Empty result
    if not stripped:
        return "empty"

    # Check if result is reasoning-only (wrapped in think tags or similar)
    if _is_reasoning_only(stripped):
        return "reasoning-only"

    # Check if result is planning-only (step lists without actual output)
    if _is_planning_only(stripped):
        return "planning-only"

    return None


def _is_reasoning_only(content: str) -> bool:
    """Detect reasoning-only content (thinking blocks without response)."""
    lower = content.lower()
    # Common patterns for reasoning-only output
    think_markers = ["<think>", "<thinking>", "[thinking]", "## thinking"]
    has_think = any(m in lower for m in think_markers)
    if not has_think:
        return False
    # If it also has content outside thinking blocks, not reasoning-only
    # Simple heuristic: strip think blocks and check if anything remains
    import re
    cleaned = re.sub(r"<think(?:ing)?>[^<]*</think(?:ing)?>", "", content, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"\[thinking\][^\[]*\[/thinking\]", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return len(cleaned.strip()) < 20  # virtually nothing left


def _is_planning_only(content: str) -> bool:
    """Detect planning-only content without actual completion."""
    lower = content.lower()
    # Very rough heuristic: starts with "I will" / "I'll" / "My plan" without any actual result
    planning_prefixes = ["i will ", "i'll ", "my plan:", "here's my plan", "step 1:", "1. "]
    if not any(lower.startswith(p) for p in planning_prefixes):
        return False
    # If short and no tool calls, likely planning-only
    return len(content) < 300
