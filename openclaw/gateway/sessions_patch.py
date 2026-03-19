"""
Session entry patching with field validation.

Fully aligned with TypeScript openclaw/src/gateway/sessions-patch.ts.
Returns {"ok": True, "entry": ...} or {"ok": False, "error": ...}.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from openclaw.agents.sessions.model_overrides import ModelOverrideSelection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalisation helpers — mirrors TS private helpers
# ---------------------------------------------------------------------------

def normalize_exec_host(raw: str) -> str | None:
    """Normalise execHost value. Mirrors TS normalizeExecHost()."""
    normalized = raw.strip().lower()
    if normalized in ("sandbox", "gateway", "node"):
        return normalized
    return None


def normalize_exec_security(raw: str) -> str | None:
    """Normalise execSecurity value. Mirrors TS normalizeExecSecurity()."""
    normalized = raw.strip().lower()
    if normalized in ("deny", "allowlist", "full"):
        return normalized
    return None


def normalize_exec_ask(raw: str) -> str | None:
    """Normalise execAsk value. Mirrors TS normalizeExecAsk()."""
    normalized = raw.strip().lower()
    if normalized in ("off", "on-miss", "always"):
        return normalized
    return None


def _normalize_think_level(raw: str) -> str | None:
    """Normalise a thinkingLevel string."""
    normalized = raw.strip().lower()
    valid = ("off", "minimal", "low", "medium", "high", "xhigh")
    return normalized if normalized in valid else None


def _normalize_reasoning_level(raw: str) -> str | None:
    normalized = raw.strip().lower()
    return normalized if normalized in ("on", "off", "stream") else None


def _normalize_elevated_level(raw: str) -> str | None:
    normalized = raw.strip().lower()
    return normalized if normalized in ("on", "off", "ask", "full") else None


def _normalize_usage_display(raw: str) -> str | None:
    normalized = raw.strip().lower()
    return normalized if normalized in ("off", "tokens", "full") else None


def _normalize_send_policy(raw: str) -> str | None:
    normalized = raw.strip().lower()
    return normalized if normalized in ("allow", "deny") else None


def _normalize_group_activation(raw: str) -> str | None:
    normalized = raw.strip().lower()
    return normalized if normalized in ("mention", "always") else None


def _supports_xhigh_thinking(provider: str, model: str) -> bool:
    """Return True if provider/model supports xhigh thinking level."""
    p = (provider or "").lower()
    m = (model or "").lower()
    if p == "anthropic" and "claude" in m:
        return True
    return False


def format_xhigh_model_hint() -> str:
    """Return hint message for xhigh-only models. Mirrors TS formatXHighModelHint()."""
    return "Claude (Anthropic) models only"


def _format_thinking_levels_hint(provider: str, model: str) -> str:
    """Format valid thinking levels for a provider/model."""
    if _supports_xhigh_thinking(provider, model):
        return '"off"|"minimal"|"low"|"medium"|"high"|"xhigh"'
    return '"off"|"minimal"|"low"|"medium"|"high"'


def _parse_session_label(raw: Any) -> dict[str, Any]:
    """Validate and normalise a session label. Returns {"ok": True, "label": ...} or {"ok": False, "error": ...}."""
    if raw is None:
        return {"ok": False, "error": "invalid label: null"}
    label = str(raw).strip()
    if not label:
        return {"ok": False, "error": "invalid label: empty"}
    if len(label) > 64:
        return {"ok": False, "error": f"label must be ≤64 characters (got {len(label)})"}
    # Allow alphanumeric, hyphens, underscores, dots
    import re
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', label):
        return {"ok": False, "error": f"invalid label: use a-z, 0-9, -, _, ."}
    return {"ok": True, "label": label}


def _is_subagent_session_key(key: str) -> bool:
    """Check if session key is a subagent key (matches TS isSubagentSessionKey)"""
    raw = key.strip()
    if not raw:
        return False
    # Check for legacy format: subagent:xxx
    if raw.lower().startswith("subagent:"):
        return True
    # Check for new format: agent:main:subagent:xxx
    # Parse and check if rest starts with "subagent:"
    from openclaw.routing.session_key import parse_agent_session_key
    parsed = parse_agent_session_key(raw)
    if parsed:
        rest = (parsed.rest or "").lower()
        return rest.startswith("subagent:")
    return False


def _invalid(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": message}}


# ---------------------------------------------------------------------------
# Label uniqueness check
# ---------------------------------------------------------------------------

def check_label_uniqueness(
    label: str | None,
    store: dict[str, Any],
    current_key: str,
) -> None:
    """Raise ValueError if label is already used by another session."""
    if not label:
        return
    for key, entry in store.items():
        if key == current_key:
            continue
        entry_label = (
            entry.get("label") if isinstance(entry, dict) else getattr(entry, "label", None)
        )
        if entry_label == label:
            raise ValueError(f"label already in use: {label}")


# ---------------------------------------------------------------------------
# Main patch function
# ---------------------------------------------------------------------------

def apply_sessions_patch_to_store(
    store: dict[str, Any],
    store_key: str,
    patch: dict[str, Any],
    cfg: Any = None,
    model_catalog: Any = None,
) -> dict[str, Any]:
    """
    Apply patch to session entry with validation.
    Mirrors TS applySessionsPatchToStore().

    Returns {"ok": True, "entry": SessionEntry} or {"ok": False, "error": {...}}.
    """
    from openclaw.agents.sessions.model_overrides import apply_model_override_to_session_entry

    now = int(time.time() * 1000)

    # Resolve default model for this session's agent
    try:
        from openclaw.routing.session_key import parse_agent_session_key, normalize_agent_id
        from openclaw.agents.model_selection import resolve_default_model_for_agent, DEFAULT_PROVIDER, DEFAULT_MODEL
        parsed_agent = parse_agent_session_key(store_key)
        session_agent_id = normalize_agent_id(
            parsed_agent.agent_id if parsed_agent else "main"
        )
        resolved_default = resolve_default_model_for_agent(cfg, session_agent_id)
    except Exception:
        from openclaw.agents.model_selection import DEFAULT_PROVIDER, DEFAULT_MODEL, ModelRef
        session_agent_id = "main"
        resolved_default = ModelRef(provider=DEFAULT_PROVIDER, model=DEFAULT_MODEL)

    # Resolve subagent model hint
    subagent_model_hint: str | None = None
    if _is_subagent_session_key(store_key):
        try:
            from openclaw.agents.model_selection import resolve_subagent_configured_model_selection
            subagent_model_hint = resolve_subagent_configured_model_selection(cfg, session_agent_id)
        except Exception:
            pass

    existing = store.get(store_key)
    if existing is None:
        next_entry: dict[str, Any] = {"sessionId": str(uuid.uuid4()), "updatedAt": now}
    else:
        # Work on a dict copy; support both dict and dataclass/pydantic
        if isinstance(existing, dict):
            next_entry = dict(existing)
        else:
            try:
                next_entry = existing.model_dump()
            except AttributeError:
                import dataclasses
                try:
                    next_entry = dataclasses.asdict(existing)
                except Exception:
                    next_entry = dict(vars(existing))
        next_entry["updatedAt"] = max(next_entry.get("updatedAt") or 0, now)

    # -------------------------------------------------------------------------
    # spawnedBy (immutable once set)
    # -------------------------------------------------------------------------
    if "spawnedBy" in patch:
        raw = patch["spawnedBy"]
        if raw is None:
            if next_entry.get("spawnedBy"):
                return _invalid("spawnedBy cannot be cleared once set")
        else:
            trimmed = str(raw).strip()
            if not trimmed:
                return _invalid("invalid spawnedBy: empty")
            if not _is_subagent_session_key(store_key):
                return _invalid("spawnedBy is only supported for subagent:* sessions")
            existing_sb = next_entry.get("spawnedBy")
            if existing_sb and existing_sb != trimmed:
                return _invalid("spawnedBy cannot be changed once set")
            next_entry["spawnedBy"] = trimmed

    # -------------------------------------------------------------------------
    # spawnDepth (immutable once set, subagent only)
    # -------------------------------------------------------------------------
    if "spawnDepth" in patch:
        raw = patch["spawnDepth"]
        if raw is None:
            if isinstance(next_entry.get("spawnDepth"), int):
                return _invalid("spawnDepth cannot be cleared once set")
        else:
            if not _is_subagent_session_key(store_key):
                return _invalid("spawnDepth is only supported for subagent:* sessions")
            try:
                numeric = int(raw)
            except (TypeError, ValueError):
                return _invalid("invalid spawnDepth (use an integer >= 0)")
            if numeric < 0:
                return _invalid("invalid spawnDepth (use an integer >= 0)")
            existing_sd = next_entry.get("spawnDepth")
            if isinstance(existing_sd, int) and existing_sd != numeric:
                return _invalid("spawnDepth cannot be changed once set")
            next_entry["spawnDepth"] = numeric

    # -------------------------------------------------------------------------
    # label
    # -------------------------------------------------------------------------
    if "label" in patch:
        raw = patch["label"]
        if raw is None:
            next_entry.pop("label", None)
        else:
            parsed = _parse_session_label(raw)
            if not parsed["ok"]:
                return _invalid(parsed["error"])
            label = parsed["label"]
            for key, entry in store.items():
                if key == store_key:
                    continue
                entry_label = (
                    entry.get("label") if isinstance(entry, dict)
                    else getattr(entry, "label", None)
                )
                if entry_label == label:
                    return _invalid(f"label already in use: {label}")
            next_entry["label"] = label

    # -------------------------------------------------------------------------
    # thinkingLevel
    # -------------------------------------------------------------------------
    if "thinkingLevel" in patch:
        raw = patch["thinkingLevel"]
        if raw is None:
            next_entry.pop("thinkingLevel", None)
        else:
            normalized = _normalize_think_level(str(raw))
            if not normalized:
                hint_provider = (next_entry.get("providerOverride") or "").strip() or resolved_default.provider
                hint_model = (next_entry.get("modelOverride") or "").strip() or resolved_default.model
                return _invalid(
                    f"invalid thinkingLevel (use {_format_thinking_levels_hint(hint_provider, hint_model)})"
                )
            next_entry["thinkingLevel"] = normalized

    # -------------------------------------------------------------------------
    # verboseLevel
    # -------------------------------------------------------------------------
    if "verboseLevel" in patch:
        raw = patch["verboseLevel"]
        if raw is None:
            next_entry.pop("verboseLevel", None)
        else:
            next_entry["verboseLevel"] = str(raw)

    # -------------------------------------------------------------------------
    # reasoningLevel
    # -------------------------------------------------------------------------
    if "reasoningLevel" in patch:
        raw = patch["reasoningLevel"]
        if raw is None:
            next_entry.pop("reasoningLevel", None)
        else:
            normalized = _normalize_reasoning_level(str(raw))
            if not normalized:
                return _invalid('invalid reasoningLevel (use "on"|"off"|"stream")')
            if normalized == "off":
                next_entry.pop("reasoningLevel", None)
            else:
                next_entry["reasoningLevel"] = normalized

    # -------------------------------------------------------------------------
    # responseUsage
    # -------------------------------------------------------------------------
    if "responseUsage" in patch:
        raw = patch["responseUsage"]
        if raw is None:
            next_entry.pop("responseUsage", None)
        else:
            normalized = _normalize_usage_display(str(raw))
            if not normalized:
                return _invalid('invalid responseUsage (use "off"|"tokens"|"full")')
            if normalized == "off":
                next_entry.pop("responseUsage", None)
            else:
                next_entry["responseUsage"] = normalized

    # -------------------------------------------------------------------------
    # elevatedLevel
    # -------------------------------------------------------------------------
    if "elevatedLevel" in patch:
        raw = patch["elevatedLevel"]
        if raw is None:
            next_entry.pop("elevatedLevel", None)
        else:
            normalized = _normalize_elevated_level(str(raw))
            if not normalized:
                return _invalid('invalid elevatedLevel (use "on"|"off"|"ask"|"full")')
            next_entry["elevatedLevel"] = normalized

    # -------------------------------------------------------------------------
    # execHost / execSecurity / execAsk / execNode
    # -------------------------------------------------------------------------
    if "execHost" in patch:
        raw = patch["execHost"]
        if raw is None:
            next_entry.pop("execHost", None)
        else:
            normalized = normalize_exec_host(str(raw))
            if not normalized:
                return _invalid('invalid execHost (use "sandbox"|"gateway"|"node")')
            next_entry["execHost"] = normalized

    if "execSecurity" in patch:
        raw = patch["execSecurity"]
        if raw is None:
            next_entry.pop("execSecurity", None)
        else:
            normalized = normalize_exec_security(str(raw))
            if not normalized:
                return _invalid('invalid execSecurity (use "deny"|"allowlist"|"full")')
            next_entry["execSecurity"] = normalized

    if "execAsk" in patch:
        raw = patch["execAsk"]
        if raw is None:
            next_entry.pop("execAsk", None)
        else:
            normalized = normalize_exec_ask(str(raw))
            if not normalized:
                return _invalid('invalid execAsk (use "off"|"on-miss"|"always")')
            next_entry["execAsk"] = normalized

    if "execNode" in patch:
        raw = patch["execNode"]
        if raw is None:
            next_entry.pop("execNode", None)
        else:
            trimmed = str(raw).strip()
            if not trimmed:
                return _invalid("invalid execNode: empty")
            next_entry["execNode"] = trimmed

    # -------------------------------------------------------------------------
    # model override — with catalog validation + apply_model_override_to_session_entry
    # -------------------------------------------------------------------------
    if "model" in patch:
        raw = patch["model"]
        if raw is None:
            # Reset to default
            apply_model_override_to_session_entry(
                entry=next_entry,
                selection={
                    "provider": resolved_default.provider,
                    "model": resolved_default.model,
                    "isDefault": True,
                },
            )
        else:
            trimmed = str(raw).strip()
            if not trimmed:
                return _invalid("invalid model: empty")
            # Validate against catalog if available
            try:
                from openclaw.agents.model_selection import resolve_allowed_model_ref
                catalog_list = []
                if model_catalog is not None:
                    catalog_list = (
                        list(model_catalog) if hasattr(model_catalog, "__iter__") else []
                    )
                resolved = resolve_allowed_model_ref(
                    cfg or {},
                    trimmed,
                    default_provider=resolved_default.provider,
                    catalog=catalog_list,
                    default_model=subagent_model_hint or resolved_default.model,
                )
                if "error" in resolved:
                    return _invalid(resolved["error"])
                ref = resolved["ref"]
                is_default = (
                    ref.provider == resolved_default.provider
                    and ref.model == resolved_default.model
                )
                apply_model_override_to_session_entry(
                    entry=next_entry,
                    selection=ModelOverrideSelection(
                        provider=ref.provider,
                        model=ref.model,
                        is_default=is_default,
                    ),
                )
            except Exception as exc:
                logger.warning("Model override validation error: %s", exc)
                next_entry["modelOverride"] = trimmed
                next_entry["providerOverride"] = resolved_default.provider

    # -------------------------------------------------------------------------
    # xhigh guard — downgrade to "high" for unsupported models
    # -------------------------------------------------------------------------
    if next_entry.get("thinkingLevel") == "xhigh":
        effective_provider = (next_entry.get("providerOverride") or "").strip() or resolved_default.provider
        effective_model = (next_entry.get("modelOverride") or "").strip() or resolved_default.model
        if not _supports_xhigh_thinking(effective_provider, effective_model):
            if "thinkingLevel" in patch:
                return _invalid(
                    f'thinkingLevel "xhigh" is only supported for {format_xhigh_model_hint()}'
                )
            next_entry["thinkingLevel"] = "high"

    # -------------------------------------------------------------------------
    # sendPolicy
    # -------------------------------------------------------------------------
    if "sendPolicy" in patch:
        raw = patch["sendPolicy"]
        if raw is None:
            next_entry.pop("sendPolicy", None)
        else:
            normalized = _normalize_send_policy(str(raw))
            if not normalized:
                return _invalid('invalid sendPolicy (use "allow"|"deny")')
            next_entry["sendPolicy"] = normalized

    # -------------------------------------------------------------------------
    # groupActivation
    # -------------------------------------------------------------------------
    if "groupActivation" in patch:
        raw = patch["groupActivation"]
        if raw is None:
            next_entry.pop("groupActivation", None)
        else:
            normalized = _normalize_group_activation(str(raw))
            if not normalized:
                return _invalid('invalid groupActivation (use "mention"|"always")')
            next_entry["groupActivation"] = normalized

    # -------------------------------------------------------------------------
    # Passthrough fields (no special validation)
    # -------------------------------------------------------------------------
    _passthrough = [
        "sessionFile", "systemSent", "abortedLastRun",
        "chatType", "channel", "groupId", "groupChannel", "space",
        "ttsAuto", "authProfileOverride", "authProfileOverrideSource",
        "queueMode", "queueDebounceMs", "queueCap", "queueDrop",
        "modelProvider", "model", "contextTokens",
        "compactionCount", "memoryFlushAt", "memoryFlushCompactionCount",
        "origin", "deliveryContext",
        "lastChannel", "lastTo", "lastAccountId", "lastThreadId",
        "lastHeartbeatText", "lastHeartbeatSentAt",
        "skillsSnapshot", "systemPromptReport",
        "inputTokens", "outputTokens", "totalTokens",
        "displayName", "subject",
    ]
    for field in _passthrough:
        if field in patch:
            next_entry[field] = patch[field]

    store[store_key] = next_entry
    logger.debug("Patched session key=%s, sessionId=%s, store_size=%d",
                 store_key, next_entry.get("sessionId"), len(store))
    return {"ok": True, "entry": next_entry}
