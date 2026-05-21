"""Speech provider registry (minimal TS-aligned subset)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class SpeechProviderInfo:
    id: str
    label: str
    models: list[str]
    voices: list[str]

    def is_configured(
        self,
        *,
        cfg: dict[str, Any],
        provider_config: dict[str, Any],
        timeout_ms: int,
    ) -> bool:
        _ = timeout_ms
        if self.id == "openai":
            key = (
                provider_config.get("apiKey")
                or os.environ.get("OPENAI_API_KEY")
            )
            return bool(_normalize_secret(key))
        if self.id in ("elevenlabs", "11labs"):
            key = provider_config.get("apiKey") or os.environ.get("ELEVENLABS_API_KEY")
            return bool(_normalize_secret(key))
        if self.id in ("edge", "microsoft"):
            return True
        api_key = provider_config.get("apiKey")
        return bool(_normalize_secret(api_key))


def _normalize_secret(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        # secret ref objects are "configured" if present
        return "configured"
    return None


KNOWN_PROVIDERS: list[SpeechProviderInfo] = [
    SpeechProviderInfo("openai", "OpenAI", ["tts-1", "tts-1-hd"], ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]),
    SpeechProviderInfo("elevenlabs", "ElevenLabs", [], []),
    SpeechProviderInfo("edge", "Microsoft Edge", [], []),
    SpeechProviderInfo("microsoft", "Microsoft", [], []),
]


def canonicalize_speech_provider_id(provider_id: str | None, cfg: dict[str, Any] | None = None) -> str | None:
    _ = cfg
    if not provider_id or not isinstance(provider_id, str):
        return None
    normalized = provider_id.strip().lower()
    if normalized == "11labs":
        return "elevenlabs"
    if normalized == "edge":
        return "microsoft"
    return normalized if normalized else None


def get_speech_provider(provider_id: str, cfg: dict[str, Any] | None = None) -> SpeechProviderInfo | None:
    _ = cfg
    canonical = canonicalize_speech_provider_id(provider_id)
    if not canonical:
        return None
    for provider in KNOWN_PROVIDERS:
        if provider.id == canonical:
            return provider
    return None


def list_speech_providers(cfg: dict[str, Any] | None = None) -> list[SpeechProviderInfo]:
    _ = cfg
    return list(KNOWN_PROVIDERS)


def get_resolved_speech_provider_config(
    config: dict[str, Any],
    provider_id: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    providers = config.get("providers")
    if isinstance(providers, dict):
        scoped = providers.get(provider_id)
        if isinstance(scoped, dict):
            return scoped
        if provider_id == "microsoft":
            for alias in ("edge", "microsoft"):
                scoped = providers.get(alias)
                if isinstance(scoped, dict):
                    return scoped
    if provider_id in config and isinstance(config[provider_id], dict):
        return config[provider_id]
    return {}
