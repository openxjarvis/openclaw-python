"""Bundled plugin sources

Mirrors openclaw/src/plugins/bundled-sources.ts
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .bundled_dir import resolve_bundled_plugins_dir
from .discovery import discover_openclaw_plugins
from .manifest import load_plugin_manifest

logger = logging.getLogger(__name__)


@dataclass
class BundledPluginSource:
    """Bundled plugin source information"""
    
    plugin_id: str
    """Plugin identifier"""
    
    local_path: Path
    """Local filesystem path to plugin"""
    
    npm_spec: str | None = None
    """NPM package spec (e.g., '@openclaw/plugin-name@1.0.0')"""


BundledPluginLookupKind = Literal["npmSpec", "pluginId"]


@dataclass
class BundledPluginLookup:
    """Bundled plugin lookup criteria"""
    
    kind: BundledPluginLookupKind
    """Lookup type: 'npmSpec' or 'pluginId'"""
    
    value: str
    """Lookup value"""


def _extract_npm_spec_from_manifest(plugin_path: Path) -> str | None:
    """Extract NPM spec from plugin manifest or package.json.
    
    Args:
        plugin_path: Path to plugin directory
        
    Returns:
        NPM spec string (e.g., '@openclaw/plugin-name@1.0.0') or None
    """
    # Try to load plugin manifest
    manifest_result = load_plugin_manifest(str(plugin_path))
    if manifest_result and hasattr(manifest_result, "manifest"):
        manifest = manifest_result.manifest
        # Check if manifest has npm field or package info
        if hasattr(manifest, "npm"):
            return manifest.npm
    
    # Try package.json if it exists
    package_json = plugin_path / "package.json"
    if package_json.exists():
        try:
            with open(package_json) as f:
                pkg = json.load(f)
                name = pkg.get("name")
                version = pkg.get("version")
                if name and version:
                    return f"{name}@{version}"
                if name:
                    return name
        except Exception as e:
            logger.debug(f"Failed to read package.json for {plugin_path}: {e}")
    
    return None


def resolve_bundled_plugin_sources(
    bundled_dir: Path | None = None,
) -> dict[str, BundledPluginSource]:
    """Resolve bundled plugin sources.
    
    Discovers bundled plugins and builds a map of plugin ID to source info.
    
    Args:
        bundled_dir: Optional bundled plugins directory (defaults to auto-resolved)
        
    Returns:
        Dict mapping plugin ID to BundledPluginSource
    """
    if bundled_dir is None:
        bundled_dir = resolve_bundled_plugins_dir()
    
    if bundled_dir is None or not bundled_dir.exists():
        return {}
    
    sources: dict[str, BundledPluginSource] = {}
    
    # Discover bundled plugins
    plugin_paths = discover_openclaw_plugins(str(bundled_dir), origin="bundled")
    
    for plugin_path_str, _, origin in plugin_paths:
        if origin != "bundled":
            continue
        
        plugin_path = Path(plugin_path_str)
        plugin_id = plugin_path.name
        
        # Extract NPM spec if available
        npm_spec = _extract_npm_spec_from_manifest(plugin_path)
        
        sources[plugin_id] = BundledPluginSource(
            plugin_id=plugin_id,
            local_path=plugin_path,
            npm_spec=npm_spec,
        )
    
    return sources


def find_bundled_plugin_source(
    lookup: BundledPluginLookup,
    bundled_dir: Path | None = None,
) -> BundledPluginSource | None:
    """Find bundled plugin source by lookup criteria.
    
    Args:
        lookup: Lookup criteria (plugin ID or NPM spec)
        bundled_dir: Optional bundled plugins directory
        
    Returns:
        BundledPluginSource if found, None otherwise
    """
    sources = resolve_bundled_plugin_sources(bundled_dir)
    
    if lookup.kind == "pluginId":
        return sources.get(lookup.value)
    
    elif lookup.kind == "npmSpec":
        # Search by NPM spec
        for source in sources.values():
            if source.npm_spec == lookup.value:
                return source
            # Also try matching package name without version
            if source.npm_spec and source.npm_spec.split("@")[0] == lookup.value.split("@")[0]:
                return source
    
    return None


__all__ = [
    "BundledPluginSource",
    "BundledPluginLookup",
    "BundledPluginLookupKind",
    "resolve_bundled_plugin_sources",
    "find_bundled_plugin_source",
]
