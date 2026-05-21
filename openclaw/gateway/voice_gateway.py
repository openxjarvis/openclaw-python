"""Gateway voice/TTS/talk/voicewake handlers (TS-aligned)."""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any

from openclaw.config.talk import (
    build_talk_config_response,
    normalize_talk_section,
    redact_talk_config,
    resolve_active_talk_provider_config,
)
from openclaw.gateway.error_codes import InvalidRequestError, UnavailableError
from openclaw.infra.voicewake import (
    default_voice_wake_triggers,
    load_voice_wake_config,
    set_voice_wake_triggers,
)
from openclaw.infra.voicewake_routing import (
    _routing_to_dict,
    load_voice_wake_routing_config,
    normalize_voice_wake_routing_config,
    set_voice_wake_routing_config,
    validate_voice_wake_routing_config_input,
)
from openclaw.tts.provider_registry import canonicalize_speech_provider_id, get_speech_provider
from openclaw.tts.status import build_tts_status_payload
from openclaw.tts.tts import (
    resolve_explicit_tts_overrides,
    resolve_tts_config,
    resolve_tts_prefs_path,
    set_tts_enabled,
    set_tts_persona,
    set_tts_provider,
    synthesize_speech,
    text_to_speech,
)
from openclaw.tts.tts import list_tts_personas as list_configured_tts_personas

logger = logging.getLogger(__name__)

TALK_SECRETS_SCOPE = "talk.secrets"


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def normalize_voice_wake_triggers(input_value: Any) -> list[str]:
    raw = input_value if isinstance(input_value, list) else []
    cleaned = [
        item
        for item in (_normalize_optional_string(v) for v in raw)
        if item is not None
    ][:32]
    cleaned = [item[:64] for item in cleaned]
    return cleaned if cleaned else default_voice_wake_triggers()


async def _broadcast_gateway_event(
    connection: Any,
    event: str,
    payload: Any,
) -> None:
    gateway = getattr(connection, "gateway", None)
    if gateway is not None and hasattr(gateway, "broadcast_event"):
        try:
            await gateway.broadcast_event(event, payload)
        except Exception as exc:
            logger.debug("broadcast %s failed: %s", event, exc)


def _get_cfg_dict(connection: Any) -> dict[str, Any]:
    try:
        from openclaw.gateway.config_service import get_config_service

        svc = get_config_service()
        if svc:
            cfg = svc.get_config()
            if isinstance(cfg, dict):
                return cfg
            if hasattr(cfg, "model_dump"):
                return cfg.model_dump()
    except Exception:
        pass
    config = getattr(connection, "config", None)
    if config is not None and hasattr(config, "model_dump"):
        return config.model_dump()
    return {}


def _can_read_talk_secrets(connection: Any) -> bool:
    auth = getattr(connection, "auth_context", None)
    scopes = set(getattr(auth, "scopes", set()) or set())
    return "operator.admin" in scopes or TALK_SECRETS_SCOPE in scopes


