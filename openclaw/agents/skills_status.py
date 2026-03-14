"""
Skills Status - builds skill status reports for workspaces

Aligned with openclaw/src/agents/skills-status.ts
Implements full dependency detection: bins, anyBins, env, config, os
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _has_binary(bin_name: str) -> bool:
    """Check if a binary is available on PATH (matches TS hasBinary)."""
    return shutil.which(bin_name) is not None


def _get_platform() -> str:
    """Get current platform string (darwin, linux, win32)."""
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "win32"
    return system


def _is_config_path_truthy(config: dict | None, path_str: str) -> bool:
    """
    Check if a config path is truthy (matches TS isConfigPathTruthy).
    Supports dot notation: "api.keys.openai"
    """
    if not config:
        return False
    parts = path_str.split(".")
    current: Any = config
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return bool(current)


def _resolve_missing_bins(
    required: list[str],
    has_local_bin: Callable[[str], bool],
    has_remote_bin: Callable[[str], bool] | None = None,
) -> list[str]:
    """Return bins that are missing (not found locally or remotely)."""
    result = []
    for bin_name in required:
        if has_local_bin(bin_name):
            continue
        if has_remote_bin and has_remote_bin(bin_name):
            continue
        result.append(bin_name)
    return result


def _resolve_missing_any_bins(
    required: list[str],
    has_local_bin: Callable[[str], bool],
    has_remote_any_bin: Callable[[list[str]], bool] | None = None,
) -> list[str]:
    """Return anyBins list if none are found (at least one must exist)."""
    if not required:
        return []
    if any(has_local_bin(b) for b in required):
        return []
    if has_remote_any_bin and has_remote_any_bin(required):
        return []
    return required


def _resolve_missing_os(
    required: list[str],
    local_platform: str,
    remote_platforms: list[str] | None = None,
) -> list[str]:
    """Return os list if current platform not in required."""
    if not required:
        return []
    if local_platform in required:
        return []
    if remote_platforms and any(p in required for p in remote_platforms):
        return []
    return required


def _resolve_missing_env(
    required: list[str],
    is_satisfied: Callable[[str], bool],
) -> list[str]:
    """Return env vars that are not satisfied."""
    return [e for e in required if not is_satisfied(e)]


def _normalize_install_options(
    entry: Any,
    prefs: dict | None = None,
) -> list[dict[str, Any]]:
    """
    Build install options from skill metadata (matches TS normalizeInstallOptions).
    Returns list of {id, kind, label, bins}.
    """
    metadata = getattr(entry, "metadata", None) or getattr(entry, "skill", None)
    if metadata:
        metadata = getattr(metadata, "metadata", metadata)
    install = getattr(metadata, "install", None) or []
    if not install:
        return []

    local_platform = _get_platform()
    result = []

    for i, spec in enumerate(install):
        # Check OS filter
        spec_os = getattr(spec, "os", None) or []
        if spec_os and local_platform not in spec_os:
            continue

        spec_id = getattr(spec, "id", None) or f"{getattr(spec, 'kind', 'install')}-{i}"
        bins = getattr(spec, "bins", None) or []
        label = (getattr(spec, "label", None) or "").strip()

        if not label:
            kind = getattr(spec, "kind", "")
            if kind == "brew" and getattr(spec, "formula", None):
                label = f"Install {spec.formula} (brew)"
            elif kind == "node" and getattr(spec, "package", None):
                node_mgr = (prefs or {}).get("nodeManager", "npm")
                label = f"Install {spec.package} ({node_mgr})"
            elif kind == "go" and getattr(spec, "module", None):
                label = f"Install {spec.module} (go)"
            elif kind == "uv" and getattr(spec, "package", None):
                label = f"Install {spec.package} (uv)"
            elif kind == "download" and getattr(spec, "url", None):
                url = spec.url
                last = url.rstrip("/").split("/")[-1] if url else "file"
                label = f"Download {last or url}"
            else:
                label = "Run installer"

        result.append({"id": spec_id, "kind": getattr(spec, "kind", "install"), "label": label, "bins": bins})

    return result


def _build_skill_status_entry(
    entry: Any,
    config: dict | None,
    eligibility: dict | None,
    bundled_names: set[str] | None,
) -> dict[str, Any]:
    """
    Build a single skill status entry (matches TS buildSkillStatus).
    """
    skill = getattr(entry, "skill", entry)
    metadata = getattr(entry, "metadata", None) or getattr(skill, "metadata", None)
    skill_key = (
        getattr(metadata, "skill_key", None)
        or getattr(metadata, "skillKey", None)
        or getattr(skill, "name", "unknown")
    )

    # Resolve config
    disabled = False
    blocked_by_allowlist = False
    if config:
        entries_cfg = (config.get("skills") or {}).get("entries") or {}
        skill_cfg = entries_cfg.get(skill_key) or entries_cfg.get(getattr(skill, "name", ""))
        if isinstance(skill_cfg, dict) and skill_cfg.get("enabled") is False:
            disabled = True

        allow_bundled = (config.get("skills") or {}).get("allowBundled")
        if allow_bundled is not None and isinstance(allow_bundled, list):
            source = getattr(skill, "source", "")
            if source == "openclaw-bundled" and skill_key not in allow_bundled:
                blocked_by_allowlist = True

    always = bool(getattr(metadata, "always", False) if metadata else False)

    # Requirements from metadata (handles both dict and SkillRequires)
    requires: dict[str, list] = {"bins": [], "anyBins": [], "env": [], "config": [], "os": []}
    if metadata:
        raw_requires = getattr(metadata, "requires", None)
        if raw_requires is not None:
            if hasattr(raw_requires, "bins"):
                requires["bins"] = list(getattr(raw_requires, "bins", []) or [])
            elif isinstance(raw_requires, dict):
                requires["bins"] = list(raw_requires.get("bins", []) or [])

            if hasattr(raw_requires, "any_bins"):
                requires["anyBins"] = list(getattr(raw_requires, "any_bins", []) or [])
            elif hasattr(raw_requires, "anyBins"):
                requires["anyBins"] = list(getattr(raw_requires, "anyBins", []) or [])
            elif isinstance(raw_requires, dict):
                requires["anyBins"] = list(raw_requires.get("anyBins", raw_requires.get("any_bins", [])) or [])

            if hasattr(raw_requires, "env"):
                requires["env"] = list(getattr(raw_requires, "env", []) or [])
            elif isinstance(raw_requires, dict):
                requires["env"] = list(raw_requires.get("env", []) or [])

            if hasattr(raw_requires, "config"):
                requires["config"] = list(getattr(raw_requires, "config", []) or [])
            elif isinstance(raw_requires, dict):
                requires["config"] = list(raw_requires.get("config", []) or [])

    os_list = getattr(metadata, "os", []) or [] if metadata else []
    requires["os"] = os_list if isinstance(os_list, list) else []

    # Resolve env satisfaction (env var or config apiKey)
    primary_env = getattr(metadata, "primary_env", None) or getattr(metadata, "primaryEnv", None) if metadata else None

    def is_env_satisfied(env_name: str) -> bool:
        if os.environ.get(env_name):
            return True
        if config and skill_key:
            entries_cfg = (config.get("skills") or {}).get("entries") or {}
            skill_cfg = entries_cfg.get(skill_key) or {}
            if isinstance(skill_cfg, dict):
                if skill_cfg.get("env", {}).get(env_name):
                    return True
                if skill_cfg.get("apiKey") and env_name == primary_env:
                    return True
        return False

    def is_config_satisfied(path_str: str) -> bool:
        return _is_config_path_truthy(config, path_str)

    # Evaluate requirements
    remote = (eligibility or {}).get("remote") if eligibility else None
    has_remote_bin = (remote.get("hasBin") if isinstance(remote, dict) else None) or None
    has_remote_any_bin = (remote.get("hasAnyBin") if isinstance(remote, dict) else None) or None
    remote_platforms = (remote.get("platforms") if isinstance(remote, dict) else None) or []

    missing_bins = _resolve_missing_bins(
        requires.get("bins", []),
        _has_binary,
        has_remote_bin,
    )
    missing_any_bins = _resolve_missing_any_bins(
        requires.get("anyBins", []),
        _has_binary,
        has_remote_any_bin,
    )
    missing_os = _resolve_missing_os(requires.get("os", []), _get_platform(), remote_platforms)
    missing_env = _resolve_missing_env(requires.get("env", []), is_env_satisfied)
    missing_config = [
        p for p in (requires.get("config", []) or [])
        if not is_config_satisfied(p)
    ]

    if always:
        missing = {"bins": [], "anyBins": [], "env": [], "config": [], "os": []}
    else:
        missing = {
            "bins": missing_bins,
            "anyBins": missing_any_bins,
            "env": missing_env,
            "config": missing_config,
            "os": missing_os,
        }

    requirements_satisfied = (
        len(missing["bins"]) == 0
        and len(missing["anyBins"]) == 0
        and len(missing["env"]) == 0
        and len(missing["config"]) == 0
        and len(missing["os"]) == 0
    )
    eligible = not disabled and not blocked_by_allowlist and (always or requirements_satisfied)

    # Config checks
    config_checks = [
        {"path": p, "satisfied": is_config_satisfied(p)}
        for p in (requires.get("config", []) or [])
    ]

    # Install options
    prefs = (config.get("skills") or {}).get("install") or {} if config else {}
    install = _normalize_install_options(entry, prefs)

    # Bundled check
    source = getattr(skill, "source", "unknown")
    bundled = bool(bundled_names and getattr(skill, "name", "") in bundled_names) or (source == "openclaw-bundled")

    return {
        "name": getattr(skill, "name", "unknown"),
        "description": getattr(skill, "description", "") or "",
        "source": source,
        "bundled": bundled,
        "filePath": getattr(skill, "location", "") or getattr(skill, "file_path", ""),
        "baseDir": str(Path(getattr(skill, "location", "") or "").parent) if getattr(skill, "location", "") else "",
        "skillKey": skill_key,
        "primaryEnv": primary_env,
        "emoji": getattr(metadata, "emoji", None) if metadata else None,
        "homepage": getattr(metadata, "homepage", None) if metadata else None,
        "always": always,
        "disabled": disabled,
        "blockedByAllowlist": blocked_by_allowlist,
        "eligible": eligible,
        "requirements": {
            "bins": requires.get("bins", []),
            "anyBins": requires.get("anyBins", []),
            "env": requires.get("env", []),
            "config": requires.get("config", []),
            "os": requires.get("os", []),
        },
        "missing": missing,
        "configChecks": config_checks,
        "install": install,
    }


def build_workspace_skill_status(
    workspace_dir: Path | str,
    config: dict | None = None,
    eligibility: dict | None = None,
    managed_skills_dir: Path | str | None = None,
    entries: list | None = None,
    bundled_skills_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Build skill status report for workspace (matches TS buildWorkspaceSkillStatus).

    Args:
        workspace_dir: Workspace directory path
        config: Optional config dict
        eligibility: Optional eligibility context (remote skills, etc.)
        managed_skills_dir: Optional managed skills directory
        entries: Optional pre-loaded skill entries
        bundled_skills_dir: Optional bundled skills directory

    Returns:
        Skill status report matching TypeScript SkillStatusReport
    """
    workspace_dir = Path(workspace_dir)
    managed_dir = Path(managed_skills_dir) if managed_skills_dir else (Path.home() / ".openclaw" / "skills")

    if entries is None:
        from openclaw.agents.skills.workspace import load_workspace_skill_entries
        bundled_dir = Path(bundled_skills_dir) if bundled_skills_dir else None
        entries = load_workspace_skill_entries(
            workspace_dir,
            config=config,
            managed_skills_dir=managed_dir,
            bundled_skills_dir=bundled_dir,
        )

    bundled_names: set[str] = set()
    if bundled_skills_dir:
        bundled_path = Path(bundled_skills_dir)
        if bundled_path.exists():
            for d in bundled_path.iterdir():
                if d.is_dir() and (d / "SKILL.md").exists():
                    bundled_names.add(d.name)

    skills = [
        _build_skill_status_entry(entry, config, eligibility, bundled_names)
        for entry in entries
    ]

    return {
        "workspaceDir": str(workspace_dir),
        "managedSkillsDir": str(managed_dir),
        "skills": skills,
    }


