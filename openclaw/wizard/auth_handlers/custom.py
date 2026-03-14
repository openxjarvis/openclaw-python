"""Custom provider auth handler

Handles custom OpenAI/Anthropic-compatible endpoints.
Mirrors openclaw/src/commands/onboard-custom.ts (promptCustomApiConfig)
"""
from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..auth_choice_types import AuthChoice
    from ...config.schema import OpenClawConfig

from .base import ApplyAuthChoiceResult


def _derive_custom_provider_id(base_url: str) -> str:
    """Derive provider ID from base URL
    
    Example: http://127.0.0.1:11434/v1 → custom-127-0-0-1-11434
    
    Args:
        base_url: Base URL
        
    Returns:
        Provider ID
    """
    try:
        parsed = urllib.parse.urlparse(base_url)
        netloc = parsed.netloc  # e.g. "127.0.0.1:11434"
        safe = netloc.replace(".", "-").replace(":", "-")
        return f"custom-{safe}"
    except Exception:
        return "custom-provider"


async def _verify_custom_endpoint(
    base_url: str,
    model_id: str,
    api_key: str | None,
    compatibility: str
) -> str:
    """Verify custom endpoint by making test requests
    
    Args:
        base_url: Base URL
        model_id: Model ID
        api_key: Optional API key
        compatibility: Expected compatibility ("openai", "anthropic", "unknown")
        
    Returns:
        Detected compatibility ("openai" or "anthropic")
    """
    try:
        import httpx
        
        headers: dict = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try OpenAI endpoint
            if compatibility in ("openai", "unknown"):
                try:
                    response = await client.post(
                        f"{base_url}/chat/completions",
                        json={
                            "model": model_id,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                        headers=headers,
                    )
                    if response.status_code < 500:
                        return "openai"
                except Exception:
                    pass
            
            # Try Anthropic endpoint
            if compatibility in ("anthropic", "unknown"):
                try:
                    response = await client.post(
                        f"{base_url}/messages",
                        json={
                            "model": model_id,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
                        },
                        headers=headers,
                    )
                    if response.status_code < 500:
                        return "anthropic"
                except Exception:
                    pass
    except Exception:
        pass
    
    # Default to OpenAI if verification fails
    return "openai" if compatibility in ("openai", "unknown") else "anthropic"


async def apply_auth_choice_custom(
    auth_choice: AuthChoice,
    config: OpenClawConfig,
    set_default_model: bool = True,
    agent_dir: str | None = None,
    agent_id: str | None = None,
    opts: dict | None = None,
) -> ApplyAuthChoiceResult | None:
    """Handle custom provider configuration
    
    Supports:
    - custom-api-key: Any OpenAI/Anthropic-compatible endpoint
    
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
    if auth_choice != "custom-api-key":
        return None
    
    print("\nCustom Provider Configuration")
    print("=" * 80)
    print("Configure any OpenAI or Anthropic compatible endpoint.")
    
    # 1. Base URL
    default_url = "http://localhost:11434/v1"
    base_url = None
    while not base_url:
        raw_url = input(f"\nAPI Base URL [{default_url}]: ").strip()
        base_url = raw_url if raw_url else default_url
        base_url = base_url.rstrip("/")
        
        # Validate URL
        try:
            parsed = urllib.parse.urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                print("  Invalid URL. Please enter a full URL including scheme (e.g., http://localhost:11434/v1)")
                base_url = None
        except Exception:
            print("  Invalid URL format.")
            base_url = None
    
    # 2. Compatibility
    print("\nEndpoint compatibility:")
    print("  1. OpenAI-compatible (uses /chat/completions)")
    print("  2. Anthropic-compatible (uses /messages)")
    print("  3. Unknown — detect automatically")
    compat_choice = input("Select [1]: ").strip() or "1"
    compat_map = {"1": "openai", "2": "anthropic", "3": "unknown"}
    compatibility = compat_map.get(compat_choice, "openai")
    
    # 3. Model ID
    model_id = None
    while not model_id:
        model_id = input("\nModel ID (e.g., llama3, claude-3-7-sonnet): ").strip()
        if not model_id:
            print("  Model ID is required.")
    
    # 4. Optional API key
    api_key = input("\nAPI Key (leave blank if not required): ").strip() or None
    
    # 5. Verify endpoint
    print(f"\nVerifying endpoint {base_url} with model {model_id}...")
    verified_compat = await _verify_custom_endpoint(base_url, model_id, api_key, compatibility)
    
    if verified_compat:
        api_field = "openai-completions" if verified_compat == "openai" else "anthropic"
        print(f"  ✓ Endpoint verified ({api_field})")
    else:
        print("  ⚠️  Could not verify endpoint (proceeding anyway)")
        api_field = "openai-completions" if compatibility in ("openai", "unknown") else "anthropic"
    
    # 6. Provider ID
    default_pid = _derive_custom_provider_id(base_url)
    provider_id = input(f"\nEndpoint ID [{default_pid}]: ").strip() or default_pid
    
    # 7. Model alias (optional)
    alias = input("Model alias (optional, e.g., local, ollama): ").strip() or None
    
    # 8. Write to config
    from ...config.schema import ModelsConfig
    
    if not config.models:
        config.models = ModelsConfig()
    if not config.models.providers:
        config.models.providers = {}
    
    provider_entry: dict = {
        "baseUrl": base_url,
        "api": api_field,
        "models": [
            {
                "id": model_id,
                "name": f"{model_id} (Custom Provider)",
                "contextWindow": 8192,
                "maxTokens": 4096,
                "input": ["text"],
            }
        ],
    }
    
    if api_key:
        provider_entry["apiKey"] = api_key
    
    config.models.providers[provider_id] = provider_entry
    
    # 9. Set model (keep for custom provider as user manually specified it)
    model_ref = f"{provider_id}/{model_id}"
    
    if set_default_model:
        from ...config.schema import AgentsConfig, AgentDefaults, AgentConfig
        
        if not config.agents:
            config.agents = AgentsConfig()
        if not config.agents.defaults:
            config.agents.defaults = AgentDefaults()
        if not config.agent:
            config.agent = AgentConfig()
        
        # For custom providers, we keep the user's specified model
        config.agents.defaults.model = model_ref
        config.agent.model = model_id
        
        # 10. Optional alias
        if alias:
            if not config.agents.defaults.models:
                config.agents.defaults.models = {}
            config.agents.defaults.models[model_ref] = {"alias": alias}
    
    print(f"\n✓ Custom provider configuration saved")
    print(f"  Provider ID: {provider_id}")
    print(f"  Endpoint: {base_url}")
    print(f"  Model: {model_ref}")
    if alias:
        print(f"  Alias: {alias}")
    
    return ApplyAuthChoiceResult(config=config)
