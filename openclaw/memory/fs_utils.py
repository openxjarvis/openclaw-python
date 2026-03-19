"""Filesystem utilities for memory

Mirrors openclaw/src/memory/fs-utils.ts
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def hash_file(file_path: Path) -> str:
    """Compute SHA-256 hash of file content.
    
    Args:
        file_path: Path to file
        
    Returns:
        Hex hash string
    """
    hasher = hashlib.sha256()
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
    
    return hasher.hexdigest()


def resolve_memory_files(
    memory_dir: Path,
    pattern: str = "**/*.md",
) -> list[Path]:
    """Resolve memory files from memory directory.
    
    Args:
        memory_dir: Memory directory
        pattern: Glob pattern for files
        
    Returns:
        List of file paths
    """
    if not memory_dir.exists():
        return []
    
    return list(memory_dir.glob(pattern))


__all__ = [
    "hash_file",
    "resolve_memory_files",
]
