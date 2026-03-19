"""Infrastructure modules"""

from .heartbeat_events import (
    HeartbeatEventPayload,
    HeartbeatIndicatorType,
    emit_heartbeat_event,
    on_heartbeat_event,
    get_last_heartbeat_event,
    resolve_indicator_type,
)
# Aliases for backward compatibility
HeartbeatEvent = HeartbeatEventPayload
HeartbeatEventType = HeartbeatEventPayload  # Type alias
IndicatorType = HeartbeatIndicatorType
from .heartbeat_runner import (
    start_heartbeat_runner,
    run_heartbeat_once,
    is_heartbeat_enabled_for_agent,
    resolve_heartbeat_interval_ms,
    resolve_heartbeat_prompt,
    set_heartbeats_enabled,
)
from .diagnostic_events import (
    DiagnosticStats,
    DiagnosticFlag,
    log_webhook_received,
    log_webhook_processed,
    log_webhook_error,
    log_message_queued,
    log_message_processed,
    log_session_state_change,
    log_session_stuck,
    log_run_attempt,
    log_active_runs,
    get_diagnostic_stats,
    start_diagnostic_heartbeat,
    stop_diagnostic_heartbeat,
)

__all__ = [
    # Heartbeat events
    "HeartbeatEventPayload",
    "HeartbeatEvent",  # Alias
    "HeartbeatEventType",
    "IndicatorType",
    "emit_heartbeat_event",
    "on_heartbeat_event",
    "get_last_heartbeat_event",
    "resolve_indicator_type",
    # Heartbeat runner
    "start_heartbeat_runner",
    "run_heartbeat_once",
    "is_heartbeat_enabled_for_agent",
    "resolve_heartbeat_interval_ms",
    "resolve_heartbeat_prompt",
    "set_heartbeats_enabled",
    # Diagnostic events
    "DiagnosticStats",
    "DiagnosticFlag",
    "log_webhook_received",
    "log_webhook_processed",
    "log_webhook_error",
    "log_message_queued",
    "log_message_processed",
    "log_session_state_change",
    "log_session_stuck",
    "log_run_attempt",
    "start_diagnostic_heartbeat",
    "stop_diagnostic_heartbeat",
]

