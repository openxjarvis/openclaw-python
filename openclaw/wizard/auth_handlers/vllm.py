"""vLLM auth handler

Handles vLLM self-hosted OpenAI-compatible endpoints.
Mirrors openclaw/src/commands/auth-choice.apply.vllm.ts
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


async def apply_auth_choice_vllm(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle vLLM self-hosted configuration
    
    Supports:
    - vllm: vLLM OpenAI-compatible endpoint
    
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
    if auth_choice != "vllm":
        return None
    
    print("\nvLLM Self-Hosted Configuration")
    print("=" * 80)
    print("Configure your local/self-hosted vLLM OpenAI-compatible endpoint.")
    
    # Base URL
    default_url = "http://localhost:8000/v1"
    base_url = input(f"\nBase URL [{default_url}]: ").strip() or default_url
    
    # Model ID
    print("\nEnter the model ID your vLLM server is serving.")
    print("Example: meta-llama/Meta-Llama-3.1-70B-Instruct")
    model_id = input("Model ID: ").strip()
    if not model_id:
        raise ValueError("Model ID is required")
    
    # Optional API key
    api_key = input("\nAPI Key (leave blank if not required): ").strip() or None
    
    # Write to models.providers.vllm
    from ...config.schema import ModelsConfig
    
    if not config.models:
        config.models = ModelsConfig()
    if not config.models.providers:
        config.models.providers = {}
    
    provider_entry: dict = {
        "baseUrl": base_url,
        "api": "openai-completions",
        "models": [
            {
                "id": model_id,
                "name": f"{model_id} (vLLM)",
                "contextWindow": 8192,
                "maxTokens": 4096,
                "input": ["text"],
            }
        ],
    }
    
    if api_key:
        provider_entry["apiKey"] = api_key
    
    config.models.providers["vllm"] = provider_entry
    
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
    
    print(f"\n✓ vLLM configuration saved")
    print(f"  Endpoint: {base_url}")
    print(f"  Model: {model_id}")
    
    return ApplyAuthChoiceResult(config=config)
