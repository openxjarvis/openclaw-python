"""
Agent model-selection utilities — fully aligned with TypeScript
openclaw/src/agents/model-selection.ts.

Provides:
- ModelRef: provider + model pair
- normalize_provider_id(): canonicalise provider names
- normalize_model_ref(): provider + model ref normalization
- build_model_alias_index(): alias lookup from config
- resolve_model_ref_from_string(): parse/alias-lookup a raw model string
- resolve_thinking_default(): per-model thinking level
- resolve_allowed_model_ref(): validate + allow-list check
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ThinkLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# ---------------------------------------------------------------------------
# Defaults — imported from defaults.py (canonical source of truth)
# ---------------------------------------------------------------------------
from .defaults import DEFAULT_PROVIDER, DEFAULT_MODEL, DEFAULT_CONTEXT_TOKENS  # noqa: E402

# ---------------------------------------------------------------------------
# Provider / model normalisation
# ---------------------------------------------------------------------------

_ANTHROPIC_MODEL_ALIASES: dict[str, str] = {
    "opus-4.6": "claude-opus-4-6",
    "opus-4.5": "claude-opus-4-5",
    "sonnet-4.6": "claude-sonnet-4-6",
    "sonnet-4.5": "claude-sonnet-4-5",
}

_OPENAI_CODEX_OAUTH_MODEL_PREFIXES = ("gpt-5.3-codex",)


def normalize_provider_id(provider: str) -> str:
    """Canonicalise a provider identifier.

    Mirrors TS normalizeProviderId().
    """
    normalized = provider.strip().lower()
    if normalized in ("z.ai", "z-ai"):
        return "zai"
    if normalized == "opencode-zen":
        return "opencode"
    if normalized == "qwen":
        return "qwen-portal"
    if normalized == "kimi-code":
        return "kimi-coding"
    return normalized


def _normalize_google_model_id(model: str) -> str:
    """Expand short Google model names (e.g. "gemini-flash" → "gemini-2.0-flash")."""
    trimmed = model.strip()
    lower = trimmed.lower()
    if lower == "gemini-flash" or lower == "gemini-2.0-flash":
        return "gemini-2.0-flash"
    if lower in ("gemini-pro", "gemini-2.5-pro"):
        return "gemini-2.5-pro"
    return trimmed


def _normalize_anthropic_model_id(model: str) -> str:
    trimmed = model.strip()
    lower = trimmed.lower()
    return _ANTHROPIC_MODEL_ALIASES.get(lower, trimmed)


def _normalize_provider_model_id(provider: str, model: str) -> str:
    if provider == "anthropic":
        return _normalize_anthropic_model_id(model)
    if provider == "google":
        return _normalize_google_model_id(model)
    return model


def _should_use_openai_codex_provider(provider: str, model: str) -> bool:
    if provider != "openai":
        return False
    normalized = model.strip().lower()
    if not normalized:
        return False
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}-")
        for prefix in _OPENAI_CODEX_OAUTH_MODEL_PREFIXES
    )


# ---------------------------------------------------------------------------
# ModelRef
# ---------------------------------------------------------------------------

@dataclass
class ModelRef:
    """Provider + model pair — mirrors TS ModelRef."""
    provider: str
    model: str


def model_key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def normalize_model_ref(provider: str, model: str) -> ModelRef:
    """Normalise provider and model identifiers.

    Mirrors TS normalizeModelRef().
    """
    normalized_provider = normalize_provider_id(provider)
    normalized_model = _normalize_provider_model_id(normalized_provider, model.strip())
    if _should_use_openai_codex_provider(normalized_provider, normalized_model):
        return ModelRef(provider="openai-codex", model=normalized_model)
    return ModelRef(provider=normalized_provider, model=normalized_model)


def parse_model_ref(raw: str, default_provider: str) -> ModelRef | None:
    """Parse a raw "provider/model" or "model" string into a ModelRef.

    Mirrors TS parseModelRef().
    """
    trimmed = raw.strip()
    if not trimmed:
        return None
    slash = trimmed.find("/")
    if slash == -1:
        return normalize_model_ref(default_provider, trimmed)
    provider_raw = trimmed[:slash].strip()
    model = trimmed[slash + 1:].strip()
    if not provider_raw or not model:
        return None
    return normalize_model_ref(provider_raw, model)


# ---------------------------------------------------------------------------
# Model alias index
# ---------------------------------------------------------------------------

@dataclass
class ModelAliasIndex:
    """Bidirectional alias ↔ model-key lookup structure."""
    by_alias: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_key: dict[str, list[str]] = field(default_factory=dict)


def build_model_alias_index(cfg: Any, default_provider: str = DEFAULT_PROVIDER) -> ModelAliasIndex:
    """Build alias lookup tables from config.agents.defaults.models.

    Mirrors TS buildModelAliasIndex().
    """
    idx = ModelAliasIndex()
    raw_models: dict = {}
    if isinstance(cfg, dict):
        agents_cfg = cfg.get("agents", {})
        if isinstance(agents_cfg, dict):
            defaults_cfg = agents_cfg.get("defaults", {})
            if isinstance(defaults_cfg, dict):
                raw_models = defaults_cfg.get("models", {}) or {}

    for key_raw, entry_raw in raw_models.items():
        parsed = parse_model_ref(str(key_raw or ""), default_provider)
        if not parsed:
            continue
        alias = ""
        if isinstance(entry_raw, dict):
            alias = str(entry_raw.get("alias", "") or "").strip()
        if not alias:
            continue
        alias_key = alias.strip().lower()
        idx.by_alias[alias_key] = {"alias": alias, "ref": parsed}
        key = model_key(parsed.provider, parsed.model)
        idx.by_key.setdefault(key, []).append(alias)

    return idx


# ---------------------------------------------------------------------------
# Model ref resolution
# ---------------------------------------------------------------------------

def resolve_model_ref_from_string(
    raw: str,
    default_provider: str,
    alias_index: ModelAliasIndex | None = None,
) -> dict[str, Any] | None:
    """Resolve a raw string to a ModelRef, checking alias table first.

    Mirrors TS resolveModelRefFromString().
    Returns {"ref": ModelRef, "alias": str | None} or None.
    """
    trimmed = raw.strip()
    if not trimmed:
        return None
    # If no slash, check alias table
    if "/" not in trimmed and alias_index is not None:
        alias_key = trimmed.strip().lower()
        alias_match = alias_index.by_alias.get(alias_key)
        if alias_match:
            return {"ref": alias_match["ref"], "alias": alias_match["alias"]}
    parsed = parse_model_ref(trimmed, default_provider)
    if not parsed:
        return None
    return {"ref": parsed}


def normalize_model_selection(value: Any) -> str | None:
    """Extract a model string from config value (string or {"primary": ...}).

    Mirrors TS normalizeModelSelection().
    """
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        primary = value.get("primary")
        if isinstance(primary, str):
            return primary.strip() or None
    return None


# ---------------------------------------------------------------------------
# Allow-list
# ---------------------------------------------------------------------------

def build_configured_allowlist_keys(
    cfg: Any,
    default_provider: str = DEFAULT_PROVIDER,
) -> set[str] | None:
    """Build the set of allowed model keys from config.

    Mirrors TS buildConfiguredAllowlistKeys().
    Returns None if no allowlist is configured.
    """
    raw_allowlist: list[str] = []
    if isinstance(cfg, dict):
        agents_cfg = cfg.get("agents", {})
        if isinstance(agents_cfg, dict):
            defaults_cfg = agents_cfg.get("defaults", {})
            if isinstance(defaults_cfg, dict):
                raw_allowlist = list(defaults_cfg.get("models", {}) or {})

    if not raw_allowlist:
        return None

    keys: set[str] = set()
    for raw in raw_allowlist:
        parsed = parse_model_ref(str(raw or ""), default_provider)
        if parsed:
            keys.add(model_key(parsed.provider, parsed.model))
    return keys if keys else None


def get_model_ref_status(
    cfg: Any,
    ref: ModelRef,
    default_provider: str = DEFAULT_PROVIDER,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return allow-list status for a ModelRef.

    Mirrors TS getModelRefStatus().
    Returns {"key", "in_catalog", "allow_any", "allowed"}.
    """
    allowed_keys = build_configured_allowlist_keys(cfg, default_provider)
    allow_any = allowed_keys is None
    key = model_key(ref.provider, ref.model)
    in_catalog = (
        any(
            model_key(e.get("provider", ""), e.get("id", "")) == key
            for e in (catalog or [])
        )
    )
    return {
        "key": key,
        "in_catalog": in_catalog,
        "allow_any": allow_any,
        "allowed": allow_any or (allowed_keys is not None and key in allowed_keys),
    }


