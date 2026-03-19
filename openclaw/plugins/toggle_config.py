"""Plugin enable/disable configuration

Mirrors openclaw/src/plugins/toggle-config.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def normalize_chat_channel_id(channel_id: str) -> str:
    """Normalize chat channel ID.
    
    Converts plugin channel IDs to config channel IDs (e.g., 'telegram-channel' -> 'telegram').
    
    Args:
        channel_id: Channel ID to normalize
        
    Returns:
        Normalized channel ID
    """
    # Remove '-channel' suffix if present
    if channel_id.endswith("-channel"):
        return channel_id[: -len("-channel")]
    return channel_id


def set_plugin_enabled_in_config(
    config: dict[str, Any],
    plugin_id: str,
    enabled: bool,
) -> dict[str, Any]:
    """Set plugin enabled state in configuration.
    
    Updates plugins.entries[pluginId].enabled and, for built-in channels,
    also updates channels[channelId].enabled.
    
    Args:
        config: OpenClaw configuration dict
        plugin_id: Plugin identifier
        enabled: Whether plugin should be enabled
        
    Returns:
        Updated configuration dict
    """
    # Ensure plugins structure exists
    if "plugins" not in config:
        config["plugins"] = {}
    if "entries" not in config["plugins"]:
        config["plugins"]["entries"] = {}
    
    # Update plugin entry
    entries = config["plugins"]["entries"]
    if plugin_id not in entries:
        entries[plugin_id] = {}
    
    plugin_entry = entries[plugin_id]
    if isinstance(plugin_entry, dict):
        plugin_entry["enabled"] = enabled
        logger.info(f"Set plugin '{plugin_id}' enabled={enabled}")
    
    # Check if this is a built-in channel plugin
    # Built-in channel plugin IDs typically end with '-channel'
    if plugin_id.endswith("-channel"):
        channel_id = normalize_chat_channel_id(plugin_id)
        
        # Update channel config if it exists
        if "channels" not in config:
            config["channels"] = {}
        
        channels = config["channels"]
        if channel_id in channels:
            if isinstance(channels[channel_id], dict):
                channels[channel_id]["enabled"] = enabled
                logger.info(f"Set channel '{channel_id}' enabled={enabled}")
    
    return config


__all__ = ["set_plugin_enabled_in_config", "normalize_chat_channel_id"]
