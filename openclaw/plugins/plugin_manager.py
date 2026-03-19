"""Plugin system for extensibility"""

import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
from datetime import datetime, UTC
from typing import Any

from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


class Plugin:
    """Base class for plugins"""
    
    name: str = "unnamed"
    version: str = "0.0.0"
    description: str = ""
    
    async def initialize(self, config: dict[str, Any]) -> None:
        """Initialize plugin with configuration"""
        pass
    
    async def shutdown(self) -> None:
        """Shutdown plugin"""
        pass


class PluginManager:
    """
    Plugin manager for loading and managing plugins.
    
    Plugins can provide:
    - Custom tools
    - Custom channels
    - Custom skills
    - Custom hooks
    """
    
    def __init__(self, plugin_dirs: list[Path] | None = None):
        self.plugins: dict[str, Plugin] = {}
        self.plugin_dirs = plugin_dirs or [
            resolve_state_dir() / "plugins",
        ]
        for d in self.plugin_dirs:
            d.mkdir(parents=True, exist_ok=True)
        self._installs_path = self.plugin_dirs[0] / ".installs.json"
        self.install_records: dict[str, dict[str, Any]] = self._load_installs()

    def _load_installs(self) -> dict[str, dict[str, Any]]:
        if not self._installs_path.exists():
            return {}
        try:
            data = json.loads(self._installs_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_installs(self) -> None:
        try:
            self._installs_path.write_text(
                json.dumps(self.install_records, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("Failed to persist plugin install records", exc_info=True)

    def install_from_path(
        self,
        plugin_path: str,
        *,
        plugin_id: str | None = None,
        link: bool = False,
    ) -> dict[str, Any]:
        """Install plugin source from local path into plugin dir."""
        src = Path(plugin_path).expanduser().resolve()
        if not src.exists():
            raise ValueError(f"Plugin path not found: {src}")
        inferred = plugin_id or (src.stem if src.is_file() else src.name)
        dst_base = self.plugin_dirs[0]
        dst = dst_base / (f"{inferred}.py" if src.is_file() else inferred)
        if dst.exists() or dst.is_symlink():
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if link:
            dst.symlink_to(src, target_is_directory=src.is_dir())
        else:
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        source = "path"
        self.install_records[inferred] = {
            "source": source,
            "linked": bool(link),
            "sourcePath": str(src),
            "installPath": str(dst),
            "installedAt": datetime.now(UTC).isoformat(),
        }
        self._save_installs()
        return {"pluginId": inferred, "installPath": str(dst), "source": source}

    def remove_installed_files(self, plugin_name: str) -> bool:
        """Remove installed plugin files when tracked."""
        rec = self.install_records.get(plugin_name) or {}
        install_path = rec.get("installPath")
        if not install_path:
            return False
        p = Path(install_path)
        if not p.exists() and not p.is_symlink():
            return False
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
        return True
    
    def discover_plugins(self) -> list[str]:
        """
        Discover available plugins in plugin directories.
        
        Returns:
            List of plugin names
        """
        discovered = []
        
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.exists():
                continue
            
            for item in plugin_dir.iterdir():
                if item.is_dir() and (item / "__init__.py").exists():
                    discovered.append(item.name)
                elif item.is_file() and item.suffix == ".py" and item.stem != "__init__":
                    discovered.append(item.stem)
        
        return discovered
    
    async def load_plugin(self, plugin_name: str, config: dict[str, Any] | None = None) -> Plugin:
        """
        Load a plugin by name.
        
        Args:
            plugin_name: Name of plugin to load
            config: Configuration dict for plugin
        
        Returns:
            Loaded plugin instance
        """
        if plugin_name in self.plugins:
            logger.warning(f"Plugin {plugin_name} already loaded")
            return self.plugins[plugin_name]
        
        # Try to import plugin
        for plugin_dir in self.plugin_dirs:
            plugin_path = plugin_dir / plugin_name
            
            if plugin_path.is_dir():
                # Package plugin
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"openclaw_plugin_{plugin_name}",
                        plugin_path / "__init__.py"
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # Look for Plugin class
                    if hasattr(module, "Plugin"):
                        plugin_class = module.Plugin
                        plugin = plugin_class()
                        
                        # Initialize
                        await plugin.initialize(config or {})
                        
                        self.plugins[plugin_name] = plugin
                        logger.info(f"Loaded plugin: {plugin_name} v{plugin.version}")
                        
                        return plugin
                    else:
                        logger.error(f"Plugin {plugin_name} has no Plugin class")
                
                except Exception as e:
                    logger.error(f"Failed to load plugin {plugin_name}: {e}")
            
            elif (plugin_dir / f"{plugin_name}.py").exists():
                # Single file plugin
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"openclaw_plugin_{plugin_name}",
                        plugin_dir / f"{plugin_name}.py"
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    if hasattr(module, "Plugin"):
                        plugin_class = module.Plugin
                        plugin = plugin_class()
                        
                        await plugin.initialize(config or {})
                        
                        self.plugins[plugin_name] = plugin
                        logger.info(f"Loaded plugin: {plugin_name} v{plugin.version}")
                        
                        return plugin
                
                except Exception as e:
                    logger.error(f"Failed to load plugin {plugin_name}: {e}")
        
        raise ValueError(f"Plugin not found: {plugin_name}")
    
    async def unload_plugin(self, plugin_name: str) -> None:
        """Unload a plugin"""
        if plugin_name not in self.plugins:
            logger.warning(f"Plugin {plugin_name} not loaded")
            return
        
        plugin = self.plugins[plugin_name]
        await plugin.shutdown()
        
        del self.plugins[plugin_name]
        logger.info(f"Unloaded plugin: {plugin_name}")
    
    async def shutdown_all(self) -> None:
        """Shutdown all plugins"""
        for plugin_name in list(self.plugins.keys()):
            await self.unload_plugin(plugin_name)
    
    def list_loaded(self) -> list[str]:
        """List loaded plugin names"""
        return list(self.plugins.keys())


# =============================================================================
# Gateway Plugin Loader (mirrors TS loadGatewayPlugins)
# =============================================================================

def _resolve_bundled_plugins_dir() -> Path | None:
    """Walk up from __file__ up to 6 levels to find the project extensions/ directory.

    Mirrors TypeScript resolveBundledPluginsDir() in src/plugins/bundled-dir.ts:
    - Checks OPENCLAW_BUNDLED_PLUGINS_DIR env var first
    - Then walks up the directory tree from this file looking for extensions/
    """
    env_override = os.environ.get("OPENCLAW_BUNDLED_PLUGINS_DIR")
    if env_override:
        p = Path(env_override)
        if p.is_dir():
            return p

    cursor = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = cursor / "extensions"
        if candidate.is_dir() and not (candidate / "__init__.py").exists():
            # Only use this directory if it is NOT a Python package (i.e., it is a
            # bundled-plugins directory, not the openclaw/extensions/ infrastructure module).
            return candidate
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    return None


def _discover_plugin_paths(
    config: dict[str, Any],
    workspace_dir: Path | None,
) -> list[tuple[str, Path, str]]:
    """Discover plugin paths from all sources.

    Returns list of (plugin_id, path, origin) tuples.
    Origin is "bundled" | "global" | "workspace" | "config".
    """
    found: list[tuple[str, Path, str]] = []

    # 0. Bundled extensions shipped with openclaw (project root extensions/ directory).
    #    Mirrors TS discoverOpenClawPlugins() — bundled source has lowest priority.
    bundled_dir = _resolve_bundled_plugins_dir()
    if bundled_dir and bundled_dir.exists():
        for item in sorted(bundled_dir.iterdir()):
            if not item.is_dir():
                continue
            # Prefer openclaw.plugin.json, fallback to plugin.json
            plugin_manifest = item / "openclaw.plugin.json"
            if not plugin_manifest.is_file():
                plugin_manifest = item / "plugin.json"
            plugin_py = item / "plugin.py"
            if plugin_manifest.is_file() and plugin_py.is_file():
                # Standard bundled extension: openclaw.plugin.json + plugin.py
                found.append((item.name, plugin_py, "bundled"))
            elif (item / "__init__.py").exists():
                # Python package style bundled plugin
                found.append((item.name, item, "bundled"))

    # 1. Global plugins from ~/.openclaw/plugins/
    global_dir = resolve_state_dir() / "plugins"
    if global_dir.exists():
        for item in sorted(global_dir.iterdir()):
            if item.is_dir() and (item / "__init__.py").exists():
                found.append((item.name, item, "global"))
            elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                found.append((item.stem, item, "global"))

    # 2. Workspace plugins from workspace/.openclaw/plugins/
    if workspace_dir:
        ws_plugin_dir = workspace_dir / ".openclaw" / "plugins"
        if ws_plugin_dir.exists():
            for item in sorted(ws_plugin_dir.iterdir()):
                if item.is_dir() and (item / "__init__.py").exists():
                    found.append((item.name, item, "workspace"))
                elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                    found.append((item.stem, item, "workspace"))

    # 3. Extra load paths from config plugins.loadPaths
    plugins_config = config.get("plugins") if isinstance(config, dict) else None
    if isinstance(plugins_config, dict):
        load_paths = plugins_config.get("loadPaths") or plugins_config.get("load_paths") or []
        for load_path in load_paths:
            p = Path(load_path)
            if p.is_dir():
                for item in sorted(p.iterdir()):
                    if item.is_dir() and (item / "__init__.py").exists():
                        found.append((item.name, item, "config"))
                    elif item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
                        found.append((item.stem, item, "config"))

    return found


def _load_plugin_module(plugin_id: str, plugin_path: Path) -> Any | None:
    """Load a plugin module from a file or package path."""
    try:
        if plugin_path.is_dir():
            init_file = plugin_path / "__init__.py"
            spec = importlib.util.spec_from_file_location(
                f"openclaw_plugin_{plugin_id}", init_file
            )
        else:
            spec = importlib.util.spec_from_file_location(
                f"openclaw_plugin_{plugin_id}", plugin_path
            )

        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.warning(f"Failed to load plugin module '{plugin_id}': {exc}")
        return None


async def load_gateway_plugins(
    config: dict[str, Any],
    workspace_dir: Path | str | None = None,
) -> "PluginRegistry":
    """Discover and load all gateway plugins.

    Matches TypeScript loadGatewayPlugins() in src/plugins/loader.ts:
    1. Discover plugin paths from all sources
    2. For each plugin: load module, inspect for register() / OpenClawPluginDefinition
    3. Create PluginApi for each plugin
    4. Call plugin.register(api) to collect registrations
    5. Return populated PluginRegistry

    Args:
        config: Current gateway config dict
        workspace_dir: Workspace directory (for workspace-local plugins)

    Returns:
        Populated PluginRegistry
    """
    from .registry import create_empty_plugin_registry
    from .api import create_plugin_api
    from .types import PluginRecord, PluginDiagnostic
    from .path_safety import is_safe_plugin_path, normalize_plugin_path
    from .schema_validator import validate_plugin_manifest
    from .discovery import discover_openclaw_plugins

    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)

    registry = create_empty_plugin_registry()

    # Respect plugins.enabled / plugins.disabled lists
    plugins_config = config.get("plugins") if isinstance(config, dict) else {}
    if not isinstance(plugins_config, dict):
        plugins_config = {}
    enabled_list: list[str] | None = plugins_config.get("enabled")
    disabled_list: list[str] = list(plugins_config.get("disabled") or [])

    # Use integrated discovery from discovery.py (which internally calls _discover_plugin_paths)
    plugin_paths = discover_openclaw_plugins(
        str(workspace_dir) if workspace_dir else None,
        config=config
    )

    for plugin_path_str, plugin_id, origin in plugin_paths:
        plugin_path = Path(plugin_path_str)
        
        # Validate path safety
        if not is_safe_plugin_path(plugin_path, workspace_dir):
            logger.warning(f"Skipping unsafe plugin path: {plugin_path}")
            registry.diagnostics.append(PluginDiagnostic(
                level="warning",
                message=f"Unsafe plugin path: {plugin_path}",
                plugin_id=plugin_id,
                source=str(plugin_path),
            ))
            continue
        
        # Check enabled/disabled lists
        if enabled_list is not None and plugin_id not in enabled_list:
            continue
        if plugin_id in disabled_list:
            record = PluginRecord(
                id=plugin_id,
                name=plugin_id,
                source=str(plugin_path),
                origin=origin,
                enabled=False,
                status="disabled",
            )
            registry.plugins.append(record)
            continue

        module = _load_plugin_module(plugin_id, plugin_path)
        if module is None:
            record = PluginRecord(
                id=plugin_id,
                name=plugin_id,
                source=str(plugin_path),
                origin=origin,
                enabled=True,
                status="error",
                error="Failed to load module",
            )
            registry.plugins.append(record)
            registry.diagnostics.append(PluginDiagnostic(
                level="error",
                message=f"Failed to load plugin module '{plugin_id}'",
                plugin_id=plugin_id,
                source=str(plugin_path),
            ))
            continue

        # Detect plugin definition: can be module-level 'plugin' object or dict,
        # or module-level 'register' function, or OpenClawPluginDefinition-like dict/object.
        # Python plugins commonly use a dict literal: plugin = {"id": ..., "register": fn}
        # In that case getattr() on a dict returns None for all keys — use .get() instead.
        plugin_def = getattr(module, "plugin", None) or getattr(module, "PLUGIN", None)
        register_fn = None
        plugin_name = plugin_id
        plugin_version = None
        plugin_description = None
        plugin_kind = None
        plugin_config_for_plugin: dict[str, Any] | None = None

        def _def_get(key: str, default: Any = None) -> Any:
            """Get a field from plugin_def regardless of whether it is a dict or object."""
            if isinstance(plugin_def, dict):
                return plugin_def.get(key, default)
            return getattr(plugin_def, key, default)

        if plugin_def is not None:
            plugin_name = _def_get("name", plugin_id) or plugin_id
            plugin_version = _def_get("version")
            plugin_description = _def_get("description")
            plugin_kind = _def_get("kind")
            register_fn = _def_get("register")

        if register_fn is None:
            # Fallback: try a top-level register() function on the module itself
            register_fn = getattr(module, "register", None)

        if register_fn is None:
            logger.debug(f"Plugin '{plugin_id}' has no register() function, skipping")
            continue

        # Get per-plugin config if configured in openclaw.json
        all_plugin_configs = plugins_config.get("configs") or plugins_config.get("pluginConfigs") or {}
        plugin_config_for_plugin = all_plugin_configs.get(plugin_id)

        api = create_plugin_api(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            registry=registry,
            config=config,
            source=str(plugin_path),
            version=plugin_version,
            description=plugin_description,
            plugin_config=plugin_config_for_plugin,
            workspace_dir=str(workspace_dir) if workspace_dir else None,
        )

        try:
            result = register_fn(api)
            # Support both sync and async register functions
            if hasattr(result, "__await__"):
                import asyncio
                await result
        except Exception as exc:
            logger.error(f"Plugin '{plugin_id}' register() raised: {exc}", exc_info=True)
            record = PluginRecord(
                id=plugin_id,
                name=plugin_name,
                version=plugin_version,
                description=plugin_description,
                kind=plugin_kind,
                source=str(plugin_path),
                origin=origin,
                enabled=True,
                status="error",
                error=str(exc),
            )
            registry.plugins.append(record)
            registry.diagnostics.append(PluginDiagnostic(
                level="error",
                message=f"Plugin '{plugin_id}' register() failed: {exc}",
                plugin_id=plugin_id,
                source=str(plugin_path),
            ))
            continue

        # Count contributions from this plugin
        tool_names = [name for reg in registry.tools if reg.plugin_id == plugin_id for name in reg.names]
        hook_names = [h.hook_name for h in registry.typed_hooks if h.plugin_id == plugin_id]
        channel_ids = [getattr(reg.plugin, "id", "") for reg in registry.channels if reg.plugin_id == plugin_id]
        provider_ids = [reg.provider.id for reg in registry.providers if reg.plugin_id == plugin_id]
        gateway_methods = [m for m in registry.gateway_handlers if True]  # simplified
        services = [reg.service.id for reg in registry.services if reg.plugin_id == plugin_id]
        commands = [reg.command.name for reg in registry.commands if reg.plugin_id == plugin_id]
        http_handlers = sum(1 for reg in registry.http_handlers if reg.plugin_id == plugin_id)

        record = PluginRecord(
            id=plugin_id,
            name=plugin_name,
            version=plugin_version,
            description=plugin_description,
            kind=plugin_kind,
            source=str(plugin_path),
            origin=origin,
            enabled=True,
            status="loaded",
            tool_names=tool_names,
            hook_names=hook_names,
            channel_ids=channel_ids,
            provider_ids=provider_ids,
            gateway_methods=gateway_methods,
            services=services,
            commands=commands,
            http_handlers=http_handlers,
            hook_count=len(hook_names),
        )
        registry.plugins.append(record)
        logger.info(
            f"Loaded plugin '{plugin_id}' from {origin} "
            f"(tools={len(tool_names)}, hooks={len(hook_names)}, channels={len(channel_ids)})"
        )

    if registry.plugins:
        loaded = sum(1 for p in registry.plugins if p.status == "loaded")
        logger.info(f"Plugin loading complete: {loaded}/{len(registry.plugins)} loaded")

    return registry


