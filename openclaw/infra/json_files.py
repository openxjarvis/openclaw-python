"""JSON file helpers with atomic writes (mirrors TS infra/json-files.ts)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")

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
