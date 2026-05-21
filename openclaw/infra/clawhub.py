"""ClawHub registry API client — mirrors openclaw/src/infra/clawhub.ts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin

DEFAULT_CLAWHUB_URL = "https://clawhub.ai"
DEFAULT_FETCH_TIMEOUT_MS = 30_000


class ClawHubRequestError(Exception):
    def __init__(self, *, path: str, status: int, body: str) -> None:
        super().__init__(f"ClawHub {path} failed ({status}): {body}")
        self.status = status
        self.request_path = path
        self.response_body = body


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _normalize_base_url(base_url: str | None = None) -> str:
    env_value = (
        _normalize_optional_string(os.environ.get("OPENCLAW_CLAWHUB_URL"))
        or _normalize_optional_string(os.environ.get("CLAWHUB_URL"))
        or DEFAULT_CLAWHUB_URL
    )
    value = (_normalize_optional_string(base_url) or env_value).rstrip("/")
    return value or DEFAULT_CLAWHUB_URL


def _extract_token_from_clawhub_config(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("accessToken", "authToken", "apiToken", "token"):
        token = _normalize_optional_string(value.get(key))
        if token:
            return token
    for nested in ("auth", "session", "credentials", "user"):
        token = _extract_token_from_clawhub_config(value.get(nested))
        if token:
            return token
    return None


def _resolve_clawhub_config_paths() -> list[Path]:
    explicit = (
        _normalize_optional_string(os.environ.get("OPENCLAW_CLAWHUB_CONFIG_PATH"))
        or _normalize_optional_string(os.environ.get("CLAWHUB_CONFIG_PATH"))
        or _normalize_optional_string(os.environ.get("CLAWDHUB_CONFIG_PATH"))
    )
    if explicit:
        return [Path(explicit)]

    xdg_config_home = _normalize_optional_string(os.environ.get("XDG_CONFIG_HOME"))
    config_home = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    xdg_path = config_home / "clawhub" / "config.json"

    if platform.system() == "Darwin":
        return [
            Path.home() / "Library" / "Application Support" / "clawhub" / "config.json",
            xdg_path,
        ]
    return [xdg_path]


async def resolve_claw_hub_auth_token() -> str | None:
    env_token = (
        _normalize_optional_string(os.environ.get("OPENCLAW_CLAWHUB_TOKEN"))
        or _normalize_optional_string(os.environ.get("CLAWHUB_TOKEN"))
        or _normalize_optional_string(os.environ.get("CLAWHUB_AUTH_TOKEN"))
    )
    if env_token:
        return env_token

    for config_path in _resolve_clawhub_config_paths():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            token = _extract_token_from_clawhub_config(raw)
            if token:
                return token
        except OSError:
            continue
        except json.JSONDecodeError:
            continue
    return None


def resolve_claw_hub_base_url(base_url: str | None = None) -> str:
    return _normalize_base_url(base_url)


def format_sha256_integrity(data: bytes) -> str:
    digest = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
    return f"sha256-{digest}"


def normalize_claw_hub_sha256_integrity(value: str) -> str | None:
    trimmed = value.strip()
    if not trimmed:
        return None
    prefixed_base64 = re.match(r"^sha256-([A-Za-z0-9+/]+={0,2})$", trimmed)
    if prefixed_base64:
        try:
            decoded = base64.b64decode(prefixed_base64.group(1), validate=False)
            if len(decoded) == 32:
                return f"sha256-{base64.b64encode(decoded).decode('ascii')}"
        except Exception:
            return None
        return None
    prefixed_hex = re.match(r"^sha256:([A-Fa-f0-9]{64})$", trimmed)
    if prefixed_hex:
        return f"sha256-{base64.b64encode(bytes.fromhex(prefixed_hex.group(1))).decode('ascii')}"
    if re.fullmatch(r"[A-Fa-f0-9]{64}", trimmed):
        return f"sha256-{base64.b64encode(bytes.fromhex(trimmed)).decode('ascii')}"
    return None


def normalize_claw_hub_sha256_hex(value: str) -> str | None:
    trimmed = value.strip()
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", trimmed):
        return None
    return trimmed.lower()


def _build_url(
    *,
    base_url: str | None,
    path: str,
    search: dict[str, str | None] | None = None,
) -> str:
    root = f"{_normalize_base_url(base_url)}/"
    url = urljoin(root, path.lstrip("/"))
    if search:
        params = {k: v for k, v in search.items() if v}
        if params:
            url = f"{url}?{urlencode(params)}"
    return url


async def _fetch_json(
    *,
    path: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    search: dict[str, str | None] | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> Any:
    import httpx

    url = _build_url(base_url=base_url, path=path, search=search)
    auth_token = _normalize_optional_string(token) or await resolve_claw_hub_auth_token()
    timeout = (timeout_ms or DEFAULT_FETCH_TIMEOUT_MS) / 1000

    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None

    async def default_fetch() -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers)

    response = await (fetch_impl(url, headers=headers, timeout=timeout) if fetch_impl else default_fetch())
    if response.status_code >= 400:
        try:
            body = (response.text or "").strip() or response.reason_phrase or f"HTTP {response.status_code}"
        except Exception:
            body = response.reason_phrase or f"HTTP {response.status_code}"
        raise ClawHubRequestError(path=path, status=response.status_code, body=body)
    return response.json()


async def search_claw_hub_skills(
    *,
    query: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    limit: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    result = await _fetch_json(
        path="/api/v1/search",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        search={
            "q": query.strip(),
            "limit": str(limit) if limit else None,
        },
        fetch_impl=fetch_impl,
    )
    return list((result or {}).get("results") or [])


async def fetch_claw_hub_skill_detail(
    *,
    slug: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return await _fetch_json(
        path=f"/api/v1/skills/{slug}",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        fetch_impl=fetch_impl,
    )


async def list_claw_hub_skills(
    *,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    limit: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return await _fetch_json(
        path="/api/v1/skills",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        search={"limit": str(limit) if limit else None},
        fetch_impl=fetch_impl,
    )


async def _download_bytes(
    *,
    path: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    search: dict[str, str | None] | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> bytes:
    import httpx

    url = _build_url(base_url=base_url, path=path, search=search)
    auth_token = _normalize_optional_string(token) or await resolve_claw_hub_auth_token()
    timeout = (timeout_ms or DEFAULT_FETCH_TIMEOUT_MS) / 1000
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None

    async def default_fetch() -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(url, headers=headers)

    response = await (fetch_impl(url, headers=headers, timeout=timeout) if fetch_impl else default_fetch())
    if response.status_code >= 400:
        try:
            body = (response.text or "").strip() or response.reason_phrase or f"HTTP {response.status_code}"
        except Exception:
            body = response.reason_phrase or f"HTTP {response.status_code}"
        raise ClawHubRequestError(path=path, status=response.status_code, body=body)
    return bytes(response.content)


@dataclass
class ClawHubDownloadResult:
    archive_path: str
    integrity: str
    cleanup: Callable[[], Any]


async def download_claw_hub_skill_archive(
    *,
    slug: str,
    version: str | None = None,
    tag: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> ClawHubDownloadResult:
    data = await _download_bytes(
        path="/api/v1/download",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        search={
            "slug": slug,
            "version": version,
            "tag": None if version else tag,
        },
        fetch_impl=fetch_impl,
    )
    fd, archive_path = tempfile.mkstemp(prefix="openclaw-clawhub-skill-", suffix=f"-{slug}.zip")
    os.close(fd)
    path = Path(archive_path)
    path.write_bytes(data)

    def cleanup() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return ClawHubDownloadResult(
        archive_path=str(path),
        integrity=format_sha256_integrity(data),
        cleanup=cleanup,
    )


async def download_claw_hub_package_archive(
    *,
    name: str,
    version: str | None = None,
    tag: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> ClawHubDownloadResult:
    search: dict[str, str | None]
    if version:
        search = {"version": version}
    elif tag:
        search = {"tag": tag}
    else:
        search = {}
    data = await _download_bytes(
        path=f"/api/v1/packages/{name}/download",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        search=search,
        fetch_impl=fetch_impl,
    )
    fd, archive_path = tempfile.mkstemp(prefix="openclaw-clawhub-package-", suffix=f"-{name}.zip")
    os.close(fd)
    path = Path(archive_path)
    path.write_bytes(data)

    def cleanup() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return ClawHubDownloadResult(
        archive_path=str(path),
        integrity=format_sha256_integrity(data),
        cleanup=cleanup,
    )


async def search_claw_hub_packages(
    *,
    query: str,
    family: str | None = None,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    limit: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    result = await _fetch_json(
        path="/api/v1/packages/search",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        search={
            "q": query.strip(),
            "family": family,
            "limit": str(limit) if limit else None,
        },
        fetch_impl=fetch_impl,
    )
    return list((result or {}).get("results") or [])


async def fetch_claw_hub_package_detail(
    *,
    name: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return await _fetch_json(
        path=f"/api/v1/packages/{name}",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        fetch_impl=fetch_impl,
    )


async def fetch_claw_hub_package_version(
    *,
    name: str,
    version: str,
    base_url: str | None = None,
    token: str | None = None,
    timeout_ms: int | None = None,
    fetch_impl: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    return await _fetch_json(
        path=f"/api/v1/packages/{name}/versions/{version}",
        base_url=base_url,
        token=token,
        timeout_ms=timeout_ms,
        fetch_impl=fetch_impl,
    )


__all__ = [
    "ClawHubDownloadResult",
    "ClawHubRequestError",
    "DEFAULT_CLAWHUB_URL",
    "download_claw_hub_package_archive",
    "download_claw_hub_skill_archive",
    "fetch_claw_hub_package_detail",
    "fetch_claw_hub_package_version",
    "fetch_claw_hub_skill_detail",
    "format_sha256_integrity",
    "list_claw_hub_skills",
    "normalize_claw_hub_sha256_hex",
    "normalize_claw_hub_sha256_integrity",
    "resolve_claw_hub_auth_token",
    "resolve_claw_hub_base_url",
    "search_claw_hub_packages",
    "search_claw_hub_skills",
]
