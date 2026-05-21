"""TTS config resolution (mirrors TS tts-config.ts)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_state_dir, resolve_user_path
from openclaw.tts.tts_auto_mode import TtsAutoMode, normalize_tts_auto_mode

BLOCKED_MERGE_KEYS = frozenset({"__proto__", "prototype", "constructor"})
DEFAULT_MAX_TEXT_LENGTH = 4096
DEFAULT_TIMEOUT_MS = 30_000


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _deep_merge_defined(base: Any, override: Any) -> Any:
    if not _is_plain_object(base) or not _is_plain_object(override):
        return override if override is not None else base
    result = dict(base)
    for key, value in override.items():
        if key in BLOCKED_MERGE_KEYS or value is None:
            continue
        existing = result.get(key)
        result[key] = _deep_merge_defined(existing, value) if key in result else value
    return result


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _normalize_agent_id(value: str) -> str:
    from openclaw.routing.session_key import normalize_agent_id

    return normalize_agent_id(value)


def _normalize_account_id(value: str) -> str:
    from openclaw.routing.session_key import normalize_account_id

    return normalize_account_id(value)


def _resolve_record_entry(
    entries: dict[str, Any] | None,
    entry_id: str | None,
    normalize_fn: Any,
) -> Any:
    normalized_id = _normalize_optional_string(entry_id)
    if not entries or not normalized_id:
        return None
    if normalized_id in entries:
        return entries[normalized_id]
    normalized = normalize_fn(normalized_id)
    for candidate, value in entries.items():
        if normalize_fn(candidate) == normalized:
            return value
    return None


def resolve_effective_tts_config(
    cfg: dict[str, Any],
    *,
    agent_id: str | None = None,
    channel_id: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    base = (cfg.get("messages") or {}).get("tts") or {}
    if not isinstance(base, dict):
        base = {}

    agent_override: dict[str, Any] | None = None
    agents = cfg.get("agents")
    if agent_id and isinstance(agents, dict):
        agent_list = agents.get("list")
        if isinstance(agent_list, list):
            norm = _normalize_agent_id(agent_id)
            for entry in agent_list:
                if isinstance(entry, dict) and _normalize_agent_id(str(entry.get("id", ""))) == norm:
                    tts = entry.get("tts")
                    if isinstance(tts, dict):
                        agent_override = tts
                    break

    channel_override: dict[str, Any] | None = None
    account_override: dict[str, Any] | None = None
    channels = cfg.get("channels")
    if isinstance(channels, dict):
        channel_cfg = _resolve_record_entry(
            channels,
            channel_id,
            lambda v: v.strip().lower() if isinstance(v, str) else "",
        )
        if isinstance(channel_cfg, dict):
            tts = channel_cfg.get("tts")
            if isinstance(tts, dict):
                channel_override = tts
            accounts = channel_cfg.get("accounts")
            if isinstance(accounts, dict):
                account_cfg = _resolve_record_entry(accounts, account_id, _normalize_account_id)
                if isinstance(account_cfg, dict):
                    tts = account_cfg.get("tts")
                    if isinstance(tts, dict):
                        account_override = tts

    merged: Any = base
    for override in (agent_override, channel_override, account_override):
        merged = _deep_merge_defined(merged, override or {})
    return merged if isinstance(merged, dict) else {}


def resolve_tts_prefs_path_value(prefs_path: str | None = None) -> str:
    configured = _normalize_optional_string(prefs_path)
    if configured:
        return resolve_user_path(configured)
    env_path = _normalize_optional_string(os.environ.get("OPENCLAW_TTS_PREFS"))
    if env_path:
        return resolve_user_path(env_path)
    return str(Path(resolve_state_dir()) / "settings" / "tts.json")