def resolve_allowed_model_ref(
    cfg: Any,
    raw: str,
    default_provider: str = DEFAULT_PROVIDER,
    catalog: list[dict[str, Any]] | None = None,
    default_model: str | None = None,
) -> dict[str, Any]:
    """Validate and allow-list check a raw model string.

    Mirrors TS resolveAllowedModelRef().
    Returns {"ref": ModelRef, "key": str} or {"error": str}.
    """
    trimmed = raw.strip()
    if not trimmed:
        return {"error": "invalid model: empty"}

    alias_index = build_model_alias_index(cfg, default_provider)
    resolved = resolve_model_ref_from_string(trimmed, default_provider, alias_index)
    if not resolved:
        return {"error": f"invalid model: {trimmed}"}

    ref: ModelRef = resolved["ref"]
    status = get_model_ref_status(cfg, ref, default_provider, catalog)
    if not status["allowed"]:
        return {"error": f"model not allowed: {status['key']}"}
    return {"ref": ref, "key": status["key"]}


# ---------------------------------------------------------------------------
# Thinking level
# ---------------------------------------------------------------------------

def resolve_thinking_default(
    cfg: Any,
    provider: str,
    model: str,
    catalog: list[dict[str, Any]] | None = None,
) -> ThinkLevel:
    """Return the default thinking level for a model.

    Mirrors TS resolveThinkingDefault().
    """
    configured: str | None = None
    if isinstance(cfg, dict):
        agents_cfg = cfg.get("agents", {})
        if isinstance(agents_cfg, dict):
            defaults_cfg = agents_cfg.get("defaults", {})
            if isinstance(defaults_cfg, dict):
                configured = defaults_cfg.get("thinkingDefault") or None
    if configured:
        return configured  # type: ignore[return-value]

    candidate = None
    if catalog:
        for entry in catalog:
            if entry.get("provider") == provider and entry.get("id") == model:
                candidate = entry
                break
    if candidate and candidate.get("reasoning"):
        return "low"
    return "off"


