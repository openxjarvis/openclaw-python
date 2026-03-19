"""Plugin slots management

Mirrors openclaw/src/plugins/slots.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


PluginSlotKey = Literal["memory"]
"""Plugin slot keys (currently only 'memory' is supported)"""


@dataclass
class SlotSelectionResult:
    """Result of applying slot selection"""
    
    config: dict[str, Any]
    """Updated configuration"""
    
    warnings: list[str] = field(default_factory=list)
    """Warnings generated during selection"""
    
    changed: bool = False
    """Whether configuration was changed"""


def slot_key_for_plugin_kind(kind: str | None) -> PluginSlotKey | None:
    """Get slot key for plugin kind.
    
    Args:
        kind: Plugin kind (e.g., 'memory-backend')
        
    Returns:
        Slot key or None if not a slotted kind
    """
    if kind == "memory-backend":
        return "memory"
    return None


def default_slot_id_for_key(slot_key: PluginSlotKey) -> str:
    """Get default slot ID for slot key.
    
    Args:
        slot_key: Plugin slot key
        
    Returns:
        Default slot ID
    """
    if slot_key == "memory":
        return "memory-core"
    return slot_key


def apply_exclusive_slot_selection(
    config: dict[str, Any],
    plugin_id: str,
    slot_key: PluginSlotKey,
    slot_id: str | None = None,
) -> SlotSelectionResult:
    """Apply exclusive slot selection.
    
    When a plugin is selected for an exclusive slot:
    1. Disable all other plugins of the same kind
    2. Enable the selected plugin
    3. Update slots configuration
    
    Args:
        config: OpenClaw configuration dict
        plugin_id: Plugin to select
        slot_key: Slot key (e.g., 'memory')
        slot_id: Optional slot ID (defaults to default for key)
        
    Returns:
        SlotSelectionResult with updated config and warnings
    """
    result = SlotSelectionResult(config=config)
    
    if slot_id is None:
        slot_id = default_slot_id_for_key(slot_key)
    
    # Ensure plugins structure exists
    if "plugins" not in config:
        config["plugins"] = {}
    if "entries" not in config["plugins"]:
        config["plugins"]["entries"] = {}
    if "slots" not in config["plugins"]:
        config["plugins"]["slots"] = {}
    
    plugins_config = config["plugins"]
    entries = plugins_config["entries"]
    slots = plugins_config["slots"]
    
    # Get the kind for this plugin
    plugin_kind = None
    if slot_key == "memory":
        plugin_kind = "memory-backend"
    
    if not plugin_kind:
        result.warnings.append(f"Unknown slot key: {slot_key}")
        return result
    
    # Disable all plugins of the same kind
    for pid, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        
        entry_kind = entry.get("kind")
        if entry_kind == plugin_kind and pid != plugin_id:
            if entry.get("enabled", True):
                entry["enabled"] = False
                result.changed = True
                logger.info(f"Disabled plugin '{pid}' (conflicting {plugin_kind})")
    
    # Enable the selected plugin
    if plugin_id not in entries:
        entries[plugin_id] = {}
    
    plugin_entry = entries[plugin_id]
    if isinstance(plugin_entry, dict):
        if not plugin_entry.get("enabled", True):
            plugin_entry["enabled"] = True
            result.changed = True
        
        # Update slots
        if slot_key not in slots or slots[slot_key] != plugin_id:
            slots[slot_key] = plugin_id
            result.changed = True
            logger.info(f"Set slot '{slot_key}' to plugin '{plugin_id}'")
    
    return result


__all__ = [
    "PluginSlotKey",
    "SlotSelectionResult",
    "slot_key_for_plugin_kind",
    "default_slot_id_for_key",
    "apply_exclusive_slot_selection",
]
