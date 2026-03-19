"""Core pairing store — aligned with TypeScript openclaw/src/pairing/pairing-store.ts.

Provides account-scoped pairing request management and allowFrom list operations.
JSON files use camelCase field names for interoperability with the TS gateway.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_state_dir
from .code_generator import generate_pairing_code, normalize_pairing_code
from .types import ChannelPairingAdapter, PairingRequest

logger = logging.getLogger(__name__)

# ── Constants (mirrors TS) ──────────────────────────────────────────────────

PAIRING_CODE_LENGTH = 8
PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_PENDING_TTL_MS: int = 60 * 60 * 1_000  # 1 hour
PAIRING_PENDING_MAX: int = 3


# ── Path helpers ────────────────────────────────────────────────────────────

def _resolve_credentials_dir() -> Path:
    """Return the credentials directory (mirrors TS resolveOAuthDir → stateDir/credentials)."""
    state_dir = resolve_state_dir()
    # Respect explicit override first (env var kept for compatibility)
    oauth_override = os.environ.get("OPENCLAW_OAUTH_DIR", "").strip()
    if oauth_override:
        creds = Path(oauth_override).expanduser().resolve()
    else:
        # TS uses stateDir/credentials (src/config/paths.ts resolveOAuthDir)
        creds = Path(state_dir) / "credentials"
    creds.mkdir(parents=True, exist_ok=True)
    return creds


def _safe_channel_key(channel: str) -> str:
    """Sanitize channel ID for use in file names (mirrors safeChannelKey)."""
    raw = str(channel).strip().lower()
    if not raw:
        raise ValueError("invalid pairing channel")
    safe = raw.replace("\\", "_").replace("/", "_").replace(":", "_") \
               .replace("*", "_").replace("?", "_").replace('"', "_") \
               .replace("<", "_").replace(">", "_").replace("|", "_") \
               .replace("..", "_")
    if not safe or safe == "_":
        raise ValueError("invalid pairing channel")
    return safe


def _safe_account_key(account_id: str) -> str:
    """Sanitize account ID for use in file names (mirrors safeAccountKey)."""
    raw = str(account_id).strip().lower()
    if not raw:
        raise ValueError("invalid pairing account id")
    safe = raw.replace("\\", "_").replace("/", "_").replace(":", "_") \
               .replace("*", "_").replace("?", "_").replace('"', "_") \
               .replace("<", "_").replace(">", "_").replace("|", "_") \
               .replace("..", "_")
    if not safe or safe == "_":
        raise ValueError("invalid pairing account id")
    return safe


def _resolve_pairing_path(channel: str) -> Path:
    """Return path to pairing requests file for *channel*."""
    return _resolve_credentials_dir() / f"{_safe_channel_key(channel)}-pairing.json"


def _resolve_allow_from_path(channel: str, account_id: str | None = None) -> Path:
    """Return path to allowFrom file, optionally scoped to an account.

    Mirrors TS resolveAllowFromPath().

    TS alignment: only empty / None account_id produces the unscoped filename
    (telegram-allowFrom.json).  Any non-empty string — including "default" —
    is treated as a real account ID and included in the filename.
    """
    base = _safe_channel_key(channel)
    normalized = (account_id or "").strip()
    # Treat "default" as no account (produces unscoped filename), matching TS
    if not normalized or normalized.lower() == "default":
        return _resolve_credentials_dir() / f"{base}-allowFrom.json"
    return _resolve_credentials_dir() / f"{base}-{_safe_account_key(normalized)}-allowFrom.json"


# ── JSON helpers ────────────────────────────────────────────────────────────

def _read_json_file(path: Path, fallback: Any) -> Any:
    """Read JSON from *path*, returning *fallback* if missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def _write_json_file(path: Path, value: Any) -> None:
    """Atomically write JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ── Expiry helpers ──────────────────────────────────────────────────────────

def _parse_timestamp(value: str | None) -> float | None:
    """Parse ISO timestamp → epoch ms, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp() * 1_000
    except (ValueError, AttributeError):
        return None


def _is_expired(entry: PairingRequest, now_ms: float) -> bool:
    """Return True when *entry* is older than PAIRING_PENDING_TTL_MS."""
    created = _parse_timestamp(entry.created_at)
    if created is None:
        return True
    return now_ms - created > PAIRING_PENDING_TTL_MS


