"""TTS runtime (aligned with TS speech-core/src/tts.ts gateway surface)."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_preferred_openclaw_tmp_dir
from openclaw.tts.provider_registry import (
    canonicalize_speech_provider_id,
    get_resolved_speech_provider_config,
    get_speech_provider,
    list_speech_providers,
)
from openclaw.tts.tts_auto_mode import normalize_tts_auto_mode
from openclaw.tts.tts_config import (
    DEFAULT_MAX_TEXT_LENGTH,
    DEFAULT_TIMEOUT_MS,
    resolve_effective_tts_config,
    resolve_tts_prefs_path_value,
)

logger = logging.getLogger(__name__)

DEFAULT_TTS_MAX_LENGTH = 1500
DEFAULT_TTS_SUMMARIZE = True


def _read_prefs(prefs_path: str) -> dict[str, Any]:
    try:
        path = Path(prefs_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_prefs(prefs_path: str, prefs: dict[str, Any]) -> None:
    path = Path(prefs_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp.{int(time.time() * 1000)}"
    tmp.write_text(json.dumps(prefs, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _update_prefs(prefs_path: str, updater: Any) -> None:
    prefs = _read_prefs(prefs_path)
    updater(prefs)
    _write_prefs(prefs_path, prefs)


def resolve_tts_config(cfg: dict[str, Any], agent_id: str | None = None) -> dict[str, Any]:
    raw = resolve_effective_tts_config(cfg, agent_id=agent_id)
    auto = normalize_tts_auto_mode(raw.get("auto")) or ("always" if raw.get("enabled") else "off")
    provider = canonicalize_speech_provider_id(raw.get("provider"), cfg) or ""
    return {
        "auto": auto,
        "mode": raw.get("mode") or "final",
        "provider": provider,
        "persona": raw.get("persona"),
        "personas": raw.get("personas") if isinstance(raw.get("personas"), dict) else {},
        "prefsPath": raw.get("prefsPath"),
        "timeoutMs": int(raw.get("timeoutMs") or DEFAULT_TIMEOUT_MS),
        "maxTextLength": int(raw.get("maxTextLength") or DEFAULT_MAX_TEXT_LENGTH),
        "rawConfig": raw,
        "sourceConfig": cfg,
    }


def resolve_tts_prefs_path(config: dict[str, Any]) -> str:
    return resolve_tts_prefs_path_value(config.get("prefsPath"))


def resolve_tts_auto_mode(
    *,
    config: dict[str, Any],
    prefs_path: str,
    session_auto: str | None = None,
) -> str:
    session = normalize_tts_auto_mode(session_auto)
    if session:
        return session
    prefs = _read_prefs(prefs_path).get("tts")
    if isinstance(prefs, dict):
        auto = normalize_tts_auto_mode(prefs.get("auto"))
        if auto:
            return auto
        if isinstance(prefs.get("enabled"), bool):
            return "always" if prefs["enabled"] else "off"
    return config.get("auto") or "off"


def is_tts_enabled(
    config: dict[str, Any],
    prefs_path: str,
    session_auto: str | None = None,
) -> bool:
    return resolve_tts_auto_mode(config=config, prefs_path=prefs_path, session_auto=session_auto) != "off"


def set_tts_enabled(prefs_path: str, enabled: bool) -> None:
    set_tts_auto_mode(prefs_path, "always" if enabled else "off")


def set_tts_auto_mode(prefs_path: str, mode: str) -> None:
    def _upd(prefs: dict[str, Any]) -> None:
        tts = dict(prefs.get("tts") or {})
        tts.pop("enabled", None)
        tts["auto"] = mode
        prefs["tts"] = tts

    _update_prefs(prefs_path, _upd)


def get_tts_provider(config: dict[str, Any], prefs_path: str) -> str:
    from openclaw.tts.status import _get_provider

    return _get_provider(config.get("rawConfig") or config, prefs_path, config.get("sourceConfig") or {})


def set_tts_provider(prefs_path: str, provider: str) -> None:
    canonical = canonicalize_speech_provider_id(provider) or provider

    def _upd(prefs: dict[str, Any]) -> None:
        tts = dict(prefs.get("tts") or {})
        tts["provider"] = canonical
        prefs["tts"] = tts

    _update_prefs(prefs_path, _upd)


def get_tts_max_length(prefs_path: str) -> int:
    prefs = _read_prefs(prefs_path).get("tts")
    if isinstance(prefs, dict) and isinstance(prefs.get("maxLength"), int):
        return prefs["maxLength"]
    return DEFAULT_TTS_MAX_LENGTH


def set_tts_max_length(prefs_path: str, value: int) -> None:
    def _upd(prefs: dict[str, Any]) -> None:
        tts = dict(prefs.get("tts") or {})
        tts["maxLength"] = value
        prefs["tts"] = tts

    _update_prefs(prefs_path, _upd)


def is_summarization_enabled(prefs_path: str) -> bool:
    prefs = _read_prefs(prefs_path).get("tts")
    if isinstance(prefs, dict) and isinstance(prefs.get("summarize"), bool):
        return prefs["summarize"]
    return DEFAULT_TTS_SUMMARIZE


def set_summarization_enabled(prefs_path: str, enabled: bool) -> None:
    def _upd(prefs: dict[str, Any]) -> None:
        tts = dict(prefs.get("tts") or {})
        tts["summarize"] = enabled
        prefs["tts"] = tts

    _update_prefs(prefs_path, _upd)


def set_ptt_enabled(config: dict[str, Any], enabled: bool) -> None:
    _ = config, enabled


def is_ptt_enabled(config: dict[str, Any]) -> bool:
    _ = config
    return False


def get_tts_persona(config: dict[str, Any], prefs_path: str) -> dict[str, Any] | None:
    persona_id = None
    prefs = _read_prefs(prefs_path).get("tts")
    if isinstance(prefs, dict) and "persona" in prefs:
        raw = prefs.get("persona")
        persona_id = str(raw).lower() if raw else None
    elif config.get("persona"):
        persona_id = str(config["persona"]).lower()
    personas = config.get("personas") if isinstance(config.get("personas"), dict) else {}
    if persona_id and persona_id in personas and isinstance(personas[persona_id], dict):
        entry = personas[persona_id]
        return {"id": persona_id, **entry}
    return None


def list_tts_personas(config: dict[str, Any]) -> list[dict[str, Any]]:
    from openclaw.tts.status import _list_personas

    return _list_personas(config.get("rawConfig") or config)


def set_tts_persona(prefs_path: str, persona: str | None) -> None:
    def _upd(prefs: dict[str, Any]) -> None:
        tts = dict(prefs.get("tts") or {})
        tts["persona"] = persona.lower() if persona else None
        prefs["tts"] = tts

    _update_prefs(prefs_path, _upd)


def is_tts_provider_configured(config: dict[str, Any], provider: str, cfg: dict[str, Any]) -> bool:
    from openclaw.tts.status import _is_provider_configured

    return _is_provider_configured(config.get("rawConfig") or config, provider, cfg)


def resolve_tts_provider_order(provider: str, cfg: dict[str, Any]) -> list[str]:
    from openclaw.tts.status import _resolve_provider_order

    return _resolve_provider_order(provider, cfg)


def resolve_explicit_tts_overrides(
    *,
    cfg: dict[str, Any],
    provider: str | None = None,
    model_id: str | None = None,
    voice_id: str | None = None,
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if provider:
        canonical = canonicalize_speech_provider_id(provider, cfg)
        if not canonical or not get_speech_provider(canonical, cfg):
            raise ValueError("Invalid provider. Use a registered TTS provider id.")
        overrides["provider"] = canonical
    if model_id:
        overrides["modelId"] = model_id
    if voice_id:
        overrides["voiceId"] = voice_id
    return overrides


async def _synthesize_openai(
    text: str,
    *,
    provider_config: dict[str, Any],
    voice_id: str | None = None,
    model_id: str | None = None,
) -> tuple[bytes, str, str, bool]:
    from openai import AsyncOpenAI

    api_key = provider_config.get("apiKey") or os.environ.get("OPENAI_API_KEY")
    if not api_key or not isinstance(api_key, str):
        raise RuntimeError("OPENAI_API_KEY not configured")
    client = AsyncOpenAI(api_key=api_key)
    voice = voice_id or provider_config.get("voice") or provider_config.get("voiceId") or "alloy"
    model = model_id or provider_config.get("model") or provider_config.get("modelId") or "tts-1"
    response = await client.audio.speech.create(model=model, voice=voice, input=text)
    data = await response.aread() if hasattr(response, "aread") else bytes(response.content)  # type: ignore[attr-defined]
    return data, ".mp3", "mp3", False


async def synthesize_speech(
    *,
    text: str,
    cfg: dict[str, Any],
    prefs_path: str | None = None,
    channel: str | None = None,
    overrides: dict[str, Any] | None = None,
    disable_fallback: bool = False,
    timeout_ms: int | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    _ = channel, timeout_ms, agent_id
    config = resolve_tts_config(cfg, agent_id=agent_id)
    path = prefs_path or resolve_tts_prefs_path(config)
    provider = (
        canonicalize_speech_provider_id((overrides or {}).get("provider"), cfg)
        or get_tts_provider(config, path)
    )
    providers = [provider]
    if not disable_fallback:
        for candidate in resolve_tts_provider_order(provider, cfg)[1:]:
            if candidate not in providers:
                providers.append(candidate)

    errors: list[str] = []
    for candidate in providers:
        speech = get_speech_provider(candidate, cfg)
        if not speech:
            errors.append(f'provider "{candidate}" not registered')
            continue
        raw = config.get("rawConfig") or {}
        provider_config = get_resolved_speech_provider_config(raw, candidate, cfg)
        if not speech.is_configured(
            cfg=cfg,
            provider_config=provider_config,
            timeout_ms=config.get("timeoutMs") or DEFAULT_TIMEOUT_MS,
        ):
            errors.append(f'provider "{candidate}" not configured')
            continue
        voice_id = (overrides or {}).get("voiceId")
        model_id = (overrides or {}).get("modelId")
        try:
            if candidate == "openai":
                audio, ext, fmt, voice_compat = await _synthesize_openai(
                    text,
                    provider_config=provider_config,
                    voice_id=voice_id,
                    model_id=model_id,
                )
                return {
                    "success": True,
                    "audioBuffer": audio,
                    "provider": candidate,
                    "outputFormat": fmt,
                    "voiceCompatible": voice_compat,
                    "fileExtension": ext,
                }
            errors.append(f'provider "{candidate}" synthesis not implemented in Python runtime')
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            logger.debug("TTS provider %s failed: %s", candidate, exc)

    return {"success": False, "error": "; ".join(errors) if errors else "TTS conversion failed"}


async def text_to_speech(
    *,
    text: str,
    cfg: dict[str, Any],
    channel: str | None = None,
    prefs_path: str | None = None,
    overrides: dict[str, Any] | None = None,
    disable_fallback: bool = False,
) -> dict[str, Any]:
    synthesis = await synthesize_speech(
        text=text,
        cfg=cfg,
        prefs_path=prefs_path,
        channel=channel,
        overrides=overrides,
        disable_fallback=disable_fallback,
    )
    if not synthesis.get("success") or not synthesis.get("audioBuffer"):
        return {
            "success": False,
            "error": synthesis.get("error") or "TTS conversion failed",
            "provider": synthesis.get("provider"),
        }
    temp_root = Path(resolve_preferred_openclaw_tmp_dir())
    temp_root.mkdir(parents=True, exist_ok=True)
    ext = synthesis.get("fileExtension") or ".mp3"
    audio_path = temp_root / f"tts-voice-{int(time.time() * 1000)}{ext}"
    audio_path.write_bytes(synthesis["audioBuffer"])
    return {
        "success": True,
        "audioPath": str(audio_path),
        "provider": synthesis.get("provider"),
        "outputFormat": synthesis.get("outputFormat"),
        "voiceCompatible": synthesis.get("voiceCompatible"),
    }


async def text_to_speech_telephony(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {"success": False, "error": "telephony TTS not implemented"}


async def maybe_apply_tts_to_payload(
    *,
    payload: Any,
    cfg: dict[str, Any],
    channel: str | None = None,
    kind: str | None = None,
    inbound_audio: bool = False,
    tts_auto: str | None = None,
) -> Any:
    _ = cfg, channel, kind, inbound_audio, tts_auto
    return payload
