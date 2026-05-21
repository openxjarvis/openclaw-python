"""Cron serialization — converts Python snake_case store format to TypeScript camelCase API.

All functions accept dicts (output of to_dict()) and return TS-compatible dicts.
"""
from __future__ import annotations

from typing import Any


def to_camel_case(snake_str: str) -> str:
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def convert_schedule_to_api(d: dict[str, Any]) -> dict[str, Any]:
    """Convert schedule store dict → TS wire format."""
    # Store uses "type" (or "kind" after normalize), new names: at, every_ms, anchor_ms, expr, tz
    kind = d.get("kind") or d.get("type", "")

    if kind == "at":
        return {"kind": "at", "at": d.get("at") or d.get("timestamp", "")}

    if kind == "every":
        every_ms = d.get("every_ms") or d.get("interval_ms") or d.get("everyMs") or 0
        result: dict[str, Any] = {"kind": "every", "everyMs": every_ms}
        anchor_ms = d.get("anchor_ms") or d.get("anchorMs")
        if anchor_ms is not None:
            result["anchorMs"] = int(anchor_ms)
        return result

    if kind == "cron":
        expr = d.get("expr") or d.get("expression") or ""
        result = {"kind": "cron", "expr": expr}
        tz = d.get("tz") or d.get("timezone")
        if tz and tz != "UTC":
            result["tz"] = tz
        stagger_ms = d.get("stagger_ms") or d.get("staggerMs")
        if stagger_ms is not None:
            result["staggerMs"] = int(stagger_ms)
        return result

    return d


def convert_payload_to_api(d: dict[str, Any]) -> dict[str, Any]:
    """Convert payload store dict → TS wire format."""
    kind = d.get("kind", "")

    if kind == "systemEvent":
        return {"kind": "systemEvent", "text": d.get("text", "")}

    if kind == "agentTurn":
        # Store uses "message" (TS wire format)
        msg = d.get("message") or d.get("prompt") or ""
        result: dict[str, Any] = {"kind": "agentTurn", "message": msg}
        if d.get("model"):
            result["model"] = d["model"]
        if d.get("thinking"):
            result["thinking"] = d["thinking"]
        ts = d.get("timeout_seconds") or d.get("timeoutSeconds")
        if ts is not None:
            result["timeoutSeconds"] = ts
        unsafe = d.get("allow_unsafe_external_content") or d.get("allowUnsafeExternalContent")
        if unsafe:
            result["allowUnsafeExternalContent"] = True
        return result

    return d


def convert_delivery_to_api(d: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert delivery store dict → TS wire format."""
    if not d:
        return None
    result: dict[str, Any] = {"mode": d.get("mode", "none")}
    ch = d.get("channel")
    if ch:
        result["channel"] = ch
    to = d.get("to") or d.get("target")
    if to:
        result["to"] = to
    be = d.get("best_effort") or d.get("bestEffort")
    if be:
        result["bestEffort"] = True
    return result


def convert_state_to_api(d: dict[str, Any]) -> dict[str, Any]:
    """Convert state store dict → TS wire format."""
    result: dict[str, Any] = {}
    mapping = {
        "next_run_ms": "nextRunAtMs",
        "running_at_ms": "runningAtMs",
        "last_run_at_ms": "lastRunAtMs",
        "last_status": "lastStatus",
        "last_error": "lastError",
        "last_duration_ms": "lastDurationMs",
        "consecutive_errors": "consecutiveErrors",
        "schedule_error_count": "scheduleErrorCount",
    }
    for py_key, ts_key in mapping.items():
        val = d.get(py_key)
        if val is not None:
            # Normalize legacy "success" → "ok"
            if py_key == "last_status" and val == "success":
                val = "ok"
            result[ts_key] = val
    return result


def convert_job_to_api(job_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert full CronJob store dict → TS API format."""
    result: dict[str, Any] = {
        "id": job_dict.get("id", ""),
        "name": job_dict.get("name", ""),
        "enabled": job_dict.get("enabled", True),
        "sessionTarget": job_dict.get("session_target", "main"),
        "wakeMode": job_dict.get("wake_mode", "next-heartbeat"),
    }

    if job_dict.get("agent_id"):
        result["agentId"] = job_dict["agent_id"]
    if job_dict.get("session_key"):
        result["sessionKey"] = job_dict["session_key"]
    if job_dict.get("description"):
        result["description"] = job_dict["description"]
    if job_dict.get("delete_after_run"):
        result["deleteAfterRun"] = True

    if "created_at_ms" in job_dict:
        result["createdAtMs"] = job_dict["created_at_ms"]
    if "updated_at_ms" in job_dict:
        result["updatedAtMs"] = job_dict["updated_at_ms"]

    if "schedule" in job_dict:
        result["schedule"] = convert_schedule_to_api(job_dict["schedule"])
    if "payload" in job_dict:
        result["payload"] = convert_payload_to_api(job_dict["payload"])
    if job_dict.get("delivery"):
        api_del = convert_delivery_to_api(job_dict["delivery"])
        if api_del:
            result["delivery"] = api_del

    state_dict = job_dict.get("state", {})
    result["state"] = convert_state_to_api(state_dict)

    # Convenience running flag
    result["running"] = state_dict.get("running_at_ms") is not None

    return result


def convert_run_log_entry_to_api(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert run log entry → TS wire format."""
    status = entry.get("status")
    if status == "success":
        status = "ok"

    result: dict[str, Any] = {
        "ts": entry.get("ts", 0),
        "jobId": entry.get("jobId", entry.get("job_id", "")),
        "action": entry.get("action", "finished"),
    }
    if status is not None:
        result["status"] = status
    for field in ("error", "summary", "deliveryError", "jobName"):
        if entry.get(field):
            result[field] = entry[field]
    if isinstance(entry.get("delivered"), bool):
        result["delivered"] = entry["delivered"]
    if entry.get("deliveryStatus") is not None:
        result["deliveryStatus"] = entry["deliveryStatus"]
    if isinstance(entry.get("delivery"), dict):
        result["delivery"] = entry["delivery"]
    for py_key, ts_key in (
        ("runAtMs", "runAtMs"),
        ("durationMs", "durationMs"),
        ("nextRunAtMs", "nextRunAtMs"),
        ("sessionId", "sessionId"),
        ("sessionKey", "sessionKey"),
        ("model", "model"),
        ("provider", "provider"),
        ("usage", "usage"),
    ):
        if entry.get(py_key) is not None:
            result[ts_key] = entry[py_key]

    return result


__all__ = [
    "to_camel_case",
    "convert_schedule_to_api",
    "convert_payload_to_api",
    "convert_delivery_to_api",
    "convert_state_to_api",
    "convert_job_to_api",
    "convert_run_log_entry_to_api",
]
