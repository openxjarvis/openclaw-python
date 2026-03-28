"""
Plugin discovery system (aligned with TypeScript plugins/discovery.ts)

Scans multiple directories for plugin candidates:
- Bundled plugins (built-in with OpenClaw)
- Extension plugins (user-installed)
- Workspace plugins (project-specific)
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import json
import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Valid plugin file extensions
EXTENSION_EXTS = {".py"}

PluginOrigin = Literal["bundled", "extension", "workspace"]


class PluginCandidate:
    """Represents a discovered plugin candidate"""
    
    def __init__(
        self,
        id_hint: str,
        source: str,
        root_dir: str,
        origin: PluginOrigin,
        workspace_dir: str | None = None,
        package_name: str | None = None,
        package_version: str | None = None,
        package_description: str | None = None,
        package_dir: str | None = None,
        package_manifest: dict[str, Any] | None = None,
    ):
        self.id_hint = id_hint
        self.source = source
        self.root_dir = root_dir
        self.origin = origin
        self.workspace_dir = workspace_dir
        self.package_name = package_name
        self.package_version = package_version
        self.package_description = package_description
        self.package_dir = package_dir
        self.package_manifest = package_manifest or {}
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id_hint": self.id_hint,
            "source": self.source,
            "root_dir": self.root_dir,
            "origin": self.origin,
            "workspace_dir": self.workspace_dir,
            "package_name": self.package_name,
            "package_version": self.package_version,
            "package_description": self.package_description,
            "package_dir": self.package_dir,
            "package_manifest": self.package_manifest,
        }


class PluginDiagnostic:
    """Diagnostic message from plugin discovery"""
    
    def __init__(self, level: str, message: str, source: str):
        self.level = level
        self.message = message
        self.source = source
    
    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "message": self.message,
            "source": self.source,
        }


class PluginDiscoveryResult:
    """Result of plugin discovery"""
    
    def __init__(
        self,
        candidates: list[PluginCandidate],
        diagnostics: list[PluginDiagnostic]
    ):
        self.candidates = candidates
        self.diagnostics = diagnostics
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


class PluginDiscovery:
    """
    Plugin discovery service (aligned with TypeScript)
    
    Discovers plugins from:
    1. Bundled plugins directory
    2. User extensions directory (~/.openclaw/extensions)
    3. Workspace plugins directory (workspace/.openclaw/plugins)
    """
    
    def __init__(
        self,
        config: dict[str, Any],
        workspace_dir: Path | None = None,
    ):
        self.config = config
        self.workspace_dir = workspace_dir or Path.cwd()
        self.bundled_dir = self._resolve_bundled_dir()
        self.extensions_dir = self._resolve_extensions_dir()
        self.workspace_plugins_dir = self.workspace_dir / ".openclaw" / "plugins"
    
    def _resolve_bundled_dir(self) -> Path:
        """Resolve bundled plugins directory"""
        # Get openclaw package root
        package_root = Path(__file__).parent.parent
        bundled_dir = package_root / "plugins" / "bundled"
        return bundled_dir
    
    def _resolve_extensions_dir(self) -> Path:
        """Resolve user extensions directory"""
        config_dir = resolve_state_dir()
        return config_dir / "extensions"
    
    async def discover_all(self) -> PluginDiscoveryResult:
        """
        Discover all plugins from all sources
        
        Returns:
            PluginDiscoveryResult with candidates and diagnostics
        """
        candidates: list[PluginCandidate] = []
        diagnostics: list[PluginDiagnostic] = []
        seen: set[str] = set()
        
        # 1. Discover bundled plugins
        logger.info(f"Scanning bundled plugins: {self.bundled_dir}")
        self._discover_in_directory(
            directory=self.bundled_dir,
            origin="bundled",
            candidates=candidates,
            diagnostics=diagnostics,
            seen=seen,
        )
        
        # 2. Discover extension plugins
        logger.info(f"Scanning extensions: {self.extensions_dir}")
        self._discover_in_directory(
            directory=self.extensions_dir,
            origin="extension",
            candidates=candidates,
            diagnostics=diagnostics,
            seen=seen,
        )
        
        # 3. Discover workspace plugins
        if self.workspace_plugins_dir.exists():
            logger.info(f"Scanning workspace plugins: {self.workspace_plugins_dir}")
            self._discover_in_directory(
                directory=self.workspace_plugins_dir,
                origin="workspace",
                workspace_dir=str(self.workspace_dir),
                candidates=candidates,
                diagnostics=diagnostics,
                seen=seen,
            )
        
        logger.info(f"Plugin discovery complete: {len(candidates)} candidates, {len(diagnostics)} diagnostics")
        
        return PluginDiscoveryResult(candidates=candidates, diagnostics=diagnostics)
    
    def _discover_in_directory(
        self,
        directory: Path,
        origin: PluginOrigin,
        candidates: list[PluginCandidate],
        diagnostics: list[PluginDiagnostic],
        seen: set[str],
        workspace_dir: str | None = None,
    ) -> None:
        """
        Discover plugins in a directory
        
        Supports:
        - Direct plugin files (e.g., my_plugin.py)
        - Package directories with package.json
        """
        if not directory.exists():
            logger.debug(f"Plugin directory does not exist: {directory}")
            return
        
        try:
            entries = list(directory.iterdir())
        except Exception as e:
            diagnostics.append(
                PluginDiagnostic(
                    level="warn",
                    message=f"Failed to read plugins directory: {directory} ({e})",
                    source=str(directory),
                )
            )
            return
        
        for entry in entries:
            if entry.is_file() and self._is_extension_file(entry):
                # Direct plugin file
                self._add_candidate(
                    candidates=candidates,
                    seen=seen,
                    id_hint=entry.stem,
                    source=str(entry),
                    root_dir=str(entry.parent),
                    origin=origin,
                    workspace_dir=workspace_dir,
                )
            
            elif entry.is_dir():
                # Package directory
                manifest = self._read_package_manifest(entry)
                
                if manifest:
                    # Has package.json - check for openclaw.extensions
                    extensions = self._resolve_package_extensions(manifest)
                    
                    if extensions:
                        for ext_path in extensions:
                            full_path = entry / ext_path
                            if full_path.exists() and self._is_extension_file(full_path):
                                id_hint = self._derive_id_hint(
                                    file_path=str(full_path),
                                    package_name=manifest.get("name"),
                                    has_multiple_extensions=len(extensions) > 1,
                                )
                                
                                self._add_candidate(
                                    candidates=candidates,
                                    seen=seen,
                                    id_hint=id_hint,
                                    source=str(full_path),
                                    root_dir=str(entry),
                                    origin=origin,
                                    workspace_dir=workspace_dir,
                                    manifest=manifest,
                                    package_dir=str(entry),
                                )
                    else:
                        # No extensions declared, warn
                        diagnostics.append(
                            PluginDiagnostic(
                                level="info",
                                message=f"Package has no openclaw.extensions: {entry.name}",
                                source=str(entry),
                            )
                        )
                else:
                    # No package.json, scan for plugin files
                    for plugin_file in entry.glob("*.py"):
                        if self._is_extension_file(plugin_file):
                            self._add_candidate(
                                candidates=candidates,
                                seen=seen,
                                id_hint=plugin_file.stem,
                                source=str(plugin_file),
                                root_dir=str(entry),
                                origin=origin,
                                workspace_dir=workspace_dir,
                            )
    
    def _is_extension_file(self, file_path: Path) -> bool:
        """Check if file is a valid plugin extension"""
        if file_path.suffix not in EXTENSION_EXTS:
            return False
        
        # Exclude __init__.py and test files
        if file_path.name in ("__init__.py", "__main__.py"):
            return False
        
        if file_path.stem.startswith("test_"):
            return False
        
        return True
    
    def _read_package_manifest(self, directory: Path) -> dict[str, Any] | None:
        """Read package.json from directory"""
        manifest_path = directory / "package.json"
        
        if not manifest_path.exists():
            return None
        
        try:
            with open(manifest_path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read package.json at {manifest_path}: {e}")
            return None
    
    def _resolve_package_extensions(self, manifest: dict[str, Any]) -> list[str]:
        """
        Resolve extensions from package.json
        
        Looks for:
        - openclaw.extensions field
        """
        openclaw_meta = manifest.get("openclaw", {})
        if not isinstance(openclaw_meta, dict):
            return []
        
        extensions = openclaw_meta.get("extensions", [])
        if not isinstance(extensions, list):
            return []
        
        return [str(ext).strip() for ext in extensions if ext]
    
    def _derive_id_hint(
        self,
        file_path: str,
        package_name: str | None,
        has_multiple_extensions: bool,
    ) -> str:
        """
        Derive plugin ID hint from file path and package name
        
        Examples:
        - voice_call.py -> "voice_call"
        - @openclaw/voice-call/main.py -> "voice-call" (single extension)
        - @openclaw/multi/plugin_a.py -> "multi/plugin_a" (multiple extensions)
        """
        base = Path(file_path).stem
        
        if not package_name:
            return base
        
        # Prefer unscoped name for stability
        unscoped = package_name.split("/")[-1] if "/" in package_name else package_name
        
        if not has_multiple_extensions:
            return unscoped
        
        return f"{unscoped}/{base}"
    
    def _add_candidate(
        self,
        candidates: list[PluginCandidate],
        seen: set[str],
        id_hint: str,
        source: str,
        root_dir: str,
        origin: PluginOrigin,
        workspace_dir: str | None = None,
        manifest: dict[str, Any] | None = None,
        package_dir: str | None = None,
    ) -> None:
        """Add a plugin candidate, avoiding duplicates"""
        resolved_source = str(Path(source).resolve())
        
        if resolved_source in seen:
            return
        
        seen.add(resolved_source)
        
        candidate = PluginCandidate(
            id_hint=id_hint,
            source=resolved_source,
            root_dir=str(Path(root_dir).resolve()),
            origin=origin,
            workspace_dir=workspace_dir,
            package_name=manifest.get("name") if manifest else None,
            package_version=manifest.get("version") if manifest else None,
            package_description=manifest.get("description") if manifest else None,
            package_dir=package_dir,
            package_manifest=manifest.get("openclaw") if manifest else None,
        )
        
        candidates.append(candidate)
        logger.debug(f"Discovered plugin: {candidate.id_hint} from {origin}")


def discover_plugins(
    config: dict[str, Any],
    workspace_dir: Path | None = None,
) -> PluginDiscoveryResult:
    """
    Convenience function to discover all plugins
    
    Args:
        config: Gateway configuration
        workspace_dir: Workspace directory
        
    Returns:
        PluginDiscoveryResult
    """
    import asyncio
    
    discovery = PluginDiscovery(config=config, workspace_dir=workspace_dir)
    return asyncio.run(discovery.discover_all())


def discover_openclaw_plugins(
    workspace_dir: str | Path | None = None,
    extra_paths: list[str] | None = None,
    **kwargs,  # ✅ Accept and ignore extra kwargs like 'config'
) -> PluginDiscoveryResult:
    """
    Discover OpenClaw plugins from standard locations and extra paths.
    
    This function is called by manifest_registry.py and provides compatibility
    with the discovery interface expected by the registry system.
    
    Args:
        workspace_dir: Workspace directory (optional)
        extra_paths: Additional plugin search paths
        **kwargs: Additional arguments (ignored for compatibility)
        
    Returns:
        PluginDiscoveryResult with candidates and diagnostics
    """
    import asyncio
    
    # Convert workspace_dir to Path
    if workspace_dir is None:
        workspace_dir = Path.cwd()
    elif isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    # Create discovery instance
    discovery = PluginDiscovery(config={}, workspace_dir=workspace_dir)
    
    # TODO: Handle extra_paths
    # For now, we use the standard discovery locations
    # In future, we can extend PluginDiscovery to support custom paths
    
    return asyncio.run(discovery.discover_all())