async def handle_tts_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return build_tts_status_payload(_get_cfg_dict(connection))
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_tts_enable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg = resolve_tts_config(_get_cfg_dict(connection))
        set_tts_enabled(resolve_tts_prefs_path(cfg), True)
        return {"enabled": True}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_tts_disable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg = resolve_tts_config(_get_cfg_dict(connection))
        set_tts_enabled(resolve_tts_prefs_path(cfg), False)
        return {"enabled": False}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_tts_convert(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    text = _normalize_optional_string(params.get("text")) or ""
    if not text:
        raise InvalidRequestError("tts.convert requires text")
    cfg = _get_cfg_dict(connection)
    try:
        overrides = resolve_explicit_tts_overrides(
            cfg=cfg,
            provider=_normalize_optional_string(params.get("provider")),
            model_id=_normalize_optional_string(params.get("modelId")),
            voice_id=_normalize_optional_string(params.get("voiceId")),
        )
    except ValueError as exc:
        raise InvalidRequestError(str(exc)) from exc
    try:
        result = await text_to_speech(
            text=text,
            cfg=cfg,
            channel=_normalize_optional_string(params.get("channel")),
            overrides=overrides,
            disable_fallback=bool(
                overrides.get("provider")
                or params.get("modelId")
                or params.get("voiceId")
            ),
        )
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc
    if result.get("success") and result.get("audioPath"):
        return {
            "audioPath": result["audioPath"],
            "provider": result.get("provider"),
            "outputFormat": result.get("outputFormat"),
            "voiceCompatible": result.get("voiceCompatible"),
        }
    raise UnavailableError(result.get("error") or "TTS conversion failed")


async def handle_tts_set_provider(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    cfg = _get_cfg_dict(connection)
    provider = canonicalize_speech_provider_id(
        _normalize_optional_string(params.get("provider")) or "",
        cfg,
    )
    if not provider or not get_speech_provider(provider, cfg):
        raise InvalidRequestError("Invalid provider. Use a registered TTS provider id.")
    try:
        config = resolve_tts_config(cfg)
        set_tts_provider(resolve_tts_prefs_path(config), provider)
        return {"provider": provider}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_tts_personas(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg = resolve_tts_config(_get_cfg_dict(connection))
        prefs_path = resolve_tts_prefs_path(cfg)
        from openclaw.tts.tts import get_tts_persona

        active = get_tts_persona(cfg, prefs_path)
        personas = list_configured_tts_personas(cfg)
        return {
            "active": active["id"] if active else None,
            "personas": [
                {
                    "id": p["id"],
                    "label": p.get("label"),
                    "description": p.get("description"),
                    "provider": p.get("provider"),
                    "fallbackPolicy": (cfg.get("personas") or {}).get(p["id"], {}).get("fallbackPolicy")
                    if isinstance(cfg.get("personas"), dict)
                    else None,
                    "providers": list(
                        ((cfg.get("personas") or {}).get(p["id"], {}) or {}).get("providers") or {}
                    )
                    if isinstance(cfg.get("personas"), dict)
                    else [],
                }
                for p in personas
            ],
        }
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_tts_set_persona(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    cfg_dict = _get_cfg_dict(connection)
    raw_persona = _normalize_optional_string(params.get("persona"))
    try:
        config = resolve_tts_config(cfg_dict)
        prefs_path = resolve_tts_prefs_path(config)
        if not raw_persona or raw_persona.lower() in ("off", "none", "default"):
            set_tts_persona(prefs_path, None)
            return {"persona": None}
        persona = next(
            (p for p in list_configured_tts_personas(config) if p["id"] == raw_persona.lower()),
            None,
        )
        if not persona:
            raise InvalidRequestError("Invalid persona. Use a configured TTS persona id.")
        set_tts_persona(prefs_path, persona["id"])
        return {"persona": persona["id"]}
    except InvalidRequestError:
        raise
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_voicewake_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        cfg = await load_voice_wake_config()
        return {"triggers": cfg.triggers}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_voicewake_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params.get("triggers"), list):
        raise InvalidRequestError("voicewake.set requires triggers: string[]")
    try:
        triggers = normalize_voice_wake_triggers(params["triggers"])
        cfg = await set_voice_wake_triggers(triggers)
        await _broadcast_gateway_event(connection, "voicewake.changed", {"triggers": cfg.triggers})
        return {"triggers": cfg.triggers}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_voicewake_routing_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        config = await load_voice_wake_routing_config()
        return {"config": _routing_to_dict(config)}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_voicewake_routing_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    config_input = params.get("config")
    if config_input is None or not isinstance(config_input, dict):
        raise InvalidRequestError("voicewake.routing.set requires config: object")
    validated = validate_voice_wake_routing_config_input(config_input)
    if not validated.get("ok"):
        raise InvalidRequestError(validated.get("message") or "invalid routing config")
    try:
        normalized = normalize_voice_wake_routing_config(config_input)
        saved = await set_voice_wake_routing_config(normalized)
        payload = _routing_to_dict(saved)
        await _broadcast_gateway_event(connection, "voicewake.routing.changed", {"config": payload})
        return {"config": payload}
    except Exception as exc:
        raise UnavailableError(str(exc)) from exc


async def handle_talk_config(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    include_secrets = bool(params.get("includeSecrets"))
    if include_secrets and not _can_read_talk_secrets(connection):
        raise InvalidRequestError(f"missing scope: {TALK_SECRETS_SCOPE}")

    cfg = _get_cfg_dict(connection)
    config_payload: dict[str, Any] = {}
    talk_raw = cfg.get("talk")
    normalized_talk = normalize_talk_section(talk_raw if isinstance(talk_raw, dict) else None)
    talk_response = build_talk_config_response(normalized_talk or talk_raw)
    if talk_response:
        config_payload["talk"] = (
            talk_response if include_secrets else redact_talk_config(talk_response)
        )
    session = cfg.get("session")
    if isinstance(session, dict):
        main_key = session.get("mainKey")
        if isinstance(main_key, str):
            config_payload["session"] = {"mainKey": main_key}
    ui = cfg.get("ui")
    if isinstance(ui, dict):
        seam = ui.get("seamColor")
        if isinstance(seam, str):
            config_payload["ui"] = {"seamColor": seam}
    return {"config": config_payload}


def _infer_mime_type(output_format: str | None, file_extension: str | None) -> str | None:
    fmt = (output_format or "").lower()
    ext = (file_extension or "").lower()
    if fmt == "mp3" or fmt.startswith("mp3_") or fmt.endswith("-mp3") or ext == ".mp3":
        return "audio/mpeg"
    if fmt == "opus" or fmt.startswith("opus_") or ext in (".opus", ".ogg"):
        return "audio/ogg"
    if fmt.endswith("-wav") or ext == ".wav":
        return "audio/wav"
    if fmt.endswith("-webm") or ext == ".webm":
        return "audio/webm"
    return None


def _resolve_talk_speed(params: dict[str, Any]) -> float | None:
    """Resolve effective speed from speed or rateWpm — mirrors TS resolveTalkSpeed.

    speed: direct multiplier (0.25–4.0).
    rateWpm: words-per-minute (valid 50–500); 180 wpm ≈ 1.0x speed.
    rateWpm validation: raises InvalidRequestError if outside 50–500.
    """
    speed_raw = params.get("speed")
    rate_wpm_raw = params.get("rateWpm")

    if rate_wpm_raw is not None:
        try:
            rate_wpm = float(rate_wpm_raw)
        except (TypeError, ValueError) as e:
            raise InvalidRequestError(f"rateWpm must be a number: {rate_wpm_raw!r}") from e
        if rate_wpm < 50 or rate_wpm > 500:
            raise InvalidRequestError(f"rateWpm must be between 50 and 500, got {rate_wpm}")
        # 180 wpm ≈ average speaking pace = 1.0x speed
        return round(rate_wpm / 180.0, 3)

    if speed_raw is not None:
        try:
            speed = float(speed_raw)
        except (TypeError, ValueError) as e:
            raise InvalidRequestError(f"speed must be a number: {speed_raw!r}") from e
        # Clamp to valid range
        return max(0.25, min(4.0, speed))

    return None


async def handle_talk_speak(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    text = _normalize_optional_string(params.get("text"))
    if not text:
        raise InvalidRequestError("talk.speak requires text")
    cfg = _get_cfg_dict(connection)
    talk_raw = cfg.get("talk")
    resolved = resolve_active_talk_provider_config(
        talk_raw if isinstance(talk_raw, dict) else None
    )
    provider = canonicalize_speech_provider_id(resolved["provider"] if resolved else None, cfg)
    if not resolved or not provider:
        raise UnavailableError(
            "talk.speak unavailable: talk provider not configured",
            details={"reason": "talk_unconfigured", "fallbackEligible": True},
        )
    if not get_speech_provider(provider, cfg):
        raise UnavailableError(
            f'talk.speak unavailable: speech provider "{provider}" does not support Talk mode',
            details={"reason": "talk_provider_unsupported", "fallbackEligible": True},
        )

    # Resolve speed override (mirrors TS resolveTalkSpeed / buildTalkSpeakOverrides)
    speed = _resolve_talk_speed(params)

    # Resolve voiceId / voiceAlias override (mirrors TS talk.ts:73-91,243-246)
    voice_id_raw = params.get("voiceId") or params.get("voice")
    voice_aliases: dict[str, str] = {}
    try:
        tts_section = (cfg.get("messages") or {}).get("tts") or {}
        provider_cfg = (tts_section.get("providers") or {}).get(provider) or {}
        voice_aliases = provider_cfg.get("voiceAliases") or {}
    except Exception:
        pass
    voice_id = voice_aliases.get(str(voice_id_raw), str(voice_id_raw)) if voice_id_raw else None

    talk_cfg = dict(cfg)
    messages = dict(talk_cfg.get("messages") or {})
    base_tts = dict(messages.get("tts") or {})
    provider_config = dict(resolved.get("config") if isinstance(resolved.get("config"), dict) else {})
    if speed is not None:
        provider_config["speed"] = speed
    if voice_id:
        provider_config["voice"] = voice_id
    providers = dict(base_tts.get("providers") or {})
    providers[provider] = provider_config
    base_tts.update({"auto": "always", "provider": provider, "providers": providers})
    messages["tts"] = base_tts
    talk_cfg["messages"] = messages
    try:
        result = await synthesize_speech(text=text, cfg=talk_cfg, disable_fallback=True)
    except Exception as exc:
        raise UnavailableError(
            str(exc),
            details={"reason": "synthesis_failed", "fallbackEligible": False},
        ) from exc
    if not result.get("success") or not result.get("audioBuffer"):
        raise UnavailableError(
            result.get("error") or "talk synthesis failed",
            details={"reason": "synthesis_failed", "fallbackEligible": False},
        )
    audio = result["audioBuffer"]
    if not isinstance(audio, (bytes, bytearray)) or len(audio) == 0:
        raise UnavailableError(
            "talk synthesis returned empty audio",
            details={"reason": "invalid_audio_result", "fallbackEligible": False},
        )
    provider_out = (result.get("provider") or provider or "").strip()
    if not provider_out:
        raise UnavailableError(
            "talk synthesis returned empty provider",
            details={"reason": "invalid_audio_result", "fallbackEligible": False},
        )
    return {
        "audioBase64": base64.b64encode(bytes(audio)).decode("ascii"),
        "provider": provider_out,
        "outputFormat": result.get("outputFormat"),
        "voiceCompatible": result.get("voiceCompatible"),
        "mimeType": _infer_mime_type(result.get("outputFormat"), result.get("fileExtension")),
        "fileExtension": result.get("fileExtension"),
    }


async def handle_talk_realtime_session(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    _ = params
    raise UnavailableError(
        "Realtime voice provider does not support browser WebRTC sessions in Python gateway yet"
    )


async def handle_talk_mode(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    if "enabled" not in params:
        raise InvalidRequestError("invalid talk.mode params: enabled required")
    payload = {
        "enabled": bool(params.get("enabled")),
        "phase": params.get("phase") if isinstance(params.get("phase"), str) else None,
        "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
    }
    await _broadcast_gateway_event(connection, "talk.mode", payload)
    return payload
