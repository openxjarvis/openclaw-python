"""Batch error extraction utilities.

Mirrors openclaw/src/memory-host-sdk/host/batch-error-utils.ts.
"""
from __future__ import annotations

from typing import Any


def _get_response_error_message(line: dict[str, Any] | None) -> str | None:
    """Extract error message from the response body of a batch output line."""
    if not line:
        return None
    body = (line.get("response") or {}).get("body")
    if isinstance(body, str):
        return body or None
    if not isinstance(body, dict):
        return None
    msg = (body.get("error") or {}).get("message")
    return str(msg) if isinstance(msg, str) else None


def extract_batch_error_message(lines: list[dict[str, Any]]) -> str | None:
    """Return the first error message found in a list of batch output lines.

    Mirrors TS extractBatchErrorMessage().
    """
    for line in lines:
        direct = (line.get("error") or {}).get("message")
        if direct:
            return str(direct)
        resp_msg = _get_response_error_message(line)
        if resp_msg:
            return resp_msg
    return None


def format_unavailable_batch_error(err: Exception | str | None) -> str:
    """Format a batch unavailability error for logging / user display.

    Mirrors TS formatUnavailableBatchError().
    """
    if err is None:
        return "batch unavailable"
    if isinstance(err, str):
        return err or "batch unavailable"
    return str(err) or "batch unavailable"


__all__ = [
    "extract_batch_error_message",
    "format_unavailable_batch_error",
]
