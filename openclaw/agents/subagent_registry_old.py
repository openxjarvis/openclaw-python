"""
Subagent run registry

Matches TypeScript src/agents/subagent-registry.ts

Tracks active subagent runs for lifecycle management and announce flow.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

# Global registry (matches TS subagentRuns)
SUBAGENT_RUNS: dict[str, "SubagentRunRecord"] = {}

# Constants (matches TS constants in subagent-registry.ts:58-76)
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
    Record for a subagent run.
    
    Matches TS SubagentRunRecord type from src/agents/subagent-registry.types.ts:6-38
    """
    # Identity (required fields)
    run_id: str  # runId
    child_session_key: str  # childSessionKey
    requester_session_key: str  # requesterSessionKey
    requester_display_key: str  # requesterDisplayKey
    task: str
    cleanup: Literal["delete", "keep"] = "delete"
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))  # createdAt
    
    # Optional context and configuration
    requester_origin: Optional[dict[str, Any]] = None  # requesterOrigin (DeliveryContext)
    label: Optional[str] = None
    model: Optional[str] = None
    run_timeout_seconds: Optional[int] = None  # runTimeoutSeconds
    spawn_mode: Optional[str] = None  # spawnMode (SpawnSubagentMode)
    
    # Timing
    started_at: Optional[int] = None  # startedAt
    ended_at: Optional[int] = None  # endedAt
    
    # Outcome
    outcome: Optional[dict[str, Any]] = None  # SubagentRunOutcome
    ended_reason: Optional[str] = None  # endedReason (SubagentLifecycleEndedReason)
    
    # Lifecycle and hooks
    ended_hook_emitted_at: Optional[int] = None  # endedHookEmittedAt
    
    # Cleanup
    archive_at_ms: Optional[int] = None  # archiveAtMs
    cleanup_completed_at: Optional[int] = None  # cleanupCompletedAt
    cleanup_handled: Optional[bool] = None  # cleanupHandled
    
    # Announce tracking
    announce_retry_count: Optional[int] = None  # announceRetryCount
    last_announce_retry_at: Optional[int] = None  # lastAnnounceRetryAt
    suppress_announce_reason: Optional[Literal["steer-restart", "killed"]] = None  # suppressAnnounceReason
    expects_completion_message: Optional[bool] = None  # expectsCompletionMessage
    
    # Attachments
    attachments_dir: Optional[str] = None  # attachmentsDir
    attachments_root_dir: Optional[str] = None  # attachmentsRootDir
    retain_attachments_on_keep: Optional[bool] = None  # retainAttachmentsOnKeep


def register_subagent_run(
    requester_session_key: str,
    child_session_key: str,
    task: str,
    requester_display_key: Optional[str] = None,
    label: Optional[str] = None,
    model: Optional[str] = None,
    cleanup: Literal["delete", "keep"] = "delete",
    requester_origin: Optional[dict[str, Any]] = None,
    run_timeout_seconds: Optional[int] = None,
    spawn_mode: Optional[str] = None,
    archive_after_minutes: Optional[int] = None,
) -> SubagentRunRecord:
    """
    Register a new subagent run.
    
    Matches TS registerSubagentRun() concept.
    
    Args:
        requester_session_key: Parent session key
        child_session_key: Child session key
        task: Task description
        requester_display_key: Display key for requester (auto-inferred if not provided)
        label: Optional label
        model: Model identifier
        cleanup: Cleanup strategy
        requester_origin: Optional delivery context
        run_timeout_seconds: Optional timeout in seconds
        spawn_mode: Spawn mode
        archive_after_minutes: Minutes until auto-archive (None = use default from config)
    
    Returns:
        SubagentRunRecord
    """
    run_id = str(uuid.uuid4())
    
    # Auto-infer display key if not provided
    if requester_display_key is None:
        requester_display_key = requester_session_key
    
    # Calculate archive_at_ms if archiveAfterMinutes is set
    archive_at_ms = None
    if archive_after_minutes is not None and archive_after_minutes > 0:
        from openclaw.agents.subagent_archive import calculate_archive_at_ms
        archive_at_ms = calculate_archive_at_ms(archive_after_minutes)
    
    record = SubagentRunRecord(
        run_id=run_id,
        requester_session_key=requester_session_key,
        child_session_key=child_session_key,
        requester_display_key=requester_display_key,
        task=task,
        label=label,
        model=model,
        cleanup=cleanup,
        requester_origin=requester_origin,
        run_timeout_seconds=run_timeout_seconds,
        spawn_mode=spawn_mode,
        archive_at_ms=archive_at_ms,
    )
    
    SUBAGENT_RUNS[run_id] = record
    return record  # Return record instead of just run_id for easier testing


