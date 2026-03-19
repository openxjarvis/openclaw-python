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


def apply_primary_model(cfg: OpenClawConfig, model: str) -> OpenClawConfig:
    """Apply primary model while preserving existing fallbacks
    
    Mirrors TypeScript applyPrimaryModel() from src/commands/model-picker.ts:462-487
    
    Args:
        cfg: Current configuration
        model: Model ID (e.g., "anthropic/claude-sonnet-4-6")
        
    Returns:
        Updated configuration with primary model set
    """
    from ..config.schema import AgentsConfig, AgentDefaults, ModelConfig
    
    # Get existing config
    agents = cfg.agents or AgentsConfig()
    defaults = agents.defaults or AgentDefaults()
    existing_model = defaults.model
    existing_models = defaults.models or {}
    
    # Extract existing fallbacks if present
    fallbacks = None
    if isinstance(existing_model, dict):
        fallbacks = existing_model.get("fallbacks")
    elif hasattr(existing_model, "fallbacks"):
        fallbacks = existing_model.fallbacks
    
    # Build new model config
    if fallbacks:
        new_model = ModelConfig(primary=model, fallbacks=fallbacks)
    else:
        new_model = ModelConfig(primary=model)
    
    # Update models dict
    new_models = {**existing_models}
    if model not in new_models:
        new_models[model] = {}
    
    # Create updated config
    # Build kwargs without 'model' and 'models' to avoid duplication
    defaults_dict = defaults.model_dump() if hasattr(defaults, "model_dump") else vars(defaults)
    kwargs = {k: v for k, v in defaults_dict.items() if k not in ('model', 'models')}
    kwargs['model'] = new_model
    kwargs['models'] = new_models
    
    updated_defaults = AgentDefaults(**kwargs)
    
    # Build kwargs without 'defaults' to avoid duplication
    agents_dict = agents.model_dump() if hasattr(agents, "model_dump") else vars(agents)
    agents_kwargs = {k: v for k, v in agents_dict.items() if k != 'defaults'}
    agents_kwargs['defaults'] = updated_defaults
    
    updated_agents = AgentsConfig(**agents_kwargs)
    
    # Return updated config
    from copy import deepcopy
    updated_cfg = deepcopy(cfg)
    updated_cfg.agents = updated_agents
    return updated_cfg


def apply_model_fallbacks_from_selection(
    cfg: OpenClawConfig,
    selection: list[str]
) -> OpenClawConfig:
    """Build model config with primary + fallbacks from multi-select
    
    Mirrors TypeScript applyModelFallbacksFromSelection() from src/commands/model-picker.ts:524-567
    
    Args:
        cfg: Current configuration
        selection: List of model IDs, first is primary, rest are fallbacks
        
    Returns:
        Updated configuration with primary + fallbacks set
    """
    from ..config.schema import AgentsConfig, AgentDefaults, ModelConfig
    
    if not selection or len(selection) == 0:
        return cfg
    
    # Normalize selection (remove duplicates)
    normalized = []
    seen = set()
    for model_id in selection:
        model_str = str(model_id).strip()
        if model_str and model_str not in seen:
            normalized.append(model_str)
            seen.add(model_str)
    
    if len(normalized) == 0:
        return cfg
    
    # Get existing config
    agents = cfg.agents or AgentsConfig()
    defaults = agents.defaults or AgentDefaults()
    
    # First model is primary, rest are fallbacks
    primary = normalized[0]
    fallbacks = normalized[1:] if len(normalized) > 1 else None
    
    # Build new model config
    if fallbacks:
        new_model = ModelConfig(primary=primary, fallbacks=fallbacks)
    else:
        new_model = ModelConfig(primary=primary)
    
    # Create updated config
    # Build kwargs without 'model' to avoid duplication
    defaults_dict = defaults.model_dump() if hasattr(defaults, "model_dump") else vars(defaults)
    kwargs = {k: v for k, v in defaults_dict.items() if k != 'model'}
    kwargs['model'] = new_model
    
    updated_defaults = AgentDefaults(**kwargs)
    
    # Build kwargs without 'defaults' to avoid duplication
    agents_dict = agents.model_dump() if hasattr(agents, "model_dump") else vars(agents)
    agents_kwargs = {k: v for k, v in agents_dict.items() if k != 'defaults'}
    agents_kwargs['defaults'] = updated_defaults
    
    updated_agents = AgentsConfig(**agents_kwargs)
    
    # Return updated config
    from copy import deepcopy
    updated_cfg = deepcopy(cfg)
    updated_cfg.agents = updated_agents
    return updated_cfg


