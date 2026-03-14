"""OpenAI auth handler

Handles OpenAI authentication (Codex OAuth, API key).
Mirrors openclaw/src/commands/auth-choice.apply.openai.ts
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_openai(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle OpenAI authentication
    
    Supports:
    - openai-api-key: OpenAI API key
    - openai-codex: OpenAI Codex OAuth (not yet implemented)
    
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
    if auth_choice not in ("openai-api-key", "openai-codex"):
        return None
    
    # Handle Codex OAuth
    if auth_choice == "openai-codex":
        print("\n⚠️  OpenAI Codex OAuth is not yet implemented in Python version.")
        print("Please use openai-api-key instead.")
        return None
    
    # Handle API key
    if auth_choice == "openai-api-key":
        # Check opts first
        api_key = None
        if opts and "openaiApiKey" in opts:
            api_key = opts["openaiApiKey"]
        
        # Check environment
        if not api_key:
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                use_env = input("\n✓ Found OPENAI_API_KEY in environment. Use it? [Y/n]: ").strip().lower()
                if use_env == "n":
                    api_key = None
        
        # Prompt for API key
        if not api_key:
            print("\nOpenAI API Key Configuration")
            print("-" * 60)
            print("Get your API key from: https://platform.openai.com/api-keys")
            api_key = input("\nEnter your OpenAI API key: ").strip()
            if not api_key:
                raise ValueError("OpenAI API key is required")
        
        # Save to auth-profiles.json
        try:
            from ...config.auth_profiles import set_api_key
            set_api_key("openai", api_key)
            print("✓ OpenAI API key saved")
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
