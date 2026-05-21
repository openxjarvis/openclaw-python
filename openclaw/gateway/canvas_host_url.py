"""Resolve canvas host URL exposed to connected clients.

Mirrors openclaw/src/infra/canvas-host-url.ts
"""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

Scheme = Literal["http", "https"]


def _is_loopback_host(host: str) -> bool:
    lowered = host.strip().lower()
    return lowered in ("localhost", "127.0.0.1", "::1") or lowered.startswith("127.")


def _normalize_host(value: str | None, reject_loopback: bool) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    if reject_loopback and _is_loopback_host(trimmed):
        return ""
    return trimmed


def _parse_host_header(value: str | None) -> tuple[str, int | None]:
    if not value:
        return "", None
    try:
        parsed = urlparse(f"http://{value.strip()}")
        port_raw = (parsed.port or "").strip() if parsed.port else ""
        port = int(port_raw) if port_raw else None
        return parsed.hostname or "", port
    except Exception:
        return "", None


def _parse_forwarded_proto(value: str | list[str] | None) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def resolve_canvas_host_url(
    *,
    canvas_port: int | None,
    host_override: str | None = None,
    request_host: str | None = None,
    forwarded_proto: str | list[str] | None = None,
    local_address: str | None = None,
    scheme: Scheme | None = None,
) -> str | None:
    port = canvas_port
    if not port:
        return None

    resolved_scheme: Scheme = scheme or (
        "https" if (_parse_forwarded_proto(forwarded_proto) or "").strip() == "https" else "http"
    )

    override = _normalize_host(host_override, reject_loopback=True)
    parsed_request_host, parsed_request_port = _parse_host_header(request_host)
    request_host_normalized = _normalize_host(parsed_request_host, reject_loopback=bool(override))
    local_address_normalized = _normalize_host(
        local_address, reject_loopback=bool(override or request_host_normalized)
    )

    host = override or request_host_normalized or local_address_normalized
    if not host:
        return None

    exposed_port = port
    if not override and request_host_normalized and port == 18789:
        if parsed_request_port and parsed_request_port > 0:
            exposed_port = parsed_request_port
        elif resolved_scheme == "https":
            exposed_port = 443
        elif resolved_scheme == "http":
            exposed_port = 80

    formatted = f"[{host}]" if ":" in host else host
    return f"{resolved_scheme}://{formatted}:{exposed_port}"
