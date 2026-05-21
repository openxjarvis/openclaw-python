"""Cron job scheduling service — aligned with TypeScript openclaw/src/cron/

Matches: service/ops.ts, service/timer.ts, service/jobs.ts
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable, Dict, Literal, Optional

from .locked import locked
from .types import (
    CronJob,
    CronJobState,
    AgentTurnPayload,
    SystemEventPayload,
    AtSchedule,
    EverySchedule,
    CronSchedule as CronScheduleType,
    CronDelivery,
)
from .schedule import compute_next_run
from .timer import CronTimer, MAX_TIMER_DELAY_MS
from .store import CronStore, CronRunLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (mirrors TS service/timer.ts)
# ---------------------------------------------------------------------------

MIN_REFIRE_GAP_MS = 2_000          # Safety net for cron spin-loops
DEFAULT_JOB_TIMEOUT_MS = 10 * 60_000  # 10 minutes per job
MAX_SCHEDULE_ERRORS = 3            # Auto-disable after this many consecutive schedule errors
STUCK_RUN_MS = 2 * 60 * 60 * 1000 # Clear stale running_at_ms after 2 h

# Exponential backoff indexed by consecutive error count (0-based)
ERROR_BACKOFF_SCHEDULE_MS = [
    30_000,        # 1st error  →  30 s
    60_000,        # 2nd error  →   1 min
    5 * 60_000,    # 3rd error  →   5 min
    15 * 60_000,   # 4th error  →  15 min
    60 * 60_000,   # 5th+ error →  60 min
]

# One-shot (at) transient retry backoff (first 3 steps only — mirrors TS default)
ERROR_BACKOFF_RETRY_MS = ERROR_BACKOFF_SCHEDULE_MS[:3]  # [30s, 1min, 5min]
DEFAULT_MAX_TRANSIENT_RETRIES = 3
DEFAULT_FAILURE_ALERT_AFTER = 2  # Aligned with TS (was 3)
DEFAULT_FAILURE_ALERT_COOLDOWN_MS = 60 * 60_000  # 1 hour

# Transient error patterns (mirrors TS TRANSIENT_PATTERNS)
import re as _re
_TRANSIENT_PATTERNS: dict[str, _re.Pattern] = {
    "rate_limit": _re.compile(r"rate[_ ]limit|too many requests|429|resource has been exhausted|cloudflare", _re.I),
    "network": _re.compile(r"network|econnreset|econnrefused|fetch failed|socket", _re.I),
    "timeout": _re.compile(r"timeout|etimedout", _re.I),
    "server_error": _re.compile(r"\b5\d{2}\b"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _error_backoff_ms(consecutive_errors: int) -> int:
    idx = min(consecutive_errors - 1, len(ERROR_BACKOFF_SCHEDULE_MS) - 1)
    return ERROR_BACKOFF_SCHEDULE_MS[max(0, idx)]


def _is_transient_cron_error(error: str | None, retry_on: list[str] | None = None) -> bool:
    """Return True if the error string matches known transient patterns.

    Mirrors TS ``isTransientCronError()``.
    """
    if not error or not isinstance(error, str):
        return False
    keys = retry_on if retry_on else list(_TRANSIENT_PATTERNS.keys())
    return any(_TRANSIENT_PATTERNS.get(k, _re.compile(r"^$")).search(error) for k in keys)


def _resolve_retry_config(cron_config: dict) -> dict:
    """Resolve transient retry configuration from global cron config.

    Mirrors TS ``resolveRetryConfig()``.
    """
    retry = cron_config.get("retry", {}) if isinstance(cron_config, dict) else {}
    max_attempts = retry.get("maxAttempts") if isinstance(retry, dict) else None
    backoff_ms = retry.get("backoffMs") if isinstance(retry, dict) else None
    retry_on = retry.get("retryOn") if isinstance(retry, dict) else None
    return {
        "max_attempts": max_attempts if isinstance(max_attempts, int) else DEFAULT_MAX_TRANSIENT_RETRIES,
        "backoff_ms": backoff_ms if isinstance(backoff_ms, list) and backoff_ms else ERROR_BACKOFF_RETRY_MS,
        "retry_on": retry_on if isinstance(retry_on, list) and retry_on else None,
    }


def _resolve_delivery_status(job: CronJob, delivered: bool | None) -> str:
    """Map delivered flag to a CronDeliveryStatus string.

    Mirrors TS ``resolveDeliveryStatus()``.
    """
    if delivered is True:
        return "delivered"
    if delivered is False:
        return "not-delivered"
    # Determine if delivery was requested at all
    try:
        from .normalize import resolve_cron_delivery_plan
        plan = resolve_cron_delivery_plan(job)
        return "unknown" if plan.get("requested") else "not-requested"
    except Exception:
        return "not-requested"


def _resolve_failure_alert_config(job: CronJob, cron_config: dict) -> dict | None:
    """Resolve the effective failure alert config for a job.

    Merges job-level failureAlert → global cronConfig.failureAlert → defaults.
    Returns None if alerts are disabled (job.failure_alert is False or global is
    not enabled and no job-level config exists).

    Mirrors TS ``resolveFailureAlert()``.
    """
    from .types import CronFailureAlert as _CronFailureAlert

    global_cfg = cron_config.get("failureAlert", {}) if isinstance(cron_config, dict) else {}
    job_cfg = job.failure_alert

    # Explicit disable
    if job_cfg is False:
        return None

    # No job config AND global not explicitly enabled
    if not job_cfg and not (isinstance(global_cfg, dict) and global_cfg.get("enabled") is True):
        return None

    jc: dict = {}
    gc: dict = global_cfg if isinstance(global_cfg, dict) else {}
    if isinstance(job_cfg, _CronFailureAlert):
        jc = {
            "after": job_cfg.after_n_errors,
            "cooldownMs": job_cfg.cooldown_ms,
            "channel": None,
            "to": None,
            "mode": None,
            "accountId": None,
        }
    elif isinstance(job_cfg, dict):
        jc = job_cfg

    def _clamp_pos(v: Any, fallback: int) -> int:
        if isinstance(v, (int, float)) and v >= 1:
            return int(v)
        return fallback

    def _clamp_non_neg(v: Any, fallback: int) -> int:
        if isinstance(v, (int, float)) and v >= 0:
            return int(v)
        return fallback

    def _norm_channel(v: Any) -> str | None:
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
        return None

    def _norm_to(v: Any) -> str | None:
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None

    mode = jc.get("mode") or gc.get("mode")
    explicit_to = _norm_to(jc.get("to"))
    channel = (
        _norm_channel(jc.get("channel"))
        or _norm_channel(job.delivery.channel if job.delivery else None)
        or "last"
    )
    to_val = explicit_to if mode == "webhook" else (
        explicit_to or _norm_to(job.delivery.to if job.delivery else None)
    )

    return {
        "after": _clamp_pos(jc.get("after") or gc.get("after"), DEFAULT_FAILURE_ALERT_AFTER),
        "cooldown_ms": _clamp_non_neg(jc.get("cooldownMs") or gc.get("cooldownMs"), DEFAULT_FAILURE_ALERT_COOLDOWN_MS),
        "channel": channel,
        "to": to_val,
        "mode": mode,
        "account_id": jc.get("accountId") or gc.get("accountId"),
    }


def _resolve_stable_cron_offset_ms(job_id: str, stagger_ms: int) -> int:
    """Deterministic per-job SHA256-based stagger offset in [0, stagger_ms)."""
    if stagger_ms <= 1:
        return 0
    digest = hashlib.sha256(job_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") % stagger_ms


def _compute_staggered_cron_next_run(job: CronJob, now_ms: int) -> int | None:
    """Compute next run for cron schedule with deterministic stagger offset."""
    if not isinstance(job.schedule, CronScheduleType):
        return compute_next_run(job.schedule, now_ms)

    from .normalize import resolve_cron_stagger_ms
    stagger_ms = resolve_cron_stagger_ms(job.schedule)
    offset_ms = _resolve_stable_cron_offset_ms(job.id, stagger_ms)

    if offset_ms <= 0:
        return compute_next_run(job.schedule, now_ms)

    # Shift cursor backwards so we can still catch the current window
    cursor_ms = max(0, now_ms - offset_ms)
    for _ in range(4):
        base_next = compute_next_run(job.schedule, cursor_ms)
        if base_next is None:
            return None
        shifted = base_next + offset_ms
        if shifted > now_ms:
            return shifted
        cursor_ms = max(cursor_ms + 1, base_next + 1_000)
    return None


def compute_job_next_run_at_ms(job: CronJob, now_ms: int) -> int | None:
    """Compute next run time for a job (matches TS computeJobNextRunAtMs)."""
    if not job.enabled:
        return None

    if isinstance(job.schedule, EverySchedule):
        # Resolve anchor: explicit anchor_ms or job creation time
        anchor_ms = job.schedule.anchor_ms
        if anchor_ms is None:
            anchor_ms = job.created_at_ms
        sched_with_anchor = EverySchedule(
            every_ms=job.schedule.every_ms,
            type="every",
            anchor_ms=anchor_ms,
        )
        return compute_next_run(sched_with_anchor, now_ms)

    if isinstance(job.schedule, AtSchedule):
        # One-shot: stays due until it successfully finishes
        if job.state.last_status == "ok" and job.state.last_run_at_ms:
            return None
        from .normalize import parse_absolute_time_ms
        at_ms = parse_absolute_time_ms(job.schedule.at) if job.schedule.at else None
        return at_ms

    # CronSchedule — use staggered computation
    nxt = _compute_staggered_cron_next_run(job, now_ms)
    if nxt is None:
        # Retry with next-second cursor (TS does the same)
        next_second_ms = (now_ms // 1000 + 1) * 1000
        nxt = _compute_staggered_cron_next_run(job, next_second_ms)
    return nxt


def _record_schedule_error(job: CronJob, err: Exception) -> None:
    """Track consecutive schedule computation errors, auto-disable after threshold."""
    count = (job.state.schedule_error_count or 0) + 1
    job.state.schedule_error_count = count
    job.state.next_run_ms = None
    job.state.last_error = f"schedule error: {err}"

    if count >= MAX_SCHEDULE_ERRORS:
        job.enabled = False
        logger.error(
            f"cron: auto-disabled job {job.id!r} ({job.name!r}) "
            f"after {count} consecutive schedule errors: {err}"
        )
    else:
        logger.warning(
            f"cron: failed to compute next run for job {job.id!r} "
            f"(error {count}/{MAX_SCHEDULE_ERRORS}): {err}"
        )


def _recompute_job_next_run(job: CronJob, now_ms: int) -> bool:
    """Recompute nextRunAtMs for a single job. Returns True if changed."""
    try:
        new_next = compute_job_next_run_at_ms(job, now_ms)
        if job.state.next_run_ms != new_next:
            job.state.next_run_ms = new_next
            if job.state.schedule_error_count:
                job.state.schedule_error_count = None
            return True
        if job.state.schedule_error_count:
            job.state.schedule_error_count = None
            return True
    except Exception as err:
        _record_schedule_error(job, err)
        return True
    return False


def _normalize_job_tick_state(job: CronJob, now_ms: int) -> tuple[bool, bool]:
    """Normalize job state. Returns (changed, skip)."""
    changed = False

    if not job.enabled:
        if job.state.next_run_ms is not None:
            job.state.next_run_ms = None
            changed = True
        if job.state.running_at_ms is not None:
            job.state.running_at_ms = None
            changed = True
        return changed, True

    # Clear stuck running_at_ms (> 2 h)
    if job.state.running_at_ms is not None and now_ms - job.state.running_at_ms > STUCK_RUN_MS:
        logger.warning(f"cron: clearing stuck running marker for job {job.id!r}")
        job.state.running_at_ms = None
        changed = True

    return changed, False


def _recompute_next_runs(jobs: list[CronJob], now_ms: int | None = None) -> bool:
    """Full recompute: recompute nextRunAtMs if missing or past-due."""
    now = now_ms or _now_ms()
    changed = False
    for job in jobs:
        tick_changed, skip = _normalize_job_tick_state(job, now)
        if tick_changed:
            changed = True
        if skip:
            continue
        nxt = job.state.next_run_ms
        if nxt is None or now >= nxt:
            if _recompute_job_next_run(job, now):
                changed = True
    return changed


def _recompute_next_runs_for_maintenance(jobs: list[CronJob], now_ms: int | None = None) -> bool:
    """Maintenance-only recompute: only fills missing nextRunAtMs, never advances existing.

    Used during timer ticks when no due jobs were found, to avoid silently
    advancing past-due nextRunAtMs values without execution.
    """
    now = now_ms or _now_ms()
    changed = False
    for job in jobs:
        tick_changed, skip = _normalize_job_tick_state(job, now)
        if tick_changed:
            changed = True
        if skip:
            continue
        if job.state.next_run_ms is None:
            if _recompute_job_next_run(job, now):
                changed = True
    return changed


def _next_wake_at_ms(jobs: list[CronJob]) -> int | None:
    """Find earliest nextRunAtMs across enabled jobs."""
    earliest: int | None = None
    for j in jobs:
        if not j.enabled:
            continue
        nxt = j.state.next_run_ms
        if nxt is not None and (earliest is None or nxt < earliest):
            earliest = nxt
    return earliest


def _apply_job_result(
    job: CronJob,
    status: Literal["ok", "error", "skipped"],
    error: str | None,
    started_at: int,
    ended_at: int,
    delivered: bool | None = None,
    cron_config: dict | None = None,
) -> tuple[bool, dict | None]:
    """Apply execution result to job state.

    Returns (should_delete, failure_alert_params | None).
    ``failure_alert_params`` is non-None when a failure alert should be fired;
    callers use it to invoke the external ``send_failure_alert`` callback.

    Mirrors TS ``applyJobResult()`` in service/timer.ts.
    """
    cfg = cron_config or {}

    job.state.running_at_ms = None
    job.state.last_run_at_ms = started_at
    # Set both fields: lastRunStatus (preferred) and lastStatus (back-compat)
    job.state.last_run_status = status          # TS: lastRunStatus
    job.state.last_status = status              # TS: lastStatus (alias)
    job.state.last_duration_ms = max(0, ended_at - started_at)
    job.state.last_error = error
    job.state.last_delivered = delivered        # TS: lastDelivered
    delivery_status = _resolve_delivery_status(job, delivered)
    job.state.last_delivery_status = delivery_status   # TS: lastDeliveryStatus
    job.state.last_delivery_error = (           # TS: lastDeliveryError
        error if delivery_status == "not-delivered" else None
    )
    job.updated_at_ms = ended_at

    # Track consecutive errors; reset on success and clear cooldown gate
    if status == "error":
        job.state.consecutive_errors = (job.state.consecutive_errors or 0) + 1
    else:
        job.state.consecutive_errors = 0
        job.state.last_failure_alert_at_ms = None   # reset cooldown on success

    should_delete = (
        isinstance(job.schedule, AtSchedule)
        and job.delete_after_run is True
        and status == "ok"
    )

    # --- Failure alert resolution (mirrors TS resolveFailureAlert + emitFailureAlert) ---
    failure_alert_params: dict | None = None
    if status == "error":
        alert_cfg = _resolve_failure_alert_config(job, cfg)
        if alert_cfg:
            consecutive = job.state.consecutive_errors or 0
            is_best_effort = (
                (job.delivery and getattr(job.delivery, "best_effort", False))
                or (isinstance(job.payload, AgentTurnPayload)
                    and getattr(job.payload, "best_effort_deliver", False))
            )
            if not is_best_effort and consecutive >= alert_cfg["after"]:
                now_ms = _now_ms()
                last_alert = job.state.last_failure_alert_at_ms or 0
                cooldown_ms = max(0, alert_cfg["cooldown_ms"])
                in_cooldown = (now_ms - last_alert) < cooldown_ms
                if not in_cooldown:
                    failure_alert_params = {
                        "job": job,
                        "error": error,
                        "consecutive_errors": consecutive,
                        "channel": alert_cfg["channel"],
                        "to": alert_cfg["to"],
                        "mode": alert_cfg["mode"],
                        "account_id": alert_cfg["account_id"],
                    }
                    job.state.last_failure_alert_at_ms = now_ms

    if not should_delete:
        if isinstance(job.schedule, AtSchedule):
            if status in ("ok", "skipped"):
                # One-shot completed normally: disable
                job.enabled = False
                job.state.next_run_ms = None
            elif status == "error":
                # Check if error is transient and retries remain (mirrors TS #24355)
                retry_cfg = _resolve_retry_config(cfg)
                consecutive = job.state.consecutive_errors or 0
                transient = _is_transient_cron_error(error, retry_cfg["retry_on"])
                if transient and consecutive <= retry_cfg["max_attempts"]:
                    # Schedule retry with backoff
                    backoff_schedule = retry_cfg["backoff_ms"]
                    idx = min(consecutive - 1, len(backoff_schedule) - 1)
                    backoff = backoff_schedule[max(0, idx)]
                    job.state.next_run_ms = ended_at + backoff
                    logger.info(
                        "cron: scheduling one-shot retry after transient error for job %r "
                        "(consecutiveErrors=%d, backoffMs=%d, nextRunAtMs=%d)",
                        job.id, consecutive, backoff, job.state.next_run_ms,
                    )
                else:
                    # Permanent error or max retries exhausted: disable
                    job.enabled = False
                    job.state.next_run_ms = None
                    reason = "max retries exhausted" if transient else "permanent error"
                    logger.warning(
                        "cron: disabling one-shot job %r after error (%s, consecutiveErrors=%d)",
                        job.id, reason, consecutive,
                    )
        elif status == "error" and job.enabled:
            # Recurring job: exponential backoff
            backoff = _error_backoff_ms(job.state.consecutive_errors or 1)
            normal_next = compute_job_next_run_at_ms(job, ended_at)
            backoff_next = ended_at + backoff
            job.state.next_run_ms = (
                max(normal_next, backoff_next) if normal_next is not None else backoff_next
            )
            logger.info(
                "cron: error backoff for job %r — consecutiveErrors=%d, backoffMs=%d, nextRunAtMs=%d",
                job.id, job.state.consecutive_errors, backoff, job.state.next_run_ms,
            )
        elif job.enabled:
            natural_next = compute_job_next_run_at_ms(job, ended_at)
            if isinstance(job.schedule, CronScheduleType):
                # MIN_REFIRE_GAP_MS safety net
                min_next = ended_at + MIN_REFIRE_GAP_MS
                job.state.next_run_ms = (
                    max(natural_next, min_next) if natural_next is not None else min_next
                )
            else:
                job.state.next_run_ms = natural_next
        else:
            job.state.next_run_ms = None

    return should_delete, failure_alert_params


def _is_job_runnable(
    job: CronJob,
    now_ms: int,
    skip_ids: frozenset[str] | None = None,
    skip_at_if_already_ran: bool = False,
) -> bool:
    if not job.enabled:
        return False
    if skip_ids and job.id in skip_ids:
        return False
    if job.state.running_at_ms is not None:
        return False
    if skip_at_if_already_ran and isinstance(job.schedule, AtSchedule) and job.state.last_status:
        return False
    nxt = job.state.next_run_ms
    return isinstance(nxt, int) and now_ms >= nxt


# ---------------------------------------------------------------------------
# CronEvent
# ---------------------------------------------------------------------------

CronEventAction = Literal["added", "updated", "removed", "started", "finished"]


class CronEvent(dict):
    """Structured cron event."""
    pass


def _make_event(**kwargs: Any) -> CronEvent:
    return CronEvent({k: v for k, v in kwargs.items() if v is not None})


# ---------------------------------------------------------------------------
# CronService
# ---------------------------------------------------------------------------

class CronService:
    """
    Complete Cron scheduling service — aligned with TypeScript CronService.

    Matches: service/ops.ts + service/timer.ts + service/jobs.ts
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
        cron_enabled: bool = True,
        # Callbacks matching TypeScript CronServiceDeps
        enqueue_system_event: Optional[Callable[..., Any]] = None,
        request_heartbeat_now: Optional[Callable[..., Any]] = None,
        run_heartbeat_once: Optional[Callable[..., Any]] = None,
        run_isolated_agent_job: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        on_event: Optional[Callable[[CronEvent], None]] = None,
        resolve_session_store_path: Optional[Callable[[str], str]] = None,
        session_store_path: Optional[str] = None,
        cron_config: Optional[Dict[str, Any]] = None,
        default_agent_id: Optional[str] = None,
        lane_manager: Optional[Any] = None,  # QueueManager for lane-based execution
        # Failure alert callback: async fn(job, consecutive_errors, last_error) -> bool
        send_failure_alert: Optional[Callable[..., Awaitable[bool]]] = None,
        # Legacy callback names (backward compat)
        on_system_event: Optional[Callable[..., Any]] = None,
        on_isolated_agent: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
    ):
        self.jobs: Dict[str, CronJob] = {}
        self._service_running = False
        self._cron_enabled = cron_enabled

        # Store
        self.store_path = store_path
        self.log_dir = log_dir
        self._store: Optional[CronStore] = None
        if store_path:
            self._store = CronStore(store_path)

        # Callbacks (TypeScript-aligned)
        self.enqueue_system_event = enqueue_system_event or on_system_event
        self.request_heartbeat_now = request_heartbeat_now
        self.run_heartbeat_once = run_heartbeat_once
        self.run_isolated_agent_job = run_isolated_agent_job or on_isolated_agent
        self.on_event = on_event
        self.resolve_session_store_path = resolve_session_store_path
        self.session_store_path = session_store_path
        self.cron_config = cron_config or {}
        self.default_agent_id = default_agent_id or "default"
        self.lane_manager = lane_manager
        self.send_failure_alert = send_failure_alert

        # Store path for per-store-path locking (aligned with TS)
        self._store_path = str(store_path) if store_path else ""

        # Timer
        self._timer: Optional[CronTimer] = None

        # Running flag (matches TS state.running guard)
        self._timer_running = False

        # Warned-disabled once
        self._warned_disabled = False

        logger.info("CronService initialized")

    # ------------------------------------------------------------------
    # Lock helper (aligned with TS locked.ts)
    # ------------------------------------------------------------------

    async def _locked(self, fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
        """Execute function under per-store-path lock"""
        async def wrapper():
            return await fn(*args)
        return await locked(self._store_path, wrapper)

    # ------------------------------------------------------------------
    # Store helpers
    # ------------------------------------------------------------------

    async def _ensure_loaded(
        self,
        force_reload: bool = False,
        skip_recompute: bool = False,
    ) -> None:
        if self._store is None:
            return
        if self.jobs and not force_reload:
            return

        jobs = self._store.load()
        self.jobs = {j.id: j for j in jobs}

        if not skip_recompute:
            # Use maintenance-mode recompute on initial load: only fill in None
            # values, never advance past-due nextRunAtMs.  This preserves the
            # stored past-due timestamps so that _run_missed_jobs() can still
            # detect them.  start() calls the full _recompute_next_runs() AFTER
            # running missed jobs, mirroring the TS order.
            _recompute_next_runs_for_maintenance(list(self.jobs.values()))

        logger.debug(f"Store loaded: {len(self.jobs)} jobs (force={force_reload})")

    async def _persist(self) -> None:
        if self._store is None:
            return
        self._store.save(list(self.jobs.values()))

    # ------------------------------------------------------------------
    # Emit helper
    # ------------------------------------------------------------------

    def _emit(self, **kwargs: Any) -> None:
        evt = _make_event(**kwargs)
        try:
            if self.on_event:
                self.on_event(evt)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Warn-if-disabled
    # ------------------------------------------------------------------

    def _warn_if_disabled(self, action: str) -> None:
        if self._cron_enabled:
            return
        if self._warned_disabled:
            return
        self._warned_disabled = True
        logger.warning(
            f"cron: scheduler disabled; jobs will not run automatically (action={action})"
        )

    # ------------------------------------------------------------------
    # Timer management
    # ------------------------------------------------------------------

    def _arm_timer(self) -> None:
        """Arm timer at next wake time (clamped to MAX_TIMER_DELAY_MS)."""
        if not self._timer or not self._cron_enabled:
            return
        nxt = _next_wake_at_ms(list(self.jobs.values()))
        self._timer.arm(nxt)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start cron service."""
        async def _do_start() -> None:
            if not self._cron_enabled:
                logger.info("cron: disabled")
                return

            await self._ensure_loaded()

            # Clear stale running markers
            now = _now_ms()
            for job in self.jobs.values():
                if job.state.running_at_ms is not None:
                    logger.warning(f"cron: clearing stale running marker for job {job.id!r}")
                    job.state.running_at_ms = None

            # Create timer before running missed jobs (timer is used by _arm_timer inside)
            self._timer = CronTimer(on_timer_callback=self._on_timer)

            # Run overdue jobs (missed while process was down) BEFORE recomputing next runs.
            # TS does the same: runMissedJobs() is called before recomputeNextRuns() so that
            # past-due nextRunAtMs values are not silently advanced past due-time without firing.
            await self._run_missed_jobs(now)

            # Now recompute and persist
            _recompute_next_runs(list(self.jobs.values()), now)
            await self._persist()

            self._arm_timer()
            self._service_running = True

            nxt = _next_wake_at_ms(list(self.jobs.values()))
            logger.info(f"cron: started (jobs={len(self.jobs)}, nextWakeAtMs={nxt})")

        await self._locked(_do_start)

    async def stop(self) -> None:
        """Stop cron service.
        
        Mirrors TS stopTimer(): clears the timer and sets it to null.
        The async nature ensures we properly await timer cancellation in Python's
        asyncio model (NodeJS clearTimeout is synchronous and immediate).
        """
        if self._timer:
            await self._timer.stop_async()  # ✅ Async cancellation for Python
            self._timer = None
        self._service_running = False
        logger.info("cron: stopped")

    def shutdown(self) -> None:
        """Synchronous shutdown wrapper for backward compatibility."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.stop())
            else:
                loop.run_until_complete(self.stop())
        except RuntimeError:
            asyncio.run(self.stop())

    async def status(self) -> Dict[str, Any]:
        async def _do() -> Dict[str, Any]:
            await self._ensure_loaded()
            nxt = _next_wake_at_ms(list(self.jobs.values())) if self._cron_enabled else None
            return {
                "enabled": self._cron_enabled,
                "storePath": str(self.store_path) if self.store_path else None,
                "jobs": len(self.jobs),
                "nextWakeAtMs": nxt,
            }
        return await self._locked(_do)

    async def list_jobs(self, include_disabled: bool = False) -> list[Dict[str, Any]]:
        async def _do() -> list[Dict[str, Any]]:
            await self._ensure_loaded()
            jobs = list(self.jobs.values())
            if not include_disabled:
                jobs = [j for j in jobs if j.enabled]
            jobs.sort(key=lambda j: j.state.next_run_ms or 0)
            return [self._job_to_dict(j) for j in jobs]
        return await self._locked(_do)

    async def list_page(
        self,
        *,
        include_disabled: bool = False,
        limit: int | None = None,
        offset: int = 0,
        query: str | None = None,
        enabled: str | None = None,
        sort_by: str = "nextRunAtMs",
        sort_dir: str = "asc",
    ) -> Dict[str, Any]:
        """Paginated/filtered job list — mirrors TS cron.listPage."""

        def _normalize_query(value: str | None) -> str:
            if not isinstance(value, str):
                return ""
            return value.strip().lower()

        def _resolve_enabled_filter() -> str:
            if enabled in ("all", "enabled", "disabled"):
                return enabled
            return "all" if include_disabled else "enabled"

        def _sort_jobs(jobs: list[CronJob]) -> list[CronJob]:
            dir_mult = -1 if sort_dir == "desc" else 1

            def compare(a: CronJob, b: CronJob) -> int:
                cmp = 0
                if sort_by == "name":
                    a_name = a.name if isinstance(a.name, str) else ""
                    b_name = b.name if isinstance(b.name, str) else ""
                    cmp = (a_name.casefold() > b_name.casefold()) - (
                        a_name.casefold() < b_name.casefold()
                    )
                elif sort_by == "updatedAtMs":
                    cmp = a.updated_at_ms - b.updated_at_ms
                else:
                    a_next = a.state.next_run_ms
                    b_next = b.state.next_run_ms
                    if isinstance(a_next, int) and isinstance(b_next, int):
                        cmp = a_next - b_next
                    elif isinstance(a_next, int):
                        cmp = -1
                    elif isinstance(b_next, int):
                        cmp = 1
                if cmp != 0:
                    return cmp * dir_mult
                a_id = a.id if isinstance(a.id, str) else ""
                b_id = b.id if isinstance(b.id, str) else ""
                return (a_id > b_id) - (a_id < b_id)

            from functools import cmp_to_key

            return sorted(jobs, key=cmp_to_key(compare))

        async def _do() -> Dict[str, Any]:
            await self._ensure_loaded()
            enabled_filter = _resolve_enabled_filter()
            normalized_query = _normalize_query(query)
            source = list(self.jobs.values())

            filtered: list[CronJob] = []
            for job in source:
                if enabled_filter == "enabled" and not job.enabled:
                    continue
                if enabled_filter == "disabled" and job.enabled:
                    continue
                if normalized_query:
                    haystack = _normalize_query(
                        " ".join(
                            [
                                job.name or "",
                                job.description or "",
                                job.agent_id or "",
                            ]
                        )
                    )
                    if normalized_query not in haystack:
                        continue
                filtered.append(job)

            sorted_jobs = _sort_jobs(filtered)
            total = len(sorted_jobs)
            safe_offset = max(0, min(total, int(offset)))
            default_limit = total if total > 0 else 50
            page_limit = max(1, min(200, int(limit if limit is not None else default_limit)))
            page_jobs = sorted_jobs[safe_offset : safe_offset + page_limit]
            next_offset = safe_offset + len(page_jobs)
            return {
                "jobs": [self._job_to_dict(j) for j in page_jobs],
                "total": total,
                "offset": safe_offset,
                "limit": page_limit,
                "hasMore": next_offset < total,
                "nextOffset": next_offset if next_offset < total else None,
            }

        return await self._locked(_do)

    async def add_job(self, job: CronJob) -> CronJob:
        async def _do() -> CronJob:
            self._warn_if_disabled("add")
            await self._ensure_loaded()

            now = _now_ms()
            if job.state.next_run_ms is None:
                job.state.next_run_ms = compute_job_next_run_at_ms(job, now)

            self.jobs[job.id] = job
            await self._persist()
            self._arm_timer()

            self._emit(jobId=job.id, action="added", nextRunAtMs=job.state.next_run_ms)
            logger.info(f"cron: added job {job.name!r} (id={job.id})")
            return job

        return await self._locked(_do)

    async def update_job(self, job_id: str, patch: Dict[str, Any]) -> CronJob:
        async def _do() -> CronJob:
            self._warn_if_disabled("update")
            await self._ensure_loaded()

            job = self.jobs.get(job_id)
            if not job:
                raise ValueError(f"cron: unknown job id: {job_id}")

            now = _now_ms()
            _apply_job_patch(job, patch)
            job.updated_at_ms = now

            if job.enabled:
                job.state.next_run_ms = compute_job_next_run_at_ms(job, now)
            else:
                job.state.next_run_ms = None
                job.state.running_at_ms = None

            await self._persist()
            self._arm_timer()
            self._emit(jobId=job_id, action="updated", nextRunAtMs=job.state.next_run_ms)
            logger.info(f"cron: updated job {job_id!r}")
            return job

        return await self._locked(_do)

    async def remove_job(self, job_id: str) -> Dict[str, Any]:
        async def _do() -> Dict[str, Any]:
            self._warn_if_disabled("remove")
            await self._ensure_loaded()

            removed = job_id in self.jobs
            if removed:
                del self.jobs[job_id]

            await self._persist()
            self._arm_timer()

            if removed:
                self._emit(jobId=job_id, action="removed")
                logger.info(f"cron: removed job {job_id!r}")

            return {"ok": True, "removed": removed}

        return await self._locked(_do)

    async def run(self, job_id: str, mode: Literal["due", "force"] = "force") -> Dict[str, Any]:
        async def _do() -> Dict[str, Any]:
            self._warn_if_disabled("run")
            await self._ensure_loaded()

            job = self.jobs.get(job_id)
            if not job:
                raise ValueError(f"cron: unknown job id: {job_id}")

            now = _now_ms()
            forced = mode == "force"

            if not forced and not _is_job_runnable(job, now):
                return {"ok": True, "ran": False, "reason": "not-due"}

            await self._execute_job(job, forced=forced)
            await self._persist()
            self._arm_timer()
            return {"ok": True, "ran": True}

        return await self._locked(_do)

    async def run_job_now(self, job_id: str) -> Dict[str, Any]:
        return await self.run(job_id, mode="force")

    def enqueue_run(self, job_id: str) -> Dict[str, Any]:
        """Schedule job asynchronously and return immediately.

        Creates an asyncio task to run the job and returns {ok, enqueued, runId}
        without waiting for completion.  Mirrors TS cron.run enqueue semantics.
        """
        import uuid as _uuid

        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "enqueued": False, "error": f"unknown job: {job_id}"}

        run_id = str(_uuid.uuid4())

        async def _run_task() -> None:
            try:
                await self.run(job_id, mode="force")
            except Exception as exc:
                logger.error("enqueue_run error for %s: %s", job_id, exc)

        try:
            asyncio.ensure_future(_run_task())
        except RuntimeError:
            # No running event loop — fall back to creating a task once one is available
            try:
                loop = asyncio.get_event_loop()
                loop.call_soon(lambda: asyncio.ensure_future(_run_task()))
            except Exception as exc:
                logger.warning("enqueue_run could not schedule task: %s", exc)
                return {"ok": False, "enqueued": False, "error": str(exc)}

        return {"ok": True, "enqueued": True, "runId": run_id}

    def wake(
        self,
        text: str,
        mode: Literal["now", "next-heartbeat"] = "now",
    ) -> Dict[str, Any]:
        text = text.strip()
        if not text:
            return {"ok": False}

        if self.enqueue_system_event:
            try:
                result = self.enqueue_system_event(text)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception as e:
                logger.error(f"cron: error in enqueue_system_event: {e}")

        if mode == "now" and self.request_heartbeat_now:
            try:
                self.request_heartbeat_now(reason="wake")
            except Exception as e:
                logger.error(f"cron: error in request_heartbeat_now: {e}")

        return {"ok": True}

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self.jobs.get(job_id)

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        return self._job_to_dict(job) if job else None

    # ------------------------------------------------------------------
    # Timer callback (matches TS onTimer)
    # ------------------------------------------------------------------

    async def _run_single_job(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        """Execute one due job and return a result dict.

        Extracted helper so ``_on_timer`` can run multiple jobs concurrently
        via ``asyncio.gather``.  Mirrors TS timer loop per-job logic.
        """
        job_id: str = snap["id"]
        job: CronJob = snap["job"]
        started_at = _now_ms()
        job.state.running_at_ms = started_at
        self._emit(jobId=job_id, action="started", runAtMs=started_at)

        timeout_ms = self._resolve_job_timeout_ms(job)

        try:
            if self.lane_manager:
                from openclaw.agents.queuing.lanes import CommandLane

                async def job_task():
                    return await self._execute_job_core(job)

                if timeout_ms is not None:
                    core_result = await asyncio.wait_for(
                        self.lane_manager.enqueue_in_lane(CommandLane.CRON, job_task),
                        timeout=timeout_ms / 1000,
                    )
                else:
                    core_result = await self.lane_manager.enqueue_in_lane(
                        CommandLane.CRON, job_task
                    )
            else:
                if timeout_ms is not None:
                    core_result = await asyncio.wait_for(
                        self._execute_job_core(job),
                        timeout=timeout_ms / 1000,
                    )
                else:
                    core_result = await self._execute_job_core(job)
            return {
                "id": job_id,
                "job": job,
                "started_at": started_at,
                "ended_at": _now_ms(),
                **core_result,
            }
        except asyncio.TimeoutError:
            logger.warning("cron: job %r timed out after %sms", job_id, timeout_ms)
            return {
                "id": job_id,
                "job": job,
                "started_at": started_at,
                "ended_at": _now_ms(),
                "status": "error",
                "error": "cron: job execution timed out",
            }
        except Exception as err:
            logger.warning("cron: job %r failed: %s", job_id, err, exc_info=True)
            return {
                "id": job_id,
                "job": job,
                "started_at": started_at,
                "ended_at": _now_ms(),
                "status": "error",
                "error": str(err),
            }

    async def _on_timer(self) -> None:
        """Timer callback — matches TS onTimer with running-guard + re-arm.

        Multiple due jobs are executed **concurrently** via ``asyncio.gather``,
        bounded by ``max_concurrent_runs`` (default 1 — matches TS default).
        This mirrors the TS ``maxConcurrentRuns`` limit in timer.ts.
        """
        if self._timer_running:
            # Re-arm at MAX_TIMER_DELAY_MS to keep scheduler alive
            # Mirrors TS armRunningRecheckTimer behavior
            if self._timer:
                self._timer.arm_at_max_delay()
            return

        self._timer_running = True
        try:
            due_snapshots = await self._locked(self._pick_due_jobs)

            if due_snapshots:
                # Resolve max_concurrent_runs from cron config (default 1 — matches TS)
                max_concurrent: int = int(self.cron_config.get("maxConcurrentRuns", 1))
                if max_concurrent < 1:
                    max_concurrent = 1

                # Process in batches of max_concurrent, parallel within each batch
                results: list[Dict[str, Any]] = []
                for batch_start in range(0, len(due_snapshots), max_concurrent):
                    batch = due_snapshots[batch_start: batch_start + max_concurrent]
                    batch_results = await asyncio.gather(
                        *[self._run_single_job(snap) for snap in batch],
                        return_exceptions=False,
                    )
                    results.extend(batch_results)

                await self._locked(self._apply_results, results)

            # Session reaper (outside lock, self-throttled)
            await self._sweep_sessions()

        finally:
            self._timer_running = False
            self._arm_timer()

    async def _pick_due_jobs(self) -> list[Dict[str, Any]]:
        """Under lock: reload store, find due jobs, mark running, persist."""
        await self._ensure_loaded(force_reload=True, skip_recompute=True)
        now = _now_ms()
        due = [
            j for j in self.jobs.values()
            if _is_job_runnable(j, now)
        ]

        if not due:
            jobs_list = list(self.jobs.values())
            if _recompute_next_runs_for_maintenance(jobs_list, now):
                await self._persist()
            return []

        for job in due:
            job.state.running_at_ms = now
            job.state.last_error = None
        await self._persist()

        return [{"id": j.id, "job": j} for j in due]

    async def _apply_results(self, results: list[Dict[str, Any]]) -> None:
        """Under lock: apply execution results, recompute, persist."""
        await self._ensure_loaded(force_reload=True, skip_recompute=True)

        for r in results:
            job = self.jobs.get(r["id"])
            if not job:
                continue

            status: Literal["ok", "error", "skipped"] = r.get("status", "error")
            error: str | None = r.get("error")
            started_at: int = r["started_at"]
            ended_at: int = r["ended_at"]
            delivered: bool | None = r.get("delivered")

            should_delete, alert_params = _apply_job_result(
                job, status, error, started_at, ended_at,
                delivered=delivered,
                cron_config=self.cron_config,
            )

            self._emit_job_finished(job, r, started_at)
            self._append_run_log(job, r)

            # Fire failure alert if _apply_job_result resolved one
            if alert_params and self.send_failure_alert:
                asyncio.create_task(
                    self._fire_failure_alert(alert_params)
                )

            if should_delete and job.id in self.jobs:
                del self.jobs[job.id]
                self._emit(jobId=job.id, action="removed")

        # Maintenance-only recompute (don't advance past-due values)
        now = _now_ms()
        _recompute_next_runs_for_maintenance(list(self.jobs.values()), now)
        await self._persist()

    async def _run_missed_jobs(self, now_ms: int) -> None:
        """Run overdue jobs after startup (matches TS runMissedJobs).
        
        NOTE: Jobs are executed synchronously (with await) to ensure they
        complete during start() for testing reliability. This matches TS behavior
        where runMissedJobs() awaits Promise.allSettled() before returning.
        """
        missed = [
            j for j in self.jobs.values()
            if _is_job_runnable(j, now_ms, skip_at_if_already_ran=True)
        ]

        if missed:
            logger.info(
                f"cron: running {len(missed)} missed jobs after restart: "
                f"{[j.id for j in missed]}"
            )
            # Execute missed jobs and wait for completion
            import asyncio
            await asyncio.gather(
                *[self._execute_job(job, forced=False) for job in missed],
                return_exceptions=True
            )

    async def _sweep_sessions(self) -> None:
        """Call session reaper (self-throttled, outside lock)."""
        try:
            from .session_reaper import sweep_cron_run_sessions
        except ImportError:
            return

        try:
            store_paths: set[str] = set()
            if self.resolve_session_store_path:
                agent_id = self.default_agent_id
                if self.jobs:
                    for job in self.jobs.values():
                        aid = job.agent_id or agent_id
                        store_paths.add(self.resolve_session_store_path(aid))
                else:
                    store_paths.add(self.resolve_session_store_path(agent_id))
            elif self.session_store_path:
                store_paths.add(self.session_store_path)

            now = _now_ms()
            for sp in store_paths:
                try:
                    await sweep_cron_run_sessions(
                        cron_config=self.cron_config,
                        session_store_path=sp,
                        now_ms=now,
                    )
                except Exception as e:
                    logger.warning(f"cron: session reaper sweep failed for {sp!r}: {e}")
        except Exception as e:
            logger.debug(f"cron: session reaper error: {e}")

    # ------------------------------------------------------------------
    # Job execution
    # ------------------------------------------------------------------

    def _resolve_job_timeout_ms(self, job: CronJob) -> int | None:
        """Resolve per-job execution timeout."""
        if isinstance(job.payload, AgentTurnPayload):
            ts = job.payload.timeout_seconds
            if ts is not None:
                configured = int(ts * 1000)
                return None if configured <= 0 else configured
        return DEFAULT_JOB_TIMEOUT_MS

    async def _execute_job(self, job: CronJob, *, forced: bool = False) -> Dict[str, Any]:
        """Execute a single job with full state management (used by run command)."""
        started_at = _now_ms()
        job.state.running_at_ms = started_at
        job.state.last_error = None
        self._emit(jobId=job.id, action="started", runAtMs=started_at)

        try:
            core_result = await self._execute_job_core(job)
        except Exception as err:
            core_result = {"status": "error", "error": str(err)}

        ended_at = _now_ms()
        status: Literal["ok", "error", "skipped"] = core_result.get("status", "error")
        error: str | None = core_result.get("error")
        delivered: bool | None = core_result.get("delivered")

        should_delete, alert_params = _apply_job_result(
            job, status, error, started_at, ended_at,
            delivered=delivered,
            cron_config=self.cron_config,
        )

        self._emit_job_finished(job, core_result, started_at)
        self._append_run_log(job, {**core_result, "started_at": started_at, "ended_at": ended_at})

        if alert_params and self.send_failure_alert:
            asyncio.create_task(self._fire_failure_alert(alert_params))

        if should_delete and job.id in self.jobs:
            del self.jobs[job.id]
            self._emit(jobId=job.id, action="removed")

        return core_result

    async def _execute_job_core(self, job: CronJob) -> Dict[str, Any]:
        """Core execution — matches TS executeJobCore."""
        if job.session_target == "main":
            return await self._execute_main_session_job(job)
        else:
            return await self._execute_isolated_job(job)

    async def _execute_main_session_job(self, job: CronJob) -> Dict[str, Any]:
        """Execute main session job (systemEvent). Matches TS executeJobCore main branch."""
        text = _resolve_job_payload_text_for_main(job)
        if not text:
            kind = getattr(job.payload, "kind", "unknown")
            reason = (
                "main job requires non-empty systemEvent text"
                if kind == "systemEvent"
                else 'main job requires payload.kind="systemEvent"'
            )
            return {"status": "skipped", "error": reason}

        if self.enqueue_system_event:
            try:
                res = self.enqueue_system_event(
                    text,
                    agent_id=job.agent_id,
                    session_key=job.session_key,
                    context_key=f"cron:{job.id}",
                )
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"cron: error enqueuing system event: {e}")

        if job.wake_mode == "now" and self.run_heartbeat_once:
            reason = f"cron:{job.id}"
            max_wait_ms = 2 * 60_000
            retry_delay_ms = 250
            wait_started = _now_ms()

            heartbeat_result: Dict[str, Any] = {"status": "error", "reason": "not-run"}
            while True:
                try:
                    heartbeat_result = await self.run_heartbeat_once(
                        reason=reason,
                        agent_id=job.agent_id,
                        session_key=job.session_key,
                    )
                except Exception as e:
                    heartbeat_result = {"status": "error", "reason": str(e)}
                    break

                hb_status = heartbeat_result.get("status")
                hb_reason = heartbeat_result.get("reason")
                if hb_status != "skipped" or hb_reason != "requests-in-flight":
                    break
                if _now_ms() - wait_started > max_wait_ms:
                    # Timeout: fall back to request
                    if self.request_heartbeat_now:
                        self.request_heartbeat_now(
                            reason=reason,
                            agent_id=job.agent_id,
                            session_key=job.session_key,
                        )
                    return {"status": "ok", "summary": text}
                await asyncio.sleep(retry_delay_ms / 1000)

            hb_status = heartbeat_result.get("status", "error")
            if hb_status == "ran":
                return {"status": "ok", "summary": text}
            elif hb_status == "skipped":
                return {"status": "skipped", "error": heartbeat_result.get("reason"), "summary": text}
            else:
                return {"status": "error", "error": heartbeat_result.get("reason"), "summary": text}
        else:
            if self.request_heartbeat_now:
                try:
                    self.request_heartbeat_now(
                        reason=f"cron:{job.id}",
                        agent_id=job.agent_id,
                        session_key=job.session_key,
                    )
                except Exception as e:
                    logger.error(f"cron: error requesting heartbeat: {e}")
            return {"status": "ok", "summary": text}

    async def _execute_isolated_job(self, job: CronJob) -> Dict[str, Any]:
        """Execute isolated agent job. Matches TS executeJobCore isolated branch."""
        if not isinstance(job.payload, AgentTurnPayload):
            return {"status": "skipped", "error": "isolated job requires payload.kind=agentTurn"}

        if not self.run_isolated_agent_job:
            return {"status": "error", "error": "isolated agent callback not configured"}

        # TS passes {job, message: job.payload.message}
        res = await self.run_isolated_agent_job(
            job=job,
            message=job.payload.message,
        )

        # Post summary to main session (only if delivery was NOT already done)
        summary_text = (res.get("summary") or "").strip()
        from .normalize import resolve_cron_delivery_plan
        delivery_plan = resolve_cron_delivery_plan(job)
        delivered = bool(res.get("delivered"))

        if summary_text and delivery_plan.get("requested") and not delivered:
            prefix = "Cron"
            if res.get("status") == "error":
                label = f"{prefix} (error): {summary_text}"
            else:
                label = f"{prefix}: {summary_text}"

            if self.enqueue_system_event:
                try:
                    r = self.enqueue_system_event(
                        label,
                        agent_id=job.agent_id,
                        session_key=job.session_key,
                        context_key=f"cron:{job.id}",
                    )
                    if asyncio.iscoroutine(r):
                        await r
                except Exception as e:
                    logger.error(f"cron: error posting summary to main: {e}")

            if job.wake_mode == "now" and self.request_heartbeat_now:
                try:
                    self.request_heartbeat_now(
                        reason=f"cron:{job.id}",
                        agent_id=job.agent_id,
                        session_key=job.session_key,
                    )
                except Exception as e:
                    logger.error(f"cron: error requesting heartbeat: {e}")

        return {
            "status": res.get("status", "ok"),
            "error": res.get("error"),
            "summary": res.get("summary"),
            "delivered": res.get("delivered"),          # TS: pass through for lastDelivered tracking
            "delivery_attempted": res.get("deliveryAttempted") or res.get("delivery_attempted"),
            "session_id": res.get("session_id") or res.get("sessionId"),
            "session_key": res.get("session_key") or res.get("sessionKey"),
            "model": res.get("model"),
            "provider": res.get("provider"),
            "usage": res.get("usage"),
        }

    # ------------------------------------------------------------------
    # Emit / log helpers
    # ------------------------------------------------------------------

    async def _fire_failure_alert(self, params: dict) -> None:
        """Invoke the external send_failure_alert callback with full routing params.

        ``params`` is the dict returned by ``_resolve_failure_alert_config`` and
        populated by ``_apply_job_result``.  The cooldown gate timestamp is already
        written to ``job.state.last_failure_alert_at_ms`` before this is called, so
        here we just deliver the alert and persist state.

        Mirrors TS ``emitFailureAlert()`` in service/timer.ts.
        """
        if not self.send_failure_alert:
            return

        job: CronJob = params["job"]
        error: str | None = params.get("error")
        consecutive: int = params.get("consecutive_errors", 0)
        channel: str | None = params.get("channel")
        to: str | None = params.get("to")
        mode: str | None = params.get("mode")
        account_id: str | None = params.get("account_id")

        safe_job_name = job.name or job.id
        truncated_error = (error or "unknown error").strip()[:200]
        text = f'Cron job "{safe_job_name}" failed {consecutive} times\nLast error: {truncated_error}'

        logger.info(
            "cron: sending failure alert for job %r (consecutiveErrors=%d, channel=%s)",
            job.id, consecutive, channel,
        )
        try:
            ok = await self.send_failure_alert(
                job, consecutive, error,
                text=text,
                channel=channel,
                to=to,
                mode=mode,
                account_id=account_id,
            )
            if ok and self._store:
                try:
                    self._store.save(list(self.jobs.values()))
                except Exception:
                    pass
        except Exception as e:
            logger.error("cron: failure alert send error for job %r: %s", job.id, e)

    def _emit_job_finished(
        self,
        job: CronJob,
        result: Dict[str, Any],
        run_at_ms: int,
    ) -> None:
        self._emit(
            jobId=job.id,
            action="finished",
            status=result.get("status"),
            error=result.get("error"),
            summary=result.get("summary"),
            sessionId=result.get("session_id") or result.get("sessionId"),
            sessionKey=result.get("session_key") or result.get("sessionKey"),
            runAtMs=run_at_ms,
            durationMs=job.state.last_duration_ms,
            nextRunAtMs=job.state.next_run_ms,
            model=result.get("model"),
            provider=result.get("provider"),
            usage=result.get("usage"),
        )

    def _append_run_log(self, job: CronJob, result: Dict[str, Any]) -> None:
        if not self.log_dir:
            return
        try:
            # Import resolve function
            from openclaw.cron.run_log import resolve_cron_run_log_prune_options
            
            # Resolve prune options from config
            run_log_config = None
            if isinstance(self.cron_config, dict):
                run_log_config = self.cron_config.get("runLog")
            
            prune_options = resolve_cron_run_log_prune_options(run_log_config)
            
            run_log = CronRunLog(self.log_dir, job.id, prune_options=prune_options)
            run_log.append({
                "ts": _now_ms(),
                "jobId": job.id,
                "action": "finished",
                "status": result.get("status"),
                "error": result.get("error"),
                "summary": result.get("summary"),
                "runAtMs": job.state.last_run_at_ms,
                "durationMs": job.state.last_duration_ms,
                "nextRunAtMs": job.state.next_run_ms,
                "sessionId": result.get("session_id") or result.get("sessionId"),
                "sessionKey": result.get("session_key") or result.get("sessionKey"),
                "model": result.get("model"),
                "provider": result.get("provider"),
                "usage": result.get("usage"),
            })
        except Exception as e:
            logger.warning(f"cron: failed to write run log: {e}")

    def _job_to_dict(self, job: CronJob) -> Dict[str, Any]:
        from openclaw.cron.serialization import convert_job_to_api
        return convert_job_to_api(job.to_dict())


