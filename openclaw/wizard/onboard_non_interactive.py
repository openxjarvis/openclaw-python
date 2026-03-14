"""Non-interactive onboarding mode"""
from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path
from typing import Optional

from openclaw.config.schema import OpenClawConfig, GatewayConfig, AgentConfig, ModelsConfig
from openclaw.config.loader import save_config

logger = logging.getLogger(__name__)


def _derive_provider_id(base_url: str) -> str:
    """Derive a provider ID from a base URL, e.g. http://127.0.0.1:11434/v1 → custom-127-0-0-1-11434.
    Mirrors resolveCustomProviderId in TS onboard-custom.ts.
    """
    try:
        parsed = urllib.parse.urlparse(base_url)
        netloc = parsed.netloc
        safe = netloc.replace(".", "-").replace(":", "-")
        return f"custom-{safe}"
    except Exception:
        return "custom-provider"


async def run_non_interactive_onboarding(
    provider: str = "gemini",
    api_key: Optional[str] = None,
    gateway_port: int = 18789,
    gateway_bind: str = "loopback",
    gateway_auth: str = "token",
    gateway_token: Optional[str] = None,
    telegram_token: Optional[str] = None,
    workspace: Optional[Path] = None,
    accept_risk: bool = False,
    # Ollama-specific flags
    ollama_base_url: Optional[str] = None,      # --ollama-base-url
    # Custom provider flags (mirrors TS parseNonInteractiveCustomApiFlags)
    custom_base_url: Optional[str] = None,      # --custom-base-url
    custom_model_id: Optional[str] = None,      # --custom-model-id
    custom_compatibility: Optional[str] = None, # --custom-compatibility (openai|anthropic)
    custom_api_key: Optional[str] = None,       # --custom-api-key
    custom_provider_id: Optional[str] = None,   # --custom-provider-id
) -> dict:
    """Run non-interactive onboarding with CLI flags.

    Args:
        provider: LLM provider (anthropic, openai, gemini, ollama, custom)
        api_key: API key for cloud providers (anthropic, openai, gemini)
        gateway_port: Gateway port (default: 18789)
        gateway_bind: Gateway bind mode
        gateway_auth: Auth mode (token, password, none)
        gateway_token: Gateway token
        telegram_token: Telegram bot token
        workspace: Workspace directory
        accept_risk: Must be True for non-interactive mode
        ollama_base_url: Ollama server base URL (default: http://127.0.0.1:11434)
        custom_base_url: Custom provider base URL (required when provider=custom)
        custom_model_id: Custom provider model ID (required when provider=custom)
        custom_compatibility: Endpoint compatibility — "openai" | "anthropic" (default: openai)
        custom_api_key: Custom provider API key
        custom_provider_id: Custom provider ID (auto-derived from URL if omitted)

    Returns:
        Dict with setup result
    """
    if not accept_risk:
        logger.error("Non-interactive mode requires --accept-risk flag")
        return {
            "success": False,
            "error": "Risk not accepted. Use --accept-risk flag."
        }

    # Validate custom provider flags early (mirrors TS parseNonInteractiveCustomApiFlags)
    if provider == "custom":
        if not custom_base_url or not custom_model_id:
            return {
                "success": False,
                "error": "Custom provider requires --custom-base-url and --custom-model-id"
            }

    print("OpenClaw Non-Interactive Onboarding")
    print("=" * 60)

    # Create config
    config = OpenClawConfig()

    # Configure agent model
    if provider == "anthropic":
        model = "anthropic/claude-sonnet-4"
    elif provider == "openai":
        model = "openai/gpt-4o"
    elif provider == "gemini":
        model = "google/gemini-2.0-flash"
    elif provider == "ollama":
        _ollama_model_id = "llama3"
        model = f"ollama/{_ollama_model_id}"
    elif provider == "custom":
        _pid = custom_provider_id or _derive_provider_id(custom_base_url)  # type: ignore[arg-type]
        model = f"{_pid}/{custom_model_id}"
    else:
        model = f"{provider}/default"

    config.agent = AgentConfig(model=model)

    # Write models.providers block for Ollama
    if provider == "ollama":
        base_url = (ollama_base_url or "http://127.0.0.1:11434").rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        config.models = ModelsConfig(providers={
            "ollama": {
                "baseUrl": base_url,
                "api": "ollama",
                "apiKey": "ollama-local",
                "models": [],  # runtime auto-discovery via models_config.py
            }
        })

    # Write models.providers block for Custom provider
    elif provider == "custom":
        _pid = custom_provider_id or _derive_provider_id(custom_base_url)  # type: ignore[arg-type]
        _api_compat = custom_compatibility or "openai"
        _api_field = "openai-completions" if _api_compat == "openai" else "anthropic"
        _provider_entry: dict = {
            "baseUrl": custom_base_url,
            "api": _api_field,
            "models": [{
                "id": custom_model_id,
                "name": custom_model_id,
                "contextWindow": 8192,
                "maxTokens": 4096,
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "reasoning": False,
            }],
        }
        if custom_api_key:
            _provider_entry["apiKey"] = custom_api_key
        config.models = ModelsConfig(providers={_pid: _provider_entry})

    # Save API key for cloud providers (fixes silent-ignore bug)
    elif api_key and provider in ("anthropic", "openai", "gemini"):
        try:
            from openclaw.wizard.auth import save_api_key_to_credentials
            save_api_key_to_credentials(provider, api_key)
        except Exception as e:
            logger.warning(f"Could not save API key for {provider}: {e}")

    # Configure gateway
    config.gateway = GatewayConfig(
        port=gateway_port,
        bind=gateway_bind,
        mode="local"
    )

    # Configure gateway auth
    if gateway_auth == "token":
        if not gateway_token:
            import secrets
            gateway_token = secrets.token_hex(24)
        config.gateway.auth = {"mode": "token", "token": gateway_token}
    elif gateway_auth == "password":
        config.gateway.auth = {"mode": "password"}

    # Configure channels — align with TS Zod defaults: dmPolicy="pairing", groupPolicy="allowlist"
    if telegram_token:
        config.channels = {
            "telegram": {
                "enabled": True,
                "botToken": telegram_token,
                "dmPolicy": "pairing",
                "groupPolicy": "allowlist",
            }
        }

    # Save config
    try:
        save_config(config)
        print("Configuration saved")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return {"success": False, "error": str(e)}

    # TS alignment: onboarding completion is tracked via workspace-state.json
    # (see openclaw/agents/ensure_workspace.py for workspace-state.json management)

    print("\nNon-interactive onboarding complete!")
    print("\nNext steps:")
    print("   openclaw start              # Start gateway and channels")
    print("   openclaw tui                # Launch Terminal UI")
    print(f"   http://localhost:{gateway_port}/  # Open Web UI")

    return {
        "success": True,
        "config": {
            "provider": provider,
            "gateway_port": gateway_port,
            "gateway_bind": gateway_bind,
            "channels_configured": bool(telegram_token)
        }
    }


__all__ = ["run_non_interactive_onboarding"]
