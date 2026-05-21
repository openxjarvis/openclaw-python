"""Media loading from URLs and files.

Matches TypeScript src/media/store.ts and src/web/media.ts
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import mimetypes
from urllib.parse import urlparse, unquote
import aiohttp

from openclaw.media.mime import MediaKind, media_kind_from_mime
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions (mirrors TS LocalMediaAccessError from src/web/media.ts)
# ---------------------------------------------------------------------------

class LocalMediaAccessError(Exception):
    """Error when local media path access is not allowed.
    
    Mirrors TS LocalMediaAccessError from src/web/media.ts lines 67-75.
    """
    
    def __init__(self, code: str, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Security validation (mirrors TS assertLocalMediaAllowed)
# ---------------------------------------------------------------------------

async def _assert_local_media_allowed(
    media_path: Path,
    local_roots: Optional[list[str] | str],
) -> None:
    """Validate that local media path is under allowed roots.
    
    Mirrors TS assertLocalMediaAllowed() from src/web/media.ts lines 81-138.
    
    Args:
        media_path: Path to validate
        local_roots: List of allowed root directories, "any" to bypass, or None for defaults
    
    Raises:
        LocalMediaAccessError: If path is not allowed
    """
    # Bypass check if explicitly "any" (dangerous, should only be used with sandbox_validated=True)
    if local_roots == "any":
        return
    if local_roots is not None and isinstance(local_roots, list) and len(local_roots) == 1 and local_roots[0] == "any":
        return
    
    # Get roots (use defaults if None)
    if local_roots is None:
        from openclaw.media.local_roots import get_default_media_local_roots
        roots = get_default_media_local_roots()
    else:
        roots = local_roots
    
    # Resolve symlinks to prevent symlink attacks (e.g., /tmp/link → /etc/passwd)
    try:
        resolved = media_path.resolve(strict=False)
    except Exception:
        resolved = media_path.absolute()
    
    # Hardening: block per-agent workspace-* subdirectories unless explicitly allowed
    # (prevents temp-dir-based agents from accessing other agents' workspaces)
    if local_roots is None:
        try:
            state_dir = Path(resolve_state_dir()).resolve()
            rel = resolved.relative_to(state_dir)
            # Check if trying to access workspace-* subdirectory directly under state_dir
            parts = rel.parts
            if parts and parts[0].startswith("workspace-"):
                raise LocalMediaAccessError(
                    "path-not-allowed",
                    f"Local media path is not under an allowed directory: {media_path}"
                )
        except LocalMediaAccessError:
            raise
        except Exception:
            # Path is not relative to state_dir, continue normal validation
            pass
    
    # Check if resolved path is under any allowed root
    for root_str in roots:
        try:
            root_path = Path(root_str).resolve(strict=False)
        except Exception:
            root_path = Path(root_str).absolute()
        
        # Refuse filesystem root as a localRoot (security)
        if root_path == root_path.anchor or str(root_path) == root_path.anchor:
            raise LocalMediaAccessError(
                "invalid-root",
                f"Invalid localRoots entry (refuses filesystem root): {root_str}. Pass a narrower directory."
            )
        
        # Check if resolved path is under this root
        try:
            resolved.relative_to(root_path)
            # Success - path is under this root
            return
        except ValueError:
            # Not under this root, try next
            continue
    
    # Path is not under any allowed root
    raise LocalMediaAccessError(
        "path-not-allowed",
        f"Local media path is not under an allowed directory: {media_path}"
    )


@dataclass
class LoadedMedia:
    """Loaded media data."""

    buffer: bytes
    content_type: Optional[str] = None
    file_name: Optional[str] = None
    kind: Optional[MediaKind] = None


# Alias for backward compatibility
MediaResult = LoadedMedia


async def load_web_media(
    url_or_path: str,
    max_bytes: Optional[int] = None,
    *,
    # Extra kwargs accepted for TS-API parity (load_web_media callers may pass these)
    source: Optional[str] = None,
    local_roots: Optional[list[str] | str] = None,
    allow_remote: bool = True,
    workspace_root: Optional[Path] = None,
    optimize_images: bool = False,
    sandbox_validated: bool = False,
) -> LoadedMedia:
    """Load media from URL or local file path.

    Mirrors TypeScript loadWebMedia (src/web/media.ts).

    Args:
        url_or_path: URL or file path. When called with ``source=`` keyword (TS-API
            parity), that value is used instead.
        max_bytes: Maximum bytes to load (optional).
        source: Alias for ``url_or_path`` — accepted so callers that use the
            ``source=`` keyword argument from a previous API version never crash.
        local_roots: Allowed local directory roots. When None, uses default roots.
            Pass "any" string to bypass restrictions (dangerous).
        allow_remote: Whether remote HTTP downloads are permitted (accepted).
        workspace_root: Workspace root for sandbox path validation (accepted).
        optimize_images: Whether to optimize loaded images (accepted, not applied here).
        sandbox_validated: If True, caller has validated sandbox paths (requires local_roots).

    Returns:
        LoadedMedia with buffer, content_type, file_name, and kind.

    Raises:
        ValueError: If max_bytes exceeded or file not found.
        IOError: If download fails.
        LocalMediaAccessError: If local path is not under allowed roots.
    """
    # Support ``source=`` keyword used by some callers (TS-API parity)
    if source is not None:
        url_or_path = source

    max_size = max_bytes or 10 * 1024 * 1024

    # TS-compatible convenience prefix.
    if url_or_path.startswith("MEDIA:"):
        url_or_path = url_or_path[len("MEDIA:") :].strip()
    if url_or_path.startswith("~/"):
        url_or_path = str(Path.home() / url_or_path[2:])

    parsed = urlparse(url_or_path)
    scheme = parsed.scheme.lower()

    # Handle data URIs: data:<content-type>;base64,<data>
    if scheme == "data":
        rest = url_or_path[5:]  # strip "data:"
        if "," not in rest:
            raise ValueError(f"Invalid data URL: missing comma separator in '{url_or_path}'")
        meta, _, encoded = rest.partition(",")
        parts = meta.split(";")
        content_type = parts[0] if parts[0] else "application/octet-stream"
        if "base64" in parts:
            import base64 as _base64
            try:
                buffer = _base64.b64decode(encoded)
            except Exception as exc:
                raise ValueError(f"Invalid data URL: base64 decode failed: {exc}") from exc
        else:
            from urllib.parse import unquote_plus
            buffer = unquote_plus(encoded).encode("utf-8")
        file_name = None
        kind = media_kind_from_mime(content_type)
        return LoadedMedia(buffer=buffer, content_type=content_type, file_name=file_name, kind=kind)

    # Check if it's a local file path
    if scheme in ("", "file") or url_or_path.startswith("/"):
        path_str = unquote(url_or_path.replace("file://", ""))
        path = Path(path_str)
        
        # ✅ FIX: Resolve relative paths against workspace_root
        # Mirrors TS behavior where relative paths are resolved against session workspace.
        # If path is relative and workspace_root is provided, resolve it first.
        if not path.is_absolute() and workspace_root:
            path = (Path(workspace_root) / path).resolve()
        elif not path.is_absolute():
            # No workspace_root provided - resolve against CWD
            path = path.resolve()

        # Assert local media is allowed (mirrors TS lines 81-138 in src/web/media.ts)
        if not sandbox_validated:
            await _assert_local_media_allowed(path, local_roots)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        file_size = path.stat().st_size
        if file_size > max_size:
            raise ValueError(
                f"File size {file_size} exceeds size limit of {max_size} bytes"
            )

        buffer = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        file_name = path.name

        return LoadedMedia(
            buffer=buffer,
            content_type=content_type,
            file_name=file_name,
            kind=media_kind_from_mime(content_type),
        )

    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported media scheme: {scheme}")

    # Download from URL
    try:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        # Retry logic for transient failures
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url_or_path, allow_redirects=True, max_redirects=5) as response:
                        response.raise_for_status()

                        # Check content length if provided
                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            if int(content_length) > max_size:
                                raise ValueError(
                                    f"Content length {content_length} exceeds max_bytes {max_size}"
                                )

                        # Read response in chunks to enforce bounds during transfer.
                        chunks: list[bytes] = []
                        current = 0
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            current += len(chunk)
                            if current > max_size:
                                raise ValueError(
                                    f"Downloaded {current} bytes, exceeds max_bytes {max_size}"
                                )
                            chunks.append(chunk)
                        buffer = b"".join(chunks)

                        # Check actual size
                        if len(buffer) > max_size:
                            raise ValueError(
                                f"Downloaded {len(buffer)} bytes, exceeds max_bytes {max_size}"
                            )

                        content_type = response.headers.get("Content-Type")
                        if content_type:
                            # Strip charset / boundary parameters
                            content_type = content_type.split(";")[0].strip()

                        # Extract filename from URL
                        file_name = Path(unquote(parsed.path)).name
                        if not file_name or "." not in file_name:
                            file_name = None

                        return LoadedMedia(
                            buffer=buffer,
                            content_type=content_type,
                            file_name=file_name,
                            kind=media_kind_from_mime(content_type),
                        )
            
            except aiohttp.ClientResponseError as e:
                last_error = e
                # Don't retry on 404 or other client errors
                if e.status in (404, 403, 401):
                    logger.warning(f"Image URL not accessible (HTTP {e.status}): {url_or_path}")
                    raise IOError(
                        f"Failed to download media from {url_or_path}: HTTP {e.status} - {e.message}. "
                        "The URL may be expired or invalid. Try using a different image URL."
                    )
                
                # Retry on server errors (500+) or temporary issues
                if attempt < max_retries - 1 and e.status >= 500:
                    logger.warning(f"Retrying image download (attempt {attempt + 1}/{max_retries}): {e}")
                    continue
                raise
            
            except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError) as e:
                last_error = e
                if attempt < max_retries - 1:
                    logger.warning(f"Retrying image download (attempt {attempt + 1}/{max_retries}): {e}")
                    continue
                raise
        
        # All retries failed
        raise IOError(f"Failed to download media from {url_or_path} after {max_retries} attempts: {last_error}")

    except aiohttp.ClientError as e:
        raise IOError(f"Failed to download media from {url_or_path}: {e}")


# Backward compatibility alias
load_media = load_web_media


class MediaLoader:
    """
    Media loader with optional workspace sandbox and size limit.

    Mirrors the TypeScript WebMediaOptions constructor pattern.
    """

    def __init__(
        self,
        max_bytes: Optional[int] = None,
        workspace_root: Optional[Path] = None,
        allow_remote: bool = True,
    ) -> None:
        self.max_bytes = max_bytes
        self.workspace_root = workspace_root
        self.allow_remote = allow_remote

    async def load(self, url_or_path: str) -> LoadedMedia:
        """Load media from URL or file path, applying sandbox and size constraints."""
        # Parse URL to check scheme
        from urllib.parse import urlparse
        parsed = urlparse(url_or_path)
        scheme = parsed.scheme.lower()
        
        # Build local_roots from workspace_root for security checks
        local_roots_list: list[str] | None = None
        if self.workspace_root is not None:
            local_roots_list = [str(self.workspace_root)]
            # Only validate path for local files (not http/https/data)
            if scheme not in ("http", "https", "data"):
                # For file:// URLs, extract the path first
                if scheme == "file":
                    from urllib.parse import unquote
                    path_str = unquote(url_or_path.replace("file://", ""))
                    resolved = Path(path_str).resolve()
                else:
                    resolved = Path(url_or_path).resolve()
                    
                ws_resolved = Path(self.workspace_root).resolve()
                # Check if the resolved path is inside workspace or media/inbound fallback
                try:
                    resolved.relative_to(ws_resolved)
                except ValueError:
                    # Try media/inbound fallback (bare filename → {workspace}/media/inbound/{name})
                    if "/" not in url_or_path and "\\" not in url_or_path and scheme == "":
                        fallback = ws_resolved / "media" / "inbound" / url_or_path
                        if fallback.exists():
                            url_or_path = str(fallback)
                        else:
                            raise ValueError(f"Path outside workspace: {url_or_path}")
                    else:
                        raise ValueError(f"Path outside workspace: {url_or_path}")

        return await load_web_media(
            url_or_path,
            max_bytes=self.max_bytes,
            workspace_root=self.workspace_root,
            allow_remote=self.allow_remote,
            local_roots=local_roots_list,  # Pass local_roots for security check
        )


__all__ = [
    "LoadedMedia",
    "load_web_media",
    "load_media",
    "MediaLoader",
    "LocalMediaAccessError",
]
