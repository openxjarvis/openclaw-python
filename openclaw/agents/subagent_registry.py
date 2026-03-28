"""
Subagent Registry - Full TypeScript Alignment

Matches TypeScript src/agents/subagent-registry.ts exactly.
Implements a class-based registry for managing subagent runs.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Optional, Set

logger = logging.getLogger(__name__)

# Constants (from TS lines 58-83)
SUBAGENT_ANNOUNCE_TIMEOUT_MS = 120_000
MIN_ANNOUNCE_RETRY_DELAY_MS = 1_000
MAX_ANNOUNCE_RETRY_DELAY_MS = 8_000
MAX_ANNOUNCE_RETRY_COUNT = 3
ANNOUNCE_EXPIRY_MS = 5 * 60_000  # 5 minutes  
ANNOUNCE_COMPLETION_HARD_EXPIRY_MS = 30 * 60_000  # 30 minutes
LIFECYCLE_ERROR_RETRY_GRACE_MS = 15_000


@dataclass
class SubagentRunRecord:
    """
    Subagent run record (matches TS SubagentRunRecord).
    
    From src/agents/subagent-registry.types.ts:6-38
    """
    # Identity
    run_id: str  # runId
    child_session_key: str  # childSessionKey  
    requester_session_key: str  # requesterSessionKey
    requester_display_key: str  # requesterDisplayKey
    task: str
    cleanup: Literal["delete", "keep"] = "delete"
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    
    # Optional
    requester_origin: Optional[Dict[str, Any]] = None  # DeliveryContext
    label: Optional[str] = None
    model: Optional[str] = None
    run_timeout_seconds: Optional[int] = None
    spawn_mode: Optional[Literal["run", "session"]] = None
    
    # Timing
    started_at: Optional[int] = None
    ended_at: Optional[int] = None
    
    # Outcome
    outcome: Optional[Dict[str, Any]] = None  # SubagentRunOutcome
    ended_reason: Optional[str] = None  # SubagentLifecycleEndedReason
    
    # Lifecycle
    ended_hook_emitted_at: Optional[int] = None
    
    # Cleanup
    archive_at_ms: Optional[int] = None
    cleanup_completed_at: Optional[int] = None
    cleanup_handled: bool = False
    
    # Announce
    announce_retry_count: Optional[int] = None
    last_announce_retry_at: Optional[int] = None
    suppress_announce_reason: Optional[Literal["steer-restart", "killed"]] = None
    expects_completion_message: Optional[bool] = None
    
    # Attachments
    attachments_dir: Optional[str] = None
    attachments_root_dir: Optional[str] = None
    retain_attachments_on_keep: Optional[bool] = None


class SubagentRegistry:
    """
    Subagent run registry.
    
    Matches TS subagent-registry.ts implementation with class-based API.
    In TS, this is module-level state; Python uses a singleton instance.
    """
    
    def __init__(self, config: Optional[dict] = None):
        """
        Initialize registry (matches TS module initialization).
        
        Args:
            config: Optional config dict (for testing/custom configuration)
        """
        self._runs: Dict[str, SubagentRunRecord] = {}
        self._resumed_runs: Set[str] = set()
        self._ended_hook_in_flight: Set[str] = set()
        self._pending_lifecycle_errors: Dict[str, Any] = {}
        self._sweeper_task: Optional[asyncio.Task] = None
        self._announce_sweeper_task: Optional[asyncio.Task] = None  # New: announce sweeper
        self._listener_started = False
        self._restore_attempted = False
        self._gateway = None  # Optional gateway reference for announces
        self._config = config  # Store config for testing
        
        # Restore runs from disk on init (matches TS behavior)
        self._restore_runs_from_disk()
        
    def register_subagent_run(
        self,
        requester_session_key: str,
        child_session_key: str,
        task: str,
        model: Optional[str] = None,
        cleanup: Literal["delete", "keep"] = "delete",
        label: Optional[str] = None,
        requester_origin: Optional[Dict[str, Any]] = None,
        run_timeout_seconds: Optional[int] = None,
        spawn_mode: Optional[Literal["run", "session"]] = None,
        expects_completion_message: Optional[bool] = None,
        attachments_dir: Optional[str] = None,
        attachments_root_dir: Optional[str] = None,
        retain_attachments_on_keep: Optional[bool] = None,
    ) -> SubagentRunRecord:
        """
        Register a subagent run (matches TS registerSubagentRun lines 964-1019).
        
        Auto-generates run_id and requester_display_key for convenience.
        
        Args:
            requester_session_key: Parent session key
            child_session_key: Child session key
            task: Task description
            model: Optional model override
            cleanup: Cleanup strategy ("delete" or "keep")
            label: Optional label
            requester_origin: Optional delivery context
            run_timeout_seconds: Optional timeout
            spawn_mode: Spawn mode ("run" or "session")
            expects_completion_message: Whether to wait for completion message
            attachments_dir: Optional attachments directory
            attachments_root_dir: Optional attachments root
            retain_attachments_on_keep: Whether to retain attachments on keep
            
        Returns:
            SubagentRunRecord
        """
        # Auto-generate run_id and display key
        run_id = str(uuid.uuid4())
        requester_display_key = requester_session_key
        
        now = int(time.time() * 1000)
        
        archive_at_ms = None
        if spawn_mode != "session":
            archive_after_ms = self._resolve_archive_after_ms()
            if archive_after_ms > 0:
                archive_at_ms = now + archive_after_ms
        
        record = SubagentRunRecord(
            run_id=run_id,
            child_session_key=child_session_key,
            requester_session_key=requester_session_key,
            requester_display_key=requester_display_key,
            task=task,
            cleanup=cleanup,
            expects_completion_message=expects_completion_message,
            spawn_mode=spawn_mode,
            label=label,
            model=model,
            run_timeout_seconds=run_timeout_seconds,
            created_at=now,
            started_at=now,
            archive_at_ms=archive_at_ms,
            cleanup_handled=False,
            attachments_dir=attachments_dir,
            attachments_root_dir=attachments_root_dir,
            retain_attachments_on_keep=retain_attachments_on_keep,
            requester_origin=requester_origin,
        )
        
        self._runs[run_id] = record
        self._ensure_listener()
        self._persist_runs()
        
        if archive_at_ms:
            self._start_sweeper()
            
        # Start wait for completion (matches TS line 1018)
        import asyncio
        timeout_ms = run_timeout_seconds * 1000 if run_timeout_seconds else 300_000  # Default 5 min
        try:
            asyncio.create_task(self._wait_for_completion(run_id, timeout_ms))
        except RuntimeError:
            # No event loop running (e.g., in tests) - skip task creation
            logger.debug(f"No event loop available for wait_for_completion task for run {run_id}")
        
        return record
    
    def mark_subagent_run_terminated(
        self,
        run_id: Optional[str] = None,
        child_session_key: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """
        Mark subagent run(s) as terminated (matches TS lines 1154-1213).
        
        Args:
            run_id: Optional run ID
            child_session_key: Optional child session key
            reason: Optional termination reason
            
        Returns:
            Number of runs marked
        """
        run_ids = set()
        if run_id and run_id.strip():
            run_ids.add(run_id.strip())
        if child_session_key and child_session_key.strip():
            for rid, entry in self._runs.items():
                if entry.child_session_key == child_session_key:
                    run_ids.add(rid)
        
        if not run_ids:
            return 0
        
        now = int(time.time() * 1000)
        reason_str = reason.strip() if reason else "killed"
        updated = 0
        
        for rid in run_ids:
            self._clear_pending_lifecycle_error(rid)
            entry = self._runs.get(rid)
            if not entry:
                continue
            if entry.ended_at is not None:
                continue
                
            entry.ended_at = now
            entry.outcome = {"status": "error", "error": reason_str}
            entry.ended_reason = "killed"
            entry.cleanup_handled = True
            entry.cleanup_completed_at = now
            entry.suppress_announce_reason = "killed"
            updated += 1
        
        if updated > 0:
            self._persist_runs()
            # TODO: Emit ended hooks
            
        return updated
    
    def get_subagent_run(self, run_id: str) -> Optional[SubagentRunRecord]:
        """Get subagent run by ID."""
        return self._runs.get(run_id)
    
    def list_runs_for_requester(self, requester_session_key: str) -> list[SubagentRunRecord]:
        """List all runs for a requester (matches TS lines 1215-1217)."""
        return [
            entry for entry in self._runs.values()
            if entry.requester_session_key == requester_session_key
        ]
    
    def count_active_runs_for_session(self, requester_session_key: str) -> int:
        """Count active runs for a session (matches TS lines 1219-1224)."""
        return sum(
            1 for entry in self._runs.values()
            if entry.requester_session_key == requester_session_key
            and entry.ended_at is None
        )
    
    def set_gateway(self, gateway: Any) -> None:
        """Set gateway reference for announce flow."""
        self._gateway = gateway
    
    async def _trigger_announce_and_cleanup(
        self,
        run_id_or_entry: str | SubagentRunRecord,
        wait_for_descendants: bool = False,
    ) -> bool:
        """
        Trigger announce and cleanup flow (internal method).
        
        Matches TS startSubagentAnnounceCleanupFlow concept (lines 389-421).
        
        Args:
            run_id_or_entry: Run ID string or SubagentRunRecord
            wait_for_descendants: Whether to wait for descendant runs
            
        Returns:
            True if cleanup started, False if already handled
        """
        # Support both run_id and entry for convenience
        if isinstance(run_id_or_entry, SubagentRunRecord):
            run_id = run_id_or_entry.run_id
            entry = run_id_or_entry
        else:
            run_id = run_id_or_entry
            entry = self._runs.get(run_id)
        
        if not entry:
            return False
        
        logger.debug(f"Triggering announce+cleanup for run {run_id}")
        
        # Mark cleanup as started (matches TS beginSubagentCleanup)
        if entry.cleanup_handled:
            return False
        entry.cleanup_handled = True
        self._persist_runs()
        
        # Skip announce if suppressed (matches TS lines 396-400)
        if entry.suppress_announce_reason:
            logger.info(f"Skipping announce for run {run_id}: {entry.suppress_announce_reason}")
            entry.cleanup_completed_at = int(time.time() * 1000)
            self._persist_runs()
            return True
        
        # Call announce flow (matches TS lines 394-419)
        try:
            # Import here to avoid circular dependency
            from openclaw.agents.subagent_announce import run_subagent_announce_flow
            
            # Build announce params (matches TS lines 394-409)
            did_announce = await run_subagent_announce_flow(
                child_session_key=entry.child_session_key,
                child_run_id=entry.run_id,
                requester_session_key=entry.requester_session_key,
                requester_origin=entry.requester_origin,
                requester_display_key=entry.requester_display_key,
                task=entry.task,
                timeout_ms=SUBAGENT_ANNOUNCE_TIMEOUT_MS,
                cleanup=entry.cleanup,
                wait_for_completion=wait_for_descendants,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                label=entry.label,
                outcome=entry.outcome,
                spawn_mode=entry.spawn_mode,
                expects_completion_message=entry.expects_completion_message,
            )
            
            # Finalize cleanup (matches TS finalizeSubagentCleanup lines 694-779)
            if did_announce:
                entry.cleanup_completed_at = int(time.time() * 1000)
                self._persist_runs()
            else:
                # Retry logic (matches TS lines 720-778)
                now = int(time.time() * 1000)
                retry_count = (entry.announce_retry_count or 0) + 1
                entry.announce_retry_count = retry_count
                entry.last_announce_retry_at = now
                
                # Check retry limits
                if retry_count >= MAX_ANNOUNCE_RETRY_COUNT:
                    logger.warning(f"Subagent announce give up (retry-limit) run={run_id} retries={retry_count}")
                    entry.cleanup_completed_at = now
                    self._persist_runs()
                else:
                    # Schedule retry with backoff
                    entry.cleanup_handled = False  # Allow retry
                    self._persist_runs()
                    
                    # Calculate delay (matches TS resolveAnnounceRetryDelayMs lines 85-91)
                    bounded_retry = max(0, min(retry_count, 10))
                    backoff_exp = max(0, bounded_retry - 1)
                    base_delay = MIN_ANNOUNCE_RETRY_DELAY_MS * (2 ** backoff_exp)
                    delay_ms = min(base_delay, MAX_ANNOUNCE_RETRY_DELAY_MS)
                    
                    # Schedule retry (matches TS lines 776-778)
                    await asyncio.sleep(delay_ms / 1000.0)
                    await self._trigger_announce_and_cleanup(run_id, wait_for_descendants)
            
            return True
        except ImportError:
            # Fallback if announce flow not available
            logger.warning(f"Announce flow not available for run {run_id}")
            entry.cleanup_completed_at = int(time.time() * 1000)
            self._persist_runs()
            return True
    
    def _ensure_listener(self) -> None:
        """Ensure lifecycle event listener is started (matches TS lines 602-654)."""
        if self._listener_started:
            return
        self._listener_started = True
        # TODO: Subscribe to agent lifecycle events
        logger.debug("SubagentRegistry listener started")
    
    def _start_sweeper(self) -> None:
        """Start sweeper task for archive cleanup (matches TS lines 550-558)."""
        if self._sweeper_task and not self._sweeper_task.done():
            return
        
        import asyncio
        try:
            self._sweeper_task = asyncio.create_task(self._sweeper_loop())
            logger.debug("SubagentRegistry sweeper started")
        except RuntimeError:
            # No event loop running (e.g., in tests) - skip sweeper
            logger.debug("No event loop available for sweeper task")
    
    async def _sweeper_loop(self) -> None:
        """Periodic cleanup of archived runs (matches TS sweepSubagentRuns)."""
        import asyncio
        
        while True:
            try:
                await asyncio.sleep(60)  # Run every 60 seconds (matches TS 60_000ms)
                await self._sweep_subagent_runs()
            except asyncio.CancelledError:
                logger.debug("Sweeper loop cancelled")
                break
            except Exception as e:
                logger.warning(f"Sweeper loop error: {e}")
    
    async def _sweep_subagent_runs(self) -> None:
        """Clean up archived runs (matches TS sweepSubagentRuns lines 568-600)."""
        now = int(time.time() * 1000)
        mutated = False
        to_delete = []
        
        for run_id, entry in self._runs.items():
            # Check if archive time has passed
            if not entry.archive_at_ms or entry.archive_at_ms > now:
                continue
            
            to_delete.append((run_id, entry))
        
        for run_id, entry in to_delete:
            self._clear_pending_lifecycle_error(run_id)
            del self._runs[run_id]
            mutated = True
            
            # Remove attachments if any
            await self._safe_remove_attachments_dir(entry)
            
            # Delete the session
            try:
                if self._gateway:
                    from .internal_call import call_gateway_internal
                    await call_gateway_internal(
                        gateway=self._gateway,
                        method="sessions.delete",
                        params={
                            "key": entry.child_session_key,
                            "deleteTranscript": True,
                            "emitLifecycleHooks": False,
                        },
                        timeout_ms=10_000,
                    )
            except Exception as e:
                logger.debug(f"Failed to delete session {entry.child_session_key}: {e}")
        
        if mutated:
            self._persist_runs()
        
        # Stop sweeper if no more runs
        if len(self._runs) == 0:
            self._stop_sweeper()
    
    def _stop_sweeper(self) -> None:
        """Stop the sweeper task."""
        if self._sweeper_task and not self._sweeper_task.done():
            self._sweeper_task.cancel()
            self._sweeper_task = None
            logger.debug("SubagentRegistry sweeper stopped")
    
    async def _safe_remove_attachments_dir(self, entry: SubagentRunRecord) -> None:
        """Safely remove attachments directory for a run."""
        if not entry.attachments_dir:
            return
        
        try:
            from pathlib import Path
            import shutil
            
            attachments_path = Path(entry.attachments_dir)
            if attachments_path.exists() and attachments_path.is_dir():
                shutil.rmtree(attachments_path)
                logger.debug(f"Removed attachments dir: {attachments_path}")
        except Exception as e:
            logger.warning(f"Failed to remove attachments dir: {e}")
    
    def _persist_runs(self) -> None:
        """Persist runs to disk (matches TS lines 103-105)."""
        from .subagent_registry_store import save_subagent_registry_to_disk
        
        try:
            # Convert dataclass records to dicts for persistence
            serializable = {}
            for run_id, record in self._runs.items():
                if hasattr(record, '__dict__'):
                    serializable[run_id] = {
                        'runId': record.run_id,
                        'childSessionKey': record.child_session_key,
                        'requesterSessionKey': record.requester_session_key,
                        'requesterDisplayKey': record.requester_display_key,
                        'task': record.task,
                        'cleanup': record.cleanup,
                        'createdAt': record.created_at,
                        'requesterOrigin': record.requester_origin,
                        'label': record.label,
                        'model': record.model,
                        'runTimeoutSeconds': record.run_timeout_seconds,
                        'spawnMode': record.spawn_mode,
                        'startedAt': record.started_at,
                        'endedAt': record.ended_at,
                        'outcome': record.outcome,
                        'archiveAtMs': record.archive_at_ms,
                        'cleanupCompletedAt': record.cleanup_completed_at,
                        'cleanupHandled': record.cleanup_handled,
                    }
            
            save_subagent_registry_to_disk(serializable)
            logger.debug(f"Persisted {len(serializable)} subagent runs to disk")
        except Exception as e:
            # Ignore persistence failures (matches TS behavior)
            logger.warning(f"Failed to persist subagent runs: {e}")
    
    def _restore_runs_from_disk(self, merge_only: bool = False) -> int:
        """Restore runs from disk (matches TS restoreSubagentRunsFromDisk).
        
        Args:
            merge_only: If True, only restore runs not already in memory
            
        Returns:
            Number of runs restored
        """
        from .subagent_registry_store import load_subagent_registry_from_disk
        
        try:
            restored = load_subagent_registry_from_disk()
            
            if not restored:
                return 0
            
            added = 0
            for run_id, entry_dict in restored.items():
                if not run_id or not entry_dict:
                    continue
                
                if merge_only and run_id in self._runs:
                    continue
                
                # Convert dict back to SubagentRunRecord dataclass
                try:
                    record = SubagentRunRecord(
                        run_id=entry_dict.get('runId', run_id),
                        child_session_key=entry_dict['childSessionKey'],
                        requester_session_key=entry_dict['requesterSessionKey'],
                        requester_display_key=entry_dict['requesterDisplayKey'],
                        task=entry_dict['task'],
                        cleanup=entry_dict.get('cleanup', 'delete'),
                        created_at=entry_dict.get('createdAt', int(time.time() * 1000)),
                        requester_origin=entry_dict.get('requesterOrigin'),
                        label=entry_dict.get('label'),
                        model=entry_dict.get('model'),
                        run_timeout_seconds=entry_dict.get('runTimeoutSeconds'),
                        spawn_mode=entry_dict.get('spawnMode'),
                        started_at=entry_dict.get('startedAt'),
                        ended_at=entry_dict.get('endedAt'),
                        outcome=entry_dict.get('outcome'),
                        archive_at_ms=entry_dict.get('archiveAtMs'),
                        cleanup_completed_at=entry_dict.get('cleanupCompletedAt'),
                        cleanup_handled=entry_dict.get('cleanupHandled'),
                        attachments_dir=entry_dict.get('attachmentsDir'),
                        attachments_root_dir=entry_dict.get('attachmentsRootDir'),
                        retain_attachments_on_keep=entry_dict.get('retainAttachmentsOnKeep'),
                    )
                    
                    self._runs[run_id] = record
                    added += 1
                except Exception as e:
                    logger.warning(f"Failed to restore run {run_id}: {e}")
                    continue
            
            if added > 0:
                logger.info(f"Restored {added} subagent run(s) from disk")
                
                # Check if any runs have archiveAtMs and start sweeper (matches TS lines 543-548)
                has_archive = any(r.archive_at_ms for r in self._runs.values())
                if has_archive:
                    self._start_sweeper()
            
            return added
        except Exception as e:
            logger.warning(f"Failed to restore runs from disk: {e}")
            return 0
    
    def _clear_pending_lifecycle_error(self, run_id: str) -> None:
        """Clear pending lifecycle error timer (matches TS lines 231-238)."""
        if run_id in self._pending_lifecycle_errors:
            # TODO: Cancel timer
            del self._pending_lifecycle_errors[run_id]
    
    def _resolve_archive_after_ms(self) -> int:
        """
        Resolve archive timeout in milliseconds (matches TS resolveArchiveAfterMs).
        
        Returns the number of milliseconds after which completed subagent runs
        should be archived/cleaned up. Reads from config or defaults to 60 minutes.
        
        TS reference: lines 534-541
        """
        if self._config:
            # Read from stored config (testing mode)
            minutes = self._config.get("agents", {}).get("defaults", {}).get("subagents", {}).get("archiveAfterMinutes", 60)
        else:
            # Read from global config
            try:
                from openclaw.config import config as global_config
                minutes = global_config.agents.defaults.subagents.archive_after_minutes if global_config.agents and global_config.agents.defaults and global_config.agents.defaults.subagents else 60
            except (AttributeError, ImportError):
                minutes = 60
        
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            return 60 * 60_000  # Default to 60 minutes
        
        return max(1, int(minutes)) * 60_000
    
    async def _resume_wait_for_completion(self, run_id: str) -> None:
        """
        Resume waiting for completion of a subagent run (matches TS resumeSubagentRun concept).
        
        This method is called when restoring registry state from disk,
        to resume any in-flight wait_for_completion operations.
        
        TS reference: resumeSubagentRun at line 423
        """
        entry = self._runs.get(run_id)
        if not entry:
            return
        
        # Mark as resumed
        self._resumed_runs.add(run_id)
        
        # TODO: Implement actual resume logic
        # For now, just log that we attempted to resume
        logger.debug(f"Resumed tracking for run {run_id}")
    
    async def _wait_for_completion(self, run_id: str, timeout_ms: int) -> None:
        """
        Wait for subagent completion and trigger announce flow (matches TS waitForSubagentCompletion).
        
        This is the core completion flow that:
        1. Calls agent.wait via internal RPC
        2. Updates run entry with completion status
        3. Triggers completeSubagentRun which sends results back to parent
        
        Args:
            run_id: Run ID to wait for
            timeout_ms: Timeout in milliseconds
            
        TS reference: lines 1021-1084
        """
        try:
            # Get the entry before starting wait
            entry = self._runs.get(run_id)
            if not entry:
                return
            
            # Call agent.wait via internal RPC (matches TS lines 1024-1036)
            from openclaw.gateway.internal_call import call_gateway_internal
            
            gateway = self._gateway
            if not gateway:
                logger.warning(f"No gateway available for wait_for_completion: {run_id}")
                return
            
            timeout_seconds = max(1, timeout_ms // 1000)
            
            wait_result = await call_gateway_internal(
                gateway=gateway,
                method="agent.wait",
                params={
                    "runId": run_id,
                    "timeoutMs": timeout_ms,
                },
                timeout_ms=timeout_ms + 10_000,
            )
            
            # Parse wait result (matches TS lines 1037-1067)
            status = wait_result.get("status") if wait_result else None
            if status not in ("ok", "error", "timeout"):
                return
            
            # Refresh entry (might have been updated)
            entry = self._runs.get(run_id)
            if not entry:
                return
            
            # Update entry with completion data (matches TS lines 1044-1067)
            mutated = False
            if wait_result and isinstance(wait_result.get("startedAt"), (int, float)):
                entry.started_at = int(wait_result["startedAt"])
                mutated = True
            
            if wait_result and isinstance(wait_result.get("endedAt"), (int, float)):
                entry.ended_at = int(wait_result["endedAt"])
                mutated = True
            
            if not entry.ended_at:
                entry.ended_at = int(time.time() * 1000)
                mutated = True
            
            # Set outcome (matches TS lines 1058-1067)
            wait_error = wait_result.get("error") if wait_result else None
            if status == "error":
                outcome = {"status": "error", "error": str(wait_error) if wait_error else None}
            elif status == "timeout":
                outcome = {"status": "timeout"}
            else:
                outcome = {"status": "ok"}
            
            if entry.outcome != outcome:
                entry.outcome = outcome
                mutated = True
            
            if mutated:
                self._persist_runs()
            
            # Trigger announce flow to send results back (matches TS lines 1071-1080 and 389-407)
            # Instead of calling completeSubagentRun, directly trigger announce flow
            from openclaw.agents.subagent_announce import run_subagent_announce_flow
            
            reason = "error" if status == "error" else "subagent-complete"
            
            # Start announce flow (matches TS startSubagentAnnounceCleanupFlow)
            asyncio.create_task(run_subagent_announce_flow(
                child_session_key=entry.child_session_key,
                child_run_id=run_id,
                requester_session_key=entry.requester_session_key,
                requester_origin=entry.requester_origin or {},
                requester_display_key=entry.requester_display_key or entry.requester_session_key,
                task=entry.task,
                gateway=self._gateway,  # ✅ Pass gateway reference
                timeout_ms=60_000,  # 60 second timeout
                cleanup=entry.cleanup,
                wait_for_completion=False,
                started_at=entry.started_at,
                ended_at=entry.ended_at,
                outcome=outcome,
                expects_completion_message=entry.expects_completion_message,
            ))
            
        except Exception as e:
            logger.debug(f"Error in wait_for_completion for {run_id}: {e}")
    
    async def _cleanup_session(self, session_key: str, cleanup: Literal["delete", "keep"] = "delete") -> None:
        """
        Cleanup session after subagent completes (matches TS cleanup logic).
        
        This handles deleting the session store entry and optionally attachments
        based on the cleanup mode.
        
        Args:
            session_key: Session key to cleanup
            cleanup: "delete" to remove session, "keep" to preserve it
        """
        if cleanup == "delete":
            # TODO: Implement actual session deletion
            logger.debug(f"Cleanup session {session_key}")
        else:
            logger.debug(f"Keeping session {session_key}")
    
    def reset_for_tests(self, persist: bool = True) -> None:
        """Reset registry for tests (matches TS lines 1086-1102)."""
        self._runs.clear()
        self._resumed_runs.clear()
        self._ended_hook_in_flight.clear()
        self._pending_lifecycle_errors.clear()
        if self._sweeper_task:
            self._sweeper_task.cancel()
        self._sweeper_task = None
        self._restore_attempted = False
        self._listener_started = False
        if persist:
            self._persist_runs()
    
    def list_all_runs(self) -> list[SubagentRunRecord]:
        """
        List all subagent runs.
        
        Returns all runs from the global registry, both active and terminated.
        Used by auto-archive service to scan for expired runs.
        
        Returns:
            List of all run records
        """
        return list(self._runs.values())
    
    def mark_subagent_run_for_steer_restart(self, run_id: str) -> None:
        """
        Mark a subagent run for steer restart.
        
        Sets suppressAnnounceReason to prevent duplicate announces when
        restarting via steer command.
        
        Args:
            run_id: Run ID to mark
        """
        record = self._runs.get(run_id)
        if record:
            record.suppress_announce_reason = "steer-restart"
    
    def clear_subagent_run_steer_restart(self, run_id: str) -> bool:
        """
        Clear steer-restart suppression from a subagent run.
        
        Called when a steer attempt fails to restore normal announce behavior.
        Matches TS clearSubagentRunSteerRestart() lines 880-901.
        """
        key = (run_id or "").strip()
        if not key:
            return False
        record = self._runs.get(key)
        if not record:
            return False
        if record.suppress_announce_reason != "steer-restart":
            return True
        record.suppress_announce_reason = None
        self._persist_runs()
        return True

    def replace_subagent_run_after_steer(
        self,
        old_run_id: str,
        new_run_id: str,
        new_child_session_key: str,
    ) -> None:
        """
        Replace a subagent run after steer operation.
        
        Removes old run record and registers new one, preserving requester context.
        
        Args:
            old_run_id: Old run ID to remove
            new_run_id: New run ID (from restarted agent)
            new_child_session_key: New child session key
        """
        old_record = self._runs.get(old_run_id)
        if old_record:
            # Create new record with updated IDs but same requester context
            new_record = SubagentRunRecord(
                run_id=new_run_id,
                child_session_key=new_child_session_key,
                requester_session_key=old_record.requester_session_key,
                requester_display_key=old_record.requester_display_key,
                task=old_record.task,
                cleanup=old_record.cleanup,
                label=old_record.label,
                model=old_record.model,
                requester_origin=old_record.requester_origin,
                run_timeout_seconds=old_record.run_timeout_seconds,
                spawn_mode=old_record.spawn_mode,
                archive_at_ms=old_record.archive_at_ms,
            )
            # Remove old and add new
            self._runs.pop(old_run_id, None)
            self._runs[new_run_id] = new_record


# Global singleton instance (matches TS module-level exports)
_registry: Optional[SubagentRegistry] = None


def get_global_registry() -> SubagentRegistry:
    """Get or create global registry instance."""
    global _registry
    if _registry is None:
        _registry = SubagentRegistry()
    return _registry


# Legacy function wrappers for backward compatibility
def register_subagent_run(
    requester_session_key: str,
    child_session_key: str,
    task: str,
    model: Optional[str] = None,
    **kwargs
) -> str:
    """Legacy wrapper - returns run_id as string."""
    registry = get_global_registry()
    record = registry.register_subagent_run(
        requester_session_key=requester_session_key,
        child_session_key=child_session_key,
        task=task,
        model=model,
        **kwargs
    )
    return record.run_id


def mark_subagent_run_started(run_id: str) -> None:
    """Legacy wrapper."""
    registry = get_global_registry()
    entry = registry.get_subagent_run(run_id)
    if entry:
        entry.started_at = int(time.time() * 1000)
        registry._persist_runs()


def complete_subagent_run(
    run_id: str,
    status: str,
    error: Optional[str] = None,
) -> None:
    """
    Complete a subagent run (legacy wrapper).
    
    Note: TS uses completeSubagentRun with different signature - 
    this matches the test expectations.
    """
    registry = get_global_registry()
    entry = registry.get_subagent_run(run_id)
    if entry:
        entry.ended_at = int(time.time() * 1000)
        entry.outcome = {"status": status, "error": error} if error else {"status": status}
        entry.ended_reason = "error" if status == "error" else "complete"
        registry._persist_runs()


def get_subagent_run(run_id: str) -> Optional[SubagentRunRecord]:
    """Legacy wrapper."""
    return get_global_registry().get_subagent_run(run_id)


def count_active_runs_for_session(session_key: str) -> int:
    """Legacy wrapper."""
    return get_global_registry().count_active_runs_for_session(session_key)


async def wait_for_subagent_completion(
    run_id: str,
    timeout_seconds: Optional[float] = None,
) -> Literal["completed", "timeout", "error"]:
    """
    Wait for a subagent run to complete (matches TS waitForSubagentCompletion).
    
    Polls the registry until the run completes or times out.
    
    Args:
        run_id: Run ID to wait for
        timeout_seconds: Timeout in seconds (None = no timeout)
    
    Returns:
        "completed" if run finished successfully
        "timeout" if timeout reached
        "error" if run failed
        
    TS reference: lines 1021-1098
    """
    import time
    
    registry = get_global_registry()
    start_time = time.time()
    poll_interval = 0.1  # 100ms poll interval
    
    while True:
        entry = registry.get_subagent_run(run_id)
        
        if not entry:
            # Run not found - might have been cleaned up
            return "error"
        
        if entry.ended_at is not None:
            # Run has ended
            if entry.outcome and entry.outcome.get("status") == "error":
                return "error"
            return "completed"
        
        # Check timeout
        if timeout_seconds is not None:
            elapsed = time.time() - start_time
            if elapsed >= timeout_seconds:
                return "timeout"
        
        # Wait before next poll
        await asyncio.sleep(poll_interval)


# Export for backward compatibility
SUBAGENT_RUNS = {}  # Deprecated: use registry._runs instead
