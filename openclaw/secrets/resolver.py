"""Secret reference resolution -- mirrors TS src/secrets/resolve.ts.

Resolves secret refs from env vars, files, or exec commands.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from .types import CommandSecretAssignment, SecretRef, SecretResolution

logger = logging.getLogger(__name__)


def read_json_pointer(data: dict, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON pointer against a dict.

    Args:
        data: The JSON-like dict to traverse.
        pointer: RFC 6901 pointer string (e.g. "/foo/bar/0").

    Returns:
        The value at the pointer path.

    Raises:
        KeyError: If any path segment is missing.
        IndexError: If an array index is out of range.
        ValueError: If the pointer format is invalid.
    """
    if not pointer:
        return data
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    parts = pointer[1:].split("/")
    current: Any = data
    for part in parts:
        # Unescape ~1 → / and ~0 → ~
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(f"key not found: {part!r}")
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError:
                raise KeyError(f"invalid list index: {part!r}")
            current = current[idx]
        else:
            raise KeyError(f"cannot traverse into {type(current).__name__} with key {part!r}")
    return current


def trusted_dir_check(file_path: str, trusted_dirs: list[str]) -> bool:
    """Check if a file path is inside one of the trusted directories.

    Args:
        file_path: Absolute or relative file path to check.
        trusted_dirs: List of trusted directory paths.

    Returns:
        True if file_path is inside at least one trusted directory.
    """
    if not trusted_dirs:
        return False
    try:
        resolved = Path(file_path).expanduser().resolve()
    except Exception:
        return False
    for td in trusted_dirs:
        try:
            trusted = Path(td).expanduser().resolve()
            resolved.relative_to(trusted)
            return True
        except (ValueError, Exception):
            continue
    return False


KNOWN_SECRET_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "xai": "XAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}


