"""Anthropic auth handler

Handles Anthropic authentication (setup-token, API key, OAuth).
Mirrors openclaw/src/commands/auth-choice.apply.anthropic.ts
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_anthropic(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle Anthropic authentication
    
    Supports:
    - setup-token / token: Anthropic setup token
    - apiKey: Anthropic API key
    - oauth: OAuth flow (legacy alias for setup-token)
    
    Args:
        auth_choice: Selected authentication choice
        config: Current configuration
        set_default_model: Whether to set default model
        agent_dir: Optional agent directory
        agent_id: Optional agent ID
        opts: Optional parameters
        
    Returns:
        ApplyAuthChoiceResult if handled, None otherwise
    """
    if auth_choice not in ("setup-token", "token", "oauth", "apiKey"):
        return None
    
    # Normalize oauth -> token
    if auth_choice == "oauth":
        auth_choice = "token"
    
    # Handle API key
    if auth_choice == "apiKey":
        # Check opts first
        api_key = None
        if opts and "anthropicApiKey" in opts:
            api_key = opts["anthropicApiKey"]
        
        # Check environment
        if not api_key:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                use_env = input("\n✓ Found ANTHROPIC_API_KEY in environment. Use it? [Y/n]: ").strip().lower()
                if use_env == "n":
                    api_key = None
        
        # Prompt for API key
        if not api_key:
            print("\nAnthropic API Key Configuration")
            print("-" * 60)
            print("Get your API key from: https://console.anthropic.com/settings/keys")
            api_key = input("\nEnter your Anthropic API key: ").strip()
            if not api_key:
                raise ValueError("Anthropic API key is required")
        
        # Save to auth-profiles.json
        try:
            from ...config.auth_profiles import set_api_key
            set_api_key("anthropic", api_key)
            print("✓ Anthropic API key saved")
        except Exception as e:
            print(f"Warning: Could not save to auth-profiles.json: {e}")
        
        # Model selection moved to main onboarding flow (prompt_default_model)
        # Just ensure the config structures exist
        if set_default_model:
            from ...config.schema import AgentsConfig, AgentDefaults, AgentConfig
            
            if not config.agents:
                config.agents = AgentsConfig()
            if not config.agents.defaults:
                config.agents.defaults = AgentDefaults()
            if not config.agent:
                config.agent = AgentConfig()
        
        return ApplyAuthChoiceResult(config=config)
    
    # Handle setup-token / token
    if auth_choice in ("setup-token", "token"):
        # Check opts first
        token = None
        if opts and "token" in opts:
            token = opts["token"]
        
        # Prompt for setup token
        if not token:
            print("\nAnthropic Setup Token Configuration")
            print("-" * 60)
            print("Run `claude setup-token` on another machine, then paste the token here.")
            print("Or get it from: https://console.anthropic.com/settings/keys")
            token = input("\nPaste setup token: ").strip()
            if not token:
                raise ValueError("Setup token is required")
        
        # Save to auth-profiles.json as a token
        try:
            from ...config.auth_profiles import set_oauth_token
            set_oauth_token("anthropic", token)
            print("✓ Anthropic setup token saved")
        except Exception as e:
            print(f"Warning: Could not save to auth-profiles.json: {e}")
        
        # Model selection moved to main onboarding flow (prompt_default_model)
        # Just ensure the config structures exist
        if set_default_model:
            from ...config.schema import AgentsConfig, AgentDefaults, AgentConfig
            
            if not config.agents:
                config.agents = AgentsConfig()
            if not config.agents.defaults:
                config.agents.defaults = AgentDefaults()
            if not config.agent:
                config.agent = AgentConfig()
        
        return ApplyAuthChoiceResult(config=config)
    
    return None
