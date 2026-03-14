"""
Session store maintenance utilities

Matches TypeScript src/config/sessions/store-maintenance.ts

Provides functions for:
- Pruning stale entries based on updatedAt threshold
- Capping entry count to max size
- Session disk budget enforcement
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Literal, Optional, TypedDict

from openclaw.agents.session_entry import SessionEntry

# Default constants (matches TS lines 12-16)
DEFAULT_SESSION_PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000  # 30 days
DEFAULT_SESSION_MAX_ENTRIES = 500
DEFAULT_SESSION_ROTATE_BYTES = 10_485_760  # 10 MB
DEFAULT_SESSION_MAINTENANCE_MODE = "warn"
DEFAULT_SESSION_DISK_BUDGET_HIGH_WATER_RATIO = 0.8


class SessionMaintenanceConfig(TypedDict, total=False):
    """Configuration for session maintenance"""
    mode: Literal["off", "warn", "auto"]
    pruneAfterMs: int
    pruneAfter: str  # Duration string
    pruneDays: int  # Legacy
    maxEntries: int
    rotateBytes: int
    maxDiskBytes: Optional[int]
    highWaterBytes: Optional[int]
    resetArchiveRetention: Optional[str]


class ResolvedSessionMaintenanceConfig(TypedDict):
    """Resolved maintenance configuration"""
    mode: Literal["off", "warn", "auto"]
    pruneAfterMs: int
    maxEntries: int
    rotateBytes: int
    resetArchiveRetentionMs: Optional[int]
    maxDiskBytes: Optional[int]
    highWaterBytes: Optional[int]


def resolve_maintenance_config(
    config: Optional[SessionMaintenanceConfig] = None
) -> ResolvedSessionMaintenanceConfig:
    """
    Resolve maintenance settings with defaults.
    
    Matches TS resolveMaintenanceConfig() lines 130-148.
    """
    if config is None:
        config = {}
    
    prune_after_ms = config.get("pruneAfterMs", DEFAULT_SESSION_PRUNE_AFTER_MS)
    max_disk_bytes = config.get("maxDiskBytes")
    
    high_water_bytes = config.get("highWaterBytes")
    if high_water_bytes is None and max_disk_bytes is not None:
        high_water_bytes = max(
            1,
            min(
                max_disk_bytes,
                int(max_disk_bytes * DEFAULT_SESSION_DISK_BUDGET_HIGH_WATER_RATIO)
            )
        )
    
    return {
        "mode": config.get("mode", DEFAULT_SESSION_MAINTENANCE_MODE),  # type: ignore
        "pruneAfterMs": prune_after_ms,
        "maxEntries": config.get("maxEntries", DEFAULT_SESSION_MAX_ENTRIES),
        "rotateBytes": config.get("rotateBytes", DEFAULT_SESSION_ROTATE_BYTES),
        "resetArchiveRetentionMs": None,  # TODO: resolve from config
        "maxDiskBytes": max_disk_bytes,
        "highWaterBytes": high_water_bytes,
    }


def prune_stale_entries(
    store: dict[str, SessionEntry],
    override_max_age_ms: Optional[int] = None,
    on_pruned: Optional[Callable[[str, SessionEntry], None]] = None,
) -> int:
    """
    Remove entries older than the configured threshold.
    
    Matches TS pruneStaleEntries() lines 155-174.
    
    Args:
        store: Session store (mutated in-place)
        override_max_age_ms: Override max age threshold
        on_pruned: Callback for each pruned entry
    
    Returns:
        Number of entries pruned
    """
    max_age_ms = override_max_age_ms or DEFAULT_SESSION_PRUNE_AFTER_MS
    cutoff_ms = int(time.time() * 1000) - max_age_ms
    
    pruned = 0
    keys_to_delete = []
    
    for key, entry in store.items():
        if entry.updatedAt is not None and entry.updatedAt < cutoff_ms:
            if on_pruned:
                on_pruned(key, entry)
            keys_to_delete.append(key)
            pruned += 1
    
    for key in keys_to_delete:
        del store[key]
    
    return pruned


def cap_entry_count(
    store: dict[str, SessionEntry],
    max_entries: int,
    active_session_key: Optional[str] = None,
    on_capped: Optional[Callable[[str, SessionEntry], None]] = None,
) -> int:
    """
    Keep only the most recent N entries.
    
    Matches TS capEntryCount() lines 209-253.
    
    Args:
        store: Session store (mutated in-place)
        max_entries: Maximum entries to keep
        active_session_key: Key to always preserve
        on_capped: Callback for each capped entry
    
    Returns:
        Number of entries capped
    """
    if len(store) <= max_entries:
        return 0
    
    # Sort by updatedAt (most recent first)
    entries = sorted(
        store.items(),
        key=lambda x: x[1].updatedAt if x[1].updatedAt is not None else 0,
        reverse=True
    )
    
    # Keep max_entries most recent
    to_keep = set()
    kept_count = 0
    
    for key, _ in entries:
        if kept_count >= max_entries:
            break
        to_keep.add(key)
        kept_count += 1
    
    # Always preserve active session
    if active_session_key and active_session_key in store:
        to_keep.add(active_session_key)
    
    # Remove the rest
    capped = 0
    keys_to_delete = []
    
    for key, entry in store.items():
        if key not in to_keep:
            if on_capped:
                on_capped(key, entry)
            keys_to_delete.append(key)
            capped += 1
    
    for key in keys_to_delete:
        del store[key]
    
    return capped


def get_directory_size(directory: Path) -> int:
    """
    Calculate total size of directory in bytes.
    
    Args:
        directory: Directory path
    
    Returns:
        Total size in bytes
    """
    if not directory.exists():
        return 0
    
    total = 0
    try:
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                total += file_path.stat().st_size
    except Exception:
        pass
    
    return total


def enforce_session_disk_budget(
    sessions_dir: Path,
    max_disk_bytes: int,
    high_water_bytes: Optional[int] = None,
    active_session_key: Optional[str] = None,
) -> dict[str, int]:
    """
    Enforce disk budget for sessions.
    
    Matches TS enforceSessionDiskBudget() concept.
    
    Strategy:
    1. Calculate current disk usage
    2. If over max, delete oldest session files until under high water mark
    3. Never delete active session
    
    Args:
        sessions_dir: Sessions directory
        max_disk_bytes: Maximum allowed disk usage
        high_water_bytes: Target after cleanup (defaults to 0.8 * max)
        active_session_key: Session key to preserve
    
    Returns:
        Dict with cleanup stats
    """
    if high_water_bytes is None:
        high_water_bytes = int(max_disk_bytes * DEFAULT_SESSION_DISK_BUDGET_HIGH_WATER_RATIO)
    
    current_size = get_directory_size(sessions_dir)
    
    if current_size <= max_disk_bytes:
        return {
            "current_bytes": current_size,
            "max_bytes": max_disk_bytes,
            "deleted_files": 0,
            "freed_bytes": 0,
        }
    
    # Collect all session files with their sizes and mod times
    session_files = []
    for file_path in sessions_dir.rglob("*.jsonl"):
        if file_path.is_file():
            # Skip active session
            if active_session_key and active_session_key in file_path.stem:
                continue
            
            try:
                stat = file_path.stat()
                session_files.append({
                    "path": file_path,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
            except Exception:
                continue
    
    # Sort by modification time (oldest first)
    session_files.sort(key=lambda x: x["mtime"])
    
    # Delete oldest files until under high water mark
    deleted_count = 0
    freed_bytes = 0
    
    for file_info in session_files:
        if current_size - freed_bytes <= high_water_bytes:
            break
        
        try:
            file_info["path"].unlink()
            deleted_count += 1
            freed_bytes += file_info["size"]
        except Exception:
            continue
    
    return {
        "current_bytes": current_size,
        "max_bytes": max_disk_bytes,
        "deleted_files": deleted_count,
        "freed_bytes": freed_bytes,
        "final_bytes": current_size - freed_bytes,
    }


__all__ = [
    "SessionMaintenanceConfig",
    "ResolvedSessionMaintenanceConfig",
    "resolve_maintenance_config",
    "prune_stale_entries",
    "cap_entry_count",
    "get_directory_size",
    "enforce_session_disk_budget",
    "DEFAULT_SESSION_PRUNE_AFTER_MS",
    "DEFAULT_SESSION_MAX_ENTRIES",
    "DEFAULT_SESSION_ROTATE_BYTES",
]
