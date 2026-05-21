"""Cron run log configuration and utilities.

Mirrors TypeScript openclaw/src/cron/run-log.ts
Provides functions for resolving run log pruning options and paginated reads.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

CronRunStatus = Literal["ok", "error", "skipped"]
CronDeliveryStatus = Literal["delivered", "not-delivered", "unknown", "not-requested"]
CronRunLogSortDir = Literal["asc", "desc"]


def parse_byte_size(size_str: str) -> int:
    """Parse byte size string like '2mb', '1gb' to bytes.
    
    Mirrors TS parseByteSize() logic from src/cron/run-log.ts
    
    Args:
        size_str: Size string like "2mb", "1gb", "1024", "100kb"
        
    Returns:
        Size in bytes
        
    Raises:
        ValueError: If format is invalid
        
    Example:
        >>> parse_byte_size("2mb")
        2097152
        >>> parse_byte_size("1gb")
        1073741824
        >>> parse_byte_size("1024")
        1024
    """
    size_str = size_str.strip().lower()
    
    # Extract number and unit
    import re
    match = re.match(r'^(\d+(?:\.\d+)?)\s*([kmgt]?b)?$', size_str)
    if not match:
        raise ValueError(f"Invalid byte size: {size_str}")
    
    num = float(match.group(1))
    unit = match.group(2) or 'b'
    
    multipliers = {
        'b': 1,
        'kb': 1024,
        'mb': 1024 ** 2,
        'gb': 1024 ** 3,
        'tb': 1024 ** 4,
    }
    
    return int(num * multipliers[unit])


def resolve_cron_run_log_prune_options(
    run_log_config: dict[str, Any] | None
) -> dict[str, int]:
    """Resolve run log pruning options from config.
    
    Mirrors TS resolveCronRunLogPruneOptions() from src/cron/run-log.ts:81-99
    
    Args:
        run_log_config: The runLog section of cron config
        
    Returns:
        Dict with 'max_bytes' and 'keep_lines' keys
        
    Example:
        >>> resolve_cron_run_log_prune_options({"maxBytes": "2mb", "keepLines": 1000})
        {'max_bytes': 2097152, 'keep_lines': 1000}
        
        >>> resolve_cron_run_log_prune_options({"maxBytes": 5000000})
        {'max_bytes': 5000000, 'keep_lines': 2000}
        
        >>> resolve_cron_run_log_prune_options(None)
        {'max_bytes': 2000000, 'keep_lines': 2000}
    """
    max_bytes = 2_000_000  # Default: 2MB
    keep_lines = 2_000     # Default: 2000 lines
    
    if not run_log_config:
        return {"max_bytes": max_bytes, "keep_lines": keep_lines}
    
    # Parse maxBytes
    if "maxBytes" in run_log_config:
        raw = run_log_config["maxBytes"]
        try:
            if isinstance(raw, int):
                max_bytes = raw
            elif isinstance(raw, str):
                max_bytes = parse_byte_size(raw)
        except (ValueError, TypeError):
            # Use default on parse error (matches TS behavior)
            pass
    
    # Parse keepLines
    if "keepLines" in run_log_config:
        raw = run_log_config["keepLines"]
        if isinstance(raw, (int, float)) and raw > 0:
            keep_lines = int(raw)
    
    return {"max_bytes": max_bytes, "keep_lines": keep_lines}


def _normalize_lowercase(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _assert_safe_cron_run_log_job_id(job_id: str) -> str:
    trimmed = job_id.strip()
    if not trimmed:
        raise ValueError("invalid cron run log job id")
    if "/" in trimmed or "\\" in trimmed or "\0" in trimmed:
        raise ValueError("invalid cron run log job id")
    return trimmed


def resolve_cron_run_log_path(*, store_path: str | Path, job_id: str) -> Path:
    """Resolve per-job run log path under the cron store runs directory."""
    store = Path(store_path).resolve()
    runs_dir = (store.parent / "runs").resolve()
    safe_job_id = _assert_safe_cron_run_log_job_id(job_id)
    resolved = (runs_dir / f"{safe_job_id}.jsonl").resolve()
    try:
        resolved.relative_to(runs_dir)
    except ValueError as exc:
        raise ValueError("invalid cron run log job id") from exc
    return resolved


def _normalize_run_statuses(
    *,
    statuses: list[str] | None = None,
    status: str | None = None,
) -> list[CronRunStatus] | None:
    if statuses:
        filtered = [s for s in statuses if s in ("ok", "error", "skipped")]
        if filtered:
            return list(dict.fromkeys(filtered))  # type: ignore[arg-type]
    if status in ("ok", "error", "skipped"):
        return [status]  # type: ignore[list-item]
    return None


def _normalize_delivery_statuses(
    *,
    delivery_statuses: list[str] | None = None,
    delivery_status: str | None = None,
) -> list[CronDeliveryStatus] | None:
    allowed = ("delivered", "not-delivered", "unknown", "not-requested")
    if delivery_statuses:
        filtered = [s for s in delivery_statuses if s in allowed]
        if filtered:
            return list(dict.fromkeys(filtered))  # type: ignore[arg-type]
    if delivery_status in allowed:
        return [delivery_status]  # type: ignore[list-item]
    return None


def _parse_run_log_line(obj: dict[str, Any], *, filter_job_id: str | None = None) -> dict[str, Any] | None:
    if obj.get("action") != "finished":
        return None
    job_id_val = obj.get("jobId", "")
    if not isinstance(job_id_val, str) or not job_id_val.strip():
        return None
    ts_val = obj.get("ts")
    if not isinstance(ts_val, (int, float)) or not math.isfinite(float(ts_val)):
        return None
    if filter_job_id and job_id_val != filter_job_id:
        return None

    entry: dict[str, Any] = {
        "ts": ts_val,
        "jobId": job_id_val,
        "action": "finished",
        "status": obj.get("status"),
        "error": obj.get("error"),
        "summary": obj.get("summary"),
        "runAtMs": obj.get("runAtMs"),
        "durationMs": obj.get("durationMs"),
        "nextRunAtMs": obj.get("nextRunAtMs"),
    }

    for key in ("model", "provider", "sessionId", "sessionKey"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            entry[key] = val

    raw_usage = obj.get("usage")
    if isinstance(raw_usage, dict):
        normalized_usage: dict[str, Any] = {}
        for token_key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            v = raw_usage.get(token_key)
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                normalized_usage[token_key] = v
        if normalized_usage:
            entry["usage"] = normalized_usage

    if isinstance(obj.get("delivered"), bool):
        entry["delivered"] = obj["delivered"]
    delivery_status = obj.get("deliveryStatus")
    if delivery_status in ("delivered", "not-delivered", "unknown", "not-requested"):
        entry["deliveryStatus"] = delivery_status
    if isinstance(obj.get("deliveryError"), str):
        entry["deliveryError"] = obj["deliveryError"]
    if isinstance(obj.get("delivery"), dict):
        entry["delivery"] = obj["delivery"]

    return entry


def parse_all_run_log_entries(raw: str, *, job_id: str | None = None) -> list[dict[str, Any]]:
    """Parse JSONL run log content into normalized entries."""
    if not raw.strip():
        return []
    filter_job_id = job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
    parsed: list[dict[str, Any]] = []
    for line in raw.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            obj = json.loads(trimmed)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        entry = _parse_run_log_line(obj, filter_job_id=filter_job_id)
        if entry:
            parsed.append(entry)
    return parsed


def filter_run_log_entries(
    entries: list[dict[str, Any]],
    *,
    statuses: list[CronRunStatus] | None,
    delivery_statuses: list[CronDeliveryStatus] | None,
    query: str,
    query_text_for_entry: Any,
) -> list[dict[str, Any]]:
    """Filter run log entries by status, delivery status, and query."""
    normalized_query = _normalize_lowercase(query)

    def matches(entry: dict[str, Any]) -> bool:
        if statuses and entry.get("status") not in statuses:
            return False
        if delivery_statuses:
            delivery_status = entry.get("deliveryStatus") or "not-requested"
            if delivery_status not in delivery_statuses:
                return False
        if not normalized_query:
            return True
        haystack = _normalize_lowercase(query_text_for_entry(entry))
        return normalized_query in haystack

    return [e for e in entries if matches(e)]


def read_cron_run_log_entries_page(
    file_path: str | Path,
    *,
    limit: int | None = None,
    offset: int = 0,
    job_id: str | None = None,
    statuses: list[str] | None = None,
    status: str | None = None,
    delivery_statuses: list[str] | None = None,
    delivery_status: str | None = None,
    query: str | None = None,
    sort_dir: CronRunLogSortDir | None = None,
) -> dict[str, Any]:
    """Read a paginated page from a single job's run log."""
    path = Path(file_path)
    page_limit = max(1, min(200, int(limit if limit is not None else 50)))
    normalized_statuses = _normalize_run_statuses(statuses=statuses, status=status)
    normalized_delivery = _normalize_delivery_statuses(
        delivery_statuses=delivery_statuses,
        delivery_status=delivery_status,
    )
    normalized_query = _normalize_lowercase(query)
    direction: CronRunLogSortDir = "asc" if sort_dir == "asc" else "desc"

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    except OSError:
        raw = ""

    all_entries = parse_all_run_log_entries(raw, job_id=job_id)
    filtered = filter_run_log_entries(
        all_entries,
        statuses=normalized_statuses,
        delivery_statuses=normalized_delivery,
        query=normalized_query,
        query_text_for_entry=lambda entry: " ".join(
            [
                str(entry.get("summary") or ""),
                str(entry.get("error") or ""),
                str(entry.get("jobId") or ""),
                str((entry.get("delivery") or {}).get("intended", {}).get("channel") or ""),
                str((entry.get("delivery") or {}).get("resolved", {}).get("channel") or ""),
                *[
                    str(t.get("channel") or "")
                    for t in (entry.get("delivery") or {}).get("messageToolSentTo") or []
                    if isinstance(t, dict)
                ],
            ]
        ),
    )
    sorted_entries = sorted(
        filtered,
        key=lambda e: float(e.get("ts") or 0),
        reverse=(direction == "desc"),
    )
    total = len(sorted_entries)
    safe_offset = max(0, min(total, int(offset)))
    page_entries = sorted_entries[safe_offset : safe_offset + page_limit]
    next_offset = safe_offset + len(page_entries)
    return {
        "entries": page_entries,
        "total": total,
        "offset": safe_offset,
        "limit": page_limit,
        "hasMore": next_offset < total,
        "nextOffset": next_offset if next_offset < total else None,
    }


