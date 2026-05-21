"""Thinking level utilities

Fully aligned with TypeScript openclaw/src/auto-reply/thinking.ts

This module provides utilities for thinking levels, which control
how much internal reasoning models perform before responding.
"""
from __future__ import annotations

from typing import Literal

ThinkLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]

# XHigh models (matches TS XHIGH_MODEL_REFS from thinking.ts lines 24-32)
XHIGH_MODEL_REFS = [
    "openai/gpt-5.2",
    "openai-codex/gpt-5.3-codex",
    "openai-codex/gpt-5.3-codex-spark",
    "openai-codex/gpt-5.2-codex",
    "openai-codex/gpt-5.1-codex",
    "github-copilot/gpt-5.2-codex",
    "github-copilot/gpt-5.2",
]

XHIGH_MODEL_SET = {ref.lower() for ref in XHIGH_MODEL_REFS}
XHIGH_MODEL_IDS = {
    ref.split("/")[1].lower()
    for ref in XHIGH_MODEL_REFS
    if "/" in ref
}


from dataclasses import dataclass, field


@dataclass
class ThinkingProviderProfile:
    """Per-provider thinking configuration.

    Mirrors TS ProviderThinkingProfile in plugins/provider-thinking.ts.
    """

    binary: bool = False             # on/off only (e.g. zai)
    xhigh_capable: bool = False      # supports xhigh level
    available_levels: list[str] = field(default_factory=list)  # empty = use defaults
    default_level: str = "off"       # default thinking level for new sessions


# ── Built-in provider profiles ────────────────────────────────────────────────
_BUILTIN_PROVIDER_PROFILES: dict[str, ThinkingProviderProfile] = {
    "zai": ThinkingProviderProfile(binary=True, default_level="off"),
    "moonshot": ThinkingProviderProfile(binary=False, default_level="off"),  # Kimi thinking OFF by default
    "anthropic": ThinkingProviderProfile(binary=False, xhigh_capable=False, default_level="medium"),
    "openai": ThinkingProviderProfile(binary=False, xhigh_capable=False, default_level="medium"),
    "openai-codex": ThinkingProviderProfile(binary=False, xhigh_capable=True, default_level="medium"),
    "github-copilot": ThinkingProviderProfile(binary=False, xhigh_capable=True, default_level="medium"),
}

# ── Plugin-registered profiles (populated by api.register_provider_thinking_profile) ──
_PLUGIN_PROVIDER_PROFILES: dict[str, ThinkingProviderProfile] = {}

_DEFAULT_PROFILE = ThinkingProviderProfile(binary=False, default_level="off")


def register_provider_thinking_profile(provider: str, profile: ThinkingProviderProfile) -> None:
    """Register a thinking profile for a provider from a plugin.

    Mirrors TS registerProviderThinkingProfile().
    Plugin profiles override built-in defaults.
    """
    _PLUGIN_PROVIDER_PROFILES[normalize_provider_id(provider)] = profile


def resolve_thinking_profile(
    provider: str | None,
    model_id: str | None,
) -> ThinkingProviderProfile:
    """Resolve the thinking profile for a provider/model combination.

    Priority: plugin-registered > built-in defaults > global default.
    Mirrors TS resolveThinkingProfile().
    """
    normalized = normalize_provider_id(provider)
    if normalized in _PLUGIN_PROVIDER_PROFILES:
        return _PLUGIN_PROVIDER_PROFILES[normalized]
    if normalized in _BUILTIN_PROVIDER_PROFILES:
        base = _BUILTIN_PROVIDER_PROFILES[normalized]
        # kimi-k2.6 can use thinking.keep="all" but still defaults to OFF
        if normalized == "moonshot" and model_id and "k2.6" in model_id:
            return ThinkingProviderProfile(binary=False, xhigh_capable=False, default_level="off")
        return base
    return _DEFAULT_PROFILE


def resolve_supported_thinking_level(
    requested: str | None,
    profile: ThinkingProviderProfile,
) -> str:
    """Return the closest supported thinking level given a profile.

    Mirrors TS resolveSupportedThinkingLevel().
    """
    if not requested:
        return profile.default_level

    if profile.binary:
        # Binary providers: map everything to "on" or "off"
        return "off" if requested == "off" else "on"

    available = profile.available_levels or ["off", "minimal", "low", "medium", "high"]
    if profile.xhigh_capable and "xhigh" not in available:
        available = list(available) + ["xhigh"]

    if requested in available:
        return requested

    # Fallback: nearest lower level
    order = ["off", "minimal", "low", "medium", "high", "xhigh"]
    try:
        req_idx = order.index(requested)
        for i in range(req_idx, -1, -1):
            if order[i] in available:
                return order[i]
    except ValueError:
        pass
    return profile.default_level


