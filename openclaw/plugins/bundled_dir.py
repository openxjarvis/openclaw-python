"""Bundled plugins directory resolution

Mirrors openclaw/src/plugins/bundled-dir.ts
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def resolve_bundled_plugins_dir() -> Path | None:
    """Resolve bundled plugins directory.
    
    Resolution order:
    1. OPENCLAW_BUNDLED_PLUGINS_DIR environment variable
    2. Sibling to process executable (for compiled binaries)
    3. Walk up from module location (for npm/dev installs)
    
    Returns:
        Path to bundled plugins directory (extensions/), or None if not found
    
    Example:
        >>> dir = resolve_bundled_plugins_dir()
        >>> if dir:
        ...     print(list(dir.iterdir()))
    """
    # 1. Check environment variable
    env_override = os.environ.get("OPENCLAW_BUNDLED_PLUGINS_DIR")
    if env_override:
        p = Path(env_override)
        if p.is_dir():
            return p
    
    # 2. Check sibling to executable (for compiled binaries)
    if getattr(sys, "frozen", False):
        # Running in PyInstaller bundle or similar
        exe_path = Path(sys.executable).resolve()
        candidate = exe_path.parent / "extensions"
        if candidate.is_dir():
            return candidate
    
    # 3. Walk up from module location (for npm/dev installs)
    cursor = Path(__file__).resolve().parent
    for _ in range(6):  # Walk up max 6 levels
        candidate = cursor / "extensions"
        if candidate.is_dir():
            # Only use if it's NOT a Python package (i.e., it's a bundled-plugins directory)
            if not (candidate / "__init__.py").exists():
                return candidate
        
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    
    return None


__all__ = ["resolve_bundled_plugins_dir"]
