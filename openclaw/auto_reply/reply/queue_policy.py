"""
Queue policy for active run handling

Matches TypeScript src/auto-reply/reply/queue-policy.ts

Determines how to handle new inbound messages when there's an active run.
"""
from __future__ import annotations

from typing import Literal

# Queue action types (matches TS ActiveRunQueueAction)
QueueAction = Literal["run-now", "enqueue-followup", "drop"]

# Queue modes
QueueMode = Literal[
    "steer",
    "followup",
    "collect",
    "steer-backlog",
    "steer+backlog",
    "queue",
    "interrupt"
]


def resolve_active_run_queue_action(
    is_active: bool,
    is_heartbeat: bool,
    should_followup: bool,
    queue_mode: QueueMode,
) -> QueueAction:
    """
    Determine how to handle a new message when there's an active run.
    
    Matches TS resolveActiveRunQueueAction() from src/auto-reply/reply/queue-policy.ts.
    
    Logic (lines 11-20):
    1. If no active run → "run-now"
    2. If heartbeat → "drop"
    3. If should_followup OR mode is "steer" → "enqueue-followup"
    4. Otherwise → "run-now"
    
    Args:
        is_active: Whether there's an active run for this session
        is_heartbeat: Whether this is a heartbeat message
        should_followup: Whether message should be enqueued as followup
        queue_mode: Current queue mode setting
    
    Returns:
        Queue action: "run-now", "enqueue-followup", or "drop"
    """
    # No active run - run immediately
    if not is_active:
        return "run-now"
    
    # Heartbeat messages are always dropped when active
    if is_heartbeat:
        return "drop"
    
    # Followup or steer mode - enqueue for later
    if should_followup or queue_mode == "steer":
        return "enqueue-followup"
    
    # Default: run now (will trigger interrupt if needed)
    return "run-now"


__all__ = [
    "QueueAction",
    "QueueMode",
    "resolve_active_run_queue_action",
]