def mark_subagent_run_started(run_id: str) -> None:
    """
    Mark subagent run as started.
    
    Args:
        run_id: Run ID
    """
    record = SUBAGENT_RUNS.get(run_id)
    if record:
        record.started_at = int(time.time() * 1000)


def mark_subagent_run_terminated(
    run_id: str,
    outcome: Optional[dict[str, Any]] = None,
    ended_reason: Optional[str] = None,
) -> None:
    """
    Mark subagent run as terminated.
    
    Matches TS markSubagentRunTerminated() concept.
    
    Args:
        run_id: Run ID
        outcome: Run outcome
        ended_reason: Reason for termination
    """
    record = SUBAGENT_RUNS.get(run_id)
    if record:
        record.ended_at = int(time.time() * 1000)
        record.outcome = outcome
        record.ended_reason = ended_reason


async def wait_for_subagent_completion(
    run_id: str,
    timeout_seconds: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """
    Wait for subagent run to complete.
    
    Matches TS waitForSubagentCompletion() concept.
    
    Args:
        run_id: Run ID to wait for
        timeout_seconds: Optional timeout in seconds
    
    Returns:
        Final outcome or None if timed out/not found
    """
    record = SUBAGENT_RUNS.get(run_id)
    if not record:
        return None
    
    # If already ended, return immediately
    if record.ended_at is not None:
        return record.outcome
    
    # Poll for completion
    timeout_at = None
    if timeout_seconds:
        timeout_at = time.time() + timeout_seconds
    
    while True:
        record = SUBAGENT_RUNS.get(run_id)
        if not record:
            return None
        
        if record.ended_at is not None:
            return record.outcome
        
        # Check timeout
        if timeout_at and time.time() >= timeout_at:
            return None
        
        # Wait a bit before polling again
        await asyncio.sleep(0.5)


def count_active_runs_for_session(requester_session_key: str) -> int:
    """
    Count active subagent runs for a session.
    
    Matches TS countActiveRunsForSessionFromRuns().
    
    Args:
        requester_session_key: Requester session key
    
    Returns:
        Count of active runs (not ended)
    """
    count = 0
    for record in SUBAGENT_RUNS.values():
        if record.requester_session_key == requester_session_key:
            if record.ended_at is None:
                count += 1
    return count


def list_runs_for_requester(requester_session_key: str) -> list[SubagentRunRecord]:
    """
    List all runs for a requester.
    
    Args:
        requester_session_key: Requester session key
    
    Returns:
        List of run records
    """
    return [
        record
        for record in SUBAGENT_RUNS.values()
        if record.requester_session_key == requester_session_key
    ]


def get_run(run_id: str) -> Optional[SubagentRunRecord]:
    """
    Get a run record by ID.
    
    Args:
        run_id: Run ID
    
    Returns:
        Run record or None
    """
    return SUBAGENT_RUNS.get(run_id)


def delete_run(run_id: str) -> None:
    """
    Delete a run record.
    
    Args:
        run_id: Run ID
    """
    SUBAGENT_RUNS.pop(run_id, None)


class SubagentRegistry:
    """
    Subagent registry wrapper for compatibility.
    
    Provides class-based interface matching test expectations.
    """
    
    def __init__(self):
        """Initialize registry with access to global runs dict."""
        # Provide access to global SUBAGENT_RUNS dict via _runs property
        pass
    
    @property
    def _runs(self) -> dict[str, SubagentRunRecord]:
        """Access to global SUBAGENT_RUNS dict."""
        return SUBAGENT_RUNS
    
    @property  
    def _gateway(self):
        """Gateway instance (placeholder for compatibility)."""
        return None
    
    @_gateway.setter
    def _gateway(self, value):
        """Set gateway instance (placeholder for compatibility)."""
        pass
    
    @staticmethod
    def register_subagent_run(
        requester_session_key: str,
        child_session_key: str,
        task: str,
        requester_display_key: Optional[str] = None,
        label: Optional[str] = None,
        model: Optional[str] = None,
        cleanup: Literal["delete", "keep"] = "delete",
        requester_origin: Optional[dict[str, Any]] = None,
        run_timeout_seconds: Optional[int] = None,
        spawn_mode: Optional[str] = None,
    ) -> SubagentRunRecord:
        """Register a new subagent run."""
        return register_subagent_run(
            requester_session_key=requester_session_key,
            child_session_key=child_session_key,
            task=task,
            requester_display_key=requester_display_key,
            label=label,
            model=model,
            cleanup=cleanup,
            requester_origin=requester_origin,
            run_timeout_seconds=run_timeout_seconds,
            spawn_mode=spawn_mode,
        )
    
    @staticmethod
    def mark_subagent_run_started(run_id: str) -> None:
        """Mark subagent run as started."""
        mark_subagent_run_started(run_id)
    
    @staticmethod
    def mark_subagent_run_terminated(
        run_id: str,
        outcome: Optional[dict[str, Any]] = None,
        ended_reason: Optional[str] = None,
        reason: Optional[str] = None,  # Alias for ended_reason
    ) -> None:
        """Mark subagent run as terminated."""
        # Support both 'reason' and 'ended_reason' for compatibility
        if reason and not ended_reason:
            ended_reason = reason
        mark_subagent_run_terminated(run_id, outcome, ended_reason)
    
    @staticmethod
    def count_active_runs(requester_session_key: str) -> int:
        """Count active runs for a session."""
        return count_active_runs_for_session(requester_session_key)
    
    @staticmethod
    def list_runs_for_requester(requester_session_key: str) -> list[SubagentRunRecord]:
        """List all runs for a requester."""
        return list_runs_for_requester(requester_session_key)
    
    @staticmethod
    def count_active_runs_for_session(requester_session_key: str) -> int:
        """Count active runs for a session (alias)."""
        return count_active_runs_for_session(requester_session_key)
    
    @staticmethod
    def get_run(run_id: str) -> Optional[SubagentRunRecord]:
        """Get a run record."""
        return get_run(run_id)
    
    @staticmethod
    def delete_run(run_id: str) -> None:
        """Delete a run record."""
        delete_run(run_id)
    
    @staticmethod
    def list_all_runs() -> list[SubagentRunRecord]:
        """
        List all subagent runs.
        
        Returns all runs from the global registry, both active and terminated.
        Used by auto-archive service to scan for expired runs.
        
        Returns:
            List of all run records
        """
        return list(SUBAGENT_RUNS.values())
    
    @staticmethod
    def mark_subagent_run_for_steer_restart(run_id: str) -> None:
        """
        Mark a subagent run for steer restart.
        
        Sets suppressAnnounceReason to prevent duplicate announces when
        restarting via steer command.
        
        Args:
            run_id: Run ID to mark
        """
        record = SUBAGENT_RUNS.get(run_id)
        if record:
            record.suppress_announce_reason = "steer-restart"
    
    @staticmethod
    def replace_subagent_run_after_steer(
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
        old_record = SUBAGENT_RUNS.get(old_run_id)
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
            SUBAGENT_RUNS.pop(old_run_id, None)
            SUBAGENT_RUNS[new_run_id] = new_record


# Singleton registry instance
_GLOBAL_REGISTRY: Optional[SubagentRegistry] = None


def get_subagent_run(run_id: str) -> Optional[SubagentRunRecord]:
    """
    Get subagent run record.
    
    Args:
        run_id: Run ID
    
    Returns:
        Run record or None
    """
    return SUBAGENT_RUNS.get(run_id)


def delete_subagent_run(run_id: str) -> bool:
    """
    Delete subagent run from registry.
    
    Args:
        run_id: Run ID
    
    Returns:
        True if deleted, False if not found
    """
    if run_id in SUBAGENT_RUNS:
        del SUBAGENT_RUNS[run_id]
        return True
    return False




def get_global_registry() -> SubagentRegistry:
    """
    Get global subagent registry singleton.
    
    Provides object interface for compatibility with code expecting registry pattern.
    """
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = SubagentRegistry()
    return _GLOBAL_REGISTRY


# Compatibility alias
def complete_subagent_run(
    run_id: str,
    outcome: Optional[dict[str, Any]] = None,
    ended_reason: Optional[str] = None,
) -> None:
    """
    Alias for mark_subagent_run_terminated for backward compatibility.
    
    Args:
        run_id: Run ID
        outcome: Run outcome
        ended_reason: Reason for termination
    """
    mark_subagent_run_terminated(run_id, outcome, ended_reason)


__all__ = [
    "SubagentRunRecord",
    "SUBAGENT_RUNS",
    "SUBAGENT_ANNOUNCE_TIMEOUT_MS",
    "MIN_ANNOUNCE_RETRY_DELAY_MS",
    "MAX_ANNOUNCE_RETRY_DELAY_MS",
    "MAX_ANNOUNCE_RETRY_COUNT",
    "ANNOUNCE_EXPIRY_MS",
    "ANNOUNCE_COMPLETION_HARD_EXPIRY_MS",
    "LIFECYCLE_ERROR_RETRY_GRACE_MS",
    "register_subagent_run",
    "mark_subagent_run_started",
    "mark_subagent_run_terminated",
    "complete_subagent_run",  # Alias for compatibility
    "wait_for_subagent_completion",
    "count_active_runs_for_session",
    "list_runs_for_requester",
    "get_run",
    "delete_run",
    "get_subagent_run",
    "delete_subagent_run",
    "SubagentRegistry",
    "get_global_registry",
]
