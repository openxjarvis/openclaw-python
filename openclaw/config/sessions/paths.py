"""Session store paths

Fully aligned with TypeScript openclaw/src/config/sessions/paths.ts

Session store structure:
- Default: ~/.openclaw/agents/<agentId>/sessions/sessions.json
- Supports {agentId} placeholder in custom paths
- Per-agent session isolation
"""
from pathlib import Path
from typing import Optional
import logging

from openclaw.config.paths import resolve_state_dir
from openclaw.routing.session_key import DEFAULT_AGENT_ID, normalize_agent_id

logger = logging.getLogger(__name__)


def resolve_agent_sessions_dir(agent_id: str | None = None) -> Path:
    """
    Resolve agent sessions directory.
    
    Mirrors TS resolveAgentSessionsDir() from paths.ts lines 7-15
    
    Returns: ~/.openclaw/agents/<agentId>/sessions/
    
    Args:
        agent_id: Agent ID (defaults to "main")
    
    Returns:
        Path to agent sessions directory
    """
    from pathlib import Path
    
    state_dir = Path(resolve_state_dir())
    normalized_id = normalize_agent_id(agent_id or DEFAULT_AGENT_ID)
    
    return state_dir / "agents" / normalized_id / "sessions"


def resolve_default_session_store_path(agent_id: str | None = None) -> Path:
    """
    Resolve default session store file path.
    
    Mirrors TS resolveDefaultSessionStorePath() from paths.ts lines 32-34
    
    Returns: ~/.openclaw/agents/<agentId>/sessions/sessions.json
    
    Args:
        agent_id: Agent ID (defaults to "main")
    
    Returns:
        Path to session store file
    """
    return resolve_agent_sessions_dir(agent_id) / "sessions.json"


def resolve_store_path(
    store: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """
    Resolve session store path with {agentId} placeholder support.
    
    Aligned with TS resolveStorePath() logic from sessions.ts
    
    Priority:
    1. If store contains {agentId}, expand it
    2. If store is absolute/~ path, use it as-is
    3. Otherwise use default path
    
    Args:
        store: Custom store path (may contain {agentId} placeholder)
        agent_id: Agent ID for placeholder expansion
    
    Returns:
        Resolved store path
    """
    normalized_id = normalize_agent_id(agent_id or DEFAULT_AGENT_ID)
    
    # No custom path: use default
    if not store or not store.strip():
        return resolve_default_session_store_path(normalized_id)
    
    store_str = store.strip()
    
    # Expand {agentId} placeholder
    if "{agentId}" in store_str:
        expanded = store_str.replace("{agentId}", normalized_id)
        return Path(expanded).expanduser().resolve()
    
    # Use custom path as-is
    return Path(store_str).expanduser().resolve()


def get_default_store_path(agent_id: str = "main") -> Path:
    """
    Get default session store path (legacy compatibility).
    
    Args:
        agent_id: Agent identifier (default: "main")
    
    Returns:
        Path to session store file
    """
    return resolve_default_session_store_path(agent_id)


def get_store_path(custom_path: Optional[str] = None) -> Path:
    """Get session store path (legacy compatibility)."""
    if custom_path:
        return Path(custom_path).expanduser().resolve()
    return get_default_store_path()


def resolve_session_store_path(config: dict = None, agent_id: str | None = None) -> Path:
    """
    Resolve session store path from config.
    
    Args:
        config: Configuration dict
        agent_id: Agent ID
        
    Returns:
        Path to session store
    """
    store_path = None
    
    if config:
        sessions = config.get("sessions") or {}
        if isinstance(sessions, dict):
            store_path = sessions.get("storePath") or sessions.get("store")
    
    return resolve_store_path(store_path, agent_id)


__all__ = [
    "get_default_store_path",
    "get_store_path", 
    "resolve_session_store_path",
    "resolve_default_session_store_path",
    "resolve_store_path",
    "resolve_agent_sessions_dir",
]
