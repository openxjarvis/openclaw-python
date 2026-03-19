"""Plugin uninstall

Mirrors openclaw/src/plugins/uninstall.ts
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UninstallActions:
    """Actions taken during plugin uninstall"""
    
    entry: bool = False
    """Whether plugin entry was removed from config"""
    
    install: bool = False
    """Whether install record was removed"""
    
    allowlist: bool = False
    """Whether allowlist entry was removed"""
    
    load_path: bool = False
    """Whether load path was removed"""
    
    memory_slot: bool = False
    """Whether memory slot was reset"""
    
    directory: bool = False
    """Whether install directory was deleted"""


@dataclass
class UninstallPluginResult:
    """Result of plugin uninstall operation"""
    
    ok: bool
    """Whether uninstall succeeded"""
    
    config: dict[str, Any] | None = None
    """Updated configuration"""
    
    actions: UninstallActions | None = None
    """Actions taken during uninstall"""
    
    error: str | None = None
    """Error message if failed"""


def resolve_uninstall_directory_target(
    plugin_id: str,
    config: dict[str, Any],
) -> Path | None:
    """Resolve plugin install directory to delete.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw configuration
        
    Returns:
        Path to install directory, or None if not found
    """
    # Check installs record
    plugins_config = config.get("plugins", {})
    installs = plugins_config.get("installs", {})
    
    if plugin_id in installs:
        install_info = installs[plugin_id]
        if isinstance(install_info, dict):
            install_path = install_info.get("installPath")
            if install_path:
                return Path(install_path)
    
    # Fallback: check global plugins directory
    global_dir = resolve_state_dir() / "plugins" / plugin_id
    if global_dir.exists():
        return global_dir
    
    return None


def remove_plugin_from_config(
    config: dict[str, Any],
    plugin_id: str,
) -> tuple[dict[str, Any], UninstallActions]:
    """Remove plugin from configuration.
    
    Removes plugin from:
    - plugins.entries
    - plugins.installs
    - plugins.allowed (if present)
    - plugins.load.paths (if present)
    - plugins.slots (if assigned to memory slot)
    
    Args:
        config: OpenClaw configuration
        plugin_id: Plugin identifier
        
    Returns:
        Tuple of (updated_config, actions)
    """
    actions = UninstallActions()
    
    if "plugins" not in config:
        return config, actions
    
    plugins_config = config["plugins"]
    
    # Remove entry
    if "entries" in plugins_config and plugin_id in plugins_config["entries"]:
        del plugins_config["entries"][plugin_id]
        actions.entry = True
        logger.info(f"Removed plugin '{plugin_id}' from config entries")
    
    # Remove install record
    if "installs" in plugins_config and plugin_id in plugins_config["installs"]:
        del plugins_config["installs"][plugin_id]
        actions.install = True
        logger.info(f"Removed plugin '{plugin_id}' install record")
    
    # Remove from allowlist
    if "allowed" in plugins_config:
        allowed = plugins_config["allowed"]
        if isinstance(allowed, list) and plugin_id in allowed:
            plugins_config["allowed"] = [p for p in allowed if p != plugin_id]
            actions.allowlist = True
            logger.info(f"Removed plugin '{plugin_id}' from allowlist")
    
    # Remove from load paths
    if "load" in plugins_config and "paths" in plugins_config["load"]:
        load_paths = plugins_config["load"]["paths"]
        if isinstance(load_paths, list):
            # Filter out paths containing the plugin ID
            original_len = len(load_paths)
            plugins_config["load"]["paths"] = [
                p for p in load_paths
                if plugin_id not in str(p)
            ]
            if len(plugins_config["load"]["paths"]) < original_len:
                actions.load_path = True
                logger.info(f"Removed plugin '{plugin_id}' from load paths")
    
    # Reset memory slot if assigned
    if "slots" in plugins_config:
        slots = plugins_config["slots"]
        if isinstance(slots, dict):
            for slot_key, assigned_plugin in slots.items():
                if assigned_plugin == plugin_id:
                    del slots[slot_key]
                    actions.memory_slot = True
                    logger.info(f"Reset slot '{slot_key}' (was assigned to '{plugin_id}')")
    
    return config, actions


async def uninstall_plugin(
    plugin_id: str,
    config: dict[str, Any],
    delete_files: bool = True,
) -> UninstallPluginResult:
    """Uninstall a plugin.
    
    Args:
        plugin_id: Plugin identifier
        config: OpenClaw configuration
        delete_files: Whether to delete installed files
        
    Returns:
        UninstallPluginResult with status and updated config
    """
    try:
        # Remove from config
        updated_config, actions = remove_plugin_from_config(config, plugin_id)
        
        # Delete install directory if requested
        if delete_files:
            install_dir = resolve_uninstall_directory_target(plugin_id, config)
            if install_dir and install_dir.exists():
                # Check if it's a symlink (don't delete the target)
                if install_dir.is_symlink():
                    install_dir.unlink()
                    actions.directory = True
                    logger.info(f"Removed symlink for plugin '{plugin_id}'")
                elif install_dir.is_dir():
                    shutil.rmtree(install_dir)
                    actions.directory = True
                    logger.info(f"Deleted plugin '{plugin_id}' directory: {install_dir}")
        
        return UninstallPluginResult(
            ok=True,
            config=updated_config,
            actions=actions,
        )
    
    except Exception as e:
        logger.error(f"Failed to uninstall plugin '{plugin_id}': {e}")
        return UninstallPluginResult(
            ok=False,
            error=str(e),
        )


__all__ = [
    "UninstallActions",
    "UninstallPluginResult",
    "resolve_uninstall_directory_target",
    "remove_plugin_from_config",
    "uninstall_plugin",
]
