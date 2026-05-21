"""Voice wake routing config (mirrors TS src/infra/voicewake-routing.ts)."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from openclaw.config.paths import resolve_state_dir
from openclaw.infra.json_files import read_json_file, with_file_lock, write_json_atomic
from openclaw.routing.session_key import VALID_ID_RE, classify_session_key_shape, normalize_agent_id

MAX_VOICEWAKE_ROUTES = 32
MAX_VOICEWAKE_TRIGGER_LENGTH = 64

PUNCT_RE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


class VoiceWakeRouteTargetCurrent(TypedDict):
    mode: Literal["current"]


class VoiceWakeRouteTargetAgent(TypedDict):
    agentId: str


class VoiceWakeRouteTargetSession(TypedDict):
    sessionKey: str


VoiceWakeRouteTarget = VoiceWakeRouteTargetCurrent | VoiceWakeRouteTargetAgent | VoiceWakeRouteTargetSession


@dataclass
class VoiceWakeRouteRule:
    trigger: str
    target: VoiceWakeRouteTarget


@dataclass
class VoiceWakeRoutingConfig:
    version: int
    default_target: VoiceWakeRouteTarget
    routes: list[VoiceWakeRouteRule] = field(default_factory=list)
    updated_at_ms: int = 0


DEFAULT_ROUTING = VoiceWakeRoutingConfig(
    version=1,
    default_target={"mode": "current"},
    routes=[],
    updated_at_ms=0,
)


def _resolve_path(base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else resolve_state_dir()
    return Path(root) / "settings" / "voicewake-routing.json"


def _normalize_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def normalize_voice_wake_trigger_word(value: str) -> str:
    tokens = []
    for token in value.lower().split():
        cleaned = PUNCT_RE.sub("", token)
        if cleaned:
            tokens.append(cleaned)
    return " ".join(tokens)


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _is_valid_agent_id(value: str) -> bool:
    trimmed = value.strip()
    return bool(trimmed) and bool(VALID_ID_RE.match(trimmed))


def _is_canonical_agent_session_key(value: str) -> bool:
    trimmed = value.strip()
    if classify_session_key_shape(trimmed) != "agent":
        return False
    return ":" not in trimmed or all(part for part in trimmed.split(":"))


def _normalize_route_target(value: Any) -> VoiceWakeRouteTarget | None:
    if not _is_plain_object(value):
        return None
    mode = _normalize_optional_string(value.get("mode"))
    if mode == "current":
        return {"mode": "current"}
    agent_id = _normalize_optional_string(value.get("agentId"))
    session_key = _normalize_optional_string(value.get("sessionKey"))
    if agent_id and not session_key:
        return {"agentId": normalize_agent_id(agent_id)}
    if session_key and not agent_id:
        return {"sessionKey": session_key}
    return None


def _normalize_route_rule(value: Any) -> VoiceWakeRouteRule | None:
    if not _is_plain_object(value):
        return None
    trigger_raw = _normalize_optional_string(value.get("trigger"))
    if not trigger_raw:
        return None
    trigger = normalize_voice_wake_trigger_word(trigger_raw)
    if not trigger:
        return None
    target = _normalize_route_target(value.get("target"))
    if not target:
        return None
    return VoiceWakeRouteRule(trigger=trigger, target=target)


def _validate_route_target_input(
    value: Any,
    label: str,
) -> dict[str, Any]:
    if not _is_plain_object(value):
        return {"ok": False, "message": f"{label} must be an object"}
    mode = _normalize_optional_string(value.get("mode"))
    agent_id = _normalize_optional_string(value.get("agentId"))
    session_key = _normalize_optional_string(value.get("sessionKey"))
    if mode is not None:
        if mode != "current":
            return {"ok": False, "message": f'{label}.mode must be "current" when provided'}
        if agent_id is not None or session_key is not None:
            return {
                "ok": False,
                "message": f"{label} cannot mix mode with agentId or sessionKey",
            }
        return {"ok": True}
    if agent_id is not None and session_key is not None:
        return {"ok": False, "message": f"{label} cannot include both agentId and sessionKey"}
    if agent_id is not None:
        if not _is_valid_agent_id(agent_id):
            return {"ok": False, "message": f"{label}.agentId must be a valid agent id"}
        return {"ok": True}
    if session_key is not None:
        if not _is_canonical_agent_session_key(session_key):
            return {
                "ok": False,
                "message": f"{label}.sessionKey must be a canonical agent session key",
            }
        return {"ok": True}
    return {"ok": False, "message": f"{label} must include mode, agentId, or sessionKey"}


def validate_voice_wake_routing_config_input(
    input_value: Any,
) -> dict[str, Any]:
    if not _is_plain_object(input_value):
        return {"ok": False, "message": "config must be an object"}
    if input_value.get("defaultTarget") is not None:
        validated = _validate_route_target_input(input_value["defaultTarget"], "config.defaultTarget")
        if not validated["ok"]:
            return validated
    routes = input_value.get("routes")
    if routes is not None and not isinstance(routes, list):
        return {"ok": False, "message": "config.routes must be an array"}
    if isinstance(routes, list):
        if len(routes) > MAX_VOICEWAKE_ROUTES:
            return {
                "ok": False,
                "message": f"config.routes must contain at most {MAX_VOICEWAKE_ROUTES} entries",
            }
        normalized_triggers: dict[str, int] = {}
        for index, route in enumerate(routes):
            if not _is_plain_object(route):
                return {"ok": False, "message": f"config.routes[{index}] must be an object"}
            trigger = _normalize_optional_string(route.get("trigger"))
            normalized_trigger = (
                normalize_voice_wake_trigger_word(trigger) if trigger else ""
            )
            if not trigger or not normalized_trigger:
                return {
                    "ok": False,
                    "message": f"config.routes[{index}].trigger must be a non-empty string",
                }
            if len(trigger) > MAX_VOICEWAKE_TRIGGER_LENGTH:
                return {
                    "ok": False,
                    "message": (
                        f"config.routes[{index}].trigger must be at most "
                        f"{MAX_VOICEWAKE_TRIGGER_LENGTH} characters"
                    ),
                }
            dup = normalized_triggers.get(normalized_trigger)
            if dup is not None:
                return {
                    "ok": False,
                    "message": (
                        f"config.routes[{index}].trigger duplicates "
                        f"config.routes[{dup}].trigger after normalization"
                    ),
                }
            normalized_triggers[normalized_trigger] = index
            validated_target = _validate_route_target_input(
                route.get("target"),
                f"config.routes[{index}].target",
            )
            if not validated_target["ok"]:
                return validated_target
    return {"ok": True}


def normalize_voice_wake_routing_config(input_value: Any) -> VoiceWakeRoutingConfig:
    if not _is_plain_object(input_value):
        return VoiceWakeRoutingConfig(
            version=DEFAULT_ROUTING.version,
            default_target=dict(DEFAULT_ROUTING.default_target),
            routes=[],
            updated_at_ms=0,
        )
    default_target = (
        _normalize_route_target(input_value.get("defaultTarget")) or {"mode": "current"}
    )
    routes_raw = input_value.get("routes")
    routes: list[VoiceWakeRouteRule] = []
    if isinstance(routes_raw, list):
        for entry in routes_raw:
            rule = _normalize_route_rule(entry)
            if rule:
                routes.append(rule)
    updated = input_value.get("updatedAtMs")
    updated_at_ms = (
        int(updated)
        if isinstance(updated, (int, float)) and updated > 0
        else 0
    )
    return VoiceWakeRoutingConfig(
        version=1,
        default_target=default_target,
        routes=routes,
        updated_at_ms=updated_at_ms,
    )


def _routing_to_dict(config: VoiceWakeRoutingConfig) -> dict[str, Any]:
    return {
        "version": config.version,
        "defaultTarget": config.default_target,
        "routes": [
            {"trigger": r.trigger, "target": r.target} for r in config.routes
        ],
        "updatedAtMs": config.updated_at_ms,
    }


async def load_voice_wake_routing_config(
    base_dir: Path | None = None,
) -> VoiceWakeRoutingConfig:
    file_path = _resolve_path(base_dir)
    existing = read_json_file(file_path)
    if not existing:
        return VoiceWakeRoutingConfig(
            version=1,
            default_target={"mode": "current"},
            routes=[],
            updated_at_ms=0,
        )
    return normalize_voice_wake_routing_config(existing)


async def set_voice_wake_routing_config(
    config: Any,
    base_dir: Path | None = None,
) -> VoiceWakeRoutingConfig:
    normalized = normalize_voice_wake_routing_config(config)
    file_path = _resolve_path(base_dir)

    def _write() -> VoiceWakeRoutingConfig:
        next_cfg = VoiceWakeRoutingConfig(
            version=normalized.version,
            default_target=normalized.default_target,
            routes=normalized.routes,
            updated_at_ms=int(time.time() * 1000),
        )
        write_json_atomic(file_path, _routing_to_dict(next_cfg))
        return next_cfg

    return with_file_lock(file_path, _write)