def list_skill_names(workspace_dir: Path | str) -> list[str]:
    """List all skill names in workspace."""
    report = build_workspace_skill_status(workspace_dir)
    return [s["name"] for s in report["skills"]]


def get_skill_path(workspace_dir: Path | str, skill_name: str) -> Path | None:
    """Get skill path by name."""
    report = build_workspace_skill_status(workspace_dir)
    for s in report["skills"]:
        if s["name"] == skill_name:
            fp = s.get("filePath")
            if fp and Path(fp).exists():
                return Path(fp)
    return None


def collect_skill_bins(entries: list) -> list[str]:
    """
    Collect all binary dependencies from skill entries.
    
    Mirrors TS collectSkillBins() from skills.ts lines 26-55
    
    Args:
        entries: List of skill entries
    
    Returns:
        Sorted list of required binary names
    """
    bins: set[str] = set()
    
    for entry in entries:
        # Get requires.bins
        metadata = getattr(entry.skill, 'metadata', None) if hasattr(entry, 'skill') else entry.get('metadata')
        if metadata:
            requires = getattr(metadata, 'requires', None) if hasattr(metadata, 'requires') else metadata.get('requires', {})
            if requires:
                # Add bins
                required_bins = getattr(requires, 'bins', []) if hasattr(requires, 'bins') else requires.get('bins', [])
                for bin_name in required_bins:
                    trimmed = str(bin_name).strip()
                    if trimmed:
                        bins.add(trimmed)
                
                # Add anyBins
                any_bins = getattr(requires, 'anyBins', []) if hasattr(requires, 'anyBins') else requires.get('anyBins', [])
                for bin_name in any_bins:
                    trimmed = str(bin_name).strip()
                    if trimmed:
                        bins.add(trimmed)
            
            # Add bins from install specs
            install_specs = getattr(metadata, 'install', []) if hasattr(metadata, 'install') else metadata.get('install', [])
            for spec in install_specs:
                spec_bins = getattr(spec, 'bins', []) if hasattr(spec, 'bins') else spec.get('bins', []) if isinstance(spec, dict) else []
                for bin_name in spec_bins:
                    trimmed = str(bin_name).strip()
                    if trimmed:
                        bins.add(trimmed)
    
    return sorted(list(bins))
