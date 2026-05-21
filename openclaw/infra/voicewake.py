"""Voice wake trigger persistence (mirrors TS src/infra/voicewake.ts)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_state_dir
from openclaw.infra.json_files import read_json_file, with_file_lock, write_json_atomic

DEFAULT_TRIGGERS = ["openclaw", "claude", "computer"]


@dataclass
class VoiceWakeConfig:
    triggers: list[str]
    updated_at_ms: int


def _resolve_path(base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else resolve_state_dir()
    return Path(root) / "settings" / "voicewake.json"


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _sanitize_triggers(triggers: list[str] | None) -> list[str]:
    cleaned = [
        w
        for w in (_normalize_optional_string(item) or "" for item in (triggers or []))
        if w
    ]
    return cleaned if cleaned else list(DEFAULT_TRIGGERS)


def default_voice_wake_triggers() -> list[str]:
    return list(DEFAULT_TRIGGERS)


async def load_voice_wake_config(base_dir: Path | None = None) -> VoiceWakeConfig:
    file_path = _resolve_path(base_dir)
    existing = read_json_file(file_path)
    if not existing or not isinstance(existing, dict):
        return VoiceWakeConfig(triggers=default_voice_wake_triggers(), updated_at_ms=0)
    raw_triggers = existing.get("triggers")
    triggers = _sanitize_triggers(raw_triggers if isinstance(raw_triggers, list) else None)
    updated = existing.get("updatedAtMs")
    updated_at_ms = int(updated) if isinstance(updated, (int, float)) and updated > 0 else 0
    return VoiceWakeConfig(triggers=triggers, updated_at_ms=updated_at_ms)


async def set_voice_wake_triggers(
    triggers: list[str],
    base_dir: Path | None = None,
) -> VoiceWakeConfig:
    sanitized = _sanitize_triggers(triggers)
    file_path = _resolve_path(base_dir)

    def _write() -> VoiceWakeConfig:
        import time

        next_cfg = VoiceWakeConfig(triggers=sanitized, updated_at_ms=int(time.time() * 1000))
        write_json_atomic(
            file_path,
            {"triggers": next_cfg.triggers, "updatedAtMs": next_cfg.updated_at_ms},
        )
        return next_cfg

    return with_file_lock(file_path, _write)
