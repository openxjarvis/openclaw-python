"""
Skill environment variable overrides — mirrors TS agents/skills/env-overrides.ts

Injects API keys and env overrides from skill config into the process environment
for the duration of an agent turn, then reverts them.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable

from .config import resolve_skill_config, resolve_skill_key
from .types import SkillEntry, SkillSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var sanitisation (mirrors TS sanitize-env-vars.ts)
# ---------------------------------------------------------------------------

BLOCKED_ENV_VAR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^ANTHROPIC_API_KEY$", re.I),
    re.compile(r"^OPENAI_API_KEY$", re.I),
    re.compile(r"^GEMINI_API_KEY$", re.I),
    re.compile(r"^OPENROUTER_API_KEY$", re.I),
    re.compile(r"^MINIMAX_API_KEY$", re.I),
    re.compile(r"^ELEVENLABS_API_KEY$", re.I),
    re.compile(r"^SYNTHETIC_API_KEY$", re.I),
    re.compile(r"^TELEGRAM_BOT_TOKEN$", re.I),
    re.compile(r"^DISCORD_BOT_TOKEN$", re.I),
    re.compile(r"^SLACK_(BOT|APP)_TOKEN$", re.I),
    re.compile(r"^LINE_CHANNEL_SECRET$", re.I),
    re.compile(r"^LINE_CHANNEL_ACCESS_TOKEN$", re.I),
    re.compile(r"^OPENCLAW_GATEWAY_(TOKEN|PASSWORD)$", re.I),
    re.compile(r"^AWS_(SECRET_ACCESS_KEY|SECRET_KEY|SESSION_TOKEN)$", re.I),
    re.compile(r"^(GH|GITHUB)_TOKEN$", re.I),
    re.compile(r"^(AZURE|AZURE_OPENAI|COHERE|AI_GATEWAY|OPENROUTER)_API_KEY$", re.I),
    re.compile(r"_?(API_KEY|TOKEN|PASSWORD|PRIVATE_KEY|SECRET)$", re.I),
]

DANGEROUS_HOST_ENV_PREFIXES = [
    "LD_", "DYLD_", "BASH_FUNC_", "ENV_", "CDPATH",
]
DANGEROUS_HOST_ENV_KEYS = {
    "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "SHELL", "BASH_ENV",
    "ENV", "CDPATH", "IFS", "GLOBIGNORE", "SHELLOPTS", "BASHOPTS",
}

SKILL_ALWAYS_BLOCKED_PATTERNS: list[re.Pattern[str]] = [re.compile(r"^OPENSSL_CONF$", re.I)]


def _matches_any(value: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(value) for p in patterns)


def _is_dangerous_host_env(key: str) -> bool:
    upper = key.upper()
    if upper in DANGEROUS_HOST_ENV_KEYS:
        return True
    return any(upper.startswith(p) for p in DANGEROUS_HOST_ENV_PREFIXES)


def _is_always_blocked_skill_env(key: str) -> bool:
    return _is_dangerous_host_env(key) or _matches_any(key, SKILL_ALWAYS_BLOCKED_PATTERNS)


def _validate_env_var_value(value: str) -> str | None:
    if "\0" in value:
        return "Contains null bytes"
    if len(value) > 32768:
        return "Value exceeds maximum length"
    if re.match(r"^[A-Za-z0-9+/=]{80,}$", value):
        return "Value looks like base64-encoded credential data"
    return None


def _sanitize_skill_env_overrides(
    overrides: dict[str, str],
    allowed_sensitive_keys: set[str],
) -> tuple[dict[str, str], list[str], list[str]]:
    """Sanitize env overrides — mirrors TS sanitizeSkillEnvOverrides.

    Returns (allowed, blocked, warnings).
    """
    if not overrides:
        return {}, [], []

    allowed: dict[str, str] = {}
    blocked: list[str] = []
    warnings: list[str] = []

    for raw_key, value in overrides.items():
        key = raw_key.strip()
        if not key:
            continue

        if _matches_any(key, BLOCKED_ENV_VAR_PATTERNS):
            if key in allowed_sensitive_keys:
                warning = _validate_env_var_value(value)
                if warning == "Contains null bytes":
                    blocked.append(key)
                    continue
                if warning:
                    warnings.append(f"{key}: {warning}")
                allowed[key] = value
            else:
                blocked.append(key)
            continue

        if _is_always_blocked_skill_env(key):
            blocked.append(key)
            continue

        warning = _validate_env_var_value(value)
        if warning == "Contains null bytes":
            blocked.append(key)
            continue
        if warning:
            warnings.append(f"{key}: {warning}")

        allowed[key] = value

    return allowed, blocked, warnings


# ---------------------------------------------------------------------------
# Core env-override application
# ---------------------------------------------------------------------------

_EnvUpdate = tuple[str, str | None]  # (key, previous_value_or_None)


def _apply_skill_config_env(
    updates: list[_EnvUpdate],
    skill_config: dict[str, Any],
    primary_env: str | None,
    required_env: list[str] | None,
    skill_key: str,
) -> None:
    """Apply env overrides from a single skill config — mirrors TS applySkillConfigEnvOverrides."""
    allowed_sensitive: set[str] = set()
    normalized_primary = (primary_env or "").strip()
    if normalized_primary:
        allowed_sensitive.add(normalized_primary)
    for env_name in (required_env or []):
        trimmed = env_name.strip()
        if trimmed:
            allowed_sensitive.add(trimmed)

    pending: dict[str, str] = {}

    env_block = skill_config.get("env")
    if isinstance(env_block, dict):
        for raw_key, env_value in env_block.items():
            key = raw_key.strip()
            if not key or not env_value or os.environ.get(key):
                continue
            pending[key] = str(env_value)

    api_key = skill_config.get("apiKey")
    if isinstance(api_key, str):
        api_key = api_key.strip()
    if normalized_primary and api_key and not os.environ.get(normalized_primary):
        if normalized_primary not in pending:
            pending[normalized_primary] = api_key

    allowed, blocked, warnings = _sanitize_skill_env_overrides(pending, allowed_sensitive)

    if blocked:
        logger.warning("Blocked skill env overrides for %s: %s", skill_key, ", ".join(blocked))
    if warnings:
        logger.warning("Suspicious skill env overrides for %s: %s", skill_key, ", ".join(warnings))

    for env_key, env_value in allowed.items():
        if os.environ.get(env_key):
            continue
        updates.append((env_key, os.environ.get(env_key)))
        os.environ[env_key] = env_value


def _create_env_reverter(updates: list[_EnvUpdate]) -> Callable[[], None]:
    """Create a function that reverts env changes — mirrors TS createEnvReverter."""
    def _revert() -> None:
        for key, prev in updates:
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
    return _revert


def apply_skill_env_overrides(
    skills: list[SkillEntry],
    config: Any = None,
) -> Callable[[], None]:
    """Inject env vars from skill configs — mirrors TS applySkillEnvOverrides.

    Returns a reverter function that restores original env state.
    """
    updates: list[_EnvUpdate] = []

    for entry in skills:
        skill_key = resolve_skill_key(entry)
        skill_config = resolve_skill_config(config, skill_key)
        if not skill_config:
            continue

        _apply_skill_config_env(
            updates,
            skill_config,
            primary_env=entry.metadata.primary_env if entry.metadata else None,
            required_env=(entry.metadata.requires.env if entry.metadata and hasattr(entry.metadata.requires, "env") else None)
                if entry.metadata and entry.metadata.requires else None,
            skill_key=skill_key,
        )

    return _create_env_reverter(updates)


def apply_skill_env_overrides_from_snapshot(
    snapshot: SkillSnapshot | None = None,
    config: Any = None,
) -> Callable[[], None]:
    """Inject env vars from a skill snapshot — mirrors TS applySkillEnvOverridesFromSnapshot.

    Returns a reverter function that restores original env state.
    """
    if not snapshot:
        return lambda: None

    updates: list[_EnvUpdate] = []

    for skill_info in snapshot.skills:
        name = skill_info.get("name", "")
        skill_config = resolve_skill_config(config, name)
        if not skill_config:
            continue

        _apply_skill_config_env(
            updates,
            skill_config,
            primary_env=skill_info.get("primaryEnv"),
            required_env=skill_info.get("requiredEnv"),
            skill_key=name,
        )

    return _create_env_reverter(updates)


__all__ = [
    "apply_skill_env_overrides",
    "apply_skill_env_overrides_from_snapshot",
]
