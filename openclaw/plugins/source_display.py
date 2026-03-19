"""Plugin source display utilities

Mirrors openclaw/src/plugins/source-display.ts
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PluginSourceRoots:
    """Plugin source root directories"""
    
    stock: Path | None = None
    """Stock (bundled) plugins directory"""
    
    global_dir: Path | None = None
    """Global plugins directory (~/.openclaw/plugins)"""
    
    workspace: Path | None = None
    """Workspace plugins directory"""


def resolve_plugin_source_roots(
    workspace_dir: Path | str | None = None,
) -> PluginSourceRoots:
    """Resolve plugin source root directories.
    
    Args:
        workspace_dir: Optional workspace directory
        
    Returns:
        PluginSourceRoots with resolved directories
    """
    from .bundled_dir import resolve_bundled_plugins_dir
    
    roots = PluginSourceRoots()
    
    # Stock (bundled) plugins
    roots.stock = resolve_bundled_plugins_dir()
    
    # Global plugins
    global_dir = resolve_state_dir() / "plugins"
    if global_dir.exists():
        roots.global_dir = global_dir
    
    # Workspace plugins
    if workspace_dir:
        if isinstance(workspace_dir, str):
            workspace_dir = Path(workspace_dir)
        ws_plugins = workspace_dir / ".openclaw" / "plugins"
        if ws_plugins.exists():
            roots.workspace = ws_plugins
    
    return roots


def try_relative(path: Path, root: Path | None) -> Path:
    """Try to make path relative to root.
    
    Args:
        path: Path to make relative
        root: Root directory
        
    Returns:
        Relative path if possible, otherwise original path
    """
    if root is None:
        return path
    
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def format_plugin_source_for_table(
    plugin: Any,
    roots: PluginSourceRoots,
) -> str:
    """Format plugin source for display in tables.
    
    Args:
        plugin: Plugin record with 'source' and 'origin' fields
        roots: Plugin source roots
        
    Returns:
        Formatted source string (e.g., 'stock:extensions/foo', 'workspace:bar')
    """
    source = getattr(plugin, "source", None)
    origin = getattr(plugin, "origin", None)
    
    if not source:
        return "unknown"
    
    source_path = Path(source)
    
    # Try to format based on origin and roots
    if origin == "bundled" and roots.stock:
        rel = try_relative(source_path, roots.stock)
        if rel != source_path:
            return f"stock:{rel}"
    
    elif origin == "global" and roots.global_dir:
        rel = try_relative(source_path, roots.global_dir)
        if rel != source_path:
            return f"global:{rel}"
    
    elif origin == "workspace" and roots.workspace:
        rel = try_relative(source_path, roots.workspace)
        if rel != source_path:
            return f"workspace:{rel}"
    
    # Fallback: show full path or relative to cwd
    try:
        rel = source_path.relative_to(Path.cwd())
        return str(rel)
    except ValueError:
        return str(source_path)


__all__ = [
    "PluginSourceRoots",
    "resolve_plugin_source_roots",
    "try_relative",
    "format_plugin_source_for_table",
]
