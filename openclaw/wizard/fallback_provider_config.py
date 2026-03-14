"""Helper functions for configuring fallback model providers

Ensures that when users select fallback models from different providers,
those providers have API keys configured.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.schema import OpenClawConfig

logger = logging.getLogger(__name__)

# Provider name mapping from model ID prefix to auth handler
PROVIDER_MAP = {
    "anthropic": {
        "names": ["anthropic", "claude"],
        "auth_handler": "anthropic",
        "display_name": "Anthropic",
    },
    "openai": {
        "names": ["openai", "gpt"],
        "auth_handler": "openai",
        "display_name": "OpenAI",
    },
    "google": {
        "names": ["google", "gemini"],
        "auth_handler": "google",
        "display_name": "Google Gemini",
    },
    "moonshot": {
        "names": ["moonshot", "kimi"],
        "auth_handler": "moonshot",
        "display_name": "Moonshot (Kimi)",
    },
    "deepseek": {
        "names": ["deepseek"],
        "auth_handler": "api_providers",
        "display_name": "DeepSeek",
    },
    "minimax": {
        "names": ["minimax"],
        "auth_handler": "minimax",
        "display_name": "MiniMax",
    },
    "zhipu": {
        "names": ["zhipu", "glm"],
        "auth_handler": "api_providers",
        "display_name": "Zhipu AI",
    },
    "cohere": {
        "names": ["cohere"],
        "auth_handler": "api_providers",
        "display_name": "Cohere",
    },
}


def extract_provider_from_model_id(model_id: str) -> str | None:
    """Extract provider name from model ID
    
    Args:
        model_id: Model ID like "anthropic/claude-sonnet-4" or "openai/gpt-4o"
        
    Returns:
        Provider name (e.g., "anthropic", "openai") or None if not found
    """
    if "/" in model_id:
        prefix = model_id.split("/")[0].lower()
        
        # Direct match
        for provider, info in PROVIDER_MAP.items():
            if prefix in info["names"]:
                return provider
        
        # Fallback: return the prefix itself
        return prefix
    
    return None


def check_provider_configured(config: OpenClawConfig, provider: str) -> bool:
    """Check if a provider has API key configured
    
    Args:
        config: Current configuration
        provider: Provider name (e.g., "anthropic", "openai")
        
    Returns:
        True if provider has API key configured, False otherwise
    """
    try:
        from ..config.auth_profiles import get_api_key
        api_key = get_api_key(provider)
        return bool(api_key)
    except Exception:
        return False


async def configure_fallback_provider(
    config: OpenClawConfig,
    provider: str,
    interactive: bool = True
) -> bool:
    """Configure API key for a fallback provider
    
    Args:
        config: Current configuration
        provider: Provider name (e.g., "anthropic", "openai", "google")
        interactive: Whether to prompt user interactively
        
    Returns:
        True if configuration succeeded, False otherwise
    """
    if not interactive:
        return False
    
    provider_info = PROVIDER_MAP.get(provider)
    if not provider_info:
        logger.warning(f"Unknown provider: {provider}")
        return False
    
    display_name = provider_info["display_name"]
    auth_handler_name = provider_info["auth_handler"]
    
    print(f"\n⚠️  {display_name} API key required for fallback model")
    print("-" * 60)
    
    try:
        from . import prompter
        configure = prompter.confirm(
            f"Configure {display_name} API key now?",
            default=True
        )
    except Exception:
        configure_input = input(f"Configure {display_name} API key now? [Y/n]: ").strip().lower()
        configure = (configure_input != "n")
    
    if not configure:
        print(f"⚠️  Skipped {display_name} configuration. Fallback may not work without API key.")
        return False
    
    # Import and call the appropriate auth handler
    try:
        if auth_handler_name == "anthropic":
            from .auth_handlers.anthropic import apply_auth_choice_anthropic
            result = await apply_auth_choice_anthropic(
                auth_choice="apiKey",
                config=config,
                set_default_model=False,  # Don't change model settings
                opts={}
            )
            return result is not None
        
        elif auth_handler_name == "openai":
            from .auth_handlers.openai import apply_auth_choice_openai
            result = await apply_auth_choice_openai(
                auth_choice="openai-api-key",
                config=config,
                set_default_model=False,
                opts={}
            )
            return result is not None
        
        elif auth_handler_name == "google":
            from .auth_handlers.google import apply_auth_choice_google
            result = await apply_auth_choice_google(
                auth_choice="gemini-api-key",
                config=config,
                set_default_model=False,
                opts={}
            )
            return result is not None
        
        elif auth_handler_name == "moonshot":
            from .auth_handlers.moonshot import apply_auth_choice_moonshot
            result = await apply_auth_choice_moonshot(
                auth_choice="moonshot-api-key",
                config=config,
                set_default_model=False,
                opts={}
            )
            return result is not None
        
        elif auth_handler_name == "minimax":
            from .auth_handlers.minimax import apply_auth_choice_minimax
            result = await apply_auth_choice_minimax(
                auth_choice="minimax-api-key",
                config=config,
                set_default_model=False,
                opts={}
            )
            return result is not None
        
        elif auth_handler_name == "api_providers":
            # Generic API provider handler
            from .auth_handlers.api_providers import apply_auth_choice_api_providers
            result = await apply_auth_choice_api_providers(
                auth_choice=f"{provider}-api-key",
                config=config,
                set_default_model=False,
                opts={}
            )
            return result is not None
        
        else:
            logger.warning(f"No handler for provider: {provider}")
            return False
    
    except Exception as e:
        logger.error(f"Failed to configure {display_name}: {e}")
        print(f"❌ Failed to configure {display_name}: {e}")
        return False


async def ensure_fallback_provider_configured(
    config: OpenClawConfig,
    model_id: str,
    interactive: bool = True
) -> bool:
    """Ensure the provider for a fallback model is configured
    
    Args:
        config: Current configuration
        model_id: Model ID (e.g., "openai/gpt-4o")
        interactive: Whether to prompt user interactively
        
    Returns:
        True if provider is configured or configuration succeeded, False otherwise
    """
    provider = extract_provider_from_model_id(model_id)
    if not provider:
        logger.warning(f"Could not extract provider from model ID: {model_id}")
        return True  # Allow it anyway
    
    # Check if already configured
    if check_provider_configured(config, provider):
        return True
    
    # Not configured, prompt user to configure
    return await configure_fallback_provider(config, provider, interactive)