def resolve_secret_ref_value(
    ref: SecretRef,
    env: dict[str, str] | None = None,
    trusted_dirs: list[str] | None = None,
) -> SecretResolution:
    """Resolve a single secret ref to its value.

    Enhancements:
    - trusted_dirs: when resolving file refs, check if the path is trusted.
    - ref.pointer: if set, applies JSON pointer extraction after reading the file.
    """
    effective_env = env or dict(os.environ)

    if ref.env:
        val = effective_env.get(ref.env)
        if val:
            return SecretResolution(ref=ref, value=val)

    if ref.file:
        try:
            p = Path(ref.file).expanduser()
            # Trusted directory check
            if trusted_dirs and not trusted_dir_check(str(p), trusted_dirs):
                return SecretResolution(ref=ref, error=f"file path not in trusted dirs: {ref.file}")
            if p.exists():
                raw_content = p.read_text().strip()
                # JSON pointer extraction
                pointer = getattr(ref, "pointer", None)
                if pointer:
                    import json as _json
                    try:
                        data = _json.loads(raw_content)
                        extracted = read_json_pointer(data, pointer)
                        raw_content = str(extracted)
                    except Exception as e:
                        return SecretResolution(ref=ref, error=f"json pointer {pointer!r} extraction failed: {e}")
                return SecretResolution(ref=ref, value=raw_content)
        except Exception as e:
            return SecretResolution(ref=ref, error=f"file read failed: {e}")

    if ref.exec:
        try:
            result = subprocess.run(
                ref.exec,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return SecretResolution(ref=ref, value=result.stdout.strip())
            return SecretResolution(
                ref=ref,
                error=f"exec returned {result.returncode}: {result.stderr.strip()[:200]}",
            )
        except Exception as e:
            return SecretResolution(ref=ref, error=f"exec failed: {e}")

    known_env = KNOWN_SECRET_ENV_VARS.get(ref.provider)
    if known_env:
        val = effective_env.get(known_env)
        if val:
            return SecretResolution(ref=ref, value=val)

    return SecretResolution(ref=ref, error="no resolution strategy matched")


def resolve_secrets_resolve(
    command_name: str,
    target_ids: list[str],
    config: Any | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve secrets for gateway ``secrets.resolve`` RPC.

    Mirrors TS ``createSecretsHandlers`` ``secrets.resolve`` return shape.
    """
    from .target_registry import is_known_secret_target_id, target_id_matches_path

    diagnostics: list[str] = []
    inactive_ref_paths: list[str] = []

    if config is None:
        try:
            from openclaw.config import load_config
            config = load_config()
        except Exception as exc:
            diagnostics.append(f"config load failed: {exc}")
            return {
                "assignments": [],
                "diagnostics": diagnostics,
                "inactiveRefPaths": inactive_ref_paths,
            }

    cleaned = [t.strip() for t in target_ids if isinstance(t, str) and t.strip()]
    for tid in cleaned:
        if not is_known_secret_target_id(tid):
            diagnostics.append(f"unknown target id: {tid}")

    id_set = set(cleaned)
    all_assignments = resolve_secrets_for_command(config, target_ids=None, env=env)

    assignments: list[dict[str, Any]] = []
    for a in all_assignments:
        if not id_set:
            assignments.append({
                "path": a.path,
                "pathSegments": a.path_segments,
                "value": a.value,
            })
            continue
        for tid in id_set:
            if target_id_matches_path(tid, a.path):
                assignments.append({
                    "path": a.path,
                    "pathSegments": a.path_segments,
                    "value": a.value,
                })
                break

    if command_name:
        diagnostics.append(f"command: {command_name}")

    return {
        "assignments": assignments,
        "diagnostics": diagnostics,
        "inactiveRefPaths": inactive_ref_paths,
    }


def resolve_secrets_for_command(
    config: Any,
    target_ids: set[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[CommandSecretAssignment]:
    """Resolve secret refs from config for a CLI command.

    Mirrors TS collectCommandSecretAssignmentsFromSnapshot.
    Walks config sections looking for $ref-style secret references and resolves them.
    """
    assignments: list[CommandSecretAssignment] = []
    effective_env = env or dict(os.environ)

    secrets_cfg = getattr(config, "secrets", None)
    if secrets_cfg is None:
        return assignments

    if isinstance(secrets_cfg, dict):
        defaults = secrets_cfg.get("defaults", {})
    else:
        defaults = getattr(secrets_cfg, "defaults", {}) or {}

    _walk_config_secrets(config, defaults, effective_env, assignments, target_ids)
    return assignments


def _walk_config_secrets(
    config: Any,
    defaults: dict,
    env: dict[str, str],
    assignments: list[CommandSecretAssignment],
    target_ids: set[str] | None,
) -> None:
    """Walk config tree looking for secret ref patterns and resolve them."""
    channels = getattr(config, "channels", None)
    if channels and hasattr(channels, "__dict__"):
        for ch_name, ch_cfg in vars(channels).items():
            if ch_name.startswith("_"):
                continue
            _resolve_channel_secrets(ch_name, ch_cfg, defaults, env, assignments)


def _resolve_channel_secrets(
    channel_name: str,
    channel_cfg: Any,
    defaults: dict,
    env: dict[str, str],
    assignments: list[CommandSecretAssignment],
) -> None:
    """Check a channel config for token/apiKey/secret fields."""
    if channel_cfg is None:
        return

    secret_fields = ["token", "apiKey", "api_key", "botToken", "bot_token", "secret", "webhookSecret"]
    cfg_dict = channel_cfg if isinstance(channel_cfg, dict) else (
        vars(channel_cfg) if hasattr(channel_cfg, "__dict__") else {}
    )

    for field_name in secret_fields:
        val = cfg_dict.get(field_name)
        if not val:
            continue
        if isinstance(val, dict) and ("$ref" in val or "env" in val or "file" in val):
            ref = SecretRef(
                source="config",
                provider=channel_name,
                id=field_name,
                env=val.get("env"),
                file=val.get("file"),
                exec=val.get("exec"),
            )
            resolution = resolve_secret_ref_value(ref, env)
            if resolution.resolved:
                assignments.append(CommandSecretAssignment(
                    path=f"channels.{channel_name}.{field_name}",
                    path_segments=["channels", channel_name, field_name],
                    value=resolution.value,
                ))
