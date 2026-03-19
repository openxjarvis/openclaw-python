"""Plugin enable/disable commands — mirrors src/plugins enable functionality"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def enable_plugin(plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    Enable a plugin in the configuration.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw config dict
    
    Returns:
        Updated config dict
    """
    logger.info(f"Enabling plugin: {plugin_id}")
    
    # Ensure plugins section exists
    if "plugins" not in config:
        config["plugins"] = {}
    
    # Enable plugin
    if plugin_id in config["plugins"]:
        if isinstance(config["plugins"][plugin_id], dict):
            config["plugins"][plugin_id]["enabled"] = True
        else:
            # Convert to dict format
            config["plugins"][plugin_id] = {"enabled": True}
    else:
        config["plugins"][plugin_id] = {"enabled": True}
    
    return config


def disable_plugin(plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    Disable a plugin in the configuration.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw config dict
    
    Returns:
        Updated config dict
    """
    logger.info(f"Disabling plugin: {plugin_id}")
    
    # Ensure plugins section exists
    if "plugins" not in config:
        config["plugins"] = {}
    
    # Disable plugin
    if plugin_id in config["plugins"]:
        if isinstance(config["plugins"][plugin_id], dict):
            config["plugins"][plugin_id]["enabled"] = False
        else:
            config["plugins"][plugin_id] = {"enabled": False}
    else:
        config["plugins"][plugin_id] = {"enabled": False}
    
    return config


def toggle_plugin(plugin_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    Toggle a plugin's enabled state.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw config dict
    
    Returns:
        Updated config dict
    """
    logger.info(f"Toggling plugin: {plugin_id}")
    
    # Check current state
    current_enabled = is_plugin_enabled(plugin_id, config)
    
    # Toggle
    if current_enabled:
        return disable_plugin(plugin_id, config)
    else:
        return enable_plugin(plugin_id, config)


def is_plugin_enabled(plugin_id: str, config: dict[str, Any]) -> bool:
    """
    Check if a plugin is enabled.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw config dict
    
    Returns:
        True if enabled, False otherwise
    """
    if "plugins" not in config:
        return False
    
    plugin_config = config["plugins"].get(plugin_id)
    
    if plugin_config is None:
        return False
    
    if isinstance(plugin_config, dict):
        return plugin_config.get("enabled", True)  # Default to enabled
    
    # If it's just a truthy value, consider it enabled
    return bool(plugin_config)


def list_plugin_status(config: dict[str, Any]) -> dict[str, bool]:
    """
    List the enabled/disabled status of all plugins.
    
    Args:
        config: OpenClaw config dict
    
    Returns:
        Dict mapping plugin_id to enabled status
    """
    if "plugins" not in config:
        return {}
    
    result = {}
    for plugin_id, plugin_config in config["plugins"].items():
        result[plugin_id] = is_plugin_enabled(plugin_id, config)
    
    return result
