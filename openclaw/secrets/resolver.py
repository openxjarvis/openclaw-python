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
) -> SecretResolution:
    """Resolve a single secret ref to its value."""
    effective_env = env or dict(os.environ)

    if ref.env:
        val = effective_env.get(ref.env)
        if val:
            return SecretResolution(ref=ref, value=val)

    if ref.file:
        try:
            p = Path(ref.file).expanduser()
            if p.exists():
                return SecretResolution(ref=ref, value=p.read_text().strip())
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