# ---------------------------------------------------------------------------
# Hooks model resolution
# ---------------------------------------------------------------------------

def resolve_hooks_gmail_model(
    cfg: Any,
    default_provider: str = DEFAULT_PROVIDER,
) -> ModelRef | None:
    """Return the model configured for Gmail hook processing.

    Mirrors TS resolveHooksGmailModel().
    """
    hooks_model: str | None = None
    if isinstance(cfg, dict):
        hooks_cfg = cfg.get("hooks", {})
        if isinstance(hooks_cfg, dict):
            gmail_cfg = hooks_cfg.get("gmail", {})
            if isinstance(gmail_cfg, dict):
                hooks_model = gmail_cfg.get("model") or None
    if not hooks_model or not hooks_model.strip():
        return None

    alias_index = build_model_alias_index(cfg, default_provider)
    resolved = resolve_model_ref_from_string(hooks_model, default_provider, alias_index)
    return resolved["ref"] if resolved else None


# ---------------------------------------------------------------------------
# Provider lookup helpers — mirrors TS findNormalizedProviderValue/Key
# ---------------------------------------------------------------------------

def find_normalized_provider_value(
    entries: dict[str, Any] | None,
    provider: str,
) -> Any:
    """Return the value associated with *provider* in a provider-keyed dict.

    Normalises all keys before comparing.  Mirrors TS findNormalizedProviderValue().
    """
    if not entries:
        return None
    provider_key = normalize_provider_id(provider)
    for key, value in entries.items():
        if normalize_provider_id(key) == provider_key:
            return value
    return None


