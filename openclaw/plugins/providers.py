"""Plugin providers resolution

Mirrors openclaw/src/plugins/providers.ts
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .plugin_manager import load_gateway_plugins


async def resolve_plugin_providers(
    config: dict[str, Any] | None = None,
    workspace_dir: Path | str | None = None,
) -> list[Any]:
    """Resolve plugin providers.
    
    Loads plugins and returns provider registrations.
    Used for auth providers (OAuth, API keys, etc.).
    
    Args:
        config: Optional OpenClaw configuration
        workspace_dir: Optional workspace directory
        
    Returns:
        List of ProviderPlugin instances
    """
    if config is None:
        from openclaw.config.loader import load_config
        config_obj = load_config()
        config = config_obj.model_dump() if hasattr(config_obj, "model_dump") else {}
    
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    # Load plugins
    registry = await load_gateway_plugins(config, workspace_dir)
    
    # Return providers
    if hasattr(registry, "providers"):
        return registry.providers
    
    return []


__all__ = ["resolve_plugin_providers"]
