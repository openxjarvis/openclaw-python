"""Browser proxy file persistence utilities.

Ported from TypeScript openclaw/src/browser/proxy-files.ts.

When browser actions are executed via a remote node proxy, the proxy may
return binary files (screenshots, PDFs, downloads) encoded as base64 in
a `files` array. These functions:
1. Save those files to local temp storage
2. Update the result object so path fields point to local paths
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def _get_browser_tmp_dir() -> Path:
    """Return the preferred OpenClaw temp directory for browser files."""
    import os
    tmp = os.environ.get("OPENCLAW_TMP_DIR") or ""
    if tmp:
        return Path(tmp)
    return resolve_state_dir() / "tmp"


async def persist_browser_proxy_files(
    files: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Save base64-encoded proxy files to local disk.

    Mirrors TypeScript persistBrowserProxyFiles() from proxy-files.ts.

    Each entry in `files` has:
        - path: str — original path key used in the result
        - base64: str — base64-encoded file contents
        - mimeType: str | None — MIME type hint

    Returns a mapping from original path → local saved path.
    """
    if not files:
        return {}

    mapping: dict[str, str] = {}
    tmp_dir = _get_browser_tmp_dir() / "browser"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        if not isinstance(file, dict):
            continue
        original_path = file.get("path", "")
        b64 = file.get("base64", "")
        mime_type = file.get("mimeType") or "application/octet-stream"

        if not original_path or not b64:
            continue

        try:
            buf = base64.b64decode(b64)
        except Exception as exc:
            logger.warning("Failed to decode proxy file %s: %s", original_path, exc)
            continue

        # Determine extension from MIME type or original path
        ext = ""
        ext_guess = mimetypes.guess_extension(mime_type)
        if ext_guess:
            # mimetypes sometimes returns .jpe for jpeg; normalize
            ext = ext_guess if ext_guess != ".jpe" else ".jpg"
        else:
            suffix = Path(original_path).suffix
            if suffix:
                ext = suffix

        filename = f"proxy_{uuid.uuid4().hex}{ext}"
        local_path = tmp_dir / filename

        try:
            local_path.write_bytes(buf)
            mapping[original_path] = str(local_path)
            logger.debug(
                "Saved proxy file: %s → %s (%d bytes)",
                original_path, local_path, len(buf),
            )
        except Exception as exc:
            logger.warning("Failed to save proxy file %s: %s", original_path, exc)

    return mapping


def apply_browser_proxy_paths(result: Any, mapping: dict[str, str]) -> None:
    """Replace proxy remote paths with local file paths in a result object.

    Mirrors TypeScript applyBrowserProxyPaths() from proxy-files.ts.

    Mutates `result` in-place, replacing:
    - result["path"] if it's in the mapping
    - result["imagePath"] if it's in the mapping
    - result["download"]["path"] if it's in the mapping
    """
    if not result or not isinstance(result, dict) or not mapping:
        return

    if isinstance(result.get("path"), str) and result["path"] in mapping:
        result["path"] = mapping[result["path"]]

    if isinstance(result.get("imagePath"), str) and result["imagePath"] in mapping:
        result["imagePath"] = mapping[result["imagePath"]]

    download = result.get("download")
    if isinstance(download, dict):
        if isinstance(download.get("path"), str) and download["path"] in mapping:
            download["path"] = mapping[download["path"]]


__all__ = [
    "persist_browser_proxy_files",
    "apply_browser_proxy_paths",
]
