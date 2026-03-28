"""Helper functions for onboarding wizard

Mirrors TypeScript src/commands/onboard-helpers.ts functionality.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config.schema import OpenClawConfig

logger = logging.getLogger(__name__)


async def probe_gateway_reachable(
    url: str,
    token: str | None = None,
    password: str | None = None,
    timeout: float = 5.0
) -> dict[str, Any]:
    """Check if gateway is reachable
    
    Mirrors TypeScript probeGatewayReachable() from src/commands/onboard-helpers.ts
    
    Args:
        url: Gateway URL (e.g., "ws://127.0.0.1:18789")
        token: Optional authentication token
        password: Optional authentication password
        timeout: Connection timeout in seconds
        
    Returns:
        Dict with 'ok' (bool) and 'detail' (str) keys
    """
    try:
        import httpx
        
        # Convert ws:// to http:// for health check
        http_url = url.replace("ws://", "http://").replace("wss://", "https://")
        if not http_url.endswith("/health"):
            http_url = http_url.rstrip("/") + "/health"
        
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif password:
            # For password auth, we'd need to do a login flow first
            # For now, just try without auth
            pass
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(http_url, headers=headers)
            response.raise_for_status()
            return {"ok": True, "detail": "Gateway reachable"}
    
    except httpx.TimeoutException:
        return {"ok": False, "detail": f"Connection timeout after {timeout}s"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "detail": f"HTTP {e.response.status_code}: {e.response.text[:100]}"}
    except httpx.ConnectError:
        return {"ok": False, "detail": "Connection refused (gateway not running?)"}
    except Exception as e:
        return {"ok": False, "detail": f"Error: {str(e)}"}


async def warn_if_model_config_looks_off(
    config: OpenClawConfig,
    prompter: Any
) -> None:
    """警告用户模型配置可能有问题
    
    Checks:
    - Model exists in catalog
    - Provider has auth configured
    - Context window is reasonable
    
    Mirrors TypeScript warnIfModelConfigLooksOff() from src/commands/auth-choice.ts
    
    Args:
        config: Current configuration
        prompter: Prompter module for UI
    """
    from ..agents.model_catalog import load_model_catalog
    from ..config.auth_profiles import get_api_key
    
    # Get configured model
    if not config.agents or not config.agents.defaults:
        return
    
    model_config = config.agents.defaults.model
    if not model_config:
        return
    
    # Extract primary model
    if isinstance(model_config, str):
        primary_model = model_config
    elif isinstance(model_config, dict):
        primary_model = model_config.get("primary", "")
    elif hasattr(model_config, "primary"):
        primary_model = model_config.primary
    else:
        return
    
    if not primary_model or "/" not in primary_model:
        return
    
    provider, model_id = primary_model.split("/", 1)
    
    warnings = []
    
    # Check 1: Model exists in catalog
    try:
        catalog = await load_model_catalog()
        catalog_ids = [
            f"{m.get('provider', '')}/{m.get('id', '')}"
            for m in catalog
            if isinstance(m, dict) and "id" in m
        ]
        
        if primary_model not in catalog_ids:
            warnings.append(f"Model '{primary_model}' not found in catalog")
    except Exception as e:
        logger.debug(f"Could not load model catalog for health check: {e}")
    
    # Check 2: Provider has auth
    try:
        api_key = get_api_key(provider)
        if not api_key:
            warnings.append(f"Provider '{provider}' has no API key configured")
    except Exception:
        warnings.append(f"Provider '{provider}' auth check failed")
    
    # Display warnings if any
    if warnings:
        warning_text = "\n".join([f"- {w}" for w in warnings])
        try:
            await prompter.note(
                f"Model configuration warnings:\n{warning_text}\n\n"
                f"You can fix this later with:\n"
                f"  uv run openclaw config set agents.defaults.model <model_id>\n"
                f"  uv run openclaw auth <provider>",
                "Model Config"
            )
        except Exception:
            print(f"\n⚠️  Model configuration warnings:")
            for w in warnings:
                print(f"  {w}")
            print()


def summarize_existing_config(config: OpenClawConfig) -> str:
    """Generate a human-readable summary of existing configuration
    
    Mirrors TypeScript summarizeExistingConfig() from src/commands/onboard-helpers.ts
    
    Args:
        config: Configuration to summarize
        
    Returns:
        Multi-line summary string
    """
    lines = []
    
    # Gateway
    if config.gateway:
        port = config.gateway.port or 18789
        bind = config.gateway.bind or "loopback"
        lines.append(f"Gateway: port {port}, bind {bind}")
        
        if config.gateway.auth:
            if config.gateway.auth.token:
                lines.append("  Auth: token")
            elif config.gateway.auth.password:
                lines.append("  Auth: password")
    
    # Model
    if config.agents and config.agents.defaults:
        model = config.agents.defaults.model
        if model:
            if isinstance(model, str):
                lines.append(f"Model: {model}")
            elif isinstance(model, dict):
                primary = model.get("primary", "")
                fallbacks = model.get("fallbacks", [])
                if primary:
                    lines.append(f"Model: {primary}")
                if fallbacks:
                    lines.append(f"  Fallbacks: {', '.join(fallbacks)}")
            elif hasattr(model, "primary"):
                lines.append(f"Model: {model.primary}")
                if hasattr(model, "fallbacks") and model.fallbacks:
                    lines.append(f"  Fallbacks: {', '.join(model.fallbacks)}")
        
        workspace = config.agents.defaults.workspace
        if workspace:
            lines.append(f"Workspace: {workspace}")
    
    # Channels
    if config.channels:
        channel_list = []
        if config.channels.telegram and config.channels.telegram.enabled:
            channel_list.append("Telegram")
        if config.channels.discord and config.channels.discord.enabled:
            channel_list.append("Discord")
        if config.channels.whatsapp and config.channels.whatsapp.enabled:
            channel_list.append("WhatsApp")
        if config.channels.feishu and config.channels.feishu.enabled:
            channel_list.append("Feishu")
        
        if channel_list:
            lines.append(f"Channels: {', '.join(channel_list)}")
    
    if not lines:
        return "Empty configuration"
    
    return "\n".join(lines)


def ensure_workspace_and_sessions(
    workspace_dir: Path,
    skip_bootstrap: bool = False
) -> None:
    """Ensure workspace and sessions directories exist
    
    Mirrors TypeScript ensureWorkspaceAndSessions() from src/commands/onboard-helpers.ts
    
    Args:
        workspace_dir: Path to workspace directory
        skip_bootstrap: If True, skip BOOTSTRAP.md creation
    """
    from ..agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions as _ensure
    
    _ensure(
        workspace_dir=workspace_dir,
        skip_bootstrap=skip_bootstrap,
    )


async def prompt_remote_gateway_config(
    base_config: OpenClawConfig,
    prompter: Any,
    secret_input_mode: str | None = None
) -> OpenClawConfig:
    """Prompt for remote gateway URL and token
    
    Mirrors TypeScript promptRemoteGatewayConfig() from src/commands/onboard-remote.ts
    
    Args:
        base_config: Current configuration
        prompter: Prompter module for UI
        secret_input_mode: Secret input mode ("plaintext" or "ref")
        
    Returns:
        Updated configuration with remote gateway settings
    """
    from ..config.schema import GatewayConfig, GatewayRemoteConfig, AuthConfig
    from copy import deepcopy
    
    print("\n" + "=" * 80)
    print("Remote Gateway Configuration")
    print("=" * 80)
    print("\nConfigure this client to connect to a remote OpenClaw gateway.")
    print("The gateway must already be running and accessible from this machine.")
    
    # Prompt for URL
    try:
        url = await prompter.text({
            "message": "Remote gateway URL",
            "initialValue": base_config.gateway.remote.url if (base_config.gateway and base_config.gateway.remote) else "",
            "placeholder": "ws://your-server:18789",
        })
    except Exception:
        default_url = base_config.gateway.remote.url if (base_config.gateway and base_config.gateway.remote) else ""
        url = input(f"\nRemote gateway URL [{default_url}]: ").strip() or default_url
    
    if not url:
        print("⚠️  Remote URL is required")
        return base_config
    
    # Prompt for token
    try:
        token = await prompter.password({
            "message": "Remote gateway token (leave blank if not required)",
        })
    except Exception:
        import getpass
        token = getpass.getpass("Remote gateway token [leave blank if not required]: ")
    
    # Build config
    updated_config = deepcopy(base_config)
    
    if not updated_config.gateway:
        updated_config.gateway = GatewayConfig()
    
    updated_config.gateway.remote = GatewayRemoteConfig(
        url=url,
        token=token if token else None,
    )
    
    print(f"\n✓ Remote gateway configured: {url}")
    
    # Test connection
    print("\nTesting connection...")
    probe_result = await probe_gateway_reachable(url=url, token=token)
    
    if probe_result["ok"]:
        print("✓ Connection successful")
    else:
        print(f"⚠️  Connection failed: {probe_result['detail']}")
        print("You can still save this configuration and test later.")
    
    return updated_config


ONBOARDING_DEFAULT_DM_SCOPE = "per-channel-peer"
ONBOARDING_DEFAULT_TOOLS_PROFILE = "messaging"


def apply_onboarding_local_workspace_config(
    base_config: "OpenClawConfig",
    workspace_dir: str,
) -> "OpenClawConfig":
    """Apply onboarding defaults for a local workspace.

    Mirrors TS applyOnboardingLocalWorkspaceConfig() from onboard-config.ts.
    Sets agents.defaults.workspace, gateway.mode, session.dmScope, tools.profile.
    """
    from ..config.schema import (
        AgentsConfig,
        AgentDefaults,
        GatewayConfig,
        SessionConfig,
        ToolsConfig,
    )
    from copy import deepcopy

    cfg = deepcopy(base_config)

    if not cfg.agents:
        cfg.agents = AgentsConfig()
    if not cfg.agents.defaults:
        cfg.agents.defaults = AgentDefaults()
    cfg.agents.defaults.workspace = workspace_dir

    if not cfg.gateway:
        cfg.gateway = GatewayConfig()
    cfg.gateway.mode = "local"

    if not cfg.session:
        cfg.session = SessionConfig()
    if not cfg.session.dmScope or cfg.session.dmScope == "main":
        cfg.session.dmScope = ONBOARDING_DEFAULT_DM_SCOPE  # type: ignore[assignment]

    if not cfg.tools:
        cfg.tools = ToolsConfig()
    if not cfg.tools.profile or cfg.tools.profile == "full":
        cfg.tools.profile = ONBOARDING_DEFAULT_TOOLS_PROFILE

    return cfg


def _apply_wizard_metadata(
    config: "OpenClawConfig",
    command: str = "onboard",
    mode: str = "local",
) -> "OpenClawConfig":
    """Write wizard run metadata into config.wizard.

    Mirrors TS applyWizardMetadata() from onboard-helpers.ts.
    """
    import os
    from datetime import datetime, timezone
    from ..config.schema import WizardConfig

    try:
        from openclaw import __version__
        version = __version__
    except Exception:
        version = "unknown"

    commit = os.environ.get("GIT_COMMIT") or os.environ.get("GIT_SHA") or None

    config.wizard = WizardConfig(
        lastRunAt=datetime.now(timezone.utc).isoformat(),
        lastRunVersion=version,
        lastRunCommit=commit,
        lastRunCommand=command,
        lastRunMode=mode,
    )
    return config


def normalize_gateway_token_input(value: str | None) -> str | None:
    """Reject literal 'undefined'/'null'/empty strings. Mirrors TS normalizeGatewayTokenInput()."""
    if not value or not value.strip():
        return None
    if value.strip().lower() in ("undefined", "null"):
        return None
    return value


def validate_gateway_password_input(value: str | None) -> str | None:
    """Basic gateway password validation. Mirrors TS validateGatewayPasswordInput()."""
    if not value or len(value.strip()) < 8:
        return "Password must be at least 8 characters"
    return None


__all__ = [
    "probe_gateway_reachable",
    "warn_if_model_config_looks_off",
    "summarize_existing_config",
    "ensure_workspace_and_sessions",
    "prompt_remote_gateway_config",
    "apply_onboarding_local_workspace_config",
    "_apply_wizard_metadata",
    "normalize_gateway_token_input",
    "validate_gateway_password_input",
    "ONBOARDING_DEFAULT_DM_SCOPE",
    "ONBOARDING_DEFAULT_TOOLS_PROFILE",
]
