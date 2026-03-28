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
        # Support both dict and SessionEntry
        updated_at = entry.get("updatedAt") if isinstance(entry, dict) else getattr(entry, "updatedAt", None)
        if updated_at is not None and updated_at < cutoff_ms:
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
    def get_updated_at(item):
        entry = item[1]
        updated_at = entry.get("updatedAt") if isinstance(entry, dict) else getattr(entry, "updatedAt", None)
        return updated_at if updated_at is not None else 0
    
    entries = sorted(
        store.items(),
        key=get_updated_at,
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
    "archive_removed_transcripts",
    "cleanup_archived_transcripts",
    "rotate_session_store",
    "apply_session_maintenance",
]


def archive_removed_transcripts(
    sessions_dir: Path,
    removed_session_ids: list[str],
    removed_session_keys: list[str] | None = None,
    workspace_root: Path | None = None,
    reason: str = "deleted",
    cleanup_workspaces: bool = True,
) -> list[str]:
    """
    Archive transcript files for removed sessions and optionally clean up workspaces.
    
    Matches TS archiveSessionTranscripts() concept with added workspace cleanup.
    
    Args:
        sessions_dir: Sessions directory
        removed_session_ids: List of session IDs to archive
        removed_session_keys: Optional list of session keys for workspace cleanup
        workspace_root: Workspace root directory for cleanup
        reason: Archive reason suffix
        cleanup_workspaces: Whether to clean up session workspaces
    
    Returns:
        List of archived file paths
    """
    from openclaw.gateway.session_utils import archive_session_transcripts
    
    archived_files = []
    for session_id in removed_session_ids:
        try:
            archived = archive_session_transcripts(
                session_id=session_id,
                store_path=str(sessions_dir),
                reason=reason,
                restrict_to_store_dir=True,
            )
            archived_files.extend(archived)
        except Exception:
            pass
    
    # Clean up session workspaces if requested
    if cleanup_workspaces and workspace_root and removed_session_keys:
        from openclaw.agents.session_workspace_cleanup import cleanup_session_workspace
        
        for session_key in removed_session_keys:
            try:
                cleanup_session_workspace(
                    workspace_root=workspace_root,
                    session_key=session_key,
                    dry_run=False,
                )
            except Exception as e:
                logger.warning(f"Failed to clean workspace for {session_key}: {e}")
    
    return archived_files


