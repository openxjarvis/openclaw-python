"""Configured log file tail reader (mirrors openclaw/src/logging/log-tail.ts)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from openclaw.config.paths import resolve_state_dir
from openclaw.logging.state import get_logging_state

DEFAULT_LIMIT = 500
DEFAULT_MAX_BYTES = 250_000
MAX_LIMIT = 5000
MAX_BYTES = 1_000_000
ROLLING_LOG_RE = re.compile(r"^openclaw-\d{4}-\d{2}-\d{2}\.log$")


class LogTailPayload(TypedDict):
    file: str
    cursor: int
    size: int
    lines: list[str]
    truncated: bool
    reset: bool


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def get_resolved_logger_file() -> Path:
    """Resolve active log file path (mirrors TS getResolvedLoggerSettings().file)."""
    state = get_logging_state()
    if state.file_log_path:
        return Path(state.file_log_path)
    date_str = datetime.now().strftime("%Y-%m-%d")
    return resolve_state_dir() / "tmp" / f"openclaw-{date_str}.log"


def resolve_log_file(file_path: Path) -> Path:
    """Pick newest rolling log when today's file is missing."""
    if file_path.exists():
        return file_path
    if not ROLLING_LOG_RE.match(file_path.name):
        return file_path
    log_dir = file_path.parent
    if not log_dir.is_dir():
        return file_path
    candidates: list[tuple[Path, float]] = []
    try:
        for entry in log_dir.iterdir():
            if entry.is_file() and ROLLING_LOG_RE.match(entry.name):
                candidates.append((entry, entry.stat().st_mtime))
    except OSError:
        return file_path
    if not candidates:
        return file_path
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[0][0]


def read_log_slice(
    file_path: Path,
    *,
    cursor: int | None = None,
    limit: int = DEFAULT_LIMIT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Read a byte slice from the log file."""
    try:
        size = file_path.stat().st_size
    except OSError:
        return {
            "cursor": 0,
            "size": 0,
            "lines": [],
            "truncated": False,
            "reset": False,
        }

    max_bytes = _clamp(max_bytes, 1, MAX_BYTES)
    limit = _clamp(limit, 1, MAX_LIMIT)
    read_cursor: int | None = cursor if isinstance(cursor, int) else None
    reset = False
    truncated = False
    start = 0

    if read_cursor is not None:
        if read_cursor > size:
            reset = True
            start = max(0, size - max_bytes)
            truncated = start > 0
        else:
            start = read_cursor
            if size - start > max_bytes:
                reset = True
                truncated = True
                start = max(0, size - max_bytes)
    else:
        start = max(0, size - max_bytes)
        truncated = start > 0

    if size == 0 or size <= start:
        return {
            "cursor": size,
            "size": size,
            "lines": [],
            "truncated": truncated,
            "reset": reset,
        }

    with open(file_path, "rb") as handle:
        prefix = b""
        if start > 0:
            handle.seek(start - 1)
            prefix = handle.read(1)
        handle.seek(start)
        raw = handle.read(size - start)

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if start > 0 and prefix != b"\n":
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if len(lines) > limit:
        lines = lines[-limit:]

    return {
        "cursor": size,
        "size": size,
        "lines": lines,
        "truncated": truncated,
        "reset": reset,
    }


def redact_sensitive_lines(lines: list[str]) -> list[str]:
    """Best-effort secret redaction for log tail output."""
    patterns = [
        (re.compile(r"(Bearer\s+)[^\s]+", re.I), r"\1***"),
        (re.compile(r"(sk-[A-Za-z0-9_-]{8,})"), "sk-***"),
        (re.compile(r'("apiKey"\s*:\s*")[^"]+(")', re.I), r'\1***\2'),
    ]
    redacted: list[str] = []
    for line in lines:
        updated = line
        for pattern, repl in patterns:
            updated = pattern.sub(repl, updated)
        redacted.append(updated)
    return redacted


async def read_configured_log_tail(
    *,
    cursor: int | None = None,
    limit: int | None = None,
    max_bytes: int | None = None,
) -> LogTailPayload:
    """Read tail from configured gateway log file."""
    file_path = resolve_log_file(get_resolved_logger_file())
    result = read_log_slice(
        file_path,
        cursor=cursor,
        limit=limit if limit is not None else DEFAULT_LIMIT,
        max_bytes=max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES,
    )
    return {
        "file": str(file_path),
        **result,
        "lines": redact_sensitive_lines(result["lines"]),
    }
