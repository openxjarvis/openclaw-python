"""Canvas capability tokens and scoped host URLs.

Mirrors openclaw/src/gateway/canvas-capability.ts
"""
from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

CANVAS_CAPABILITY_PATH_PREFIX = "/__openclaw__/cap"
CANVAS_CAPABILITY_QUERY_PARAM = "oc_cap"
CANVAS_CAPABILITY_TTL_MS = 10 * 60_000


@dataclass
class NormalizedCanvasScopedUrl:
    pathname: str
    capability: str | None = None
    rewritten_url: str | None = None
    scoped_path: bool = False
    malformed_scoped_path: bool = False


def _normalize_capability(raw: str | None) -> str | None:
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed if trimmed else None


def mint_canvas_capability_token() -> str:
    """Mint a URL-safe capability token (18 random bytes, base64url)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(18)).decode("ascii").rstrip("=")


def build_canvas_scoped_host_url(base_url: str, capability: str) -> str | None:
    normalized_capability = _normalize_capability(capability)
    if not normalized_capability:
        return None
    try:
        parsed = urlparse(base_url)
        trimmed_path = parsed.path.rstrip("/")
        prefix = f"{CANVAS_CAPABILITY_PATH_PREFIX}/{quote(normalized_capability, safe='')}"
        new_path = f"{trimmed_path}{prefix}"
        rebuilt = parsed._replace(path=new_path, query="", fragment="")
        result = urlunparse(rebuilt)
        return result.rstrip("/")
    except Exception:
        return None


def normalize_canvas_scoped_url(raw_url: str) -> NormalizedCanvasScopedUrl:
    parsed = urlparse(raw_url if "://" in raw_url else f"http://localhost{raw_url if raw_url.startswith('/') else '/' + raw_url}")
    prefix = f"{CANVAS_CAPABILITY_PATH_PREFIX}/"
    scoped_path = False
    malformed_scoped_path = False
    capability_from_path: str | None = None
    rewritten_url: str | None = None

    if parsed.path.startswith(prefix):
        scoped_path = True
        remainder = parsed.path[len(prefix) :]
        slash_index = remainder.find("/")
        if slash_index <= 0:
            malformed_scoped_path = True
        else:
            encoded_capability = remainder[:slash_index]
            canonical_path = remainder[slash_index:] or "/"
            try:
                decoded = unquote(encoded_capability)
            except Exception:
                malformed_scoped_path = True
                decoded = None
            if not malformed_scoped_path:
                capability_from_path = _normalize_capability(decoded)
                if not capability_from_path or not canonical_path.startswith("/"):
                    malformed_scoped_path = True
                else:
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    if CANVAS_CAPABILITY_QUERY_PARAM not in query:
                        query[CANVAS_CAPABILITY_QUERY_PARAM] = [capability_from_path]
                    flat_query = urlencode(query, doseq=True)
                    rewritten_url = f"{canonical_path}{('?' + flat_query) if flat_query else ''}"

    query_params = parse_qs(parsed.query, keep_blank_values=True)
    query_cap = query_params.get(CANVAS_CAPABILITY_QUERY_PARAM, [None])[0]
    capability = capability_from_path or _normalize_capability(query_cap)
    return NormalizedCanvasScopedUrl(
        pathname=parsed.path,
        capability=capability,
        rewritten_url=rewritten_url,
        scoped_path=scoped_path,
        malformed_scoped_path=malformed_scoped_path,
    )
