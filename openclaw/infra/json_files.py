"""JSON file helpers with atomic writes (mirrors TS infra/json-files.ts)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class JsonFileReadError(Exception):
    """Raised when a durable JSON file read fails."""

    def __init__(self, message: str, path: Path | str | None = None) -> None:
        super().__init__(message)
        self.path = path

_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _lock_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def read_json_file(path: Path, fallback: T | None = None) -> T | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def write_json_atomic(path: Path, value: Any) -> None:
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{int(time.time() * 1000)}"
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def with_file_lock(path: Path, fn: Callable[[], T]) -> T:
    with _lock_for(path):
        return fn()


def read_durable_json_file(path: Path, fallback: T | None = None) -> T:
    """Like read_json_file but raises JsonFileReadError on failure.

    Args:
        path: Path to read.
        fallback: Not used; present for signature compat.

    Returns:
        Parsed JSON value.

    Raises:
        JsonFileReadError: If the file cannot be read or parsed.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise JsonFileReadError(f"file not found: {path}", path=path) from exc
    except json.JSONDecodeError as exc:
        raise JsonFileReadError(f"invalid JSON in {path}: {exc}", path=path) from exc
    except OSError as exc:
        raise JsonFileReadError(f"IO error reading {path}: {exc}", path=path) from exc


def write_text_atomic(path: Path, content: str) -> None:
    """Atomically write text content to a file.

    Args:
        path: Destination path.
        content: Text content to write.
    """
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{int(time.time() * 1000)}"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
