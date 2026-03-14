"""Configuration loader for OpenClaw.

Loads configuration from files and environment variables.

Matches TypeScript openclaw/src/config/io.ts:
- JSON5 parsing (comments, trailing commas, unquoted keys)
- $include directives: {"$include": "./extra.json"}
- ${ENV_VAR} environment variable substitution
- Config audit log (config-audit.jsonl)
- Backup rotation on write
- Preserve ${VAR} tokens in written config
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, Union

from openclaw.config.paths import STATE_DIR as _STATE_DIR

logger = logging.getLogger(__name__)

_cached_config: Optional["OpenClawConfig"] = None
_cached_config_path: Optional[Path] = None
_cached_config_mtime_ns: Optional[int] = None

# ---------------------------------------------------------------------------
# JSON5 parsing
# ---------------------------------------------------------------------------

# Matches string literals (to skip) or comments (to strip).
# String-aware so that // inside "http://..." is never treated as a comment.
_JSON5_TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'   # double-quoted string literal
    r"|'(?:[^'\\]|\\.)*'"  # single-quoted string literal
    r"|//[^\n]*"            # // line comment
    r"|/\*.*?\*/",          # /* block comment */
    flags=re.DOTALL,
)


def _strip_json5_comments(text: str) -> str:
    """Remove JSON5 // and /* */ comments, leaving string literals intact."""
    def _replacer(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith('"') or s.startswith("'"):
            return s  # preserve string content unchanged
        return ""  # erase comment

    return _JSON5_TOKEN_RE.sub(_replacer, text)


def _parse_json5(text: str) -> Any:
    """
    Parse JSON5 text (comments + trailing commas).

    Falls back to strict json if json5 library is unavailable.
    Uses a string-aware comment stripper so that URLs like
    "http://127.0.0.1:11434" are never mangled.
    """
    try:
        import json5  # type: ignore[import]
        return json5.loads(text)
    except ImportError:
        pass

    # String-aware comment stripping + trailing-comma removal.
    text = _strip_json5_comments(text)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return json.loads(text)


# ---------------------------------------------------------------------------
# $include + env-var substitution
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _substitute_env_vars(obj: Any, preserve: bool = False) -> Any:
    """
    Recursively replace ${VAR} with os.environ values.

    If *preserve* is True the token is left untouched (used when writing back
    to disk to preserve variable references in the config file).
    """
    if isinstance(obj, str):
        if preserve:
            return obj

        def _replace(m: re.Match) -> str:
            var = m.group(1)
            return os.environ.get(var, m.group(0))  # leave unresolved as-is

        return _ENV_VAR_RE.sub(_replace, obj)
    if isinstance(obj, dict):
        return {k: _substitute_env_vars(v, preserve) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_env_vars(v, preserve) for v in obj]
    return obj


def _resolve_includes(
    obj: Any,
    base_dir: Path,
    depth: int = 0,
    seen: set[Path] | None = None,
) -> Any:
    """
    Resolve {"$include": "./path.json"} directives recursively.

    Matches TypeScript $include directive in config/io.ts.
    """
    if depth > 10:
        raise ValueError("$include depth limit exceeded (circular?)")
    if seen is None:
        seen = set()

    if isinstance(obj, dict):
        if "$include" in obj:
            include_value = obj["$include"]
            include_list = include_value if isinstance(include_value, list) else [include_value]
            merged: dict[str, Any] = {}
            for item in include_list:
                include_path = (base_dir / str(item)).resolve()
                if include_path in seen:
                    raise ValueError(f"Circular $include detected: {include_path}")
                if not include_path.exists():
                    logger.warning(f"$include target not found: {include_path}")
                    continue
                raw = include_path.read_text(encoding="utf-8")
                included = _parse_json5(raw)
                resolved = _resolve_includes(included, include_path.parent, depth + 1, seen | {include_path})
                if isinstance(resolved, dict):
                    merged = _deep_merge(merged, resolved)
            local = {k: v for k, v in obj.items() if k != "$include"}
            resolved_local = {k: _resolve_includes(v, base_dir, depth, seen) for k, v in local.items()}
            return _deep_merge(merged, resolved_local)
        return {k: _resolve_includes(v, base_dir, depth, seen) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_includes(v, base_dir, depth, seen) for v in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts (override wins on scalar conflicts)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _append_config_audit(config_path: Path, event: str, details: str = "") -> None:
    """Append an entry to config-audit.jsonl (matches TS config/io.ts audit log)."""
    # TS writes to stateDir/logs/config-audit.jsonl, not next to the config file.
    # Resolve dynamically so that Path.home() patches in tests take effect.
    from .paths import resolve_state_dir
    audit_file = resolve_state_dir() / "logs" / "config-audit.jsonl"
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "path": str(config_path),
            "details": details,
        })
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _restore_env_refs_preserving(raw_obj: Any, new_obj: Any) -> Any:
    """
    Preserve ${VAR} references from raw config when the resolved value did not change.
    """
    if isinstance(raw_obj, str) and isinstance(new_obj, str) and "${" in raw_obj:
        try:
            expanded = _substitute_env_vars(raw_obj, preserve=False)
            if expanded == new_obj:
                return raw_obj
        except Exception:
            return new_obj
        return new_obj
    if isinstance(raw_obj, dict) and isinstance(new_obj, dict):
        out: dict[str, Any] = {}
        for key, value in new_obj.items():
            out[key] = _restore_env_refs_preserving(raw_obj.get(key), value)
        return out
    if isinstance(raw_obj, list) and isinstance(new_obj, list):
        return [
            _restore_env_refs_preserving(raw_obj[idx] if idx < len(raw_obj) else None, value)
            for idx, value in enumerate(new_obj)
        ]
    return new_obj