def normalize_provider_id(provider: str | None) -> str:
    """Normalize provider ID (matches TS normalizeProviderId lines 9-18)"""
    if not provider:
        return ""
    
    normalized = provider.strip().lower()
    if normalized in ("z.ai", "z-ai"):
        return "zai"
    return normalized


def is_binary_thinking_provider(provider: str | None) -> bool:
    """Check if provider uses binary thinking (on/off only).

    Checks plugin-registered profiles first, then built-in defaults.
    Mirrors TS provider-thinking.ts isBinaryThinkingProvider().
    """
    normalized = normalize_provider_id(provider)
    profile = resolve_thinking_profile(normalized, None)
    return profile.binary


def normalize_think_level(raw: str | None, default: ThinkLevel = "medium") -> ThinkLevel:
    """
    Normalize user-provided thinking level strings to canonical enum.
    
    Mirrors TS normalizeThinkLevel from thinking.ts lines 42-75
    
    Examples:
        "think-hard" -> "low"
        "think-harder" -> "medium"
        "ultra" -> "high"
        "xhigh" -> "xhigh"
        "off" -> "off"
    """
    if not raw:
        return default
    
    key = raw.strip().lower()
    collapsed = key.replace(" ", "").replace("_", "").replace("-", "")
    
    if collapsed in ("xhigh", "extrahigh"):
        return "xhigh"
    
    if key in ("off",):
        return "off"
    
    if key in ("on", "enable", "enabled"):
        return "low"
    
    if key in ("min", "minimal"):
        return "minimal"
    
    if key in ("low", "thinkhard", "think-hard", "think_hard"):
        return "low"
    
    if key in ("mid", "med", "medium", "thinkharder", "think-harder", "harder"):
        return "medium"
    
    if key in ("high", "ultra", "ultrathink", "think-hard", "thinkhardest", "highest", "max"):
        return "high"
    
    if key in ("think",):
        return "minimal"

    return default


def supports_xhigh_thinking(provider: str | None, model: str | None) -> bool:
    """Check if provider/model supports xhigh thinking.

    Checks plugin-registered profiles first, then model-id based lookup.
    Mirrors TS supportsXHighThinking from thinking.ts lines 77-87.
    """
    profile = resolve_thinking_profile(normalize_provider_id(provider), model)
    if profile.xhigh_capable:
        return True

    model_key = (model or "").strip().lower()
    if not model_key:
        return False

    provider_key = (provider or "").strip().lower()
    if provider_key:
        return f"{provider_key}/{model_key}" in XHIGH_MODEL_SET

    return model_key in XHIGH_MODEL_IDS


def list_thinking_levels(
    provider: str | None = None,
    model: str | None = None,
    *,
    is_binary_provider: bool | None = None,
) -> list[ThinkLevel]:
    """
    List available thinking levels for provider/model.

    Mirrors TS listThinkingLevels from thinking.ts lines 89-95.
    ``is_binary_provider`` shortcut: True → ["off", "on"], False → full levels.
    """
    if is_binary_provider is True:
        return ["off", "on"]
    if is_binary_provider is False:
        levels: list[ThinkLevel] = ["off", "minimal", "low", "medium", "high"]
        return levels
    # Normal path: derive from provider/model
    if is_binary_thinking_provider(provider):
        return ["off", "on"]
    levels = ["off", "minimal", "low", "medium", "high"]
    if supports_xhigh_thinking(provider, model):
        levels.append("xhigh")
    return levels


def list_thinking_level_labels(provider: str | None, model: str | None) -> list[str]:
    """
    List thinking level labels for display.
    
    Binary providers (like zai) use "off"/"on" instead of granular levels.
    
    Mirrors TS listThinkingLevelLabels from thinking.ts lines 97-102
    """
    if is_binary_thinking_provider(provider):
        return ["off", "on"]
    return list(list_thinking_levels(provider, model))


def format_thinking_levels(
    provider: str | None,
    model: str | None,
    separator: str = ", ",
) -> str:
    """
    Format thinking levels as a string for error messages.
    
    Mirrors TS formatThinkingLevels from thinking.ts lines 104-110
    
    Examples:
        format_thinking_levels("openai", "gpt-4") -> "off, minimal, low, medium, high"
        format_thinking_levels("openai", "gpt-5.2") -> "off, minimal, low, medium, high, xhigh"
        format_thinking_levels("zai", "zai-1") -> "off, on"
    """
    return separator.join(list_thinking_level_labels(provider, model))


def format_xhigh_model_hint() -> str:
    """
    Format hint for xhigh-capable models.
    
    Mirrors TS formatXHighModelHint from thinking.ts lines 112-124
    """
    refs = list(XHIGH_MODEL_REFS)
    if len(refs) == 0:
        return "unknown model"
    if len(refs) == 1:
        return refs[0]
    if len(refs) == 2:
        return f"{refs[0]} or {refs[1]}"
    return f"{', '.join(refs[:-1])} or {refs[-1]}"
