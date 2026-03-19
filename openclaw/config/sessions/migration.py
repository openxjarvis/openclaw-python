"""Session store path migration utilities

Migrate session stores from old structure to new structure:
- Old: ~/.openclaw/sessions/<agentId>/store.json
- New: ~/.openclaw/agents/<agentId>/sessions/sessions.json

Aligned with TypeScript session store structure.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from openclaw.routing.session_key import DEFAULT_AGENT_ID, normalize_agent_id
from ...config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


def get_old_session_store_path(agent_id: str | None = None) -> Path:
    """
    Get old session store path (legacy structure).
    
    Returns: ~/.openclaw/sessions/<agentId>/store.json
    
    Args:
        agent_id: Agent ID (defaults to "main")
    
    Returns:
        Path to old session store file
    """
    
    normalized_id = normalize_agent_id(agent_id or DEFAULT_AGENT_ID)
    state_dir = resolve_state_dir()
    
    if normalized_id == DEFAULT_AGENT_ID:
        return state_dir / "sessions" / "store.json"
    
    return state_dir / "sessions" / normalized_id / "store.json"


def migrate_session_store_if_needed(agent_id: str | None = None) -> bool:
    """
    Migrate session store from old path to new path if needed.
    
    Old: ~/.openclaw/sessions/<agentId>/store.json
    New: ~/.openclaw/agents/<agentId>/sessions/sessions.json
    
    Args:
        agent_id: Agent ID to migrate (defaults to "main")
    
    Returns:
        True if migration was performed, False otherwise
    """
    from openclaw.config.sessions.paths import resolve_default_session_store_path
    
    normalized_id = normalize_agent_id(agent_id or DEFAULT_AGENT_ID)
    
    old_path = get_old_session_store_path(normalized_id)
    new_path = resolve_default_session_store_path(normalized_id)
    
    # Check if migration needed
    if not old_path.exists():
        logger.debug(f"No old session store found at {old_path}, no migration needed")
        return False
    
    if new_path.exists():
        logger.debug(f"New session store already exists at {new_path}, skipping migration")
        return False
    
    # Perform migration
    try:
        logger.info(f"Migrating session store: {old_path} → {new_path}")
        
        # Ensure parent directory exists
        new_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Copy file (preserve metadata)
        shutil.copy2(old_path, new_path)
        
        logger.info(f"✅ Session store migrated successfully to {new_path}")
        
        # Optionally: Remove old file after successful migration
        # old_path.unlink()
        
        return True
    
    except Exception as e:
        logger.error(f"Failed to migrate session store: {e}")
        return False


def migrate_all_session_stores() -> dict[str, bool]:
    """
    Migrate all session stores found in old structure.
    
    Scans ~/.openclaw/sessions/ for all agent-specific stores
    and migrates them to new structure.
    
    Returns:
        Dict mapping agent IDs to migration success status
    """
    
    state_dir = resolve_state_dir()
    old_sessions_dir = state_dir / "sessions"
    
    if not old_sessions_dir.exists():
        logger.debug("No old sessions directory found, no migration needed")
        return {}
    
    results: dict[str, bool] = {}
    
    # Migrate main agent store
    if (old_sessions_dir / "store.json").exists():
        logger.info("Migrating main agent session store")
        results[DEFAULT_AGENT_ID] = migrate_session_store_if_needed(DEFAULT_AGENT_ID)
    
    # Migrate agent-specific stores
    for agent_dir in old_sessions_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        
        agent_id = agent_dir.name
        store_file = agent_dir / "store.json"
        
        if store_file.exists():
            logger.info(f"Migrating agent '{agent_id}' session store")
            results[agent_id] = migrate_session_store_if_needed(agent_id)
    
    # Summary
    migrated_count = sum(1 for success in results.values() if success)
    total_count = len(results)
    
    if migrated_count > 0:
        logger.info(f"✅ Migrated {migrated_count}/{total_count} session stores")
    elif total_count > 0:
        logger.info(f"No migration needed for {total_count} session stores (already migrated)")
    
    return results


def check_migration_needed() -> dict[str, bool]:
    """
    Check if session store migration is needed for any agents.
    
    Returns:
        Dict mapping agent IDs to whether migration is needed
    """
    from openclaw.config.sessions.paths import resolve_default_session_store_path
    
    state_dir = resolve_state_dir()
    old_sessions_dir = state_dir / "sessions"
    
    if not old_sessions_dir.exists():
        return {}
    
    needs_migration: dict[str, bool] = {}
    
    # Check main agent
    old_main = old_sessions_dir / "store.json"
    new_main = resolve_default_session_store_path(DEFAULT_AGENT_ID)
    if old_main.exists() and not new_main.exists():
        needs_migration[DEFAULT_AGENT_ID] = True
    
    # Check agent-specific stores
    for agent_dir in old_sessions_dir.iterdir():
        if not agent_dir.is_dir():
            continue
        
        agent_id = agent_dir.name
        old_store = agent_dir / "store.json"
        new_store = resolve_default_session_store_path(agent_id)
        
        if old_store.exists() and not new_store.exists():
            needs_migration[agent_id] = True
    
    return needs_migration


__all__ = [
    "migrate_session_store_if_needed",
    "migrate_all_session_stores",
    "check_migration_needed",
    "get_old_session_store_path",
]
