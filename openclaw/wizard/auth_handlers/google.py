"""Google Gemini auth handler

Handles Google Gemini authentication (API key, CLI OAuth).
Mirrors openclaw/src/commands/auth-choice.apply.google-gemini-cli.ts
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_google(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle Google Gemini authentication
    
    Supports:
    - gemini-api-key: Google Gemini API key
    - google-gemini-cli: Google Gemini CLI OAuth (not yet implemented)
    
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
    if auth_choice not in ("gemini-api-key", "google-gemini-cli"):
        return None
    
    # Handle CLI OAuth
    if auth_choice == "google-gemini-cli":
        print("\n⚠️  Google Gemini CLI OAuth is not yet implemented in Python version.")
        print("Please use gemini-api-key instead.")
        return None
    
    # Handle API key
    if auth_choice == "gemini-api-key":
        # Check opts first
        api_key = None
        if opts and "geminiApiKey" in opts:
            api_key = opts["geminiApiKey"]
        
        # Check environment
        if not api_key:
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if api_key:
                use_env = input("\n✓ Found Google API key in environment. Use it? [Y/n]: ").strip().lower()
                if use_env == "n":
                    api_key = None
        
        # Prompt for API key
        if not api_key:
            print("\nGoogle Gemini API Key Configuration")
            print("-" * 60)
            print("Get your API key from: https://makersuite.google.com/app/apikey")
            api_key = input("\nEnter your Google Gemini API key: ").strip()
            if not api_key:
                raise ValueError("Google Gemini API key is required")
        
        # Save to auth-profiles.json (use "google" as provider ID)
        try:
            from ...config.auth_profiles import set_api_key
            set_api_key("google", api_key)
            print("✓ Google Gemini API key saved")
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
