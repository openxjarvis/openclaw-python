"""Session files management for memory

Mirrors openclaw/src/memory/session-files.ts
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def resolve_session_files(
    sessions_dir: Path,
    agent_id: str | None = None,
) -> list[Path]:
    """Resolve session transcript files for indexing.
    
    Args:
        sessions_dir: Sessions directory
        agent_id: Optional agent ID filter
        
    Returns:
        List of session file paths
    """
    files = []
    
    if not sessions_dir.exists():
        return files
    
    # Find all .jsonl files
    for f in sessions_dir.glob("*.jsonl"):
        if f.is_file():
            files.append(f)
    
    return files


__all__ = ["resolve_session_files"]