def find_normalized_provider_key(
    entries: dict[str, Any] | None,
    provider: str,
) -> str | None:
    """Return the dict key that matches *provider* after normalisation.

    Mirrors TS findNormalizedProviderKey().
    """
    if not entries:
        return None
    provider_key = normalize_provider_id(provider)
    return next(
        (key for key in entries if normalize_provider_id(key) == provider_key),
        None,
    )


# ---------------------------------------------------------------------------
# Allow-list helpers
# ---------------------------------------------------------------------------

def resolve_allowlist_model_key(raw: str, default_provider: str) -> str | None:
    """Parse *raw* into a canonical model key "provider/model", or None.

    Mirrors TS resolveAllowlistModelKey().
    """
    parsed = parse_model_ref(raw, default_provider)
    if not parsed:
        return None
    return model_key(parsed.provider, parsed.model)


def build_allowed_model_set(
    cfg: Any,
    catalog: list[Any],
    default_provider: str = DEFAULT_PROVIDER,
    default_model: str | None = None,
) -> dict[str, Any]:
    """Return the full allowed-model set from config + catalog.

    Mirrors TS buildAllowedModelSet().
    Returns {"allow_any": bool, "allowed_catalog": list, "allowed_keys": set}.
    """
    raw_allowlist: list[str] = []
    if isinstance(cfg, dict):
        agents_section = cfg.get("agents") or {}
        if isinstance(agents_section, dict):
            defaults_section = agents_section.get("defaults") or {}
            if isinstance(defaults_section, dict):
                raw_allowlist = list(defaults_section.get("models") or {})

    allow_any = len(raw_allowlist) == 0

    default_ref = (
        parse_model_ref(default_model.strip(), default_provider)
        if default_model and default_model.strip()
        else None
    )
    default_key = (
        model_key(default_ref.provider, default_ref.model) if default_ref else None
    )

    catalog_keys: set[str] = set(
        model_key(e.get("provider", "") if isinstance(e, dict) else getattr(e, "provider", ""),
                  e.get("id", "") if isinstance(e, dict) else getattr(e, "id", ""))
        for e in catalog
    )

    if allow_any:
        if default_key:
            catalog_keys.add(default_key)
        return {
            "allow_any": True,
            "allowed_catalog": catalog,
            "allowed_keys": catalog_keys,
        }

    allowed_keys: set[str] = set()
    configured_providers: dict[str, Any] = {}
    if isinstance(cfg, dict):
        configured_providers = cfg.get("models", {}).get("providers", {}) or {}

    for raw in raw_allowlist:
        parsed = parse_model_ref(str(raw), default_provider)
        if not parsed:
            continue
        key = model_key(parsed.provider, parsed.model)
        if is_cli_provider(parsed.provider, cfg):
            allowed_keys.add(key)
        elif key in catalog_keys:
            allowed_keys.add(key)
        elif find_normalized_provider_key(configured_providers, parsed.provider) is not None:
            # Explicitly configured provider: allow even if not in catalog
            allowed_keys.add(key)

    if default_key:
        allowed_keys.add(default_key)

    allowed_catalog = [
        e for e in catalog
        if model_key(
            e.get("provider", "") if isinstance(e, dict) else getattr(e, "provider", ""),
            e.get("id", "") if isinstance(e, dict) else getattr(e, "id", ""),
        ) in allowed_keys
    ]

    if not allowed_catalog and not allowed_keys:
        if default_key:
            catalog_keys.add(default_key)
        return {
            "allow_any": True,
            "allowed_catalog": catalog,
            "allowed_keys": catalog_keys,
        }

    return {"allow_any": False, "allowed_catalog": allowed_catalog, "allowed_keys": allowed_keys}


# ---------------------------------------------------------------------------
# CLI provider detection
# ---------------------------------------------------------------------------

