"""CLI backend configuration resolver for OpenClaw Python.

Mirrors TypeScript src/agents/cli-backends.ts implementation.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from openclaw.config.schema import CliBackendConfig, CliReliabilityConfig, CliWatchdogConfig

logger = logging.getLogger("openclaw.agents.cli_backends")

# ============================================================================
# CLI Watchdog Defaults (mirroring cli-watchdog-defaults.ts)
# ============================================================================

CLI_FRESH_WATCHDOG_DEFAULTS = CliWatchdogConfig(
    noOutputTimeoutMs=None,
    noOutputTimeoutRatio=0.8,
    minMs=180_000,
    maxMs=600_000,
)

CLI_RESUME_WATCHDOG_DEFAULTS = CliWatchdogConfig(
    noOutputTimeoutMs=None,
    noOutputTimeoutRatio=0.3,
    minMs=60_000,
    maxMs=180_000,
)

# ============================================================================
# Model Aliases (mirroring cli-backends.ts)
# ============================================================================

CLAUDE_MODEL_ALIASES: Dict[str, str] = {
    "opus": "opus",
    "opus-4.6": "opus",
    "opus-4.5": "opus",
    "opus-4": "opus",
    "claude-opus-4-6": "opus",
    "claude-opus-4-5": "opus",
    "claude-opus-4": "opus",
    "sonnet": "sonnet",
    "sonnet-4.6": "sonnet",
    "sonnet-4.5": "sonnet",
    "sonnet-4.1": "sonnet",
    "sonnet-4.0": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5": "sonnet",
    "claude-sonnet-4-1": "sonnet",
    "claude-sonnet-4-0": "sonnet",
    "haiku": "haiku",
    "haiku-3.5": "haiku",
    "claude-haiku-3-5": "haiku",
}

# ============================================================================
# Default Backend Configurations (mirroring cli-backends.ts)
# ============================================================================

DEFAULT_CLAUDE_BACKEND = CliBackendConfig(
    command="claude",
    args=["-p", "--output-format", "json", "--dangerously-skip-permissions"],
    resumeArgs=[
        "-p",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--resume",
        "{sessionId}",
    ],
    output="json",
    input="arg",
    modelArg="--model",
    modelAliases=CLAUDE_MODEL_ALIASES,
    sessionArg="--session-id",
    sessionMode="always",
    sessionIdFields=["session_id", "sessionId", "conversation_id", "conversationId"],
    systemPromptArg="--append-system-prompt",
    systemPromptMode="append",
    systemPromptWhen="first",
    clearEnv=["ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_OLD"],
    reliability=CliReliabilityConfig(
        watchdog={
            "fresh": CLI_FRESH_WATCHDOG_DEFAULTS,
            "resume": CLI_RESUME_WATCHDOG_DEFAULTS,
        }
    ),
    serialize=True,
)

DEFAULT_CODEX_BACKEND = CliBackendConfig(
    command="codex",
    args=["exec", "--json", "--color", "never", "--sandbox", "read-only", "--skip-git-repo-check"],
    resumeArgs=[
        "exec",
        "resume",
        "{sessionId}",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    ],
    output="jsonl",
    resumeOutput="text",
    input="arg",
    modelArg="--model",
    sessionIdFields=["thread_id"],
    sessionMode="existing",
    imageArg="--image",
    imageMode="repeat",
    reliability=CliReliabilityConfig(
        watchdog={
            "fresh": CLI_FRESH_WATCHDOG_DEFAULTS,
            "resume": CLI_RESUME_WATCHDOG_DEFAULTS,
        }
    ),
    serialize=True,
)

# ============================================================================
# Helper Functions
# ============================================================================


def normalize_backend_key(key: str) -> str:
    """Normalize backend key (mirrors TS normalizeBackendKey)."""
    # Use the same normalization as provider IDs
    from openclaw.agents.model_selection import normalize_provider_id

    return normalize_provider_id(key)


def _pick_backend_config(
    config: Dict[str, CliBackendConfig],
    normalized_id: str,
) -> Optional[CliBackendConfig]:
    """Pick a backend config by normalized ID."""
    for key, entry in config.items():
        if normalize_backend_key(key) == normalized_id:
            return entry
    return None


def _merge_backend_config(
    base: CliBackendConfig,
    override: Optional[CliBackendConfig] = None,
) -> CliBackendConfig:
    """Merge backend configurations (mirrors TS mergeBackendConfig)."""
    if not override:
        return base.model_copy(deep=True)

    # Extract watchdog configs
    base_fresh = {}
    base_resume = {}
    if base.reliability and base.reliability.watchdog:
        base_fresh = (base.reliability.watchdog.get("fresh") or CliWatchdogConfig()).model_dump(by_alias=True, exclude_none=True)
        base_resume = (base.reliability.watchdog.get("resume") or CliWatchdogConfig()).model_dump(by_alias=True, exclude_none=True)

    override_fresh = {}
    override_resume = {}
    if override.reliability and override.reliability.watchdog:
        override_fresh = (override.reliability.watchdog.get("fresh") or CliWatchdogConfig()).model_dump(by_alias=True, exclude_none=True)
        override_resume = (override.reliability.watchdog.get("resume") or CliWatchdogConfig()).model_dump(by_alias=True, exclude_none=True)

    # Merge base and override
    merged_dict = base.model_dump(by_alias=True, exclude_none=True)
    override_dict = override.model_dump(by_alias=True, exclude_none=True)

    # Merge top-level fields
    for key, value in override_dict.items():
        if key in ("env", "modelAliases"):
            # Merge dicts
            base_val = merged_dict.get(key) or {}
            override_val = value or {}
            merged_dict[key] = {**base_val, **override_val}
        elif key == "clearEnv":
            # Merge lists (unique)
            base_val = merged_dict.get(key) or []
            override_val = value or []
            merged_dict[key] = list(set(base_val + override_val))
        elif key in ("args", "sessionIdFields", "sessionArgs", "resumeArgs"):
            # Override completely if specified
            if value is not None:
                merged_dict[key] = value
        elif key == "reliability":
            # Skip, handle separately
            pass
        else:
            # Simple override
            if value is not None:
                merged_dict[key] = value

    # Merge reliability
    merged_reliability = {}
    if base.reliability:
        merged_reliability = base.reliability.model_dump(by_alias=True, exclude_none=True)
    if override.reliability:
        override_rel_dict = override.reliability.model_dump(by_alias=True, exclude_none=True)
        for key, value in override_rel_dict.items():
            if key != "watchdog":
                merged_reliability[key] = value

    # Merge watchdog
    merged_watchdog = {
        "fresh": {**base_fresh, **override_fresh},
        "resume": {**base_resume, **override_resume},
    }
    merged_reliability["watchdog"] = merged_watchdog
    merged_dict["reliability"] = merged_reliability

    # Reconstruct CliBackendConfig
    return CliBackendConfig(**merged_dict)


# ============================================================================
# Public API (mirroring cli-backends.ts exports)
# ============================================================================


@dataclass
class ResolvedCliBackend:
    """Resolved CLI backend (mirrors TS ResolvedCliBackend)."""

    id: str
    config: CliBackendConfig


def resolve_cli_backend_ids(cfg: Optional[Any] = None) -> set:
    """Resolve all CLI backend IDs (mirrors TS resolveCliBackendIds)."""
    ids = {
        normalize_backend_key("claude-cli"),
        normalize_backend_key("codex-cli"),
    }

    if cfg and hasattr(cfg, "agents") and cfg.agents and hasattr(cfg.agents, "defaults"):
        defaults = cfg.agents.defaults
        if defaults and hasattr(defaults, "cliBackends") and defaults.cliBackends:
            configured = defaults.cliBackends
            for key in configured.keys():
                ids.add(normalize_backend_key(key))

    return ids


def resolve_cli_backend_config(
    provider: str,
    cfg: Optional[Any] = None,
) -> Optional[ResolvedCliBackend]:
    """Resolve CLI backend configuration (mirrors TS resolveCliBackendConfig).

    Args:
        provider: Provider ID (e.g., "claude-cli", "codex-cli", or custom)
        cfg: OpenClaw configuration object

    Returns:
        ResolvedCliBackend if found and valid, None otherwise
    """
    normalized = normalize_backend_key(provider)

    # Get configured backends
    configured: Dict[str, CliBackendConfig] = {}
    if cfg and hasattr(cfg, "agents") and cfg.agents and hasattr(cfg.agents, "defaults"):
        defaults = cfg.agents.defaults
        if defaults and hasattr(defaults, "cliBackends") and defaults.cliBackends:
            configured = defaults.cliBackends

    override = _pick_backend_config(configured, normalized)

    # Handle built-in backends
    if normalized == "claude-cli":
        merged = _merge_backend_config(DEFAULT_CLAUDE_BACKEND, override)
        command = merged.command.strip() if merged.command else ""
        if not command:
            return None
        return ResolvedCliBackend(id=normalized, config=merged)

    if normalized == "codex-cli":
        merged = _merge_backend_config(DEFAULT_CODEX_BACKEND, override)
        command = merged.command.strip() if merged.command else ""
        if not command:
            return None
        return ResolvedCliBackend(id=normalized, config=merged)

    # Handle custom backend
    if not override:
        return None

    command = override.command.strip() if override.command else ""
    if not command:
        return None

    return ResolvedCliBackend(id=normalized, config=override)
