"""
Lane resolution for embedded Pi agent runs

Matches TypeScript src/agents/pi-embedded-runner/lanes.ts

Provides session lane and global lane resolution for two-tier queuing.
"""
from __future__ import annotations

from typing import Optional

# Lane constants (matches TS CommandLane enum)
LANE_MAIN = "main"
LANE_CRON = "cron"
LANE_SUBAGENT = "subagent"
LANE_NESTED = "nested"


def resolve_session_lane(key: str) -> str:
    """
    Resolve session lane key.
    
    Matches TS resolveSessionLane() lines 3-6.
    
    Ensures key has "session:" prefix.
    
    Args:
        key: Session key or session ID
    
    Returns:
        Session lane key (e.g., "session:agent:main:main")
    """
    cleaned = key.strip() or LANE_MAIN
    return cleaned if cleaned.startswith("session:") else f"session:{cleaned}"


def resolve_global_lane(lane: Optional[str] = None) -> str:
    """
    Resolve global lane.
    
    Matches TS resolveGlobalLane() lines 8-11.
    
    Args:
        lane: Lane name (main, cron, subagent, nested)
    
    Returns:
        Global lane key
    """
    cleaned = lane.strip() if lane else None
    return cleaned if cleaned else LANE_MAIN


def resolve_embedded_session_lane(key: str) -> str:
    """
    Resolve embedded session lane (alias for resolve_session_lane).
    
    Matches TS resolveEmbeddedSessionLane() lines 13-15.
    
    Args:
        key: Session key or session ID
    
    Returns:
        Session lane key
    """
    return resolve_session_lane(key)


__all__ = [
    "LANE_MAIN",
    "LANE_CRON",
    "LANE_SUBAGENT",
    "LANE_NESTED",
    "resolve_session_lane",
    "resolve_global_lane",
    "resolve_embedded_session_lane",
]