def _prune_expired(
    requests: list[PairingRequest],
    now_ms: float,
) -> tuple[list[PairingRequest], bool]:
    kept = [r for r in requests if not _is_expired(r, now_ms)]
    return kept, len(kept) != len(requests)


def _resolve_last_seen_ms(entry: PairingRequest) -> float:
    return (
        _parse_timestamp(entry.last_seen_at)
        or _parse_timestamp(entry.created_at)
        or 0.0
    )


def _prune_excess(
    requests: list[PairingRequest],
    max_pending: int,
) -> tuple[list[PairingRequest], bool]:
    if max_pending <= 0 or len(requests) <= max_pending:
        return requests, False
    sorted_by_seen = sorted(requests, key=_resolve_last_seen_ms)
    return sorted_by_seen[-max_pending:], True


# ── Pairing requests I/O ────────────────────────────────────────────────────

def _read_pairing_requests(path: Path) -> list[PairingRequest]:
    raw = _read_json_file(path, {"version": 1, "requests": []})
    items = raw.get("requests") if isinstance(raw, dict) else []
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            result.append(PairingRequest.from_dict(item))
        except (KeyError, TypeError):
            pass
    return result


def _write_pairing_requests(path: Path, requests: list[PairingRequest]) -> None:
    _write_json_file(path, {"version": 1, "requests": [r.to_dict() for r in requests]})


# ── AllowFrom I/O ───────────────────────────────────────────────────────────

def _normalize_allow_entry(channel: str, entry: str) -> str:
    """Normalize a single allowFrom entry via optional channel adapter."""
    trimmed = entry.strip()
    if not trimmed or trimmed == "*":
        return ""
    try:
        from openclaw.channels.plugins.pairing import get_pairing_adapter
        adapter = get_pairing_adapter(channel)
        if adapter and hasattr(adapter, "normalize_allow_entry") and callable(adapter.normalize_allow_entry):
            normalized = adapter.normalize_allow_entry(trimmed)
            return str(normalized).strip()
    except Exception:
        pass
    return trimmed


def _read_allow_from_file(channel: str, path: Path) -> list[str]:
    raw = _read_json_file(path, {"version": 1, "allowFrom": []})
    items = raw.get("allowFrom") if isinstance(raw, dict) else []
    if not isinstance(items, list):
        return []
    normalized = [_normalize_allow_entry(channel, str(v)) for v in items]
    return [v for v in normalized if v]


def _write_allow_from_file(path: Path, entries: list[str]) -> None:
    _write_json_file(path, {"version": 1, "allowFrom": entries})


