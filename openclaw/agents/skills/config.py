"""
Skill configuration and eligibility — mirrors TS agents/skills/config.ts

Provides:
  - should_include_skill(): full 3-gate filter (enabled, bundled allowlist, runtime eligibility)
  - evaluate_runtime_eligibility(): OS, bins, env, config path checks
  - resolve_skill_config(): per-skill config lookup
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any

from .types import SkillEntry, SkillEligibilityContext, SkillRequires

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_VALUES: dict[str, bool] = {
    "browser.enabled": True,
    "browser.evaluateEnabled": True,
}

BUNDLED_SOURCES = frozenset({"openclaw-bundled"})


def resolve_runtime_platform() -> str:
    """Return the platform string matching TS process.platform."""
    return sys.platform


def has_binary(name: str) -> bool:
    """Check if a binary is on PATH (mirrors TS hasBinary)."""
    return shutil.which(name) is not None


def resolve_skill_key(entry: SkillEntry) -> str:
    """Resolve the config lookup key for a skill (mirrors TS resolveSkillKey)."""
    if entry.metadata and entry.metadata.skill_key:
        return entry.metadata.skill_key
    return entry.skill.name


def resolve_skill_config(config: Any, skill_key: str) -> dict[str, Any] | None:
    """Look up config.skills.entries.<skillKey> (mirrors TS resolveSkillConfig)."""
    if not config:
        return None
    try:
        skills = config.get("skills", {}) if isinstance(config, dict) else getattr(config, "skills", None)
        if not skills:
            return None
        entries = skills.get("entries", {}) if isinstance(skills, dict) else getattr(skills, "entries", None)
        if not entries or not isinstance(entries, dict):
            return None
        entry = entries.get(skill_key)
        if not entry or not isinstance(entry, dict):
            return None
        return entry
    except Exception:
        return None


def _normalize_allowlist(raw: Any) -> list[str] | None:
    """Normalize allowBundled to a string list or None (mirrors TS normalizeAllowlist)."""
    if not raw:
        return None
    if not isinstance(raw, list):
        return None
    normalized = [str(e).strip() for e in raw if str(e).strip()]
    return normalized if normalized else None


def _is_bundled_skill(entry: SkillEntry) -> bool:
    source = (getattr(entry.skill, "source", "") or "").strip()
    return source in BUNDLED_SOURCES


def resolve_bundled_allowlist(config: Any) -> list[str] | None:
    """Resolve config.skills.allowBundled (mirrors TS resolveBundledAllowlist)."""
    if not config:
        return None
    try:
        skills = config.get("skills", {}) if isinstance(config, dict) else getattr(config, "skills", None)
        if not skills:
            return None
        raw = skills.get("allowBundled") if isinstance(skills, dict) else getattr(skills, "allowBundled", None)
        return _normalize_allowlist(raw)
    except Exception:
        return None


def is_bundled_skill_allowed(entry: SkillEntry, allowlist: list[str] | None) -> bool:
    """Check if a bundled skill passes the allowlist (mirrors TS isBundledSkillAllowed)."""
    if not allowlist or len(allowlist) == 0:
        return True
    if not _is_bundled_skill(entry):
        return True
    key = resolve_skill_key(entry)
    return key in allowlist or entry.skill.name in allowlist


def _resolve_config_path(config: Any, path_str: str) -> Any:
    """Walk a dotted config path like 'browser.enabled' (mirrors TS resolveConfigPath)."""
    if not config:
        return None
    parts = path_str.split(".")
    current: Any = config
    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def is_config_path_truthy(config: Any, path_str: str) -> bool:
    """Check if a config path resolves to a truthy value (mirrors TS isConfigPathTruthy)."""
    value = _resolve_config_path(config, path_str)
    if value is not None:
        return bool(value)
    return DEFAULT_CONFIG_VALUES.get(path_str, False)


def _evaluate_runtime_requires(
    requires: SkillRequires | dict | None,
    *,
    has_bin: Any = has_binary,
    has_remote_bin: Any = None,
    has_any_remote_bin: Any = None,
    has_env: Any = None,
    is_config_path_truthy_fn: Any = None,
) -> bool:
    """Evaluate the requires block (bins, anyBins, env, config) — mirrors TS evaluateRuntimeRequires."""
    if not requires:
        return True

    if isinstance(requires, dict):
        bins = requires.get("bins", [])
        any_bins = requires.get("anyBins", []) or requires.get("any_bins", [])
        env = requires.get("env", [])
        config_paths = requires.get("config", [])
    else:
        bins = requires.bins or []
        any_bins = requires.any_bins or []
        env = requires.env or []
        config_paths = requires.config or []

    for b in bins:
        if not has_bin(b):
            if has_remote_bin and has_remote_bin(b):
                continue
            return False

    if any_bins:
        any_found = any(has_bin(b) for b in any_bins)
        if not any_found:
            if has_any_remote_bin and has_any_remote_bin(any_bins):
                pass
            else:
                return False

    if env and has_env:
        for env_name in env:
            if not has_env(env_name):
                return False

    if config_paths and is_config_path_truthy_fn:
        for config_path in config_paths:
            if not is_config_path_truthy_fn(config_path):
                return False

    return True


def evaluate_runtime_eligibility(
    *,
    os_list: list[str] | None = None,
    remote_platforms: list[str] | None = None,
    always: bool = False,
    requires: SkillRequires | dict | None = None,
    has_bin: Any = has_binary,
    has_remote_bin: Any = None,
    has_any_remote_bin: Any = None,
    has_env: Any = None,
    is_config_path_truthy_fn: Any = None,
) -> bool:
    """Full runtime eligibility check — mirrors TS evaluateRuntimeEligibility."""
    effective_os = os_list or []
    effective_remote = remote_platforms or []

    if effective_os:
        platform = resolve_runtime_platform()
        if platform not in effective_os and not any(rp in effective_os for rp in effective_remote):
            return False

    if always:
        return True

    return _evaluate_runtime_requires(
        requires,
        has_bin=has_bin,
        has_remote_bin=has_remote_bin,
        has_any_remote_bin=has_any_remote_bin,
        has_env=has_env,
        is_config_path_truthy_fn=is_config_path_truthy_fn,
    )


def should_include_skill(
    entry: SkillEntry,
    config: Any = None,
    eligibility: SkillEligibilityContext | None = None,
) -> bool:
    """Full 3-gate skill inclusion check — mirrors TS shouldIncludeSkill.

    Gates:
      1. Per-skill enabled toggle (config.skills.entries.<key>.enabled)
      2. Bundled allowlist (config.skills.allowBundled)
      3. Runtime eligibility (OS, bins, env vars, config paths)
    """
    skill_key = resolve_skill_key(entry)
    skill_config = resolve_skill_config(config, skill_key)
    allow_bundled = resolve_bundled_allowlist(config)

    # Gate 1: per-skill enabled toggle
    if skill_config and skill_config.get("enabled") is False:
        return False

    # Gate 2: bundled allowlist
    if not is_bundled_skill_allowed(entry, allow_bundled):
        return False

    # Gate 3: runtime eligibility
    remote = eligibility.remote if eligibility else None
    remote_platforms = remote.get("platforms", []) if isinstance(remote, dict) else []
    remote_has_bin = remote.get("hasBin") if isinstance(remote, dict) else None
    remote_has_any_bin = remote.get("hasAnyBin") if isinstance(remote, dict) else None

    metadata = entry.metadata

    def _has_env(env_name: str) -> bool:
        if os.environ.get(env_name):
            return True
        if skill_config:
            env_overrides = skill_config.get("env", {})
            if isinstance(env_overrides, dict) and env_overrides.get(env_name):
                return True
            api_key = skill_config.get("apiKey")
            if api_key and metadata and metadata.primary_env == env_name:
                return True
        return False

    def _is_config_truthy(config_path: str) -> bool:
        return is_config_path_truthy(config, config_path)

    return evaluate_runtime_eligibility(
        os_list=metadata.os if metadata else None,
        remote_platforms=remote_platforms,
        always=metadata.always if metadata else False,
        requires=metadata.requires if metadata else None,
        has_bin=has_binary,
        has_remote_bin=remote_has_bin,
        has_any_remote_bin=remote_has_any_bin,
        has_env=_has_env,
        is_config_path_truthy_fn=_is_config_truthy,
    )


# Legacy helpers kept for backward compat
def get_skill_config(config: dict | None, skill_key: str) -> dict | None:
    return resolve_skill_config(config, skill_key)


def is_skill_enabled(config: dict | None, skill_key: str) -> bool:
    sc = resolve_skill_config(config, skill_key)
    if sc is None:
        return True
    enabled = sc.get("enabled")
    return enabled is not False


def get_skill_value(config: dict | None, skill_key: str, value_key: str, default: Any = None) -> Any:
    sc = resolve_skill_config(config, skill_key)
    if sc is None:
        return default
    return sc.get(value_key, default)


__all__ = [
    "should_include_skill",
    "evaluate_runtime_eligibility",
    "resolve_skill_config",
    "resolve_skill_key",
    "resolve_bundled_allowlist",
    "is_bundled_skill_allowed",
    "has_binary",
    "is_config_path_truthy",
    "get_skill_config",
    "is_skill_enabled",
    "get_skill_value",
]