def is_cli_provider(provider: str, cfg: Any = None) -> bool:
    """Return True if provider is a CLI-based backend.

    Mirrors TS isCliProvider().
    """
    normalized = normalize_provider_id(provider)
    if normalized in ("claude-cli", "codex-cli"):
        return True
    if isinstance(cfg, dict):
        agents_cfg = cfg.get("agents", {})
        if isinstance(agents_cfg, dict):
            defaults_cfg = agents_cfg.get("defaults", {})
            if isinstance(defaults_cfg, dict):
                cli_backends = defaults_cfg.get("cliBackends", {}) or {}
                return any(normalize_provider_id(k) == normalized for k in cli_backends)
    return False


# ---------------------------------------------------------------------------
# Configured model ref resolution
# ---------------------------------------------------------------------------

def resolve_configured_model_ref(
    cfg: Any,
    default_provider: str = DEFAULT_PROVIDER,
    default_model: str = DEFAULT_MODEL,
) -> ModelRef:
    """Resolve the configured default model ref from config.

    Mirrors TS resolveConfiguredModelRef().
    """
    # Extract raw model string from agents.defaults.model (string or {primary: ...})
    raw_model = ""
    if isinstance(cfg, dict):
        agents_cfg = cfg.get("agents") or {}
        if isinstance(agents_cfg, dict):
            defaults_cfg = agents_cfg.get("defaults") or {}
            if isinstance(defaults_cfg, dict):
                raw = defaults_cfg.get("model")
                if isinstance(raw, str):
                    raw_model = raw.strip()
                elif isinstance(raw, dict):
                    primary = raw.get("primary")
                    if isinstance(primary, str):
                        raw_model = primary.strip()

    if raw_model:
        alias_index = build_model_alias_index(cfg, default_provider)
        if "/" not in raw_model:
            # Try alias lookup
            alias_key = raw_model.lower()
            alias_match = alias_index.by_alias.get(alias_key)
            if alias_match:
                return alias_match["ref"]
            # Fallback: treat as bare model name under default provider
            import warnings
            warnings.warn(
                f'[openclaw] Model "{raw_model}" specified without provider. '
                f'Falling back to "{default_provider}/{raw_model}". '
                f'Please use "{default_provider}/{raw_model}" in your config.',
                stacklevel=3,
            )
            return normalize_model_ref(default_provider, raw_model)

        resolved = resolve_model_ref_from_string(raw_model, default_provider, alias_index)
        if resolved:
            return resolved["ref"]

    return ModelRef(provider=default_provider, model=default_model)


def _resolve_agent_model_primary(cfg: Any, agent_id: str) -> str | None:
    """Extract per-agent model override from config.agents.agents[].model."""
    if not isinstance(cfg, dict):
        return None
    agents_cfg = cfg.get("agents") or {}
    if not isinstance(agents_cfg, dict):
        return None
    agents_list = agents_cfg.get("agents") or []
    for agent in (agents_list if isinstance(agents_list, list) else []):
        if not isinstance(agent, dict):
            continue
        if agent.get("id") == agent_id:
            raw = agent.get("model")
            if isinstance(raw, str):
                return raw.strip() or None
            if isinstance(raw, dict):
                primary = raw.get("primary")
                if isinstance(primary, str):
                    return primary.strip() or None
    return None


def _resolve_agent_config(cfg: Any, agent_id: str) -> dict[str, Any] | None:
    """Return the config entry for a specific agent id."""
    if not isinstance(cfg, dict):
        return None
    agents_cfg = cfg.get("agents") or {}
    if not isinstance(agents_cfg, dict):
        return None
    agents_list = agents_cfg.get("agents") or []
    for agent in (agents_list if isinstance(agents_list, list) else []):
        if isinstance(agent, dict) and agent.get("id") == agent_id:
            return agent
    return None