# ---------------------------------------------------------------------------
# Core load / save
# ---------------------------------------------------------------------------

def _resolve_config_path(config_path: Optional[str | Path]) -> Optional[Path]:
    if config_path:
        return Path(config_path)

    candidates = [
        Path.cwd() / "openclaw.json",
        Path.cwd() / "openclaw.json5",
        Path.cwd() / "config" / "openclaw.json",
        Path.home() / ".openclaw" / "openclaw.json",   # Python-native config (preferred)
        Path.home() / ".openclaw" / "openclaw.json5",
        Path.home() / ".openclaw" / "config.json",     # TS-format config (fallback)
        Path.home() / ".openclaw" / "config.json5",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_config_raw(path: Path) -> dict[str, Any]:
    """
    Load a config file with JSON5 parsing, $include resolution, and env-var substitution.

    Returns the resolved config dict (ready for schema validation).
    """
    raw = path.read_text(encoding="utf-8")
    obj = _parse_json5(raw)
    obj = _resolve_includes(obj, path.parent)
    obj = _substitute_env_vars(obj, preserve=False)
    return obj if isinstance(obj, dict) else {}


def _migrate_legacy_agent_field(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy top-level 'agent' field to agents.defaults.
    
    Provides backward compatibility for old config files that used the top-level
    'agent' field. This field has been deprecated in favor of 'agents.defaults'
    to align with TypeScript OpenClawConfig structure.
    
    Args:
        config_dict: Configuration dictionary
        
    Returns:
        Modified configuration dictionary with agent field migrated
    """
    if "agent" in config_dict and config_dict["agent"]:
        agent_config = config_dict.pop("agent")
        
        # Ensure agents structure exists
        if "agents" not in config_dict:
            config_dict["agents"] = {}
        if "defaults" not in config_dict["agents"]:
            config_dict["agents"]["defaults"] = {}
        
        # Migrate fields (don't override existing values in agents.defaults)
        defaults = config_dict["agents"]["defaults"]
        for key, value in agent_config.items():
            if key not in defaults or defaults[key] is None:
                defaults[key] = value
        
        logger.warning(
            "Deprecated: top-level 'agent' field is deprecated, "
            "use 'agents.defaults' instead. Config has been auto-migrated."
        )
    
    return config_dict


def load_config(
    config_path: Optional[str | Path] = None,
    as_dict: bool = False,
) -> Union["OpenClawConfig", dict[str, Any]]:
    """Load OpenClaw configuration.

    Args:
        config_path: Optional path to config file.  Supports JSON5.
        as_dict: If True, return dict instead of OpenClawConfig object.

    Returns:
        Configuration object (OpenClawConfig) or dictionary if as_dict=True.
    """
    from .schema import OpenClawConfig

    global _cached_config, _cached_config_path, _cached_config_mtime_ns

    config_dict: dict[str, Any] = {}
    path = _resolve_config_path(config_path)
    path_mtime = None
    if path and path.exists():
        path_mtime = path.stat().st_mtime_ns

    if (
        _cached_config is not None
        and _cached_config_path == path
        and _cached_config_mtime_ns == path_mtime
    ):
        return _cached_config.model_dump() if as_dict else _cached_config

    if path and path.exists():
        try:
            config_dict = load_config_raw(path)
            # Migrate legacy agent field to agents.defaults
            config_dict = _migrate_legacy_agent_field(config_dict)
            _append_config_audit(path, "load", f"success, keys={list(config_dict.keys())[:5]}")
        except Exception as exc:
            logger.warning(f"Failed to load config from {path}: {exc}")
            _append_config_audit(path, "load_error", str(exc))

    try:
        config_obj = OpenClawConfig(**config_dict) if config_dict else OpenClawConfig()
    except Exception as exc:
        logger.warning(f"Failed to parse config: {exc}")
        config_obj = OpenClawConfig()

    # Apply runtime in-memory overrides (matches TS applyConfigOverrides at end of loadConfig)
    try:
        from .runtime_overrides import apply_config_overrides
        config_obj = apply_config_overrides(config_obj)
    except Exception:
        pass

    _cached_config = config_obj
    _cached_config_path = path
    _cached_config_mtime_ns = path_mtime
    return config_obj.model_dump() if as_dict else config_obj


def invalidate_config_cache() -> None:
    """Invalidate the in-process config cache so the next load_config() re-reads disk."""
    global _cached_config, _cached_config_path, _cached_config_mtime_ns
    _cached_config = None
    _cached_config_path = None
    _cached_config_mtime_ns = None


# Alias used by existing code
clear_config_cache = invalidate_config_cache


def _resolve_backup_paths(path: Path) -> list[tuple[Path, Path]]:
    """
    Return (src, dst) rename pairs for 5-backup rotation.

    Convention: .bak, .bak.1, .bak.2, .bak.3, .bak.4 (matches TS backup-rotation.ts)
    """
    pairs = []
    stem = path.stem
    suffix = path.suffix
    base = path.parent
    # Rotate .bak.3 → .bak.4, .bak.2 → .bak.3, .bak.1 → .bak.2, .bak → .bak.1
    for i in range(3, 0, -1):
        src = base / f"{stem}{suffix}.bak.{i}"
        dst = base / f"{stem}{suffix}.bak.{i + 1}"
        pairs.append((src, dst))
    pairs.append((base / f"{stem}{suffix}.bak", base / f"{stem}{suffix}.bak.1"))
    return pairs


def _rotate_config_backups(path: Path) -> None:
    """Rotate config backups (5 levels, matches TS backup-rotation.ts)."""
    try:
        # Delete oldest backup first
        oldest = path.parent / f"{path.stem}{path.suffix}.bak.4"
        if oldest.exists():
            oldest.unlink()
        for src, dst in _resolve_backup_paths(path):
            if src.exists():
                shutil.move(str(src), str(dst))
        # Create new .bak from current file
        if path.exists():
            shutil.copy2(str(path), str(path.parent / f"{path.stem}{path.suffix}.bak"))
    except Exception as exc:
        logger.debug(f"Backup rotation skipped: {exc}")


def resolve_config_snapshot_hash(raw: Optional[str]) -> Optional[str]:
    """
    Compute SHA-256 hash of a config file snapshot.

    Matches TS resolveConfigSnapshotHash().
    """
    if not raw:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_app_version() -> Optional[str]:
    """Attempt to resolve the running openclaw-python version string."""
    try:
        import importlib.metadata
        return importlib.metadata.version("openclaw-python")
    except Exception:
        pass
    try:
        from openclaw import __version__  # type: ignore[import]
        return __version__
    except Exception:
        pass
    return None


def write_config_file(
    config: Any,
    config_path: Optional[str | Path] = None,
    stamp_version: bool = True,
) -> None:
    """
    Write configuration to file.

    - Preserves ${VAR} references where resolved values are unchanged.
    - Rotates up to 5 backup files (.bak, .bak.1 … .bak.4).
    - Stamps meta.lastTouchedVersion / meta.lastTouchedAt.
    - Clears config cache.
    - Appends audit log entry.

    Matches TS writeConfigFile().
    """
    global _cached_config, _cached_config_path, _cached_config_mtime_ns

    path = Path(config_path) if config_path else Path.home() / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Invalidate cache before writing
    invalidate_config_cache()

    # Convert to dict
    if hasattr(config, "model_dump"):
        config_dict: dict[str, Any] = config.model_dump(exclude_none=True)
    elif hasattr(config, "dict"):
        config_dict = config.dict(exclude_none=True)  # type: ignore[union-attr]
    elif hasattr(config, "__dict__"):
        config_dict = dict(config.__dict__)
    elif isinstance(config, dict):
        config_dict = dict(config)
    else:
        config_dict = {}

    # Preserve ${VAR} references from existing file
    if path.exists():
        try:
            existing_raw = _parse_json5(path.read_text(encoding="utf-8"))
            if isinstance(existing_raw, dict):
                config_dict = _restore_env_refs_preserving(existing_raw, config_dict)
        except Exception:
            pass

    # Stamp version metadata (matches TS meta.lastTouchedVersion / lastTouchedAt)
    if stamp_version:
        meta = config_dict.setdefault("meta", {})
        if isinstance(meta, dict):
            version = _get_app_version()
            if version:
                meta["lastTouchedVersion"] = version
            meta["lastTouchedAt"] = datetime.now(UTC).isoformat()

    # Rotate backups (5 levels)
    _rotate_config_backups(path)

    # Atomic write: temp file → rename
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(config_dict, indent=2)
    try:
        tmp_path.write_text(serialized, encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    _append_config_audit(
        path,
        "write",
        f"keys={list(config_dict.keys())[:5]},bytes={len(serialized.encode('utf-8'))},sha256={payload_hash}",
    )

    _cached_config_path = path
    _cached_config_mtime_ns = path.stat().st_mtime_ns if path.exists() else None


def save_config(config: Any, config_path: Optional[str | Path] = None) -> None:
    """Save OpenClaw configuration to file (alias for write_config_file).

    - Rotates up to 5 backup files (.bak … .bak.4)
    - Preserves ${VAR} tokens in the output (not expanded)
    - Stamps meta.lastTouchedVersion / meta.lastTouchedAt
    - Appends an audit log entry

    Args:
        config: Configuration object or dictionary to save.
        config_path: Optional path to config file (defaults to ~/.openclaw/openclaw.json).
    """
    write_config_file(config, config_path)


def get_config_path() -> Path:
    """Get the path to the active configuration file.

    Searches well-known locations.  If no file is found, returns the default
    user-level config path (``~/.openclaw/openclaw.json``) even if it does not
    yet exist.

    Returns:
        Path to config file (may not exist)
    """
    candidates = [
        Path.cwd() / "openclaw.json",
        Path.cwd() / "config" / "openclaw.json",
        Path.home() / ".openclaw" / "openclaw.json",
        Path.home() / ".openclaw" / "config.json",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    # Default: user-level config (may not exist)
    return Path.home() / ".openclaw" / "openclaw.json"


def get_config_value(key_path: str, default: Any = None) -> Any:
    """Get a configuration value by dot-separated key path.

    Args:
        key_path: Dot-separated key path (e.g., "channels.telegram.botToken")
        default: Default value if not found

    Returns:
        Configuration value or default
    """
    config = load_config()
    keys = key_path.split(".")
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value
