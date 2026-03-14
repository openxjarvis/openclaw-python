"""Gateway update check — ports TS src/infra/update-startup.ts.

Writes ~/.openclaw/update-check.json once every 24 hours with the latest
version from PyPI. Skips when running in test environments.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

UPDATE_CHECK_FILENAME = "update-check.json"
UPDATE_CHECK_INTERVAL_MS = 24 * 60 * 60 * 1000  # 24 hours
_PYPI_URL = "https://pypi.org/pypi/openclaw-python/json"


def _is_test_env() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("VITEST"))


def _resolve_state_dir() -> Path:
    from openclaw.config.paths import resolve_state_dir
    return Path(resolve_state_dir())


def _state_path() -> Path:
    return _resolve_state_dir() / UPDATE_CHECK_FILENAME


def _read_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _should_skip(cfg: Any) -> bool:
    if _is_test_env():
        return True
    if isinstance(cfg, dict):
        if cfg.get("update", {}).get("checkOnStart") is False:
            return True
    else:
        update = getattr(cfg, "update", None)
        if update is not None:
            check_on_start = getattr(update, "check_on_start", None) if getattr(update, "check_on_start", None) is not None else getattr(update, "checkOnStart", None)
            if check_on_start is False:
                return True
    return False


def _is_stale(state: dict[str, Any]) -> bool:
    """Return True if the last check was more than 24 h ago or missing."""
    checked_at = state.get("checkedAt")
    if not checked_at:
        return True
    try:
        last = datetime.fromisoformat(checked_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        delta_ms = (datetime.now(timezone.utc) - last).total_seconds() * 1000
        return delta_ms >= UPDATE_CHECK_INTERVAL_MS
    except Exception:
        return True


async def _fetch_latest_version() -> str | None:
    """Fetch latest version from PyPI. Returns version string or None."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(_PYPI_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("info", {}).get("version")
    except Exception as exc:
        logger.debug("update-check: PyPI fetch failed: %s", exc)
        return None


async def run_gateway_update_check(
    cfg: Any,
    *,
    allow_in_tests: bool = False,
) -> None:
    """Run gateway update check — writes update-check.json when stale.

    Args:
        cfg: OpenClaw config (dict or OpenClawConfig Pydantic model).
        allow_in_tests: Override test-env skip (for testing this function).
    """
    if not allow_in_tests and _should_skip(cfg):
        return

    path = _state_path()
    state = _read_state(path)

    if not _is_stale(state):
        logger.debug("update-check: skipped (checked recently)")
        return

    logger.debug("update-check: checking PyPI for latest version…")
    latest = await _fetch_latest_version()

    now_iso = datetime.now(timezone.utc).isoformat()
    new_state: dict[str, Any] = {"checkedAt": now_iso}
    if latest:
        new_state["latestVersion"] = latest
        logger.info("update-check: latest version on PyPI: %s", latest)
    else:
        logger.debug("update-check: could not determine latest version")

    try:
        _write_state(path, new_state)
        logger.debug("update-check: wrote %s", path)
    except Exception as exc:
        logger.debug("update-check: failed to write state: %s", exc)