def cleanup_archived_transcripts(
    directories: list[Path],
    older_than_ms: int,
    reason: str = "deleted",
    now_ms: Optional[int] = None,
) -> int:
    """
    Clean up old archived transcripts.
    
    Matches TS cleanupArchivedSessionTranscripts() concept.
    
    Args:
        directories: Directories to clean
        older_than_ms: Age threshold in milliseconds
        reason: Archive reason to match
        now_ms: Current timestamp (defaults to now)
    
    Returns:
        Number of files deleted
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    
    cutoff_ms = now_ms - older_than_ms
    deleted_count = 0
    
    for directory in directories:
        if not directory.exists():
            continue
        
        # Find archived files matching pattern: *.{reason}.{timestamp}
        pattern = f"*.{reason}.*"
        for file_path in directory.glob(pattern):
            if not file_path.is_file():
                continue
            
            # Extract timestamp from filename
            try:
                parts = file_path.name.split(".")
                # Find timestamp after reason
                for i, part in enumerate(parts):
                    if part == reason and i + 1 < len(parts):
                        timestamp_str = parts[i + 1]
                        timestamp_ms = int(timestamp_str)
                        
                        if timestamp_ms < cutoff_ms:
                            file_path.unlink()
                            deleted_count += 1
                        break
            except Exception:
                continue
    
    return deleted_count


def rotate_session_store(
    store_path: Path,
    max_bytes: int,
) -> bool:
    """
    Rotate sessions.json if it exceeds size threshold.
    
    Matches TS concept of rotating large session stores.
    
    Args:
        store_path: Path to sessions.json
        max_bytes: Size threshold for rotation
    
    Returns:
        True if rotated, False otherwise
    """
    if not store_path.exists():
        return False
    
    try:
        size = store_path.stat().st_size
        if size <= max_bytes:
            return False
        
        # Rotate: sessions.json -> sessions.json.rotate.{timestamp}
        timestamp_ms = int(time.time() * 1000)
        rotated_name = f"{store_path.name}.rotate.{timestamp_ms}"
        rotated_path = store_path.parent / rotated_name
        
        store_path.rename(rotated_path)
        return True
    except Exception:
        return False


def apply_session_maintenance(
    agent_id: str,
    store_path: Path,
    sessions_dir: Path,
    config: Optional[SessionMaintenanceConfig] = None,
    active_session_key: Optional[str] = None,
    workspace_root: Optional[Path] = None,
) -> dict:
    """
    Apply session maintenance based on configuration.
    
    Main entry point that orchestrates all maintenance operations.
    Matches TS applySessionMaintenance() concept with added workspace cleanup.
    
    Args:
        agent_id: Agent ID for context
        store_path: Path to sessions.json
        sessions_dir: Sessions directory
        config: Maintenance configuration
        active_session_key: Currently active session to preserve
        workspace_root: Workspace root for session workspace cleanup
    
    Returns:
        Dictionary with maintenance results
    """
    resolved = resolve_maintenance_config(config)
    mode = resolved["mode"]
    
    if mode == "off":
        return {"mode": "off", "applied": False}
    
    # Load current store
    from openclaw.config.sessions.store import load_session_store, update_session_store
    
    store = load_session_store(str(store_path))
    
    results = {
        "mode": mode,
        "applied": mode == "enforce",
        "pruned": 0,
        "capped": 0,
        "archived": 0,
        "rotated": False,
        "disk_cleanup": None,
        "workspaces_cleaned": 0,
        "orphaned_workspaces_cleaned": 0,
    }
    
    removed_session_ids = []
    removed_session_keys = []
    
    def track_removal(key: str, entry):
        session_id = entry.get("sessionId") if isinstance(entry, dict) else getattr(entry, "sessionId", None)
        if session_id:
            removed_session_ids.append(session_id)
        # Track the session key too for workspace cleanup
        removed_session_keys.append(key)
    
    # 1. Prune stale entries
    if mode == "enforce":
        pruned = prune_stale_entries(
            store,
            override_max_age_ms=resolved["pruneAfterMs"],
            on_pruned=track_removal,
        )
        results["pruned"] = pruned
    
    # 2. Cap entry count
    if mode == "enforce":
        capped = cap_entry_count(
            store,
            max_entries=resolved["maxEntries"],
            active_session_key=active_session_key,
            on_capped=track_removal,
        )
        results["capped"] = capped
    
    # 3. Archive removed transcripts and clean up workspaces
    if mode == "enforce" and removed_session_ids:
        archived_files = archive_removed_transcripts(
            sessions_dir,
            removed_session_ids,
            removed_session_keys=removed_session_keys,
            workspace_root=workspace_root,
            reason="deleted",
            cleanup_workspaces=True,
        )
        results["archived"] = len(archived_files)
        results["workspaces_cleaned"] = len(removed_session_keys)
    
    # 4. Cleanup old archived transcripts
    if mode == "enforce" and resolved["resetArchiveRetentionMs"]:
        cleanup_archived_transcripts(
            directories=[sessions_dir],
            older_than_ms=resolved["resetArchiveRetentionMs"],
            reason="deleted",
        )
    
    # 5. Rotate sessions.json if too large
    if mode == "enforce":
        rotated = rotate_session_store(store_path, resolved["rotateBytes"])
        results["rotated"] = rotated
    
    # 6. Enforce disk budget
    if mode == "enforce" and resolved["maxDiskBytes"]:
        disk_result = enforce_session_disk_budget(
            sessions_dir,
            max_disk_bytes=resolved["maxDiskBytes"],
            high_water_bytes=resolved["highWaterBytes"],
            active_session_key=active_session_key,
        )
        results["disk_cleanup"] = disk_result
    
    # 7. Clean up orphaned workspaces (best effort)
    if mode == "enforce" and workspace_root:
        try:
            from openclaw.agents.session_workspace_cleanup import cleanup_orphaned_workspaces
            
            # Get active session keys from store
            active_keys = set(store.keys())
            
            orphan_stats = cleanup_orphaned_workspaces(
                workspace_root=workspace_root,
                active_session_keys=active_keys,
                older_than_hours=24,  # Only delete workspaces older than 24h
                dry_run=False,
            )
            results["orphaned_workspaces_cleaned"] = orphan_stats.get("deleted", 0)
            
            if orphan_stats.get("deleted", 0) > 0:
                logger.info(
                    f"Cleaned up {orphan_stats['deleted']} orphaned session workspaces"
                )
        except Exception as e:
            logger.warning(f"Failed to clean orphaned workspaces: {e}")
    
    # Save updated store if enforcement mode
    if mode == "enforce":
        def save_mutator(store_dict):
            store_dict.clear()
            store_dict.update(store)
        
        update_session_store(str(store_path), save_mutator)
    
    return results

