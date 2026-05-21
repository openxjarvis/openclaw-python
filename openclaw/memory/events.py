"""Memory host event log.

Mirrors openclaw/src/memory-host-sdk/events.ts.

Provides structured event logging for memory operations:
- memory.recall.recorded  — search was performed and results recorded
- memory.promotion.applied — high-recall chunks promoted / boosted
- memory.dream.completed  — dreaming phase finished writing a report
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Union

logger = logging.getLogger(__name__)

MEMORY_HOST_EVENT_LOG_RELATIVE_PATH = "memory/.dreams/events.jsonl"


# ---------------------------------------------------------------------------
# Event types — mirrors TS MemoryHostEvent union
# ---------------------------------------------------------------------------

@dataclass
class MemoryRecallResultEntry:
    path: str
    start_line: int
    end_line: int
    score: float


@dataclass
class MemoryRecallRecordedEvent:
    """Emitted when a memory recall (search) is completed."""

    type: Literal["memory.recall.recorded"] = "memory.recall.recorded"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    query: str = ""
    result_count: int = 0
    results: list[MemoryRecallResultEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "query": self.query,
            "resultCount": self.result_count,
            "results": [
                {
                    "path": r.path,
                    "startLine": r.start_line,
                    "endLine": r.end_line,
                    "score": r.score,
                }
                for r in self.results
            ],
        }


@dataclass
class MemoryPromotionCandidate:
    key: str
    path: str
    start_line: int
    end_line: int
    score: float
    recall_count: int


@dataclass
class MemoryPromotionAppliedEvent:
    """Emitted when recall-based promotion is applied to memory chunks."""

    type: Literal["memory.promotion.applied"] = "memory.promotion.applied"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    memory_path: str = ""
    applied: int = 0
    candidates: list[MemoryPromotionCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
            "memoryPath": self.memory_path,
            "applied": self.applied,
            "candidates": [
                {
                    "key": c.key,
                    "path": c.path,
                    "startLine": c.start_line,
                    "endLine": c.end_line,
                    "score": c.score,
                    "recallCount": c.recall_count,
                }
                for c in self.candidates
            ],
        }


@dataclass
class MemoryDreamCompletedEvent:
    """Emitted when a dreaming phase finishes writing its output."""

    type: Literal["memory.dream.completed"] = "memory.dream.completed"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    phase: str = ""
    inline_path: str | None = None
    report_path: str | None = None
    line_count: int = 0
    storage_mode: Literal["inline", "separate", "both"] = "inline"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "lineCount": self.line_count,
            "storageMode": self.storage_mode,
        }
        if self.inline_path is not None:
            d["inlinePath"] = self.inline_path
        if self.report_path is not None:
            d["reportPath"] = self.report_path
        return d


MemoryHostEvent = Union[
    MemoryRecallRecordedEvent,
    MemoryPromotionAppliedEvent,
    MemoryDreamCompletedEvent,
]


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def resolve_memory_host_event_log_path(workspace_dir: str | Path) -> Path:
    """Return the absolute path to the memory event log JSONL file."""
    return Path(workspace_dir) / MEMORY_HOST_EVENT_LOG_RELATIVE_PATH


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def append_memory_host_event(workspace_dir: str | Path, event: MemoryHostEvent) -> None:
    """Append a memory host event to the event log.

    Matches TS ``appendMemoryHostEvent()``.
    """
    log_path = resolve_memory_host_event_log_path(workspace_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload: dict[str, Any]
        if hasattr(event, "to_dict"):
            payload = event.to_dict()  # type: ignore[union-attr]
        else:
            payload = vars(event)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception as exc:
        logger.warning(f"Failed to append memory host event: {exc}")


def read_memory_host_events(
    workspace_dir: str | Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Read memory host events from the event log.

    Matches TS ``readMemoryHostEvents()``.

    Args:
        workspace_dir: Workspace directory containing the memory subdirectory.
        limit: If set, return only the last *limit* events.

    Returns:
        List of raw event dicts (parsed from JSONL).
    """
    log_path = resolve_memory_host_event_log_path(workspace_dir)
    if not log_path.exists():
        return []
    try:
        raw = log_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning(f"Failed to read memory host events: {exc}")
        return []
    if not raw:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if limit is None or not isinstance(limit, int) or limit < 0:
        return events
    if limit == 0:
        return []
    return events[-limit:]
