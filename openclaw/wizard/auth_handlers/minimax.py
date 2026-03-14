"""MiniMax auth handler

Handles MiniMax authentication (Portal OAuth, API key, CN, Lightning).
Mirrors openclaw/src/commands/auth-choice.apply.minimax.ts
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_minimax(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle MiniMax authentication
    
    Supports:
    - minimax-portal: MiniMax OAuth Portal (not yet implemented)
    - minimax-api: MiniMax M2.5 API key
    - minimax-api-key-cn: MiniMax M2.5 CN endpoint
    - minimax-api-lightning: MiniMax M2.5 Highspeed
    
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
    if auth_choice not in ("minimax-portal", "minimax-api", "minimax-api-key-cn", "minimax-api-lightning", "minimax", "minimax-cloud"):
        return None
    
    # Handle OAuth Portal
    if auth_choice == "minimax-portal":
        print("\n⚠️  MiniMax OAuth Portal is not yet implemented in Python version.")
        print("Please use minimax-api instead.")
        return None
    
    # Handle API key flows
    if auth_choice in ("minimax-api", "minimax-api-key-cn", "minimax-api-lightning", "minimax", "minimax-cloud"):
        # Check opts first
        api_key = None
        if opts and "minimaxApiKey" in opts:
            api_key = opts["minimaxApiKey"]
        
        # Check environment
        if not api_key:
            api_key = os.getenv("MINIMAX_API_KEY")
            if api_key:
                use_env = input("\n✓ Found MINIMAX_API_KEY in environment. Use it? [Y/n]: ").strip().lower()
                if use_env == "n":
                    api_key = None
        
        # Prompt for API key
        if not api_key:
            print("\nMiniMax API Key Configuration")
            print("-" * 60)
            print("Get your API key from: https://platform.minimaxi.com")
            api_key = input("\nEnter your MiniMax API key: ").strip()
            if not api_key:
                raise ValueError("MiniMax API key is required")
        
        # Save to auth-profiles.json
        try:
            from ...config.auth_profiles import set_api_key
            set_api_key("minimax", api_key)
            print("✓ MiniMax API key saved")
        except Exception as e:
            print(f"Warning: Could not save to auth-profiles.json: {e}")
        
        # Determine model and base URL based on auth_choice
        if auth_choice == "minimax-api-lightning":
            model = "minimax/MiniMax-M2.5-highspeed"
            base_url = "https://api.minimaxi.com/v1"
        elif auth_choice == "minimax-api-key-cn":
            model = "minimax/MiniMax-M2.5"
            base_url = "https://api.minimaxi.com/v1"
        else:
            # minimax-api, minimax, minimax-cloud
            model = "minimax/MiniMax-M2.5"
            base_url = "https://api.minimaxi.com/v1"
        
        # Write to models.providers for CN endpoint
        if auth_choice == "minimax-api-key-cn":
            from ...config.schema import ModelsConfig
            
            if not config.models:
                config.models = ModelsConfig()
            if not config.models.providers:
                config.models.providers = {}
            
            config.models.providers["minimax"] = {
                "baseUrl": base_url,
                "apiKey": {"$ref": "auth://minimax"},
                "api": "openai-completions",
            }
        
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
