"""TTS status payload for gateway tts.status (mirrors TS tts.ts handler)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openclaw.tts.provider_registry import (
    canonicalize_speech_provider_id,
    get_resolved_speech_provider_config,
    get_speech_provider,
    list_speech_providers,
)
from openclaw.tts.tts_auto_mode import normalize_tts_auto_mode
from openclaw.tts.tts_config import resolve_effective_tts_config, resolve_tts_prefs_path_value


def _read_prefs(prefs_path: str) -> dict[str, Any]:
    try:
        if not Path(prefs_path).exists():
            return {}
        return json.loads(Path(prefs_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_configured_auto(raw: dict[str, Any]) -> str:
    auto = normalize_tts_auto_mode(raw.get("auto"))
    if auto:
        return auto
    if raw.get("enabled") is True:
        return "always"
    return "off"


def _resolve_auto_mode(config: dict[str, Any], prefs_path: str) -> str:
    prefs = _read_prefs(prefs_path)
    tts_prefs = prefs.get("tts") if isinstance(prefs.get("tts"), dict) else {}
    auto = normalize_tts_auto_mode(tts_prefs.get("auto"))
    if auto:
        return auto
    if isinstance(tts_prefs.get("enabled"), bool):
        return "always" if tts_prefs["enabled"] else "off"
    return _resolve_configured_auto(config)


def _get_provider(config: dict[str, Any], prefs_path: str, cfg: dict[str, Any]) -> str:
    prefs = _read_prefs(prefs_path)
    tts_prefs = prefs.get("tts") if isinstance(prefs.get("tts"), dict) else {}
    prefs_provider = canonicalize_speech_provider_id(tts_prefs.get("provider"), cfg)
    if prefs_provider:
        return prefs_provider
    configured = canonicalize_speech_provider_id(config.get("provider"), cfg)
    if configured:
        return configured
    for candidate in list_speech_providers(cfg):
        provider_config = get_resolved_speech_provider_config(config, candidate.id, cfg)
        if candidate.is_configured(cfg=cfg, provider_config=provider_config, timeout_ms=_timeout_ms(config)):
            return candidate.id
    return configured or "openai"


def _timeout_ms(config: dict[str, Any]) -> int:
    raw = config.get("timeoutMs")
    return int(raw) if isinstance(raw, (int, float)) and raw > 0 else 30_000


def _list_personas(config: dict[str, Any]) -> list[dict[str, Any]]:
    personas_raw = config.get("personas")
    if not isinstance(personas_raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for persona_id, persona in personas_raw.items():
        if not isinstance(persona, dict):
            continue
        out.append(
            {
                "id": str(persona_id).lower(),
                "label": persona.get("label") or persona_id,
                "description": persona.get("description"),
                "provider": persona.get("provider"),
            }
        )
    out.sort(key=lambda p: p["id"])
    return out


def _get_persona_id(config: dict[str, Any], prefs_path: str) -> str | None:
    prefs = _read_prefs(prefs_path)
    tts_prefs = prefs.get("tts") if isinstance(prefs.get("tts"), dict) else {}
    if "persona" in tts_prefs:
        raw = tts_prefs.get("persona")
        if raw is None:
            return None
        return str(raw).strip().lower() if str(raw).strip() else None
    raw = config.get("persona")
    return str(raw).strip().lower() if isinstance(raw, str) and raw.strip() else None


def _resolve_provider_order(primary: str, cfg: dict[str, Any]) -> list[str]:
    order = [primary]
    for candidate in list_speech_providers(cfg):
        if candidate.id != primary:
            order.append(candidate.id)
    return order


def build_tts_status_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build gateway tts.status response payload."""
    config = resolve_effective_tts_config(cfg)
    prefs_path = resolve_tts_prefs_path_value(config.get("prefsPath"))
    provider = _get_provider(config, prefs_path, cfg)
    auto_mode = _resolve_auto_mode(config, prefs_path)
    fallback_providers = [
        p
        for p in _resolve_provider_order(provider, cfg)[1:]
        if _is_provider_configured(config, p, cfg)
    ]
    provider_states = []
    for candidate in list_speech_providers(cfg):
        provider_config = get_resolved_speech_provider_config(config, candidate.id, cfg)
        provider_states.append(
            {
                "id": candidate.id,
                "label": candidate.label,
                "configured": candidate.is_configured(
                    cfg=cfg,
                    provider_config=provider_config,
                    timeout_ms=_timeout_ms(config),
                ),
            }
        )
    return {
        "enabled": auto_mode != "off",
        "auto": auto_mode,
        "provider": provider,
        "persona": _get_persona_id(config, prefs_path),
        "personas": _list_personas(config),
        "fallbackProvider": fallback_providers[0] if fallback_providers else None,
        "fallbackProviders": fallback_providers,
        "prefsPath": prefs_path,
        "providerStates": provider_states,
    }


def _is_provider_configured(config: dict[str, Any], provider_id: str, cfg: dict[str, Any]) -> bool:
    speech = get_speech_provider(provider_id, cfg)
    if not speech:
        return False
    provider_config = get_resolved_speech_provider_config(config, provider_id, cfg)
    return speech.is_configured(cfg=cfg, provider_config=provider_config, timeout_ms=_timeout_ms(config))
