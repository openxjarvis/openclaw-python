"""Talk config helpers (mirrors TS src/config/talk.ts)."""
from __future__ import annotations

import copy
from typing import Any


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def normalize_talk_section(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    normalized: dict[str, Any] = {}
    speech_locale = _normalize_optional_string(value.get("speechLocale"))
    if speech_locale:
        normalized["speechLocale"] = speech_locale
    if isinstance(value.get("interruptOnSpeech"), bool):
        normalized["interruptOnSpeech"] = value["interruptOnSpeech"]
    silence = value.get("silenceTimeoutMs")
    if isinstance(silence, int) and silence > 0:
        normalized["silenceTimeoutMs"] = silence
    providers_raw = value.get("providers")
    if _is_record(providers_raw):
        providers: dict[str, Any] = {}
        for raw_id, raw_cfg in providers_raw.items():
            provider_id = _normalize_optional_string(raw_id)
            if provider_id and _is_record(raw_cfg):
                providers[provider_id] = dict(raw_cfg)
        if providers:
            normalized["providers"] = providers
    provider = _normalize_optional_string(value.get("provider"))
    if provider:
        normalized["provider"] = provider
    return normalized if normalized else None


def resolve_active_talk_provider_config(
    talk: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized = normalize_talk_section(talk)
    if not normalized:
        return None
    provider = _normalize_optional_string(normalized.get("provider"))
    providers = normalized.get("providers")
    if provider:
        if isinstance(providers, dict) and provider not in providers:
            return None
    elif isinstance(providers, dict):
        keys = list(providers.keys())
        provider = keys[0] if len(keys) == 1 else None
    if not provider:
        return None
    provider_cfg = providers.get(provider, {}) if isinstance(providers, dict) else {}
    return {"provider": provider, "config": provider_cfg if isinstance(provider_cfg, dict) else {}}


def build_talk_config_response(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    normalized = normalize_talk_section(value)
    legacy: dict[str, Any] = {}
    for key in ("voiceId", "voiceAliases", "modelId", "outputFormat", "apiKey"):
        if value.get(key) is not None:
            legacy[key] = value[key]
    if not normalized and not legacy:
        return None
    payload: dict[str, Any] = {}
    if normalized:
        for key in ("interruptOnSpeech", "silenceTimeoutMs", "speechLocale"):
            if key in normalized:
                payload[key] = normalized[key]
        if normalized.get("providers"):
            payload["providers"] = normalized["providers"]
        if normalized.get("provider"):
            payload["provider"] = normalized["provider"]
    resolved = resolve_active_talk_provider_config(normalized)
    if not resolved and legacy:
        resolved = {"provider": "elevenlabs", "config": legacy}
    if resolved:
        payload["resolved"] = resolved
        if not payload.get("provider"):
            payload["provider"] = resolved["provider"]
    return payload if payload else None


def redact_talk_config(talk: dict[str, Any]) -> dict[str, Any]:
    """Mask apiKey fields in talk provider configs."""
    redacted = copy.deepcopy(talk)
    providers = redacted.get("providers")
    if isinstance(providers, dict):
        for cfg in providers.values():
            if isinstance(cfg, dict) and "apiKey" in cfg:
                cfg["apiKey"] = "[redacted]"
    resolved = redacted.get("resolved")
    if isinstance(resolved, dict):
        cfg = resolved.get("config")
        if isinstance(cfg, dict) and "apiKey" in cfg:
            cfg["apiKey"] = "[redacted]"
    return redacted