def _dedupe_preserve_order(entries: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in entries:
        n = e.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ── Account ID normalization ────────────────────────────────────────────────

def _normalize_pairing_account_id(account_id: str | None) -> str:
    return (account_id or "").strip().lower()


def _request_matches_account(entry: PairingRequest, normalized_account_id: str) -> bool:
    if not normalized_account_id:
        return True
    stored = str(entry.meta.get("accountId") or "").strip().lower()
    return stored == normalized_account_id


# ── Public API ──────────────────────────────────────────────────────────────

def upsert_channel_pairing_request(
    channel: str,
    sender_id: str | int,
    account_id: str | None = None,
    meta: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Create or refresh a pending pairing request.

    Returns ``{"code": str, "created": bool}``.
    Returns ``{"code": "", "created": False}`` when the pending cap is reached
    and all slots are filled (mirrors TS behaviour).

    Mirrors TS upsertChannelPairingRequest().
    """
    path = _resolve_pairing_path(channel)
    now = datetime.now(timezone.utc).isoformat()
    now_ms = time.time() * 1_000
    id_str = str(sender_id).strip()

    normalized_account_id = (account_id or "").strip()

    # Build meta dict (strip None values)
    base_meta: dict[str, str] = {}
    if meta and isinstance(meta, dict):
        for k, v in meta.items():
            trimmed = str(v or "").strip()
            if trimmed:
                base_meta[k] = trimmed
    if normalized_account_id:
        base_meta["accountId"] = normalized_account_id

    reqs = _read_pairing_requests(path)
    reqs, _ = _prune_expired(reqs, now_ms)

    existing_idx = next((i for i, r in enumerate(reqs) if r.id == id_str), -1)
    existing_codes = {r.code.strip().upper() for r in reqs}

    if existing_idx >= 0:
        existing = reqs[existing_idx]
        code = existing.code.strip() or generate_pairing_code(existing_codes)
        updated = PairingRequest(
            id=id_str,
            code=code,
            created_at=existing.created_at,
            last_seen_at=now,
            meta=base_meta or existing.meta,
        )
        reqs[existing_idx] = updated
        reqs, _ = _prune_excess(reqs, PAIRING_PENDING_MAX)
        _write_pairing_requests(path, reqs)
        return {"code": code, "created": False}

    # Check cap before adding
    reqs, _ = _prune_excess(reqs, PAIRING_PENDING_MAX)
    if PAIRING_PENDING_MAX > 0 and len(reqs) >= PAIRING_PENDING_MAX:
        _write_pairing_requests(path, reqs)
        return {"code": "", "created": False}

    code = generate_pairing_code(existing_codes)
    new_req = PairingRequest(
        id=id_str,
        code=code,
        created_at=now,
        last_seen_at=now,
        meta=base_meta,
    )
    reqs.append(new_req)
    _write_pairing_requests(path, reqs)
    logger.info("Created pairing request for %s:%s code=%s", channel, id_str, code)
    return {"code": code, "created": True}


def list_channel_pairing_requests(
    channel: str,
    account_id: str | None = None,
) -> list[PairingRequest]:
    """List pending pairing requests, optionally filtered by account.

    Prunes expired/excess requests before returning.
    Mirrors TS listChannelPairingRequests().
    """
    path = _resolve_pairing_path(channel)
    now_ms = time.time() * 1_000

    reqs = _read_pairing_requests(path)
    reqs, expired_removed = _prune_expired(reqs, now_ms)
    reqs, capped_removed = _prune_excess(reqs, PAIRING_PENDING_MAX)

    if expired_removed or capped_removed:
        _write_pairing_requests(path, reqs)

    normalized_account_id = _normalize_pairing_account_id(account_id)
    filtered = (
        [r for r in reqs if _request_matches_account(r, normalized_account_id)]
        if normalized_account_id
        else reqs
    )
    # Validate and sort by createdAt ascending
    valid = [
        r for r in filtered
        if r.id and r.code and r.created_at
    ]
    return sorted(valid, key=lambda r: r.created_at)


def approve_channel_pairing_code(
    channel: str,
    code: str,
    account_id: str | None = None,
) -> dict[str, Any] | None:
    """Approve a pairing code and add the sender to the allowFrom list.

    Returns ``{"id": str, "entry": PairingRequest}`` or None if not found.
    Mirrors TS approveChannelPairingCode().
    """
    code_norm = normalize_pairing_code(code)
    if not code_norm:
        return None

    path = _resolve_pairing_path(channel)
    now_ms = time.time() * 1_000

    reqs = _read_pairing_requests(path)
    reqs, removed = _prune_expired(reqs, now_ms)

    normalized_account_id = _normalize_pairing_account_id(account_id)
    idx = next(
        (
            i for i, r in enumerate(reqs)
            if normalize_pairing_code(r.code) == code_norm
            and _request_matches_account(r, normalized_account_id)
        ),
        -1,
    )
    if idx < 0:
        if removed:
            _write_pairing_requests(path, reqs)
        return None

    entry = reqs.pop(idx)
    _write_pairing_requests(path, reqs)

    # Determine effective account scope for allowFrom
    entry_account_id = str(entry.meta.get("accountId") or "").strip() or None
    effective_account_id = (account_id or "").strip() or entry_account_id

    add_channel_allow_from_store_entry(channel, entry.id, account_id=effective_account_id)
    logger.info("Approved pairing code %s for %s:%s", code_norm, channel, entry.id)
    return {"id": entry.id, "entry": entry}


def read_channel_allow_from_store(
    channel: str,
    account_id: str | None = None,
) -> list[str]:
    """Read the allowFrom list for *channel*, optionally scoped to *account_id*.

    When *account_id* is given, returns account-scoped entries merged with the
    legacy channel-level entries (backward-compat with pre-account-scope stores).
    Mirrors TS readChannelAllowFromStore().
    """
    normalized_account_id = (account_id or "").strip()
    if not normalized_account_id:
        return _read_allow_from_file(channel, _resolve_allow_from_path(channel))

    scoped_path = _resolve_allow_from_path(channel, normalized_account_id)
    scoped = _read_allow_from_file(channel, scoped_path)

    legacy_path = _resolve_allow_from_path(channel)
    legacy = _read_allow_from_file(channel, legacy_path)

    return _dedupe_preserve_order(scoped + legacy)


def add_channel_allow_from_store_entry(
    channel: str,
    entry: str | int,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Add *entry* to the allowFrom list for *channel*.

    Returns ``{"changed": bool, "allow_from": list[str]}``.
    Mirrors TS addChannelAllowFromStoreEntry().
    """
    normalized_entry = _normalize_allow_entry(channel, str(entry))
    if not normalized_entry:
        current = read_channel_allow_from_store(channel, account_id)
        return {"changed": False, "allow_from": current}

    path = _resolve_allow_from_path(channel, (account_id or "").strip() or None)
    current = _read_allow_from_file(channel, path)
    if normalized_entry in current:
        return {"changed": False, "allow_from": current}

    updated = _dedupe_preserve_order(current + [normalized_entry])
    _write_allow_from_file(path, updated)
    logger.info("Added %s to %s allowFrom", normalized_entry, channel)
    return {"changed": True, "allow_from": updated}


def remove_channel_allow_from_store_entry(
    channel: str,
    entry: str | int,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Remove *entry* from the allowFrom list for *channel*.

    Returns ``{"changed": bool, "allow_from": list[str]}``.
    Mirrors TS removeChannelAllowFromStoreEntry().
    """
    normalized_entry = _normalize_allow_entry(channel, str(entry))
    if not normalized_entry:
        current = read_channel_allow_from_store(channel, account_id)
        return {"changed": False, "allow_from": current}

    path = _resolve_allow_from_path(channel, (account_id or "").strip() or None)
    current = _read_allow_from_file(channel, path)
    updated = [e for e in current if e != normalized_entry]
    if len(updated) == len(current):
        return {"changed": False, "allow_from": current}

    _write_allow_from_file(path, updated)
    logger.info("Removed %s from %s allowFrom", normalized_entry, channel)
    return {"changed": True, "allow_from": updated}


# ── Legacy shim: keep old snake_case function names working ─────────────────

def add_channel_allow_from_entry(
    channel: str,
    entry: str,
    adapter: ChannelPairingAdapter | None = None,
) -> None:
    """Legacy compatibility wrapper (use add_channel_allow_from_store_entry)."""
    if adapter:
        entry = adapter.normalize_entry(entry)
    add_channel_allow_from_store_entry(channel, entry)


def remove_channel_allow_from_entry(
    channel: str,
    entry: str,
    adapter: ChannelPairingAdapter | None = None,
) -> bool:
    if adapter:
        entry = adapter.normalize_entry(entry)
    result = remove_channel_allow_from_store_entry(channel, entry)
    return result["changed"]


def read_channel_allow_from_store_legacy(
    channel: str,
    config_entries: list[str] | None = None,
    adapter: ChannelPairingAdapter | None = None,
) -> list[str]:
    """Legacy wrapper that merges config entries with store entries."""
    store_entries = read_channel_allow_from_store(channel)
    all_entries = list(config_entries or []) + store_entries
    if adapter:
        all_entries = [adapter.normalize_entry(e) for e in all_entries]
    return _dedupe_preserve_order(all_entries)


__all__ = [
    "PAIRING_CODE_LENGTH",
    "PAIRING_CODE_ALPHABET",
    "PAIRING_PENDING_TTL_MS",
    "PAIRING_PENDING_MAX",
    "upsert_channel_pairing_request",
    "list_channel_pairing_requests",
    "approve_channel_pairing_code",
    "read_channel_allow_from_store",
    "add_channel_allow_from_store_entry",
    "remove_channel_allow_from_store_entry",
    "add_channel_allow_from_entry",
    "remove_channel_allow_from_entry",
]
