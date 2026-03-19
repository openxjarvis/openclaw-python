"""Memory status formatting

Mirrors openclaw/src/memory/status-format.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_memory_status(
    backend: str,
    stats: dict[str, Any],
) -> str:
    """Format memory status for display.
    
    Args:
        backend: Backend name
        stats: Status statistics
        
    Returns:
        Formatted status string
    """
    lines = [
        f"Memory Backend: {backend}",
        f"Total Files: {stats.get('total_files', 0)}",
        f"Total Chunks: {stats.get('total_chunks', 0)}",
        f"Indexed Size: {_format_bytes(stats.get('indexed_bytes', 0))}",
    ]
    
    if "last_sync" in stats:
        lines.append(f"Last Sync: {stats['last_sync']}")
    
    return "\n".join(lines)


def _format_bytes(bytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.1f} TB"


__all__ = ["format_memory_status"]
