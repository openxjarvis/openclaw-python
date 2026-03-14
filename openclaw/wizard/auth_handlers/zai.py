"""Z.AI (GLM) auth handler

Handles Z.AI authentication (Coding Plan, Global, CN endpoints).
Mirrors openclaw/src/commands/auth-choice.apply.api-providers.ts (Z.AI section)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


# Z.AI endpoint mapping (aligned with TS ZAI_AUTH_CHOICE_ENDPOINT)
ZAI_AUTH_CHOICE_ENDPOINT = {
    "zai-coding-global": "https://api.z.ai/v1",
    "zai-coding-cn": "https://open.bigmodel.cn/api/paas/v4/",
    "zai-global": "https://api.z.ai/v1",
    "zai-cn": "https://open.bigmodel.cn/api/paas/v4/",
    "zai-api-key": "https://api.z.ai/v1",  # Default to global
}

# Z.AI default model mapping
ZAI_AUTH_CHOICE_DEFAULT_MODEL = {
    "zai-coding-global": "zai/glm-4-flash-coding",
    "zai-coding-cn": "zai/glm-4-flash-coding",
    "zai-global": "zai/glm-4-plus",
    "zai-cn": "zai/glm-4-plus",
    "zai-api-key": "zai/glm-4-plus",
}


def _detect_zai_endpoint(api_key: str) -> str | None:
    """Auto-detect Z.AI endpoint from API key prefix
    
    Args:
        api_key: Z.AI API key
        
    Returns:
        Detected endpoint or None
    """
    # Z.AI uses different key prefixes for different endpoints
    # This is a heuristic - actual detection logic may vary
    if api_key.startswith("cn-"):
        return "https://open.bigmodel.cn/api/paas/v4/"
    else:
        return "https://api.z.ai/v1"


async def apply_auth_choice_zai(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle Z.AI (GLM) authentication
    
    Supports:
    - zai-api-key: Z.AI API key (auto-detect endpoint)
    - zai-coding-global: Coding Plan Global endpoint
    - zai-coding-cn: Coding Plan CN endpoint
    - zai-global: Global endpoint
    - zai-cn: CN endpoint
    
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
    if auth_choice not in ZAI_AUTH_CHOICE_ENDPOINT:
        return None
    
    # Check opts first
    api_key = None
    if opts and "zaiApiKey" in opts:
        api_key = opts["zaiApiKey"]
    
    # Check environment
    if not api_key:
        api_key = os.getenv("ZAI_API_KEY") or os.getenv("GLM_API_KEY")
        if api_key:
            use_env = input("\n✓ Found Z.AI API key in environment. Use it? [Y/n]: ").strip().lower()
            if use_env == "n":
                api_key = None
    
    # Prompt for API key
    if not api_key:
        print("\nZ.AI (GLM) API Key Configuration")
        print("-" * 60)
        
        if "cn" in auth_choice:
            print("Get your API key from: https://open.bigmodel.cn")
        else:
            print("Get your API key from: https://z.ai")
        
        api_key = input("\nEnter your Z.AI API key: ").strip()
        if not api_key:
            raise ValueError("Z.AI API key is required")
    
    # Auto-detect endpoint if using zai-api-key
    if auth_choice == "zai-api-key":
        detected_endpoint = _detect_zai_endpoint(api_key)
        endpoint = detected_endpoint or ZAI_AUTH_CHOICE_ENDPOINT[auth_choice]
    else:
        endpoint = ZAI_AUTH_CHOICE_ENDPOINT[auth_choice]
    
    # Save to auth-profiles.json
    try:
        from ...config.auth_profiles import set_api_key
        set_api_key("zai", api_key)
        print("✓ Z.AI API key saved")
    except Exception as e:
        print(f"Warning: Could not save to auth-profiles.json: {e}")
    
    # Write to models.providers.zai
    from ...config.schema import ModelsConfig
    
    if not config.models:
        config.models = ModelsConfig()
    if not config.models.providers:
        config.models.providers = {}
    
    config.models.providers["zai"] = {
        "baseUrl": endpoint,
        "apiKey": {"$ref": "auth://zai"},
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
