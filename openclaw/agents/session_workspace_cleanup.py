"""
Session workspace cleanup utilities

Provides functions to clean up session workspace directories when sessions are deleted.
Mirrors TypeScript session cleanup behavior.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def extract_session_key_from_workspace_slug(slug: str) -> str | None:
    """
    Try to extract the original session key from a workspace directory slug.
    
    Workspace slugs have format: {sanitized-key}-{8-char-hash}
    This function attempts reverse lookup, but it's not guaranteed to be exact.
    
    Args:
        slug: Workspace directory name (e.g., "agent-main-telegram-direct-83660-6237ac86")
    
    Returns:
        Best-guess session key or None
    """
    # Remove the hash suffix (last 9 chars: "-" + 8 hex chars)
    if len(slug) < 10:
        return None
    
    # Check if it ends with -{8 hex chars}
    match = re.match(r"^(.+)-([0-9a-f]{8})$", slug)
    if match:
        base = match.group(1)
        # Reconstruct possible session key (replace dashes with colons for agent keys)
        # This is a best-effort guess
        return base
    
    return None


def cleanup_session_workspace(
    workspace_root: Path | str,
    session_key: str,
    dry_run: bool = False,
) -> bool:
    """
    Clean up workspace directory for a specific session.
    
    Args:
        workspace_root: Base workspace directory
        session_key: Session key to clean up
        dry_run: If True, only log what would be deleted
    
    Returns:
        True if cleanup succeeded or directory doesn't exist, False on error
    """
    from openclaw.agents.session_workspace import slugify_session_key
    
    root = Path(workspace_root).expanduser().resolve()
    slug = slugify_session_key(session_key)
    session_dir = root / slug
    
    if not session_dir.exists():
        logger.debug(f"Session workspace doesn't exist: {session_dir}")
        return True
    
    if not session_dir.is_dir():
        logger.warning(f"Session workspace path is not a directory: {session_dir}")
        return False
    
    try:
        if dry_run:
            # Count files for reporting
            file_count = sum(1 for _ in session_dir.rglob("*") if _.is_file())
            logger.info(
                f"[DRY RUN] Would delete session workspace: {session_dir} ({file_count} files)"
            )
            return True
        
        # Delete directory recursively
        import shutil
        shutil.rmtree(session_dir)
        logger.info(f"Deleted session workspace: {session_dir}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to clean up session workspace {session_dir}: {e}")
        return False


def cleanup_orphaned_workspaces(
    workspace_root: Path | str,
    active_session_keys: set[str],
    older_than_hours: int = 24,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Clean up workspace directories that don't have corresponding active sessions.
    
    Safety measures:
    - Only deletes directories with session-like names (contain hash suffix)
    - Only deletes directories older than specified hours
    - Skips directories currently being accessed (optional)
    
    Args:
        workspace_root: Base workspace directory
        active_session_keys: Set of currently active session keys
        older_than_hours: Only delete workspaces older than this many hours
        dry_run: If True, only report what would be deleted
    
    Returns:
        Dict with cleanup stats: {"checked": N, "deleted": N, "skipped": N, "errors": N}
    """
    from openclaw.agents.session_workspace import slugify_session_key
    
    root = Path(workspace_root).expanduser().resolve()
    
    if not root.exists():
        logger.debug(f"Workspace root doesn't exist: {root}")
        return {"checked": 0, "deleted": 0, "skipped": 0, "errors": 0}
    
    # Build set of expected slugs for active sessions
    active_slugs = {slugify_session_key(key) for key in active_session_keys}
    
    cutoff_time = time.time() - (older_than_hours * 3600)
    
    stats = {"checked": 0, "deleted": 0, "skipped": 0, "errors": 0}
    
    # Scan workspace root for session directories
    for item in root.iterdir():
        if not item.is_dir():
            continue
        
        # Skip non-session directories (don't have hash suffix)
        if not re.match(r"^.+-[0-9a-f]{8}$", item.name):
            continue
        
        stats["checked"] += 1
        
        # Check if this is an active session
        if item.name in active_slugs:
            stats["skipped"] += 1
            logger.debug(f"Skipping active session workspace: {item.name}")
            continue
        
        # Check age
        try:
            mtime = item.stat().st_mtime
            if mtime >= cutoff_time:
                stats["skipped"] += 1
                age_hours = (time.time() - mtime) / 3600
                logger.debug(
                    f"Skipping recent workspace: {item.name} (age: {age_hours:.1f}h)"
                )
                continue
        except Exception as e:
            logger.warning(f"Failed to check mtime for {item.name}: {e}")
            stats["errors"] += 1
            continue
        
        # This is an orphaned workspace - delete it
        try:
            age_days = (time.time() - mtime) / 86400
            
            if dry_run:
                file_count = sum(1 for _ in item.rglob("*") if _.is_file())
                logger.info(
                    f"[DRY RUN] Would delete orphaned workspace: {item.name} "
                    f"(age: {age_days:.1f}d, files: {file_count})"
                )
                stats["deleted"] += 1
            else:
                import shutil
                shutil.rmtree(item)
                logger.info(
                    f"Deleted orphaned workspace: {item.name} (age: {age_days:.1f}d)"
                )
                stats["deleted"] += 1
                
        except Exception as e:
            logger.error(f"Failed to delete orphaned workspace {item.name}: {e}")
            stats["errors"] += 1
    
    return stats


def get_workspace_disk_usage(workspace_root: Path | str) -> dict[str, int]:
    """
    Calculate disk usage for workspace directory.
    
    Args:
        workspace_root: Base workspace directory
    
    Returns:
        Dict with stats: {"total_bytes": N, "session_dirs": N, "total_files": N}
    """
    root = Path(workspace_root).expanduser().resolve()
    
    if not root.exists():
        return {"total_bytes": 0, "session_dirs": 0, "total_files": 0}
    
    total_bytes = 0
    session_dirs = 0
    total_files = 0
    
    for item in root.iterdir():
        if item.is_dir() and re.match(r"^.+-[0-9a-f]{8}$", item.name):
            session_dirs += 1
            for file in item.rglob("*"):
                if file.is_file():
                    total_files += 1
                    try:
                        total_bytes += file.stat().st_size
                    except Exception:
                        pass
    
    return {
        "total_bytes": total_bytes,
        "session_dirs": session_dirs,
        "total_files": total_files,
    }


__all__ = [
    "cleanup_session_workspace",
    "cleanup_orphaned_workspaces",
    "get_workspace_disk_usage",
    "extract_session_key_from_workspace_slug",
]
