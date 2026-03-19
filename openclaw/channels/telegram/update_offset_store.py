"""Persist the Telegram getUpdates offset per bot account.

Mirrors TypeScript ``src/telegram/update-offset-store.ts``:
- File per account at ``~/.openclaw/telegram/update-offset-{accountId}.json``
- Atomic write via temp-file + rename to avoid corruption
- Permissions 0o600 (owner read/write only)
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

_BASE_DIR = resolve_state_dir() / "telegram"


def ensure_telegram_dir() -> Path:
    """Ensure telegram directory exists (TS alignment)"""
    _BASE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return _BASE_DIR


def _offset_path(account_id: str) -> Path:
    return _BASE_DIR / f"update-offset-{account_id}.json"


def read_telegram_update_offset(account_id: str) -> int | None:
    """Return the persisted update offset, or None if not found."""
    path = _offset_path(account_id)
    try:
        if path.exists():
            data = json.loads(path.read_text())
            offset = data.get("offset")
            if isinstance(offset, int):
                return offset
    except Exception as exc:
        logger.warning("Failed to read update offset for %s: %s", account_id, exc)
    return None


def write_telegram_update_offset(account_id: str, offset: int) -> None:
    """Persist the update offset atomically."""
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    path = _offset_path(account_id)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps({"offset": offset}))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(path)
    except Exception as exc:
        logger.warning("Failed to write update offset for %s: %s", account_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def delete_telegram_update_offset(account_id: str) -> None:
    """Remove the persisted offset (e.g. on clean start)."""
    path = _offset_path(account_id)
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Failed to delete update offset for %s: %s", account_id, exc)
