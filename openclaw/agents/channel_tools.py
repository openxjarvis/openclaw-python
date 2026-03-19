"""Channel tools utilities

Mirrors openclaw/src/agents/channel-tools.ts

Provides functions to query channel capabilities, supported actions,
and channel-specific agent tools.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openclaw.config.schema import OpenClawConfig

logger = logging.getLogger(__name__)


def list_channel_supported_actions(
    config: "OpenClawConfig | None" = None,
    channel: str | None = None,
) -> list[str]:
    """Get the list of supported message actions for a specific channel.
    
    Mirrors TypeScript listChannelSupportedActions()
    
    Args:
        config: OpenClaw configuration
        channel: Channel ID
        
    Returns:
        List of action names (e.g., ["edit", "delete", "react"])
    """
    if not channel:
        return []
    
    # Import here to avoid circular dependency
    try:
        from openclaw.channels.plugins import get_channel_plugin
        
        plugin = get_channel_plugin(channel)
        if not plugin or not hasattr(plugin, "list_actions"):
            return []
        
        # Call plugin's list_actions method
        try:
            actions = plugin.list_actions(config=config)
            return list(actions) if actions else []
        except Exception as e:
            logger.error(f"[channel-tools] {channel}.list_actions failed: {e}")
            return []
    except ImportError:
        return []


def list_all_channel_supported_actions(
    config: "OpenClawConfig | None" = None,
) -> list[str]:
    """Get the list of all supported message actions across all configured channels.
    
    Mirrors TypeScript listAllChannelSupportedActions()
    
    Args:
        config: OpenClaw configuration
        
    Returns:
        Unique list of all action names
    """
    actions = set()
    
    # Import here to avoid circular dependency
    try:
        from openclaw.channels.plugins import list_channel_plugins
        
        for plugin in list_channel_plugins():
            if not hasattr(plugin, "list_actions"):
                continue
            
            try:
                channel_actions = plugin.list_actions(config=config)
                if channel_actions:
                    actions.update(channel_actions)
            except Exception as e:
                logger.error(f"[channel-tools] {plugin.id}.list_actions failed: {e}")
    except ImportError:
        pass
    
    return list(actions)


def list_channel_agent_tools(
    config: "OpenClawConfig | None" = None,
) -> list[dict[str, Any]]:
    """List channel-specific agent tools (e.g., login, send message).
    
    Mirrors TypeScript listChannelAgentTools()
    
    Args:
        config: OpenClaw configuration
        
    Returns:
        List of tool definitions
    """
    tools = []
    
    # Import here to avoid circular dependency
    try:
        from openclaw.channels.plugins import list_channel_plugins
        
        for plugin in list_channel_plugins():
            if not hasattr(plugin, "get_agent_tools"):
                continue
            
            try:
                plugin_tools = plugin.get_agent_tools(config=config)
                if plugin_tools:
                    if isinstance(plugin_tools, list):
                        tools.extend(plugin_tools)
                    else:
                        tools.append(plugin_tools)
            except Exception as e:
                logger.error(f"[channel-tools] {plugin.id}.get_agent_tools failed: {e}")
    except ImportError:
        pass
    
    return tools


def resolve_channel_message_tool_hints(
    config: "OpenClawConfig | None" = None,
    channel: str | None = None,
    account_id: str | None = None,
) -> list[str]:
    """Resolve tool hints for channel message operations.
    
    Mirrors TypeScript resolveChannelMessageToolHints()
    
    These hints help guide the agent on what message operations are available
    in the current channel context.
    
    Args:
        config: OpenClaw configuration
        channel: Channel ID
        account_id: Account/user ID
        
    Returns:
        List of tool hint strings
    """
    if not channel:
        return []
    
    # Import here to avoid circular dependency
    try:
        from openclaw.channels.registry import normalize_channel_id
        from openclaw.channels.dock import get_channel_dock
        
        channel_id = normalize_channel_id(channel)
        if not channel_id:
            return []
        
        dock = get_channel_dock(channel_id)
        if not dock or not hasattr(dock, "get_message_tool_hints"):
            return []
        
        try:
            hints = dock.get_message_tool_hints(
                config=config,
                account_id=account_id,
            )
            return [h.strip() for h in hints if h and h.strip()] if hints else []
        except Exception as e:
            logger.error(f"[channel-tools] {channel_id}.get_message_tool_hints failed: {e}")
            return []
    except ImportError:
        return []


__all__ = [
    "list_channel_supported_actions",
    "list_all_channel_supported_actions",
    "list_channel_agent_tools",
    "resolve_channel_message_tool_hints",
]
