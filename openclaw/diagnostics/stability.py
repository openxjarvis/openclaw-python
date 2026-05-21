"""Diagnostic stability ring buffer (mirrors openclaw/src/logging/diagnostic-stability.ts)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY = 1000
DEFAULT_DIAGNOSTIC_STABILITY_LIMIT = 50
MAX_DIAGNOSTIC_STABILITY_LIMIT = DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY

SAFE_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")

DiagnosticStabilityEventRecord = dict[str, Any]
DiagnosticStabilitySnapshot = dict[str, Any]


@dataclass
class _StabilityState:
    records: list[DiagnosticStabilityEventRecord | None] = field(default_factory=list)
    capacity: int = DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY
    next_index: int = 0
    count: int = 0
    dropped: int = 0
    unsubscribe: Callable[[], None] | None = None


_state: _StabilityState | None = None


def _get_state() -> _StabilityState:
    global _state
    if _state is None:
        _state = _StabilityState(
            records=[None] * DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY,
            capacity=DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY,
        )
    return _state


def _copy_reason_code(reason: Any) -> str | None:
    if not isinstance(reason, str) or not SAFE_REASON_CODE.match(reason):
        return None
    return reason


def _assign_reason_code(record: dict[str, Any], reason: Any) -> None:
    code = _copy_reason_code(reason)
    if code:
        record["reason"] = code


def sanitize_diagnostic_event(event: dict[str, Any]) -> DiagnosticStabilityEventRecord:
    """Strip diagnostic events to stability-safe records."""
    record: dict[str, Any] = {
        "seq": event.get("seq"),
        "ts": event.get("ts"),
        "type": event.get("type"),
    }
    event_type = event.get("type")

    if event_type == "model.usage":
        record["channel"] = event.get("channel")
        record["provider"] = event.get("provider")
        record["model"] = event.get("model")
        if isinstance(event.get("usage"), dict):
            record["usage"] = dict(event["usage"])
        if isinstance(event.get("context"), dict):
            record["context"] = dict(event["context"])
        record["costUsd"] = event.get("costUsd", event.get("cost_usd"))
        record["durationMs"] = event.get("durationMs", event.get("duration_ms"))
    elif event_type in ("webhook.received", "webhook.processed", "webhook.error"):
        record["channel"] = event.get("channel")
        if event_type != "webhook.received":
            record["durationMs"] = event.get("durationMs", event.get("duration_ms"))
    elif event_type == "message.queued":
        record["channel"] = event.get("channel")
        record["source"] = event.get("source", event.get("session_key"))
        record["queueDepth"] = event.get("queueDepth", event.get("queue_depth"))
    elif event_type == "message.processed":
        record["channel"] = event.get("channel")
        record["durationMs"] = event.get("durationMs", event.get("duration_ms"))
        record["outcome"] = event.get("outcome")
        _assign_reason_code(record, event.get("reason"))
    elif event_type == "session.state_change":
        record["outcome"] = event.get("new_state", event.get("newState"))
        _assign_reason_code(record, event.get("reason"))
    elif event_type == "session.stuck":
        record["outcome"] = "stuck"
        record["ageMs"] = event.get("ageMs", event.get("duration_sec"))
    elif event_type in ("lane.enqueue", "lane.dequeue", "queue.lane.enqueue", "queue.lane.dequeue"):
        record["source"] = event.get("lane", event.get("source"))
        record["queueSize"] = event.get("queueSize", event.get("queue_size"))
        if event_type.endswith("dequeue"):
            record["waitMs"] = event.get("waitMs", event.get("wait_ms"))
    elif event_type == "run.attempt":
        record["count"] = event.get("count", event.get("attempt"))
        record["model"] = event.get("model")
    elif event_type == "heartbeat":
        stats = event.get("stats")
        if isinstance(stats, dict):
            record["webhooks"] = {
                "received": stats.get("webhooks_received", 0),
                "processed": stats.get("webhooks_processed", 0),
                "errors": stats.get("webhooks_errors", 0),
            }
            record["active"] = stats.get("active_runs")
    elif event_type == "runs.active":
        record["count"] = event.get("count")
    elif event_type == "harness.run.completed":
        record["source"] = event.get("harnessId", event.get("source"))
        record["durationMs"] = event.get("durationMs", event.get("duration_ms"))
        record["outcome"] = event.get("outcome")
    elif event_type == "harness.run.error":
        record["source"] = event.get("harnessId", event.get("source"))
        record["outcome"] = "error"
        _assign_reason_code(record, event.get("errorCategory", event.get("error_category")))
    elif event_type == "log.record":
        record["level"] = event.get("level")
        record["source"] = event.get("loggerName", event.get("source"))
    elif event_type == "diagnostic.memory.pressure":
        record["level"] = event.get("level")
        _assign_reason_code(record, event.get("reason"))
        if isinstance(event.get("memory"), dict):
            record["memory"] = dict(event["memory"])
    elif event_type == "payload.large":
        record["surface"] = event.get("surface")
        record["action"] = event.get("action")
        record["bytes"] = event.get("bytes")
        record["limitBytes"] = event.get("limitBytes", event.get("limit_bytes"))
        _assign_reason_code(record, event.get("reason"))
    else:
        for key in (
            "channel",
            "provider",
            "model",
            "durationMs",
            "duration_ms",
            "toolName",
            "tool_name",
            "outcome",
            "source",
            "target",
            "count",
            "bytes",
        ):
            if key in event and event[key] is not None:
                camel = key
                if key == "duration_ms":
                    camel = "durationMs"
                elif key == "tool_name":
                    camel = "toolName"
                record[camel] = event[key]

    return record


def _append_record(record: DiagnosticStabilityEventRecord) -> None:
    state = _get_state()
    state.records[state.next_index] = record
    state.next_index = (state.next_index + 1) % state.capacity
    if state.count < state.capacity:
        state.count += 1
        return
    state.dropped += 1


def _list_records() -> list[DiagnosticStabilityEventRecord]:
    state = _get_state()
    if state.count == 0:
        return []
    if state.count < state.capacity:
        return [r for r in state.records[: state.count] if r is not None]
    return [r for r in state.records[state.next_index :] + state.records[: state.next_index] if r is not None]


def _summarize_records(records: list[DiagnosticStabilityEventRecord]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    latest_memory: dict[str, Any] | None = None
    max_rss: int | None = None
    max_heap: int | None = None
    pressure_count = 0
    payload_large: dict[str, Any] = {
        "count": 0,
        "rejected": 0,
        "truncated": 0,
        "chunked": 0,
        "bySurface": {},
    }

    for record in records:
        event_type = str(record.get("type") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        memory = record.get("memory")
        if isinstance(memory, dict):
            latest_memory = memory
            rss = memory.get("rssBytes")
            heap = memory.get("heapUsedBytes")
            if isinstance(rss, int):
                max_rss = rss if max_rss is None else max(max_rss, rss)
            if isinstance(heap, int):
                max_heap = heap if max_heap is None else max(max_heap, heap)
        if event_type == "diagnostic.memory.pressure":
            pressure_count += 1
        if event_type == "payload.large":
            payload_large["count"] += 1
            action = record.get("action")
            if action == "rejected":
                payload_large["rejected"] += 1
            elif action == "truncated":
                payload_large["truncated"] += 1
            elif action == "chunked":
                payload_large["chunked"] += 1
            surface = str(record.get("surface") or "unknown")
            by_surface = payload_large["bySurface"]
            by_surface[surface] = by_surface.get(surface, 0) + 1

    summary: dict[str, Any] = {"byType": by_type}
    if latest_memory is not None or pressure_count > 0:
        summary["memory"] = {
            "latest": latest_memory,
            "maxRssBytes": max_rss,
            "maxHeapUsedBytes": max_heap,
            "pressureCount": pressure_count,
        }
    if payload_large["count"] > 0:
        summary["payloadLarge"] = payload_large
    return summary


def _parse_optional_non_negative_integer(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a non-negative integer") from None
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _parse_optional_type(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("type must be a non-empty string")
    return value.strip()


def _normalize_limit(limit: Any, default_limit: int = DEFAULT_DIAGNOSTIC_STABILITY_LIMIT) -> int:
    parsed = _parse_optional_non_negative_integer(limit, "limit")
    if parsed is None:
        return default_limit
    if parsed < 1 or parsed > MAX_DIAGNOSTIC_STABILITY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_DIAGNOSTIC_STABILITY_LIMIT}")
    return parsed


def normalize_diagnostic_stability_query(
    input: dict[str, Any] | None = None,
    *,
    default_limit: int = DEFAULT_DIAGNOSTIC_STABILITY_LIMIT,
) -> dict[str, Any]:
    params = input or {}
    return {
        "limit": _normalize_limit(params.get("limit"), default_limit),
        "type": _parse_optional_type(params.get("type")),
        "sinceSeq": _parse_optional_non_negative_integer(params.get("sinceSeq"), "sinceSeq"),
    }


def _select_records(
    records: list[DiagnosticStabilityEventRecord],
    options: dict[str, Any] | None = None,
) -> tuple[list[DiagnosticStabilityEventRecord], list[DiagnosticStabilityEventRecord]]:
    query = normalize_diagnostic_stability_query(options or {})
    limit = query["limit"]
    event_type = query["type"]
    since_seq = query["sinceSeq"]
    filtered = []
    for record in records:
        if event_type and record.get("type") != event_type:
            continue
        seq = record.get("seq")
        if since_seq is not None and isinstance(seq, int) and seq <= since_seq:
            continue
        filtered.append(record)
    start = max(0, len(filtered) - limit)
    return filtered, filtered[start:]


def get_diagnostic_stability_snapshot(
    options: dict[str, Any] | None = None,
) -> DiagnosticStabilitySnapshot:
    state = _get_state()
    filtered, events = _select_records(_list_records(), options)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "capacity": state.capacity,
        "count": len(filtered),
        "dropped": state.dropped,
        "firstSeq": filtered[0].get("seq") if filtered else None,
        "lastSeq": filtered[-1].get("seq") if filtered else None,
        "events": events,
        "summary": _summarize_records(filtered),
    }


def _on_diagnostic_event(_event_type: str, event: dict[str, Any]) -> None:
    payload = dict(event)
    if "type" not in payload:
        payload["type"] = _event_type
    if "seq" not in payload or "ts" not in payload:
        return
    _append_record(sanitize_diagnostic_event(payload))


def start_diagnostic_stability_recorder() -> None:
    state = _get_state()
    if state.unsubscribe is not None:
        return
    from openclaw.infra.diagnostic_events import on_diagnostic_event

    state.unsubscribe = on_diagnostic_event(_on_diagnostic_event)


def stop_diagnostic_stability_recorder() -> None:
    state = _get_state()
    if state.unsubscribe:
        state.unsubscribe()
    state.unsubscribe = None


def reset_diagnostic_stability_recorder_for_test(capacity: int = DEFAULT_DIAGNOSTIC_STABILITY_CAPACITY) -> None:
    global _state
    if _state and _state.unsubscribe:
        _state.unsubscribe()
    _state = _StabilityState(
        records=[None] * capacity,
        capacity=capacity,
    )
