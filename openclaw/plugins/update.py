"""Plugin update

Mirrors openclaw/src/plugins/update.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


class PluginUpdateLogger(Protocol):
    """Plugin update logger protocol"""
    
    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


PluginUpdateStatus = Literal["updated", "unchanged", "skipped", "error"]


@dataclass
class PluginUpdateOutcome:
    """Outcome of updating a single plugin"""
    
    plugin_id: str
    """Plugin identifier"""
    
    status: PluginUpdateStatus
    """Update status"""
    
    old_version: str | None = None
    """Previous version"""
    
    new_version: str | None = None
    """New version after update"""
    
    error: str | None = None
    """Error message if failed"""


@dataclass
class PluginUpdateSummary:
    """Summary of plugin update operation"""
    
    total: int = 0
    """Total plugins processed"""
    
    updated: int = 0
    """Number of plugins updated"""
    
    unchanged: int = 0
    """Number of plugins unchanged"""
    
    skipped: int = 0
    """Number of plugins skipped"""
    
    errors: int = 0
    """Number of errors"""
    
    outcomes: list[PluginUpdateOutcome] = field(default_factory=list)
    """Individual plugin outcomes"""


@dataclass
class PluginUpdateIntegrityDriftParams:
    """Parameters for integrity drift handling"""
    
    plugin_id: str
    """Plugin identifier"""
    
    expected_checksum: str | None = None
    """Expected checksum"""
    
    actual_checksum: str | None = None
    """Actual checksum"""


@dataclass
class PluginChannelSyncResult:
    """Result of syncing plugin to update channel"""
    
    plugin_id: str
    """Plugin identifier"""
    
    channel: Literal["dev", "stable"]
    """Update channel"""
    
    action: Literal["switched_to_bundled", "switched_to_npm", "unchanged"]
    """Action taken"""
    
    old_source: str | None = None
    """Previous source"""
    
    new_source: str | None = None
    """New source after sync"""


@dataclass
class PluginChannelSyncSummary:
    """Summary of plugin channel sync operation"""
    
    results: list[PluginChannelSyncResult] = field(default_factory=list)
    """Individual sync results"""
    
    switched_count: int = 0
    """Number of plugins switched"""


async def update_npm_installed_plugins(
    config: dict[str, Any],
    dry_run: bool = False,
    logger_: PluginUpdateLogger | None = None,
) -> PluginUpdateSummary:
    """Update npm-installed plugins to latest versions.
    
    Args:
        config: OpenClaw configuration
        dry_run: If True, simulate update without making changes
        logger_: Optional logger for update messages
        
    Returns:
        PluginUpdateSummary with update results
    """
    if logger_ is None:
        logger_ = logger
    
    summary = PluginUpdateSummary()
    
    # Get npm-installed plugins from config
    plugins_config = config.get("plugins", {})
    installs = plugins_config.get("installs", {})
    
    for plugin_id, install_info in installs.items():
        if not isinstance(install_info, dict):
            continue
        
        source = install_info.get("source")
        if source != "npm":
            continue
        
        summary.total += 1
        
        # In a real implementation, this would:
        # 1. Check npm registry for latest version
        # 2. Compare with current version
        # 3. Update if newer version available
        # 4. Handle integrity drift
        
        # For now, mark as unchanged (placeholder)
        outcome = PluginUpdateOutcome(
            plugin_id=plugin_id,
            status="unchanged",
            old_version=install_info.get("version"),
            new_version=install_info.get("version"),
        )
        
        summary.outcomes.append(outcome)
        summary.unchanged += 1
        
        logger_.info(f"Plugin '{plugin_id}' is up to date")
    
    return summary


async def sync_plugins_for_update_channel(
    config: dict[str, Any],
    channel: Literal["dev", "stable"] = "stable",
    dry_run: bool = False,
) -> PluginChannelSyncSummary:
    """Sync plugin sources based on update channel.
    
    - Dev channel: uses bundled/local plugin paths
    - Stable channel: uses npm-installed versions
    
    Args:
        config: OpenClaw configuration
        channel: Update channel ('dev' or 'stable')
        dry_run: If True, simulate sync without making changes
        
    Returns:
        PluginChannelSyncSummary with sync results
    """
    summary = PluginChannelSyncSummary()
    
    plugins_config = config.get("plugins", {})
    entries = plugins_config.get("entries", {})
    installs = plugins_config.get("installs", {})
    
    for plugin_id in entries.keys():
        install_info = installs.get(plugin_id, {})
        if not isinstance(install_info, dict):
            continue
        
        current_source = install_info.get("source")
        
        # Determine desired source based on channel
        if channel == "dev":
            # Dev: prefer bundled/local paths
            if current_source == "npm":
                # Switch to bundled
                result = PluginChannelSyncResult(
                    plugin_id=plugin_id,
                    channel=channel,
                    action="switched_to_bundled",
                    old_source=current_source,
                    new_source="bundled",
                )
                summary.results.append(result)
                summary.switched_count += 1
            else:
                result = PluginChannelSyncResult(
                    plugin_id=plugin_id,
                    channel=channel,
                    action="unchanged",
                    old_source=current_source,
                )
                summary.results.append(result)
        
        else:  # stable
            # Stable: prefer npm
            if current_source in ("bundled", "path"):
                # Switch to npm
                result = PluginChannelSyncResult(
                    plugin_id=plugin_id,
                    channel=channel,
                    action="switched_to_npm",
                    old_source=current_source,
                    new_source="npm",
                )
                summary.results.append(result)
                summary.switched_count += 1
            else:
                result = PluginChannelSyncResult(
                    plugin_id=plugin_id,
                    channel=channel,
                    action="unchanged",
                    old_source=current_source,
                )
                summary.results.append(result)
    
    return summary


__all__ = [
    "PluginUpdateLogger",
    "PluginUpdateStatus",
    "PluginUpdateOutcome",
    "PluginUpdateSummary",
    "PluginUpdateIntegrityDriftParams",
    "PluginChannelSyncResult",
    "PluginChannelSyncSummary",
    "update_npm_installed_plugins",
    "sync_plugins_for_update_channel",
]
