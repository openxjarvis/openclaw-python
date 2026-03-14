"""
Subagent depth tracking

Matches TypeScript src/agents/subagent-depth.ts

Tracks the nesting depth of subagents for spawn limits and tool policy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from openclaw.routing.session_key import (
    get_subagent_depth as get_depth_from_key,
    parse_agent_session_key,
)


def normalize_spawn_depth(value) -> Optional[int]:
    """
    Normalize spawn depth value.
    
    Matches TS normalizeSpawnDepth() lines 14-27.
    """
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            numeric = int(trimmed)
            return numeric if numeric >= 0 else None
        except ValueError:
            return None
    return None


def normalize_session_key(value) -> Optional[str]:
    """
    Normalize session key value.
    
    Matches TS normalizeSessionKey() lines 29-35.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if trimmed else None


def read_session_store(store_path: Path) -> dict:
    """
    Read session store from disk.
    
    Matches TS readSessionStore() lines 37-48.
    
    Returns:
        Dict of session entries
    """
    try:
        if not store_path.exists():
            return {}
        
        with open(store_path, 'r') as f:
            content = f.read()
            # Support JSON5 by using standard JSON (most configs are valid JSON)
            parsed = json.loads(content)
            
        if isinstance(parsed, dict) and not isinstance(parsed, list):
            return parsed
    except Exception:
        # Ignore missing/invalid stores
        pass
    
    return {}


def find_entry_by_session_id(
    store: dict,
    session_id: str
) -> Optional[dict]:
    """
    Find entry by session ID.
    
    Matches TS findEntryBySessionId() lines 65-80.
    """
    normalized_session_id = normalize_session_key(session_id)
    if not normalized_session_id:
        return None
    
    for entry in store.values():
        if not isinstance(entry, dict):
            continue
        
        candidate_session_id = normalize_session_key(entry.get('sessionId'))
        if candidate_session_id and candidate_session_id == normalized_session_id:
            return entry
    
    return None


def get_subagent_depth_from_session_store(
    session_key: str,
    store: Optional[dict] = None,
    store_path: Optional[Path] = None,
) -> int:
    """
    Get subagent depth from session store.
    
    Matches TS getSubagentDepthFromSessionStore() lines 129-177.
    
    Strategy:
    1. Check session key's :subagent: count
    2. Look up spawnDepth in session store
    3. Walk spawned_by chain if needed
    4. Return max of all methods
    
    Args:
        session_key: Session key to check
        store: Optional pre-loaded session store
        store_path: Optional path to session store file
    
    Returns:
        Spawn depth (0 = main agent, 1 = first level subagent, etc.)
    """
    # Quick check from key structure
    key_depth = get_depth_from_key(session_key)
    
    # Try to load store if not provided
    if store is None and store_path is not None:
        store = read_session_store(store_path)
    
    if not store:
        return key_depth
    
    # Try direct lookup
    entry = store.get(session_key)
    if entry and isinstance(entry, dict):
        stored_depth = normalize_spawn_depth(entry.get('spawnDepth'))
        if stored_depth is not None:
            return max(key_depth, stored_depth)
    
    # Try lookup by session ID
    if not entry:
        entry = find_entry_by_session_id(store, session_key)
        if entry:
            stored_depth = normalize_spawn_depth(entry.get('spawnDepth'))
            if stored_depth is not None:
                return max(key_depth, stored_depth)
    
    # Walk spawned_by chain
    if entry:
        chain_depth = 0
        current_key = normalize_session_key(entry.get('spawnedBy'))
        visited = set()
        
        while current_key and current_key not in visited:
            visited.add(current_key)
            chain_depth += 1
            
            parent_entry = store.get(current_key)
            if not parent_entry or not isinstance(parent_entry, dict):
                parent_entry = find_entry_by_session_id(store, current_key)
            
            if not parent_entry:
                break
            
            # Check if parent has explicit depth
            parent_depth = normalize_spawn_depth(parent_entry.get('spawnDepth'))
            if parent_depth is not None:
                return max(key_depth, parent_depth + chain_depth)
            
            # Continue walking
            current_key = normalize_session_key(parent_entry.get('spawnedBy'))
        
        if chain_depth > 0:
            return max(key_depth, chain_depth)
    
    return key_depth


__all__ = [
    "get_subagent_depth_from_session_store",
    "normalize_spawn_depth",
    "read_session_store",
]