def resolve_default_model_for_agent(
    cfg: Any,
    agent_id: str | None = None,
    default_provider: str = DEFAULT_PROVIDER,
    default_model: str = DEFAULT_MODEL,
) -> ModelRef:
    """Resolve the effective default model for a given agent.

    Mirrors TS resolveDefaultModelForAgent().
    """
    agent_model_override = (
        _resolve_agent_model_primary(cfg, agent_id) if agent_id else None
    )

    if agent_model_override:
        # Temporarily override agents.defaults.model with the per-agent value
        import copy as _copy
        cfg_copy = _copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
        agents_section = cfg_copy.setdefault("agents", {})
        if not isinstance(agents_section, dict):
            agents_section = {}
            cfg_copy["agents"] = agents_section
        defaults_section = agents_section.setdefault("defaults", {})
        if not isinstance(defaults_section, dict):
            defaults_section = {}
            agents_section["defaults"] = defaults_section
        existing_model = defaults_section.get("model", {})
        if isinstance(existing_model, dict):
            existing_model = {**existing_model, "primary": agent_model_override}
        else:
            existing_model = {"primary": agent_model_override}
        defaults_section["model"] = existing_model
        return resolve_configured_model_ref(cfg_copy, default_provider, default_model)

    return resolve_configured_model_ref(cfg, default_provider, default_model)


# ---------------------------------------------------------------------------
# Subagent model resolution
# ---------------------------------------------------------------------------

def resolve_subagent_configured_model_selection(
    cfg: Any,
    agent_id: str,
) -> str | None:
    """Return the configured model for subagents spawned by a given agent.

    Mirrors TS resolveSubagentConfiguredModelSelection().
    Priority: agent.subagents.model > defaults.subagents.model > agent.model
    """
    agent_config = _resolve_agent_config(cfg, agent_id)
    defaults_cfg: dict[str, Any] = {}
    if isinstance(cfg, dict):
        agents_section = cfg.get("agents") or {}
        if isinstance(agents_section, dict):
            defaults_cfg = agents_section.get("defaults") or {}

    # agent.subagents.model
    if isinstance(agent_config, dict):
        subagents_cfg = agent_config.get("subagents") or {}
        if isinstance(subagents_cfg, dict):
            subagent_model = normalize_model_selection(subagents_cfg.get("model"))
            if subagent_model:
                return subagent_model

    # defaults.subagents.model
    if isinstance(defaults_cfg, dict):
        subagents_defaults = defaults_cfg.get("subagents") or {}
        if isinstance(subagents_defaults, dict):
            subagent_model = normalize_model_selection(subagents_defaults.get("model"))
            if subagent_model:
                return subagent_model

    # agent.model
    if isinstance(agent_config, dict):
        return normalize_model_selection(agent_config.get("model"))

    return None


def resolve_subagent_spawn_model_selection(
    cfg: Any,
    agent_id: str,
    model_override: Any = None,
    default_provider: str = DEFAULT_PROVIDER,
    default_model: str = DEFAULT_MODEL,
) -> str:
    """Return the model string to use when spawning a subagent.

    Mirrors TS resolveSubagentSpawnModelSelection().
    Priority: modelOverride > configured subagent model > defaults.model > runtime default
    """
    runtime_default = resolve_default_model_for_agent(
        cfg, agent_id, default_provider, default_model
    )
    defaults_cfg: dict[str, Any] = {}
    if isinstance(cfg, dict):
        agents_section = cfg.get("agents") or {}
        if isinstance(agents_section, dict):
            defaults_cfg = agents_section.get("defaults") or {}

    return (
        normalize_model_selection(model_override)
        or resolve_subagent_configured_model_selection(cfg, agent_id)
        or normalize_model_selection(
            (defaults_cfg.get("model") if isinstance(defaults_cfg, dict) else None)
        )
        or f"{runtime_default.provider}/{runtime_default.model}"
    )


def get_provider_from_model(model_string: str) -> str | None:
    """Extract provider from a model string (e.g., 'anthropic/claude-3-opus' -> 'anthropic').
    
    Returns None if no provider prefix is found.
    Mirrors TypeScript getProviderFromModel() functionality.
    """
    if not model_string or not isinstance(model_string, str):
        return None
    
    if "/" in model_string:
        provider, _ = model_string.split("/", 1)
        return normalize_provider_id(provider)
    
    return None
