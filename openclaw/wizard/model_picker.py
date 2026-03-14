"""Interactive model selection for onboarding
Aligns with TypeScript's src/commands/model-picker.ts
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config.schema import OpenClawConfig

from . import prompter

logger = logging.getLogger(__name__)

# Special option values
KEEP_VALUE = "__keep__"
MANUAL_VALUE = "__manual__"
VLLM_VALUE = "__vllm__"


async def prompt_default_model(
    config: OpenClawConfig,
    allow_keep: bool = True,
    include_manual: bool = True,
    include_vllm: bool = False,
    preferred_provider: str | None = None,
    message: str | None = None,
    exclude_models: list[str] | None = None,
) -> dict[str, Any]:
    """Interactive model selection prompt
    
    Mirrors TypeScript promptDefaultModel from src/commands/model-picker.ts
    
    Args:
        config: Current configuration
        allow_keep: Show "Keep current model" option
        include_manual: Show "Enter model manually" option
        include_vllm: Show "Configure vLLM" option
        preferred_provider: Filter by provider (e.g., "anthropic", "openai")
        message: Custom prompt message
        exclude_models: List of model IDs to exclude from selection
        
    Returns:
        Dict with 'model' key (model ID string) or empty dict if keeping current
    """
    from ..agents.model_catalog import load_model_catalog
    from .onboarding import _PROVIDER_MODELS
    
    # Load models from catalog (dynamic) and fallback to hardcoded list
    catalog_models = []
    try:
        catalog = await load_model_catalog()  # Added await
        if catalog:
            catalog_models = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "provider": m.get("provider", "unknown"),
                    "contextWindow": m.get("contextWindow", 0),
                    "reasoning": m.get("reasoning", False),
                }
                for m in catalog
                if isinstance(m, dict) and "id" in m
            ]
    except Exception as e:
        logger.debug(f"Could not load model catalog: {e}")
    
    # Merge with hardcoded models
    all_models = {}
    
    # Add hardcoded models first
    for provider, models in _PROVIDER_MODELS.items():
        for model_id, model_hint in models:
            if model_id not in all_models:
                # Parse provider from model_id (e.g., "anthropic/claude-sonnet-4")
                parts = model_id.split("/", 1)
                provider_name = parts[0] if len(parts) == 2 else provider
                model_name = parts[1] if len(parts) == 2 else model_id
                
                all_models[model_id] = {
                    "id": model_id,
                    "name": model_hint,
                    "provider": provider_name,
                    "contextWindow": 0,
                    "reasoning": False,
                }
    
    # Overlay catalog models (they have more accurate metadata)
    for m in catalog_models:
        all_models[m["id"]] = m
    
    # Filter by preferred provider if specified
    if preferred_provider:
        all_models = {
            k: v for k, v in all_models.items()
            if v["provider"] == preferred_provider
        }
    
    # Exclude specific models if provided
    if exclude_models:
        exclude_set = set(exclude_models)
        all_models = {
            k: v for k, v in all_models.items()
            if k not in exclude_set
        }
    
    # Get current model
    current_model = None
    if config.agents and config.agents.defaults:
        current_model = config.agents.defaults.model
    
    # Group by provider
    providers = {}
    for model_id, model_info in all_models.items():
        provider = model_info["provider"]
        if provider not in providers:
            providers[provider] = []
        providers[provider].append((model_id, model_info))
    
    # If too many models (>30), ask user to filter by provider first
    total_models = len(all_models)
    selected_provider = None
    
    if not preferred_provider and total_models > 30 and len(providers) > 1:
        print(f"\n{total_models} models available across {len(providers)} providers")
        
        provider_choices = [
            {"name": "All providers", "value": "*"},
            *[
                {
                    "name": f"{p} ({len(models)} models)",
                    "value": p,
                }
                for p, models in sorted(providers.items())
            ]
        ]
        
        try:
            selected_provider = prompter.select(
                "Filter models by provider:",
                choices=provider_choices,
            )
        except prompter.WizardCancelledError:
            return {}
        
        if selected_provider != "*":
            all_models = {
                k: v for k, v in all_models.items()
                if v["provider"] == selected_provider
            }
    
    # Build choice list
    choices = []
    
    # Keep current
    if allow_keep and current_model:
        choices.append({
            "name": f"Keep current ({current_model})",
            "value": KEEP_VALUE,
            "description": "Continue with existing model configuration",
        })
    
    # Manual entry
    if include_manual:
        choices.append({
            "name": "Enter model manually",
            "value": MANUAL_VALUE,
            "description": "Specify a custom model ID",
        })
    
    # vLLM option
    if include_vllm:
        choices.append({
            "name": "Configure vLLM server",
            "value": VLLM_VALUE,
            "description": "Set up a local vLLM inference server",
        })
    
    # Add models sorted by provider
    for provider_name in sorted(providers.keys()):
        if selected_provider and selected_provider != "*" and provider_name != selected_provider:
            continue
        
        provider_models = providers[provider_name]
        for model_id, model_info in sorted(provider_models, key=lambda x: x[0]):
            # Build hint
            hint_parts = []
            if model_info.get("contextWindow"):
                ctx_k = model_info["contextWindow"] // 1000
                hint_parts.append(f"{ctx_k}k context")
            if model_info.get("reasoning"):
                hint_parts.append("reasoning")
            
            hint = f"({', '.join(hint_parts)})" if hint_parts else ""
            
            display_name = model_info.get("name", model_id)
            if hint:
                display_name = f"{display_name} {hint}"
            
            choices.append({
                "name": display_name,
                "value": model_id,
                "description": f"{provider_name} model",
            })
    
    # Prompt user
    try:
        selection = prompter.select(
            message or "Select default model:",
            choices=choices,
            default=KEEP_VALUE if (allow_keep and current_model) else None,
        )
    except prompter.WizardCancelledError:
        return {}
    
    # Handle selection
    if selection == KEEP_VALUE:
        return {}
    
    elif selection == MANUAL_VALUE:
        try:
            model_id = prompter.text(
                "Enter model ID (e.g., anthropic/claude-sonnet-4):",
                default="",
            )
            if model_id:
                return {"model": model_id}
            return {}
        except prompter.WizardCancelledError:
            return {}
    
    elif selection == VLLM_VALUE:
        # Trigger vLLM configuration
        print("\n⚠️  vLLM configuration is not yet implemented in this wizard.")
        print("Please use: uv run openclaw config set models.providers.vllm")
        return {}
    
    else:
        # Regular model selection
        return {"model": selection}


async def prompt_model_allowlist(
    config: OpenClawConfig,
    message: str | None = None,
) -> dict[str, Any]:
    """Multi-select model whitelist for /model picker
    
    Args:
        config: Current configuration
        message: Custom prompt message
        
    Returns:
        Dict with 'models' key (list of model IDs)
    """
    from ..agents.model_catalog import load_model_catalog
    
    # Load all available models
    catalog = await load_model_catalog()  # Added await
    if not catalog:
        print("⚠️  No models found in catalog")
        return {"models": []}
    
    # Get current allowlist
    current_allowlist = set()
    if config.agents and config.agents.defaults and config.agents.defaults.modelAllowlist:
        current_allowlist = set(config.agents.defaults.modelAllowlist)
    
    # Build choices
    choices = []
    for model in catalog:
        if not isinstance(model, dict) or "id" not in model:
            continue
        
        model_id = model["id"]
        model_name = model.get("name", model_id)
        provider = model.get("provider", "unknown")
        
        choices.append({
            "name": f"{model_name} ({provider})",
            "value": model_id,
        })
    
    # Prompt user with multiselect
    try:
        selected = prompter.multiselect(
            message or "Select models to allow in /model picker (Space to select, Enter to confirm):",
            choices=choices,
            searchable=True,
            initial_values=list(current_allowlist),
        )
        return {"models": selected}
    except prompter.WizardCancelledError:
        return {"models": []}


__all__ = [
    "prompt_default_model",
    "prompt_model_allowlist",
]
