"""Workspace hooks management.

Load and merge hooks from multiple sources.
Aligned with TypeScript src/hooks/workspace.ts
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Any

from .types import HookEntry, HookSnapshot, HookSource
from .loader import load_hooks_from_dir
from openclaw.config.paths import resolve_state_dir

# Sentinel value to distinguish "not provided" from "explicitly None"
_UNSET = object()


def load_workspace_hook_entries(
    workspace_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
    managed_hooks_dir: str | Path | None = None,
    bundled_hooks_dir: str | Path | object = _UNSET,
    extra_dirs: list[Path] | None = None,
    **kwargs
) -> list[HookEntry]:
    """Load hook entries from multiple sources with priority.
    
    Priority order (highest to lowest):
    1. Workspace hooks (workspace/hooks)
    2. Managed hooks (~/.openclaw/hooks)
    3. Extra directories
    4. Bundled hooks
    
    Args:
        workspace_dir: Workspace directory (str or Path)
        config: OpenClaw configuration (optional)
        managed_hooks_dir: Managed hooks directory (optional, defaults to ~/.openclaw/hooks)
        bundled_hooks_dir: Bundled hooks directory (optional, defaults to package bundled hooks)
        extra_dirs: Additional directories
        **kwargs: Backward compatibility for old parameter names
    
    Returns:
        List of HookEntry objects (merged, deduplicated)
    """
    all_hooks: dict[str, HookEntry] = {}
    
    # Convert workspace_dir to Path if string
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    # Resolve default directories
    if managed_hooks_dir is None:
        managed_hooks_dir = resolve_state_dir() / "hooks"
    elif isinstance(managed_hooks_dir, str):
        managed_hooks_dir = Path(managed_hooks_dir)
    
    # Handle bundled_hooks_dir: None means explicitly disabled, _UNSET means use default
    if bundled_hooks_dir is _UNSET:
        # Not explicitly set - use default
        bundled_hooks_dir = Path(__file__).parent / "bundled"
    elif bundled_hooks_dir is None:
        # Explicitly disabled
        pass  # Keep as None
    elif isinstance(bundled_hooks_dir, str):
        bundled_hooks_dir = Path(bundled_hooks_dir)
    else:
        bundled_hooks_dir = Path(bundled_hooks_dir) if bundled_hooks_dir else None
    
    # Get extra directories from config if provided
    if config and not extra_dirs:
        hooks_config = config.get("hooks") if isinstance(config, dict) else None
        if not isinstance(hooks_config, dict):
            hooks_config = {}
        internal_config = hooks_config.get("internal")
        if not isinstance(internal_config, dict):
            internal_config = {}
        load_config = internal_config.get("load")
        if not isinstance(load_config, dict):
            load_config = {}
        extra_dirs_config = load_config.get("extraDirs") or load_config.get("extra_dirs")
        if extra_dirs_config:
            extra_dirs = [Path(d) for d in extra_dirs_config]
    
    # Load bundled hooks (lowest priority) - only if not explicitly disabled
    if bundled_hooks_dir is not None and bundled_hooks_dir.exists():
        bundled_hooks = load_hooks_from_dir(bundled_hooks_dir, "openclaw-bundled")
        for entry in bundled_hooks:
            all_hooks[entry.hook.name] = entry
    
    # Load extra directories
    if extra_dirs:
        for extra_dir in extra_dirs:
            if extra_dir.exists():
                extra_hooks = load_hooks_from_dir(extra_dir, "openclaw-managed")
                for entry in extra_hooks:
                    all_hooks[entry.hook.name] = entry
    
    # Load managed hooks
    if managed_hooks_dir and managed_hooks_dir.exists():
        managed_hooks = load_hooks_from_dir(managed_hooks_dir, "openclaw-managed")
        for entry in managed_hooks:
            all_hooks[entry.hook.name] = entry
    
    # Load workspace hooks (highest priority)
    if workspace_dir:
        workspace_hooks_dir = workspace_dir / ".openclaw" / "hooks"
        if workspace_hooks_dir.exists():
            workspace_hooks = load_hooks_from_dir(workspace_hooks_dir, "openclaw-workspace")
            for entry in workspace_hooks:
                all_hooks[entry.hook.name] = entry
    
    return list(all_hooks.values())


def build_workspace_hook_snapshot(
    hook_entries: list[HookEntry]
) -> HookSnapshot:
    """Build a snapshot of hook state.
    
    Args:
        hook_entries: List of hook entries
    
    Returns:
        HookSnapshot with current state
    """
    hooks_data = []
    resolved_hooks = []
    
    for entry in hook_entries:
        hook = entry.hook
        metadata = entry.metadata
        
        hooks_data.append({
            "name": hook.name,
            "events": metadata.events if metadata else []
        })
        
        resolved_hooks.append(hook)
    
    return HookSnapshot(
        hooks=hooks_data,
        resolved_hooks=resolved_hooks,
        version=1
    )


__all__ = [
    "load_workspace_hook_entries",
    "build_workspace_hook_snapshot",
]
