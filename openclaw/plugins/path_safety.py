"""Plugin path safety utilities — mirrors src/plugins/path-safety.ts"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def is_safe_plugin_path(path: str | Path, workspace_dir: str | Path) -> bool:
    """
    Check if a plugin path is safe (prevents path traversal attacks).
    
    Ensures the path is within the workspace directory and doesn't use
    parent directory references to escape.
    
    Args:
        path: Path to validate
        workspace_dir: Workspace directory
    
    Returns:
        True if path is safe, False otherwise
    """
    try:
        # Convert to Path objects
        path_obj = Path(path).resolve()
        workspace_obj = Path(workspace_dir).resolve()
        
        # Check if path is relative to workspace
        try:
            path_obj.relative_to(workspace_obj)
            return True
        except ValueError:
            # Path is not relative to workspace
            logger.warning(f"Unsafe plugin path: {path} is outside workspace {workspace_dir}")
            return False
    
    except Exception as e:
        logger.error(f"Error validating plugin path: {e}")
        return False


def normalize_plugin_path(path: str | Path, workspace_dir: str | Path) -> Path | None:
    """
    Normalize and validate a plugin path.
    
    Args:
        path: Path to normalize
        workspace_dir: Workspace directory
    
    Returns:
        Normalized Path object, or None if invalid
    """
    try:
        # Convert to absolute path
        if not Path(path).is_absolute():
            path = Path(workspace_dir) / path
        
        path_obj = Path(path).resolve()
        
        # Validate safety
        if not is_safe_plugin_path(path_obj, workspace_dir):
            return None
        
        return path_obj
    
    except Exception as e:
        logger.error(f"Error normalizing plugin path: {e}")
        return None


def is_plugin_directory(path: str | Path) -> bool:
    """
    Check if a directory is a valid plugin directory.
    
    A valid plugin directory must contain a manifest file (plugin.json or package.json).
    
    Args:
        path: Directory path to check
    
    Returns:
        True if directory contains a plugin manifest
    """
    path_obj = Path(path)
    
    if not path_obj.is_dir():
        return False
    
    # Check for plugin manifest files
    manifest_files = ["plugin.json", "package.json", "manifest.json"]
    
    for manifest_file in manifest_files:
        if (path_obj / manifest_file).is_file():
            return True
    
    return False


def list_safe_plugin_paths(
    plugin_dir: str | Path,
    workspace_dir: str | Path,
) -> list[Path]:
    """
    List all safe plugin paths in a directory.
    
    Args:
        plugin_dir: Plugin directory to scan
        workspace_dir: Workspace directory
    
    Returns:
        List of safe plugin directory paths
    """
    try:
        plugin_dir_obj = Path(plugin_dir)
        
        if not plugin_dir_obj.is_dir():
            return []
        
        safe_paths = []
        
        # Scan for plugin directories
        for entry in plugin_dir_obj.iterdir():
            if entry.is_dir():
                # Check if safe
                if is_safe_plugin_path(entry, workspace_dir):
                    # Check if it's a plugin directory
                    if is_plugin_directory(entry):
                        safe_paths.append(entry)
        
        return safe_paths
    
    except Exception as e:
        logger.error(f"Error listing safe plugin paths: {e}")
        return []