async def prompt_model_with_fallbacks(
    config: OpenClawConfig,
    prompter_module: Any,
    preferred_provider: str | None = None,
    allow_cross_provider: bool = True,
    max_fallbacks: int = 3,
) -> dict[str, Any]:
    """Interactive model + fallback selection with provider auth check
    
    Flow:
    1. Prompt primary model (using existing prompt_default_model)
    2. Ask "Add fallback models?" (loop up to max_fallbacks)
    3. For each fallback:
       - Prompt model (allow all providers if allow_cross_provider)
       - Check if provider has auth configured
       - If not, offer to configure now
    4. Return {model: "primary/model", fallbacks: ["fb1/model", "fb2/model"]}
    
    Args:
        config: Current configuration
        prompter_module: Prompter module (for UI)
        preferred_provider: Filter primary by provider (e.g., "anthropic")
        allow_cross_provider: Allow fallback models from different providers
        max_fallbacks: Maximum number of fallbacks (default 3)
        
    Returns:
        Dict with 'model' (primary), 'fallbacks' (list), 'config' (updated config)
    """
    from .fallback_provider_config import ensure_fallback_provider_configured
    
    # Step 1: Select primary model
    primary_result = await prompt_default_model(
        config=config,
        allow_keep=False,
        include_manual=True,
        include_vllm=False,
        preferred_provider=preferred_provider,
        message="Select primary model:",
        exclude_models=[],
    )
    
    if not primary_result.get("model"):
        return {}
    
    primary_model = primary_result["model"]
    fallbacks = []
    
    # Step 2: Ask about fallbacks
    try:
        add_fallback = prompter_module.confirm(
            "Add fallback models?",
            default=False
        )
    except Exception:
        add_fb_input = input("\nAdd fallback models? [y/N]: ").strip().lower()
        add_fallback = (add_fb_input == "y")
    
    if not add_fallback:
        return {
            "model": primary_model,
            "fallbacks": [],
            "config": config,
        }
    
    # Step 3: Loop to add fallbacks
    excluded = [primary_model]
    
    while len(fallbacks) < max_fallbacks:
        count_msg = f" (already have {len(fallbacks)})" if fallbacks else ""
        print(f"\nFallback #{len(fallbacks) + 1}{count_msg}:")
        
        # Prompt fallback model
        fallback_result = await prompt_default_model(
            config=config,
            allow_keep=False,
            include_manual=True,
            include_vllm=False,
            preferred_provider=None if allow_cross_provider else preferred_provider,
            message=f"Select fallback model #{len(fallbacks) + 1}:",
            exclude_models=excluded,
        )
        
        if not fallback_result.get("model"):
            # User cancelled or skipped
            break
        
        fallback_model = fallback_result["model"]
        
        # Check if provider is configured
        provider_configured = await ensure_fallback_provider_configured(
            config=config,
            model_id=fallback_model,
            interactive=True,
        )
        
        if provider_configured:
            fallbacks.append(fallback_model)
            excluded.append(fallback_model)
            print(f"✓ Added fallback: {fallback_model}")
        else:
            print(f"⚠️  Skipped fallback: {fallback_model} (provider not configured)")
        
        # Ask about another fallback
        if len(fallbacks) >= max_fallbacks:
            break
        
        try:
            another = prompter_module.confirm(
                "Add another fallback?",
                default=False
            )
        except Exception:
            another_input = input("Add another fallback? [y/N]: ").strip().lower()
            another = (another_input == "y")
        
        if not another:
            break
    
    return {
        "model": primary_model,
        "fallbacks": fallbacks,
        "config": config,
    }


__all__ = [
    "prompt_default_model",
    "prompt_model_allowlist",
    "apply_primary_model",
    "apply_model_fallbacks_from_selection",
    "prompt_model_with_fallbacks",
]
