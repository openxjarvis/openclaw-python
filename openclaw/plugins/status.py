"""Plugin status reporting

Mirrors openclaw/src/plugins/status.ts
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plugin_manager import load_gateway_plugins
from .registry import PluginRegistry


@dataclass
class PluginStatusReport:
    """Plugin status report
    
    Contains loaded plugin registry and workspace information.
    """
    
    registry: PluginRegistry
    """Plugin registry with loaded plugins"""
    
    workspace_dir: Path | None = None
    """Workspace directory"""
    
    def dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "registry": self.registry.__dict__ if hasattr(self.registry, "__dict__") else {},
            "workspace_dir": str(self.workspace_dir) if self.workspace_dir else None,
        }


async def build_plugin_status_report(
    config: dict[str, Any] | None = None,
    workspace_dir: Path | str | None = None,
) -> PluginStatusReport:
    """Build plugin status report.
    
    Loads plugins and creates a status report.
    
    Args:
        config: Optional OpenClaw configuration
        workspace_dir: Optional workspace directory
        
    Returns:
        PluginStatusReport with loaded plugins
    """
    if config is None:
        from openclaw.config.loader import load_config
        config_obj = load_config()
        config = config_obj.model_dump() if hasattr(config_obj, "model_dump") else {}
    
    if workspace_dir is None:
        # Try to resolve from config
        try:
            from openclaw.agents.context import resolve_agent_workspace_dir, resolve_default_agent_id
            agent_id = resolve_default_agent_id(config)
            workspace_dir = resolve_agent_workspace_dir(agent_id, config)
        except Exception:
            workspace_dir = resolve_state_dir() / "workspace"
    
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    # Load plugins
    registry = await load_gateway_plugins(config, workspace_dir)
    
    return PluginStatusReport(
        registry=registry,
        workspace_dir=workspace_dir,
    )


__all__ = ["PluginStatusReport", "build_plugin_status_report"]