# ---------------------------------------------------------------------------
# Patch helper (matches TS applyJobPatch)
# ---------------------------------------------------------------------------

def _apply_job_patch(job: CronJob, patch: Dict[str, Any]) -> None:
    """Apply a patch to a CronJob."""
    if "name" in patch:
        job.name = str(patch["name"]).strip() or job.name
    if "description" in patch:
        job.description = patch.get("description")
    if "enabled" in patch:
        job.enabled = bool(patch["enabled"])
    if "delete_after_run" in patch or "deleteAfterRun" in patch:
        job.delete_after_run = bool(
            patch.get("delete_after_run", patch.get("deleteAfterRun", False))
        )
    if "session_target" in patch or "sessionTarget" in patch:
        val = patch.get("session_target", patch.get("sessionTarget"))
        if val in ("main", "isolated"):
            job.session_target = val
    if "wake_mode" in patch or "wakeMode" in patch:
        val = patch.get("wake_mode", patch.get("wakeMode"))
        if val in ("now", "next-heartbeat"):
            job.wake_mode = val
    if "agent_id" in patch or "agentId" in patch:
        job.agent_id = patch.get("agent_id", patch.get("agentId"))
    if "session_key" in patch or "sessionKey" in patch:
        job.session_key = patch.get("session_key", patch.get("sessionKey"))

    # Schedule patch
    if "schedule" in patch:
        sched = patch["schedule"]
        stype = sched.get("kind") or sched.get("type")
        if stype == "at":
            at_val = sched.get("at") or sched.get("timestamp") or ""
            job.schedule = AtSchedule(at=at_val, type="at")
        elif stype == "every":
            every_ms = sched.get("every_ms") or sched.get("everyMs") or sched.get("interval_ms") or 0
            anchor_ms = sched.get("anchor_ms") or sched.get("anchorMs")
            job.schedule = EverySchedule(every_ms=int(every_ms), type="every", anchor_ms=anchor_ms)
        elif stype == "cron":
            expr = sched.get("expr") or sched.get("expression") or ""
            tz = sched.get("tz") or sched.get("timezone") or None
            stagger_ms = sched.get("stagger_ms") or sched.get("staggerMs")
            job.schedule = CronScheduleType(
                expr=expr, type="cron", tz=tz,
                stagger_ms=int(stagger_ms) if stagger_ms is not None else None,
            )

    # Payload patch
    if "payload" in patch:
        p = patch["payload"]
        kind = p.get("kind")
        if kind == "systemEvent":
            job.payload = SystemEventPayload(text=p.get("text", ""))
        elif kind == "agentTurn":
            msg = p.get("message") or p.get("prompt") or ""
            existing = job.payload if isinstance(job.payload, AgentTurnPayload) else None
            raw_fallbacks = p.get("fallbacks") or p.get("modelFallbacks")
            new_fallbacks: list[str] | None = None
            if isinstance(raw_fallbacks, list) and raw_fallbacks:
                new_fallbacks = [str(f) for f in raw_fallbacks if f]
            elif existing:
                new_fallbacks = existing.fallbacks
            job.payload = AgentTurnPayload(
                message=msg or (existing.message if existing else ""),
                kind="agentTurn",
                model=p.get("model", existing.model if existing else None),
                thinking=p.get("thinking", existing.thinking if existing else None),
                timeout_seconds=(
                    p.get("timeout_seconds")
                    or p.get("timeoutSeconds")
                    or (existing.timeout_seconds if existing else None)
                ),
                allow_unsafe_external_content=bool(
                    p.get("allow_unsafe_external_content")
                    or p.get("allowUnsafeExternalContent")
                    or (existing.allow_unsafe_external_content if existing else False)
                ),
                fallbacks=new_fallbacks,
                light_context=bool(
                    p.get("light_context")
                    or p.get("lightContext")
                    or (existing.light_context if existing else False)
                ),
            )

    # Delivery patch
    if "delivery" in patch:
        d = patch["delivery"]
        if d is None:
            job.delivery = None
        else:
            existing_d = job.delivery
            # Parse failure_destination from patch
            fd_raw = d.get("failure_destination") or d.get("failureDestination")
            failure_dest = None
            if isinstance(fd_raw, dict):
                from openclaw.cron.types import CronFailureDestination
                failure_dest = CronFailureDestination(
                    channel=fd_raw.get("channel"),
                    to=fd_raw.get("to"),
                )
            elif existing_d:
                failure_dest = existing_d.failure_destination
            job.delivery = CronDelivery(
                mode=d.get("mode", existing_d.mode if existing_d else "announce"),
                channel=d.get("channel", existing_d.channel if existing_d else None),
                to=d.get("to") or d.get("target") or (existing_d.to if existing_d else None),
                best_effort=bool(
                    d.get("best_effort", d.get("bestEffort",
                        existing_d.best_effort if existing_d else False))
                ),
                account_id=(
                    d.get("account_id") or d.get("accountId")
                    or (existing_d.account_id if existing_d else None)
                ),
                failure_destination=failure_dest,
            )

    # Failure alert patch
    if "failure_alert" in patch or "failureAlert" in patch:
        fa_raw = patch.get("failure_alert") or patch.get("failureAlert")
        if fa_raw is None:
            job.failure_alert = None
        elif isinstance(fa_raw, dict):
            from openclaw.cron.types import CronFailureAlert
            existing_fa = job.failure_alert
            job.failure_alert = CronFailureAlert(
                after_n_errors=int(
                    fa_raw.get("after_n_errors") or fa_raw.get("afterNErrors")
                    or (existing_fa.after_n_errors if existing_fa else 3)
                ),
                cooldown_ms=int(
                    fa_raw.get("cooldown_ms") or fa_raw.get("cooldownMs")
                    or (existing_fa.cooldown_ms if existing_fa else 3_600_000)
                ),
                message=fa_raw.get("message", existing_fa.message if existing_fa else None),
            )


def _is_job_due(job: CronJob, now_ms: int, *, forced: bool = False) -> bool:
    if forced:
        return True
    return _is_job_runnable(job, now_ms)


def _resolve_job_payload_text_for_main(job: CronJob) -> str | None:
    if isinstance(job.payload, SystemEventPayload):
        text = (job.payload.text or "").strip()
        return text if text else None
    return None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_cron_service: Optional[CronService] = None


def get_cron_service() -> Optional[CronService]:
    return _cron_service


def set_cron_service(service: CronService) -> None:
    global _cron_service
    _cron_service = service
