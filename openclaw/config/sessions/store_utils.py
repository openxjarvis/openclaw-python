"""
Session store utilities with mutator pattern.

Matches openclaw-ts updateSessionStore pattern for atomic updates.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from openclaw.agents.session_entry import SessionEntry

logger = logging.getLogger(__name__)


def load_session_store_from_path(store_path: Path | str) -> Dict[str, SessionEntry]:
    """
    Load session store from file path.
    
    Args:
        store_path: Path to store.json file (Path or str)
    
    Returns:
        Dict mapping canonical session keys to SessionEntry objects
    """
    # Convert to Path if string
    if isinstance(store_path, str):
        store_path = Path(store_path)
    
    if not store_path.exists():
        return {}
    
    try:
        with open(store_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Convert dict entries to SessionEntry objects
        store = {}
        for key, entry_data in data.items():
            if isinstance(entry_data, dict):
                try:
                    # Use from_dict to handle both naming conventions
                    store[key] = SessionEntry.from_dict(entry_data)
                except Exception as e:
                    logger.warning(f"Failed to parse session entry {key}: {e}")
            else:
                logger.warning(f"Invalid entry format for {key}")
        
        logger.debug(f"Loaded session store path={store_path}, entries={len(store)}, keys_sample={list(store.keys())[:3]}")
        return store
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse session store {store_path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Failed to load session store {store_path}: {e}")
        return {}


def save_session_store_to_path(
    store_path: Path | str,
    store: Dict[str, SessionEntry],
    skip_maintenance: bool = False,
    active_session_key: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> None:
    """
    Save session store to file path.
    
    Matches TS saveSessionStoreUnlocked() with maintenance support.
    
    Args:
        store_path: Path to store.json file (Path or str)
        store: Dict mapping canonical session keys to SessionEntry objects
        skip_maintenance: Skip prune/cap operations (default: False)
        active_session_key: Protected session key (won't be pruned/capped)
        agent_id: Agent ID for loading maintenance config
    """
    # Convert to Path if string
    if isinstance(store_path, str):
        store_path = Path(store_path)
    
    # Ensure parent directory exists
    store_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Apply maintenance before save (matches TS store.ts lines 390-401)
    if not skip_maintenance:
        try:
            from openclaw.agents.session_maintenance import (
                prune_stale_entries,
                cap_entry_count,
                resolve_maintenance_config,
            )
            from openclaw.config.loader import load_config
            
            # Load maintenance config from agent configuration
            maintenance_config = None
            if agent_id:
                try:
                    cfg = load_config()
                    agents_cfg = getattr(cfg, "agents", None)
                    if agents_cfg:
                        agent_list = agents_cfg.list if hasattr(agents_cfg, 'list') else []
                        for a in agent_list:
                            if a.id == agent_id:
                                agent_dict = a.model_dump() if hasattr(a, 'model_dump') else a.__dict__
                                session_cfg = agent_dict.get("session", {})
                                maintenance_config = session_cfg.get("maintenance")
                                break
                except Exception:
                    pass
            
            # Resolve config with defaults
            resolved = resolve_maintenance_config(maintenance_config)
            mode = resolved["mode"]
            
            # Apply maintenance directly to the passed-in store (in-place modification)
            if mode != "off":
                pruned = prune_stale_entries(
                    store,
                    override_max_age_ms=resolved["pruneAfterMs"],
                )
                capped = cap_entry_count(
                    store,
                    max_entries=resolved["maxEntries"],
                    active_session_key=active_session_key,
                )
                
                if pruned > 0 or capped > 0:
                    logger.info(
                        f"Session maintenance applied: pruned={pruned}, capped={capped}"
                    )
        except Exception as e:
            logger.warning(f"Session maintenance failed: {e}")
    
    # Convert SessionEntry objects to dicts
    data = {}
    for key, entry in store.items():
        if isinstance(entry, SessionEntry):
            # Use dataclass asdict or model_dump
            if hasattr(entry, 'model_dump'):
                data[key] = entry.model_dump()
            elif hasattr(entry, '__dataclass_fields__'):
                from dataclasses import asdict
                data[key] = asdict(entry)
            else:
                data[key] = entry.__dict__
        else:
            data[key] = entry
    
    # Write to file
    try:
        with open(store_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str)
        logger.debug(f"Saved session store path={store_path}, entries={len(data)}, keys_sample={list(data.keys())[:3]}")
    except Exception as e:
        logger.error(f"Failed to save session store {store_path}: {e}")
        raise


def update_session_store_with_mutator(
    store_path: Path | str,
    mutator: Callable[[Dict[str, SessionEntry]], None]
) -> None:
    """
    Update session store using mutator pattern (matches openclaw-ts).
    
    This provides atomic read-modify-write with a mutator function:
    1. Load store
    2. Call mutator(store) to modify in-place
    3. Save store
    
    Args:
        store_path: Path to store.json file
        mutator: Function that modifies the store dict in-place
    
    Example:
        ```python
        def mutator(store: Dict[str, SessionEntry]) -> None:
            entry = store.get("some-key")
            if entry:
                entry.updated_at = time.time()
        
        update_session_store_with_mutator(path, mutator)
        ```
    """
    # Load current store
    store = load_session_store_from_path(store_path)
    
    # Apply mutator
    try:
        mutator(store)
    except Exception as e:
        logger.error(f"Mutator failed: {e}")
        raise
    
    # Save modified store
    save_session_store_to_path(store_path, store)
    logger.debug(f"Updated session store at {store_path}")


# Alias for compatibility with sessions_methods.py
update_session_store = update_session_store_with_mutator
load_session_store = load_session_store_from_path
