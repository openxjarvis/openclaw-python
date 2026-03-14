"""Media local roots resolution (mirrors TS local-roots.ts)."""

import os
from pathlib import Path
from typing import Any

from openclaw.agents.agent_scope import resolve_agent_workspace_dir


def build_media_local_roots(
    state_dir: str | Path,
    preferred_tmp_dir: str | Path | None = None,
) -> list[str]:
    """
    Build list of media local roots.
    
    Mirrors TS buildMediaLocalRoots from src/media/local-roots.ts:20-33
    
    Args:
        state_dir: The state directory (e.g., ~/.openclaw)
        preferred_tmp_dir: Optional preferred temp directory
    
    Returns:
        List of root directory paths as strings
    """
    resolved_state_dir = Path(state_dir).resolve()
    
    if preferred_tmp_dir is None:
        # Use system temp directory
        preferred_tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    else:
        preferred_tmp_dir = Path(preferred_tmp_dir).resolve()
    
    return [
        str(preferred_tmp_dir),
        str(resolved_state_dir / "media"),
        str(resolved_state_dir / "agents"),
        str(resolved_state_dir / "workspace"),
        str(resolved_state_dir / "sandboxes"),
    ]


def get_default_media_local_roots() -> list[str]:
    """
    Get default media local roots.
    
    Mirrors TS getDefaultMediaLocalRoots from src/media/local-roots.ts:35-37
    
    Returns:
        List of default root directory paths as strings
    """
    state_dir = Path.home() / ".openclaw"
    return build_media_local_roots(state_dir)


def get_agent_scoped_media_local_roots(
    cfg: Any,
    agent_id: str | None = None,
) -> list[str]:
    """
    Get agent-scoped media local roots.
    
    Mirrors TS getAgentScopedMediaLocalRoots from src/media/local-roots.ts:39-56
    
    Returns list of root directory paths as strings representing allowed media roots:
    - System tmp directory
    - ~/.openclaw/media
    - ~/.openclaw/agents
    - ~/.openclaw/workspace
    - ~/.openclaw/sandboxes
    - Agent-specific workspace (if agent_id provided)
    
    Args:
        cfg: Configuration object (OpenClawConfig)
        agent_id: Optional agent identifier to include agent-specific workspace
    
    Returns:
        List of root directory paths as strings
    
    Examples:
        >>> cfg = load_config()
        >>> roots = get_agent_scoped_media_local_roots(cfg, "main")
        >>> for root in roots:
        ...     print(root)
        /tmp
        /Users/user/.openclaw/media
        /Users/user/.openclaw/agents
        /Users/user/.openclaw/workspace
        /Users/user/.openclaw/sandboxes
        /Users/user/.openclaw/workspaces/main
    """
    state_dir = Path.home() / ".openclaw"
    
    # Build base roots using build_media_local_roots
    roots_list = build_media_local_roots(state_dir)
    
    # Add agent-specific workspace if provided
    if agent_id and agent_id.strip():
        try:
            workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
            if workspace_dir:
                normalized_workspace_dir = str(Path(workspace_dir).resolve())
                # Only add if not already in roots
                if normalized_workspace_dir not in roots_list:
                    roots_list.append(normalized_workspace_dir)
        except Exception:
            # If workspace resolution fails, continue with base roots
            pass
    
    return roots_list


def is_path_in_allowed_roots(
    path: Path,
    allowed_roots: list[Path],
) -> bool:
    """
    Check if a path is within any of the allowed roots.
    
    Mirrors TS assertLocalMediaAllowed logic.
    
    Args:
        path: The path to check (must be absolute and resolved)
        allowed_roots: List of allowed root directories
    
    Returns:
        True if path is within any allowed root, False otherwise
    """
    try:
        path_resolved = path.resolve()
    except Exception:
        return False
    
    path_str = str(path_resolved)
    
    for root in allowed_roots:
        try:
            root_resolved = root.resolve()
        except Exception:
            continue
        
        root_str = str(root_resolved)
        
        # Path must be strictly under (or equal to) an allowed root
        if path_str == root_str or path_str.startswith(root_str + os.sep):
            return True
    
    return False
