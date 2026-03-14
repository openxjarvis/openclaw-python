"""API providers handler - handles 20+ simple API key providers

Unified handler for providers that only need API key configuration.
Mirrors openclaw/src/commands/auth-choice.apply.api-providers.ts
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


# Simple API key provider flows (aligned with TS SIMPLE_API_KEY_PROVIDER_FLOWS)
SIMPLE_API_KEY_PROVIDER_FLOWS = {
    "openrouter-api-key": {
        "provider": "openrouter",
        "env_var": "OPENROUTER_API_KEY",
        "profile_id": "openrouter",
        "model_prefix": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/anthropic/claude-sonnet-4",
        "help_url": "https://openrouter.ai/keys",
    },
    "mistral-api-key": {
        "provider": "mistral",
        "env_var": "MISTRAL_API_KEY",
        "profile_id": "mistral",
        "model_prefix": "mistral",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral/mistral-large",
        "help_url": "https://console.mistral.ai/api-keys",
    },
    "xai-api-key": {
        "provider": "xai",
        "env_var": "XAI_API_KEY",
        "profile_id": "xai",
        "model_prefix": "xai",
        "base_url": "https://api.x.ai/v1",
        "default_model": "xai/grok-3",
        "help_url": "https://console.x.ai",
    },
    "kilocode-api-key": {
        "provider": "kilocode",
        "env_var": "KILOCODE_API_KEY",
        "profile_id": "kilocode",
        "model_prefix": "openrouter",  # OpenRouter-compatible
        "base_url": "https://gateway.kilo.dev/v1",
        "default_model": "openrouter/anthropic/claude-sonnet-4",
        "help_url": "https://gateway.kilo.dev",
    },
    "litellm-api-key": {
        "provider": "litellm",
        "env_var": "LITELLM_API_KEY",
        "profile_id": "litellm",
        "model_prefix": "litellm",
        "base_url": "http://localhost:4000",  # Default local instance
        "default_model": "litellm/gpt-4o",
        "help_url": "https://docs.litellm.ai",
    },
    "ai-gateway-api-key": {
        "provider": "ai-gateway",
        "env_var": "AI_GATEWAY_API_KEY",
        "profile_id": "ai-gateway",
        "model_prefix": "openai",
        "base_url": "https://gateway.ai.cloudflare.com/v1",
        "default_model": "openai/gpt-4o",
        "help_url": "https://ai.cloudflare.com",
    },
    "synthetic-api-key": {
        "provider": "synthetic",
        "env_var": "SYNTHETIC_API_KEY",
        "profile_id": "synthetic",
        "model_prefix": "synthetic",
        "base_url": "https://api.synthetic.ai/v1",
        "default_model": "claude-sonnet-4",
        "help_url": "https://synthetic.ai",
    },
    "venice-api-key": {
        "provider": "venice",
        "env_var": "VENICE_API_KEY",
        "profile_id": "venice",
        "model_prefix": "venice",
        "base_url": "https://api.venice.ai/api/v1",
        "default_model": "venice/llama-3.3-70b",
        "help_url": "https://venice.ai/api",
    },
    "together-api-key": {
        "provider": "together",
        "env_var": "TOGETHER_API_KEY",
        "profile_id": "together",
        "model_prefix": "together",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "help_url": "https://api.together.xyz/settings/api-keys",
    },
    "huggingface-api-key": {
        "provider": "huggingface",
        "env_var": "HUGGINGFACE_API_KEY",
        "profile_id": "huggingface",
        "model_prefix": "huggingface",
        "base_url": "https://api-inference.huggingface.co/v1",
        "default_model": "huggingface/meta-llama/Meta-Llama-3.1-70B-Instruct",
        "help_url": "https://huggingface.co/settings/tokens",
    },
    "xiaomi-api-key": {
        "provider": "xiaomi",
        "env_var": "XIAOMI_API_KEY",
        "profile_id": "xiaomi",
        "model_prefix": "xiaomi",
        "base_url": "https://api.xiaomi.ai/v1",
        "default_model": "xiaomi/xiaomi-llm",
        "help_url": "https://xiaomi.ai",
    },
    "qianfan-api-key": {
        "provider": "qianfan",
        "env_var": "QIANFAN_API_KEY",
        "profile_id": "qianfan",
        "model_prefix": "qianfan",
        "base_url": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1",
        "default_model": "qianfan/ernie-4.0-8k",
        "help_url": "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
    },
    "volcengine-api-key": {
        "provider": "volcengine",
        "env_var": "VOLCENGINE_API_KEY",
        "profile_id": "volcengine",
        "model_prefix": "volcengine",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "volcengine/doubao-pro-32k",
        "help_url": "https://console.volcengine.com/ark",
    },
    "byteplus-api-key": {
        "provider": "byteplus",
        "env_var": "BYTEPLUS_API_KEY",
        "profile_id": "byteplus",
        "model_prefix": "byteplus",
        "base_url": "https://ark-ap-singapore-1.byteplusapi.com/api/v3",
        "default_model": "byteplus/doubao-pro-32k",
        "help_url": "https://console.byteplus.com/ark",
    },
    "opencode-zen": {
        "provider": "opencode-zen",
        "env_var": "OPENCODE_ZEN_API_KEY",
        "profile_id": "opencode-zen",
        "model_prefix": "openai",  # OpenAI-compatible
        "base_url": "https://opencode.ai/zen/v1",
        "default_model": "openai/gpt-4o",
        "help_url": "https://opencode.ai/zen",
    },
}


async def _prompt_api_key(flow: dict, opts: dict | None = None) -> str:
    """Prompt for API key with environment variable check
    
    Args:
        flow: Provider flow configuration
        opts: Optional parameters that may contain the API key
        
    Returns:
        API key string
    """
    provider = flow["provider"]
    env_var = flow["env_var"]
    help_url = flow.get("help_url")
    
    # Check if API key provided in opts
    opt_key = f"{provider}ApiKey"
    if opts and opt_key in opts:
        return opts[opt_key]
    
    # Check environment variable
    env_key = os.getenv(env_var)
    if env_key:
        use_env = input(f"\n✓ Found {env_var} in environment. Use it? [Y/n]: ").strip().lower()
        if use_env != "n":
            return env_key
    
    # Prompt for API key
    print(f"\n{provider.title()} API Key Configuration")
    print("-" * 60)
    if help_url:
        print(f"Get your API key from: {help_url}")
    
    api_key = input(f"\nEnter your {provider.title()} API key: ").strip()
    if not api_key:
        raise ValueError(f"{provider.title()} API key is required")
    
    return api_key


async def apply_auth_choice_api_providers(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle simple API key providers
    
    Covers 20+ providers that only need API key configuration.
    
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
    flow = SIMPLE_API_KEY_PROVIDER_FLOWS.get(auth_choice)
    if not flow:
        return None
    
    # Collect API key
    api_key = await _prompt_api_key(flow, opts)
    
    # Save to auth-profiles.json
    try:
        from ...config.auth_profiles import set_api_key
        set_api_key(flow["profile_id"], api_key)
        print(f"✓ {flow['provider'].title()} API key saved")
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
