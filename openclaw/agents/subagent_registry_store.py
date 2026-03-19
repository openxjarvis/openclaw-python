"""
Subagent registry persistence (store and restore from disk).

Mirrors openclaw/src/agents/subagent-registry.store.ts
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 2


def resolve_subagent_registry_path() -> Path:
    """Resolve path to subagent registry file."""
    # Test mode: use temp directory
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("NODE_ENV") == "test":
        temp_dir = Path(tempfile.gettempdir()) / "openclaw-test-state" / str(os.getpid())
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / "subagents" / "runs.json"
    
    # Production: use state dir
    state_dir = resolve_state_dir()
    return state_dir / "subagents" / "runs.json"


def load_subagent_registry_from_disk() -> Dict[str, Dict[str, Any]]:
    """Load persisted subagent runs from disk.
    
    Returns:
        Dict mapping run_id to SubagentRunRecord
    """
    path = resolve_subagent_registry_path()
    
    if not path.exists():
        return {}
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load subagent registry from {path}: {e}")
        return {}
    
    if not isinstance(raw, dict):
        return {}
    
    version = raw.get('version')
    if version not in (1, 2):
        logger.warning(f"Unknown subagent registry version: {version}")
        return {}
    
    runs_raw = raw.get('runs', {})
    if not isinstance(runs_raw, dict):
        return {}
    
    # Parse and normalize runs
    out: Dict[str, Dict[str, Any]] = {}
    is_legacy = (version == 1)
    migrated = False
    
    for run_id, entry in runs_raw.items():
        if not isinstance(entry, dict):
            continue
        
        if not entry.get('runId'):
            continue
        
        # Handle legacy fields (v1 migration)
        if is_legacy:
            # Migrate announceCompletedAt -> cleanupCompletedAt
            if 'announceCompletedAt' in entry and 'cleanupCompletedAt' not in entry:
                entry['cleanupCompletedAt'] = entry.get('announceCompletedAt')
            
            # Migrate announceHandled -> cleanupHandled
            if 'announceHandled' in entry and 'cleanupHandled' not in entry:
                entry['cleanupHandled'] = entry.get('announceHandled')
            
            # Migrate requesterChannel/requesterAccountId -> requesterOrigin
            if 'requesterOrigin' not in entry:
                entry['requesterOrigin'] = {
                    'channel': entry.get('requesterChannel'),
                    'accountId': entry.get('requesterAccountId'),
                }
            
            # Remove legacy fields
            for old_key in ('announceCompletedAt', 'announceHandled', 
                          'requesterChannel', 'requesterAccountId'):
                entry.pop(old_key, None)
            
            migrated = True
        
        # Normalize spawnMode
        if entry.get('spawnMode') not in ('run', 'session'):
            entry['spawnMode'] = 'run'
        
        out[run_id] = entry
    
    # If we migrated from v1, save immediately
    if migrated:
        try:
            save_subagent_registry_to_disk(out)
        except Exception as e:
            logger.warning(f"Failed to save migrated registry: {e}")
    
    return out


def save_subagent_registry_to_disk(runs: Dict[str, Dict[str, Any]]) -> None:
    """Persist subagent runs to disk.
    
    Args:
        runs: Dict mapping run_id to SubagentRunRecord
    """
    path = resolve_subagent_registry_path()
    
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Serialize
    out = {
        'version': REGISTRY_VERSION,
        'runs': runs,
    }
    
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save subagent registry to {path}: {e}")
        raise