def read_cron_run_log_entries_page_all(
    *,
    store_path: str | Path,
    limit: int | None = None,
    offset: int = 0,
    statuses: list[str] | None = None,
    status: str | None = None,
    delivery_statuses: list[str] | None = None,
    delivery_status: str | None = None,
    query: str | None = None,
    sort_dir: CronRunLogSortDir | None = None,
    job_name_by_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read a paginated page across all job run logs."""
    store = Path(store_path).resolve()
    runs_dir = (store.parent / "runs").resolve()
    page_limit = max(1, min(200, int(limit if limit is not None else 50)))
    normalized_statuses = _normalize_run_statuses(statuses=statuses, status=status)
    normalized_delivery = _normalize_delivery_statuses(
        delivery_statuses=delivery_statuses,
        delivery_status=delivery_status,
    )
    normalized_query = _normalize_lowercase(query)
    direction: CronRunLogSortDir = "asc" if sort_dir == "asc" else "desc"

    if not runs_dir.is_dir():
        return {
            "entries": [],
            "total": 0,
            "offset": 0,
            "limit": page_limit,
            "hasMore": False,
            "nextOffset": None,
        }

    all_entries: list[dict[str, Any]] = []
    for file_path in runs_dir.glob("*.jsonl"):
        try:
            raw = file_path.read_text(encoding="utf-8")
        except OSError:
            continue
        all_entries.extend(parse_all_run_log_entries(raw))

    name_by_id = job_name_by_id or {}

    def query_text(entry: dict[str, Any]) -> str:
        job_name = name_by_id.get(str(entry.get("jobId") or ""), "")
        return " ".join(
            [
                str(entry.get("summary") or ""),
                str(entry.get("error") or ""),
                str(entry.get("jobId") or ""),
                job_name,
                str((entry.get("delivery") or {}).get("intended", {}).get("channel") or ""),
                str((entry.get("delivery") or {}).get("resolved", {}).get("channel") or ""),
                *[
                    str(t.get("channel") or "")
                    for t in (entry.get("delivery") or {}).get("messageToolSentTo") or []
                    if isinstance(t, dict)
                ],
            ]
        )

    filtered = filter_run_log_entries(
        all_entries,
        statuses=normalized_statuses,
        delivery_statuses=normalized_delivery,
        query=normalized_query,
        query_text_for_entry=query_text,
    )
    sorted_entries = sorted(
        filtered,
        key=lambda e: float(e.get("ts") or 0),
        reverse=(direction == "desc"),
    )
    total = len(sorted_entries)
    safe_offset = max(0, min(total, int(offset)))
    page_entries = sorted_entries[safe_offset : safe_offset + page_limit]
    if name_by_id:
        for entry in page_entries:
            job_name = name_by_id.get(str(entry.get("jobId") or ""))
            if job_name:
                entry["jobName"] = job_name
    next_offset = safe_offset + len(page_entries)
    return {
        "entries": page_entries,
        "total": total,
        "offset": safe_offset,
        "limit": page_limit,
        "hasMore": next_offset < total,
        "nextOffset": next_offset if next_offset < total else None,
    }
