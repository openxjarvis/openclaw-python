"""
Unified workspace and sessions initialization.

Matches TypeScript ensureWorkspaceAndSessions() from 
openclaw/src/commands/onboard-helpers.ts lines 289-302
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


def ensure_workspace_and_sessions(
    workspace_dir: Union[str, Path],
    runtime: Optional[Any] = None,
    agent_id: Optional[str] = None,
    skip_bootstrap: bool = False,
) -> dict[str, Any]:
    """
    Ensure agent workspace and sessions directory exist.
    
    Matches TypeScript ensureWorkspaceAndSessions() exactly from
    openclaw/src/commands/onboard-helpers.ts lines 289-302:
    
    1. Call ensureAgentWorkspace() with bootstrap control
    2. Create sessions directory for agent
    3. Log success messages with shortened paths
    
    This function provides atomic initialization of both workspace and
    sessions directories, ensuring consistency and avoiding partial setup.
    
    Args:
        workspace_dir: Workspace directory path
        runtime: Optional RuntimeEnv for logging (uses logger if None)
        agent_id: Agent ID for sessions directory (default: "main")
        skip_bootstrap: Skip bootstrap file creation
    
    Returns:
        Dict with workspace and sessions info:
        {
            "workspace": {...},  # From ensure_agent_workspace()
            "sessions_dir": Path
        }
    
    Examples:
        >>> # Basic usage
        >>> result = ensure_workspace_and_sessions("~/.openclaw/workspace")
        >>> print(result["sessions_dir"])
        
        >>> # With agent ID
        >>> result = ensure_workspace_and_sessions(
        ...     workspace_dir="~/.openclaw/workspace",
        ...     agent_id="telegram-bot",
        ...     skip_bootstrap=True
        ... )
        
        >>> # With runtime logging
        >>> from openclaw.runtime_env import RuntimeEnv
        >>> runtime = RuntimeEnv(env_id="test", model="...")
        >>> result = ensure_workspace_and_sessions(
        ...     workspace_dir="./workspace",
        ...     runtime=runtime
        ... )
    """
    from .ensure_workspace import ensure_agent_workspace
    from ..config.sessions.paths import resolve_agent_sessions_dir
    from ..infra.path_utils import shorten_home_path
    
    # Resolve workspace directory
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir).expanduser().resolve()
    elif isinstance(workspace_dir, Path):
        workspace_dir = workspace_dir.expanduser().resolve()
    
    # Step 1: Ensure workspace with bootstrap files
    # Mirrors TS: const ws = await ensureAgentWorkspace({ dir, ensureBootstrapFiles })
    ws_result = ensure_agent_workspace(
        workspace_dir=workspace_dir,
        ensure_bootstrap_files=not skip_bootstrap,
        skip_bootstrap=skip_bootstrap,
    )
    
    # Log workspace success
    # Mirrors TS: runtime.log(`Workspace OK: ${shortenHomePath(ws.dir)}`)
    workspace_short = shorten_home_path(workspace_dir)
    log_message = f"Workspace OK: {workspace_short}"
    
    if runtime and hasattr(runtime, 'log'):
        runtime.log(log_message)
    else:
        logger.info(log_message)
    
    # Step 2: Ensure sessions directory for agent
    # Mirrors TS: const sessionsDir = resolveSessionTranscriptsDirForAgent(agentId)
    sessions_dir = resolve_agent_sessions_dir(agent_id)
    
    # Mirrors TS: await fs.mkdir(sessionsDir, { recursive: true })
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    # Log sessions success
    # Mirrors TS: runtime.log(`Sessions OK: ${shortenHomePath(sessionsDir)}`)
    sessions_short = shorten_home_path(sessions_dir)
    log_message = f"Sessions OK: {sessions_short}"
    
    if runtime and hasattr(runtime, 'log'):
        runtime.log(log_message)
    else:
        logger.info(log_message)
    
    # Return combined result
    return {
        "workspace": ws_result,
        "sessions_dir": sessions_dir,
    }


__all__ = [
    "ensure_workspace_and_sessions",
]
