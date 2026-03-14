"""
Subagent auto-archive service

Periodically checks for expired subagent sessions and archives them.
Mirrors TS auto-archive behavior from subagent-registry.ts
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Global background task
_archive_task: asyncio.Task | None = None
_archive_enabled = False


def calculate_archive_at_ms(archive_after_minutes: int) -> int:
    """
    Calculate archive timestamp based on archiveAfterMinutes config.
    
    Args:
        archive_after_minutes: Minutes until archive
        
    Returns:
        Timestamp in milliseconds
    """
    now_ms = int(time.time() * 1000)
    return now_ms + (archive_after_minutes * 60 * 1000)


async def _archive_expired_subagents(cfg: Any, gateway: Any) -> None:
    """
    Check for expired subagents and archive them.
    
    Archives sessions past their archiveAtMs timestamp.
    """
    from openclaw.agents.subagent_registry import get_global_registry
    
    registry = get_global_registry()
    now_ms = int(time.time() * 1000)
    
    # Get all runs
    all_runs = registry.list_all_runs()
    
    for run in all_runs:
        # Skip if not ready for archive
        if not run.archive_at_ms or run.archive_at_ms > now_ms:
            continue
        
        # Skip if already cleaned up
        if run.cleanup_completed_at:
            continue
        
        child_session_key = run.child_session_key
        logger.info(f"Auto-archiving subagent session {child_session_key} (run_id={run.run_id})")
        
        try:
            # Call sessions.delete to archive the session
            if gateway and hasattr(gateway, "session_manager"):
                session_manager = gateway.session_manager
                
                # Delete session (which renames transcript)
                if hasattr(session_manager, "delete_session"):
                    await session_manager.delete_session(child_session_key)
                elif hasattr(session_manager, "delete"):
                    # Try alternate method name
                    await session_manager.delete(child_session_key)
                else:
                    logger.warning(f"session_manager has no delete method for {child_session_key}")
            
            # Mark cleanup as completed
            run.cleanup_completed_at = now_ms
            run.cleanup_handled = True
            
        except Exception as e:
            logger.error(f"Failed to archive subagent {child_session_key}: {e}", exc_info=True)


async def _archive_worker(cfg: Any, gateway: Any) -> None:
    """
    Background worker that periodically checks for expired subagents.
    
    Runs every 60 seconds (similar to TS implementation).
    """
    logger.info("Subagent auto-archive worker started")
    
    while _archive_enabled:
        try:
            await _archive_expired_subagents(cfg, gateway)
        except Exception as e:
            logger.error(f"Archive worker error: {e}", exc_info=True)
        
        # Check every 60 seconds
        await asyncio.sleep(60)
    
    logger.info("Subagent auto-archive worker stopped")


def start_archive_service(cfg: Any, gateway: Any) -> None:
    """
    Start the auto-archive background service.
    
    Args:
        cfg: Configuration object
        gateway: Gateway instance with session_manager
    """
    global _archive_task, _archive_enabled
    
    if _archive_task is not None:
        logger.warning("Archive service already running")
        return
    
    # Check if archiveAfterMinutes is configured
    archive_minutes = 60  # default
    try:
        if hasattr(cfg, "agents") and hasattr(cfg.agents, "defaults"):
            subagents_cfg = getattr(cfg.agents.defaults, "subagents", None)
            if subagents_cfg and hasattr(subagents_cfg, "archiveAfterMinutes"):
                archive_minutes = subagents_cfg.archiveAfterMinutes
    except Exception as e:
        logger.debug(f"Failed to read archiveAfterMinutes config: {e}")
    
    if archive_minutes <= 0:
        logger.info("Subagent auto-archive disabled (archiveAfterMinutes <= 0)")
        return
    
    logger.info(f"Starting subagent auto-archive service (archiveAfterMinutes={archive_minutes})")
    _archive_enabled = True
    _archive_task = asyncio.create_task(_archive_worker(cfg, gateway))


def stop_archive_service() -> None:
    """
    Stop the auto-archive background service.
    """
    global _archive_task, _archive_enabled
    
    if _archive_task is None:
        return
    
    logger.info("Stopping subagent auto-archive service")
    _archive_enabled = False
    
    if _archive_task and not _archive_task.done():
        _archive_task.cancel()
    
    _archive_task = None
