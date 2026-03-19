"""Browser path utilities and constants.

Ported from TypeScript openclaw/src/browser/paths.ts.

Provides:
- Default directory constants for browser temp files
- Path validation helpers that scope files within a root directory
  (security: prevents path traversal attacks)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict
from openclaw.config.paths import resolve_state_dir


def _resolve_openclaw_tmp_dir() -> Path:
    """Resolve preferred OpenClaw temp directory. Mirrors TS resolvePreferredOpenClawTmpDir()."""
    env_override = os.environ.get("OPENCLAW_TMP_DIR", "").strip()
    if env_override:
        return Path(env_override)
    return resolve_state_dir() / "tmp"


_TMP_DIR = _resolve_openclaw_tmp_dir()

DEFAULT_BROWSER_TMP_DIR: Path = _TMP_DIR
DEFAULT_TRACE_DIR: Path = _TMP_DIR
DEFAULT_DOWNLOAD_DIR: Path = _TMP_DIR / "downloads"
DEFAULT_UPLOAD_DIR: Path = _TMP_DIR / "uploads"


class ResolvePathOk(TypedDict):
    ok: bool  # True
    path: str


class ResolvePathError(TypedDict):
    ok: bool  # False
    error: str


class ResolvePathsOk(TypedDict):
    ok: bool  # True
    paths: list[str]


class ResolvePathsError(TypedDict):
    ok: bool  # False
    error: str


def resolve_path_within_root(
    root_dir: str | Path,
    requested_path: str,
    scope_label: str,
    default_file_name: str | None = None,
) -> ResolvePathOk | ResolvePathError:
    """Validate that a requested path stays within root_dir.

    Mirrors TypeScript resolvePathWithinRoot() from paths.ts.

    Returns {"ok": True, "path": resolved_path} on success or
    {"ok": False, "error": message} on failure.

    Security: prevents path traversal (e.g. "../../etc/passwd").
    """
    root = Path(root_dir).resolve()
    raw = requested_path.strip() if requested_path else ""

    if not raw:
        if not default_file_name:
            return {"ok": False, "error": "path is required"}
        return {"ok": True, "path": str(root / default_file_name)}

    resolved = (root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return {
            "ok": False,
            "error": f"Invalid path: must stay within {scope_label}",
        }

    return {"ok": True, "path": str(resolved)}


def resolve_paths_within_root(
    root_dir: str | Path,
    requested_paths: list[str],
    scope_label: str,
) -> ResolvePathsOk | ResolvePathsError:
    """Validate that all requested paths stay within root_dir.

    Mirrors TypeScript resolvePathsWithinRoot() from paths.ts.

    Returns {"ok": True, "paths": [...]} or {"ok": False, "error": message}.
    """
    resolved_paths: list[str] = []
    for raw in requested_paths:
        result = resolve_path_within_root(root_dir, raw, scope_label)
        if not result["ok"]:
            return {"ok": False, "error": result["error"]}  # type: ignore[return-value]
        resolved_paths.append(result["path"])  # type: ignore[index]
    return {"ok": True, "paths": resolved_paths}


__all__ = [
    "DEFAULT_BROWSER_TMP_DIR",
    "DEFAULT_TRACE_DIR",
    "DEFAULT_DOWNLOAD_DIR",
    "DEFAULT_UPLOAD_DIR",
    "resolve_path_within_root",
    "resolve_paths_within_root",
]
