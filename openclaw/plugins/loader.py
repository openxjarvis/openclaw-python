"""Plugin loader and discovery

DEPRECATED: This PluginLoader class is deprecated. 
Use load_gateway_plugins() from openclaw.plugins.plugin_manager instead.

This module is retained for backward compatibility but should not be used in new code.

Migration guide:
    OLD: from openclaw.plugins.loader import PluginLoader
    NEW: from openclaw.plugins.plugin_manager import load_gateway_plugins
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir


import importlib.util
import logging
import sys
import warnings
from pathlib import Path

from .manifest import (
    PluginManifest,
    load_plugin_manifest,
    PluginManifestLoadFail,
    PluginManifestLoadOk,
)
from .types import Plugin, PluginAPI

logger = logging.getLogger(__name__)


class PluginLoader:
    """Loads and manages plugins
    
    DEPRECATED: Use load_gateway_plugins() instead.
    """

    def __init__(self):
        warnings.warn(
            "PluginLoader is deprecated. Use load_gateway_plugins() from "
            "openclaw.plugins.plugin_manager instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.plugins: dict[str, Plugin] = {}
        self._plugin_apis: dict[str, PluginAPI] = {}
        # Tracks manifest / load errors by plugin dir path; mirrors TS plugin diagnostics
        self._errors: dict[str, str] = {}

    def discover_plugins(self) -> list[Path]:
        """Discover plugins from standard locations"""
        plugin_dirs = []

        def _has_manifest(item: Path) -> bool:
            return (item / "openclaw.plugin.json").exists() or (item / "plugin.json").exists()

        # Extensions directory (bundled plugins)
        bundled_dir = Path(__file__).parent.parent.parent / "extensions"
        if bundled_dir.exists():
            for item in bundled_dir.iterdir():
                if item.is_dir() and _has_manifest(item):
                    plugin_dirs.append(item)

        # User plugins
        user_dir = resolve_state_dir() / "extensions"
        if user_dir.exists():
            for item in user_dir.iterdir():
                if item.is_dir() and _has_manifest(item):
                    plugin_dirs.append(item)

        logger.info(f"Discovered {len(plugin_dirs)} plugins")
        return plugin_dirs

    def load_plugin(self, plugin_dir: Path) -> Plugin | None:
        """Load a single plugin.

        Uses load_plugin_manifest() from manifest.py which enforces the 'id'
        and 'configSchema' required fields.  Broken manifests are logged as
        errors and tracked in self._errors (mirrors TS plugin validation).
        """
        try:
            result = load_plugin_manifest(str(plugin_dir))
            if isinstance(result, PluginManifestLoadFail):
                logger.error(
                    f"Plugin manifest error in {plugin_dir}: {result.error}"
                )
                self._errors[str(plugin_dir)] = result.error
                return None

            manifest = result.manifest

            # Create plugin instance
            plugin = Plugin(manifest, str(plugin_dir))

            # Load plugin module — convention: plugin.py in plugin root
            # (PluginManifest has no 'main' field; Python plugins use plugin.py)
            for candidate_name in ("plugin.py", "__init__.py"):
                main_file = plugin_dir / candidate_name
                if main_file.exists():
                    self._load_plugin_module(plugin, main_file)
                    break

            self.plugins[manifest.id] = plugin
            logger.info(f"Loaded plugin: {manifest.id} v{manifest.version}")

            return plugin

        except Exception as e:
            logger.error(f"Failed to load plugin from {plugin_dir}: {e}", exc_info=True)
            return None

    def _load_plugin_module(self, plugin: Plugin, module_file: Path) -> None:
        """Load plugin Python module"""
        try:
            # Create module spec
            spec = importlib.util.spec_from_file_location(
                f"openclaw_plugin_{plugin.manifest.id}", module_file
            )

            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)

                # Look for register() or activate() function
                if hasattr(module, "register"):
                    api = PluginAPI(plugin.manifest.id)
                    self._plugin_apis[plugin.manifest.id] = api
                    module.register(api)
                elif hasattr(module, "activate"):
                    api = PluginAPI(plugin.manifest.id)
                    self._plugin_apis[plugin.manifest.id] = api
                    module.activate(api)

        except Exception as e:
            logger.error(f"Failed to load plugin module {module_file}: {e}", exc_info=True)

    def load_all_plugins(self) -> dict[str, Plugin]:
        """Discover and load all plugins"""
        plugin_dirs = self.discover_plugins()

        for plugin_dir in plugin_dirs:
            self.load_plugin(plugin_dir)

        return self.plugins

    def get_plugin(self, plugin_id: str) -> Plugin | None:
        """Get a loaded plugin"""
        return self.plugins.get(plugin_id)

    def get_plugin_api(self, plugin_id: str) -> PluginAPI | None:
        """Get plugin API instance"""
        return self._plugin_apis.get(plugin_id)


# Global plugin loader
_global_loader = PluginLoader()


def get_plugin_loader() -> PluginLoader:
    """Get global plugin loader"""
    return _global_loader
