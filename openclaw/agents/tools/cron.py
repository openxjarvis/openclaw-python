"""
Cron tool for scheduling tasks - aligned with TypeScript openclaw/src/agents/tools/cron-tool.ts

Actions: status, list, add, update, remove, run, runs, wake
"""
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from openclaw.agents.tools.base import AgentTool, ToolResult

# ToolResult uses 'content' field (not 'output'). Helper to create results cleanly.
def _ok(text: str) -> ToolResult:
    return ToolResult(success=True, content=text)

def _err(msg: str) -> ToolResult:
    return ToolResult(success=False, content="", error=msg)

logger = logging.getLogger(__name__)


class CronTool(AgentTool):
    """
    Tool for managing scheduled tasks (cron jobs).

    Matches TypeScript cron-tool.ts actions:
    - status: Service status (enabled, jobs count, next wake)
    - list:   List all jobs
    - add:    Add new job
    - update: Update existing job (full patch support)
    - remove: Remove job
    - run:    Trigger job immediately (due|force mode)
    - runs:   Get job run history
    - wake:   Send a wake event to the main session
    """

    name = "cron"
    description = """Manage Gateway cron jobs (status/list/add/update/remove/run/runs) and send wake events.

ACTIONS:
- status: Check cron scheduler status
- list: List jobs (use includeDisabled=true to include disabled)
- add: Create job (requires job object, see schema below)
- update: Modify job (requires jobId + patch object)
- remove: Delete job (requires jobId)
- run: Trigger job immediately (requires jobId)
- runs: Get job run history (requires jobId)
- wake: Send wake event (requires text, optional mode)

JOB SCHEMA (for add action):
{
  "name": "string (optional)",
  "schedule": { ... },      // Required: when to run
  "payload": { ... },       // Required: what to execute
  "delivery": { ... },      // Optional: announce summary or webhook POST
  "sessionTarget": "main" | "isolated",  // Required
  "enabled": true | false   // Optional, default true
}

SCHEDULE TYPES (schedule.kind):
- "at": One-shot at absolute time
  { "kind": "at", "at": "<ISO-8601 timestamp>" }
- "every": Recurring interval
  { "kind": "every", "everyMs": <interval-ms>, "anchorMs": <optional-start-ms> }
- "cron": Cron expression
  { "kind": "cron", "expr": "<cron-expression>", "tz": "<optional-timezone>" }

ISO timestamps without an explicit timezone are treated as UTC.

PAYLOAD TYPES (payload.kind):
- "systemEvent": Injects text as system event into session
  { "kind": "systemEvent", "text": "<message>" }
- "agentTurn": Runs agent with message (isolated sessions only)
  { "kind": "agentTurn", "message": "<prompt>", "model": "<optional>", "thinking": "<optional>", "timeoutSeconds": <optional, 0 means no timeout> }

DELIVERY (top-level):
  { "mode": "none|announce|webhook", "channel": "<optional>", "to": "<optional>", "bestEffort": <optional-bool> }
  - Default for isolated agentTurn jobs (when delivery omitted): "announce"
  - announce: send to chat channel (optional channel/to target)
  - webhook: send finished-run event as HTTP POST to delivery.to (URL required)
  - If the task needs to send to a specific chat/recipient, set announce delivery.channel/to; do not call messaging tools inside the run.

CRITICAL CONSTRAINTS:
- sessionTarget="main" REQUIRES payload.kind="systemEvent"
- sessionTarget="isolated" REQUIRES payload.kind="agentTurn"
- For webhook callbacks, use delivery.mode="webhook" with delivery.to set to a URL.
Default: prefer isolated agentTurn jobs unless the user explicitly wants a main-session system event.

WAKE MODES (for wake action):
- "next-heartbeat" (default): Wake on next heartbeat
- "now": Wake immediately

Use jobId as the canonical identifier; id is accepted for compatibility. Use contextMessages (0-10) to add previous messages as context to the job text.
    """

    def __init__(self, cron_service=None, channel_registry=None, session_manager=None, agent_session_key=None):
        self._cron_service = cron_service
        self._channel_registry = channel_registry
        self._session_manager = session_manager
        self._agent_session_key = agent_session_key  # 新增：保存当前 session key
        self._current_chat_info: dict[str, str] | None = None
        logger.info("CronTool initialized")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def requires_confirmation(self) -> bool:
        return False

    @property
    def can_stream(self) -> bool:
        return False

    @property
    def category(self) -> str:
        return "system"

    @property
    def tags(self) -> list[str]:
        return ["scheduling", "automation", "cron", "tasks"]

    # ------------------------------------------------------------------
    # Setters
    # ------------------------------------------------------------------
    def set_cron_service(self, service: Any) -> None:
        self._cron_service = service

    def set_channel_registry(self, registry: Any) -> None:
        self._channel_registry = registry

    def set_session_manager(self, manager: Any) -> None:
        self._session_manager = manager

    def set_agent_session_key(self, session_key: str) -> None:
        """Set the current agent session key (mirrors TS agentSessionKey)."""
        self._agent_session_key = session_key

    def set_chat_context(self, channel: str, chat_id: str) -> None:
        self._current_chat_info = {"channel": channel, "chat_id": chat_id}

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "list", "add", "update", "remove", "run", "runs", "wake"],
                    "description": "Action to perform",
                },
                "includeDisabled": {
                    "type": "boolean",
                    "description": "Include disabled jobs in list (default: false)",
                },
                "job": {
                    "type": "object",
                    "description": "Job configuration for 'add' action",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "enabled": {"type": "boolean", "default": True},
                        "schedule": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["at", "every", "cron"]},
                                "timestamp": {"type": "string"},
                                "interval_ms": {"type": "number"},
                                "anchor": {"type": "string"},
                                "expression": {"type": "string"},
                                "timezone": {"type": "string"},
                            },
                            "required": ["type"],
                        },
                        "sessionTarget": {
                            "type": "string",
                            "enum": ["main", "isolated"],
                            "description": "Session target: 'main' (requires systemEvent) or 'isolated' (requires agentTurn). Default: prefer 'isolated' for most tasks.",
                        },
                        "wakeMode": {
                            "type": "string",
                            "enum": ["now", "next-heartbeat"],
                            "default": "next-heartbeat",
                        },
                        "payload": {
                            "type": "object",
                            "description": "Payload for the cron job (systemEvent or agentTurn)",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["systemEvent", "agentTurn"],
                                    "description": "CRITICAL: 'systemEvent' for main session (injects text), 'agentTurn' for isolated session (runs agent with message)",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Text for systemEvent kind",
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Message for agentTurn kind (alias for message)",
                                },
                                "message": {
                                    "type": "string",
                                    "description": "Message prompt for agentTurn kind",
                                },
                                "model": {
                                    "type": "string",
                                    "description": "Model override for agentTurn",
                                },
                                "fallbacks": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Model fallback chain for agentTurn (tried in order if primary fails)",
                                },
                                "timeoutSeconds": {
                                    "type": "integer",
                                    "description": "Per-turn timeout in seconds for agentTurn (default: no limit)",
                                },
                                "lightContext": {
                                    "type": "boolean",
                                    "description": "Skip heavy context loading for agentTurn (faster, less context)",
                                },
                            },
                            "required": ["kind"],
                        },
                        "delivery": {
                            "type": "object",
                            "description": "Delivery config for isolated agentTurn jobs",
                            "properties": {
                                "mode": {
                                    "type": "string",
                                    "enum": ["announce", "none", "webhook"],
                                    "description": "announce=send to channel, none=no delivery, webhook=HTTP POST",
                                },
                                "channel": {
                                    "type": "string",
                                    "description": "Channel to deliver to (e.g. 'telegram', 'feishu', 'last'=last-used)",
                                },
                                "to": {
                                    "type": "string",
                                    "description": "Recipient chat ID / user ID (required unless mode=none or channel=last)",
                                },
                                "accountId": {
                                    "type": "string",
                                    "description": "Specific bot account to send from (for multi-account setups)",
                                },
                                "best_effort": {
                                    "type": "boolean",
                                    "description": "If true, delivery failures are warnings not errors",
                                },
                                "failureDestination": {
                                    "type": "object",
                                    "description": "Override delivery target for failure alerts only",
                                    "properties": {
                                        "channel": {"type": "string"},
                                        "to": {"type": "string"},
                                    },
                                },
                            },
                        },
                        "failureAlert": {
                            "type": "object",
                            "description": "Send an alert after N consecutive errors",
                            "properties": {
                                "afterNErrors": {
                                    "type": "integer",
                                    "description": "Number of consecutive errors before alerting (default: 3)",
                                },
                                "cooldownMs": {
                                    "type": "integer",
                                    "description": "Minimum ms between alerts (default: 3600000 = 1 hour)",
                                },
                                "message": {
                                    "type": "string",
                                    "description": "Custom alert message template",
                                },
                            },
                        },
                    },
                    "required": ["name", "schedule", "payload"],
                },
                "jobId": {
                    "type": "string",
                    "description": "Job ID for update/remove/run/runs actions",
                },
                "patch": {
                    "type": "object",
                    "description": "Patch object for 'update' action (name, enabled, schedule, payload, delivery, sessionTarget, wakeMode, etc.)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["due", "force", "now", "next-heartbeat"],
                    "description": "Mode for 'run' (due|force) or 'wake' (now|next-heartbeat)",
                },
                "text": {
                    "type": "string",
                    "description": "Text for 'wake' action",
                },
                "limit": {
                    "type": "integer",
                    "description": "Limit for 'runs' action (default: 20)",
                },
            },
            "required": ["action"],
        }

    # ------------------------------------------------------------------
    # Execute dispatcher
    # ------------------------------------------------------------------
    async def execute(self, args: dict[str, Any]) -> ToolResult:
        if not self._cron_service:
            # Lazily resolve the global cron service set by GatewayBootstrap
            try:
                from openclaw.cron.service import get_cron_service
                self._cron_service = get_cron_service()
            except Exception:
                pass
        if not self._cron_service:
            return _err("Cron service not available")

        action = args.get("action")
        try:
            if action == "status":
                return await self._action_status()
            elif action == "list":
                return await self._action_list(args.get("includeDisabled", False))
            elif action == "add":
                return await self._action_add(args.get("job", {}))
            elif action == "update":
                return await self._action_update(args.get("jobId"), args.get("patch", {}))
            elif action == "remove":
                return await self._action_remove(args.get("jobId"))
            elif action == "run":
                return await self._action_run(args.get("jobId"), args.get("mode", "force"))
            elif action == "runs":
                return await self._action_runs(args.get("jobId"), args.get("limit", 20))
            elif action == "wake":
                return await self._action_wake(args.get("text", ""), args.get("mode", "now"))
            else:
                return _err(f"Unknown action: {action}")
        except Exception as e:
            logger.error(f"Cron tool error: {e}", exc_info=True)
            return _err(str(e))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _action_status(self) -> ToolResult:
        """Get cron service status (matches TypeScript status action)."""
        info = await self._cron_service.status()
        enabled = info.get("enabled", False)
        job_count = info.get("jobs", 0)
        nxt = info.get("nextWakeAtMs")

        lines = []
        lines.append(f"Cron service: {'enabled' if enabled else 'disabled'}")
        lines.append(f"Jobs: {job_count}")
        if nxt:
            from openclaw.cron.schedule import format_next_run
            lines.append(f"Next wake: {format_next_run(nxt)}")

        return _ok("\n".join(lines))

    async def _action_list(self, include_disabled: bool = False) -> ToolResult:
        """List all cron jobs."""
        jobs = await self._cron_service.list_jobs(include_disabled=include_disabled)

        if not jobs:
            suffix = " (excluding disabled)" if not include_disabled else ""
            return _ok(f"No scheduled jobs{suffix}")

        text = f"Scheduled Jobs ({len(jobs)}):\n\n"
        for job in jobs:
            jid = job.get("id", "?")
            name = job.get("name", "Unnamed")
            enabled = job.get("enabled", True)
            schedule = job.get("schedule", {})
            st = job.get("session_target", "main")

            status_icon = "ON" if enabled else "OFF"
            text += f"[{status_icon}] {name}\n"
            text += f"  ID: {jid}\n"
            text += f"  Schedule: {self._format_schedule(schedule)}\n"
            text += f"  Type: {'Isolated Agent' if st == 'isolated' else 'System Event'}\n"

            delivery = job.get("delivery")
            if delivery:
                ch = delivery.get("channel", "")
                tgt = delivery.get("target", "")
                if ch:
                    text += f"  Delivery: {ch}"
                    if tgt:
                        text += f" -> {tgt}"
                    text += "\n"
            text += "\n"

        return _ok(text.strip())

    async def _action_add(self, job_config: dict[str, Any]) -> ToolResult:
        """Add new cron job (matches TypeScript add with normalization)."""
        from openclaw.cron.types import (
            AgentTurnPayload,
            CronDelivery,
            CronFailureAlert,
            CronFailureDestination,
            CronJob,
        )
        from openclaw.cron.normalize import normalize_cron_job_create

        # --- 注入 session_key（镜像 TS 逻辑）---
        # 如果 job_config 中没有 session_key，尝试从多个来源获取
        if "session_key" not in job_config:
            session_key_to_inject = None
            
            # 优先使用 _agent_session_key（由 Pi runtime 设置）
            if self._agent_session_key:
                session_key_to_inject = self._agent_session_key
            
            # 次优：从当前 chat 上下文构造 session_key
            # 格式：agent:main:channel:direct:chatId（镜像 TS 的 resolveAgentSessionKey）
            elif self._current_chat_info:
                channel = self._current_chat_info.get("channel")
                chat_id = self._current_chat_info.get("chat_id")
                if channel and chat_id:
                    # 构造基本 session_key（假设 agent_id 是 "main"）
                    session_key_to_inject = f"agent:main:{channel}:direct:{chat_id}"
                    logger.debug(f"[cron tool] constructed session_key from chat context: {session_key_to_inject}")
            
            if session_key_to_inject:
                job_config["session_key"] = session_key_to_inject
                logger.debug(f"[cron tool] injected session_key: {session_key_to_inject}")
            else:
                logger.warning("[cron tool] no session_key available for injection, delivery may fail")

        job_id = f"cron-{uuid.uuid4().hex[:8]}"

        # --- Apply normalization (including auto-delivery) ---
        # This matches TypeScript behavior where normalizeCronJobCreate automatically
        # adds delivery: {mode: "announce"} for isolated agentTurn jobs
        normalized_config = normalize_cron_job_create(job_config)
        if not normalized_config:
            return _err("Invalid job configuration")

        logger.info(f"[cron tool] normalized_config: {normalized_config}")

        # --- Normalize schedule ---
        schedule_config = normalized_config.get("schedule", {})
        schedule = _normalize_schedule(schedule_config)
        if schedule is None:
            return _err(f"Unknown schedule type: {schedule_config.get('type')}")

        # --- Normalize payload ---
        payload_config = normalized_config.get("payload", {})
        payload = _normalize_payload(payload_config)
        if payload is None:
            return _err(f"Unknown payload kind: {payload_config.get('kind')}")

        # --- Session target (already normalized) ---
        session_target = normalized_config.get("session_target", "main")
        
        # --- 提取 session_key 和 agent_id（镜像 TS） ---
        session_key = normalized_config.get("session_key")
        agent_id = normalized_config.get("agent_id")
        
        # 如果 agent_id 未设置但有 session_key，从 session_key 中提取
        if not agent_id and session_key:
            from openclaw.routing.session_key import parse_agent_session_key
            parsed = parse_agent_session_key(session_key)
            if parsed:
                agent_id = parsed.agent_id
                logger.debug(f"[cron tool] extracted agent_id from session_key: {agent_id}")
        
        # --- Delivery (already normalized, but still apply context fallback) ---
        delivery = None
        delivery_config = normalized_config.get("delivery")

        if delivery_config:
            mode = delivery_config.get("mode", "announce")
            channel = delivery_config.get("channel", "")
            target = delivery_config.get("to") or delivery_config.get("target")
            
            # Fallback to current chat context if not explicitly set
            if not channel and self._current_chat_info:
                channel = self._current_chat_info.get("channel", "")
            if not target and self._current_chat_info:
                target = self._current_chat_info.get("chat_id")
            
            # Create delivery even if channel is empty (for announce mode without target)
            # The delivery resolver will handle finding the appropriate channel at runtime
            delivery = CronDelivery(
                mode=mode,
                channel=channel or None,  # None if empty, will be resolved at delivery time
                to=target or None,
                best_effort=delivery_config.get("best_effort", delivery_config.get("bestEffort", False)),
                account_id=delivery_config.get("accountId") or delivery_config.get("account_id"),
                failure_destination=_parse_failure_destination(
                    delivery_config.get("failureDestination") or delivery_config.get("failure_destination")
                ),
            )

        # --- Failure alert (from normalized config) ---
        failure_alert: CronFailureAlert | None = None
        fa_config = normalized_config.get("failure_alert") or normalized_config.get("failureAlert")
        if isinstance(fa_config, dict):
            failure_alert = CronFailureAlert(
                after_n_errors=int(fa_config.get("after_n_errors") or fa_config.get("afterNErrors") or 3),
                cooldown_ms=int(fa_config.get("cooldown_ms") or fa_config.get("cooldownMs") or 3_600_000),
                message=fa_config.get("message"),
            )

        # --- Wake mode (from normalized config) ---
        wake_mode = normalized_config.get("wake_mode", "next-heartbeat")

        # --- Create job (use normalized values, 包括 session_key 和 agent_id) ---
        job = CronJob(
            id=job_id,
            name=normalized_config.get("name", "Unnamed Job"),
            description=normalized_config.get("description"),
            enabled=normalized_config.get("enabled", True),
            schedule=schedule,
            session_target=session_target,
            wake_mode=wake_mode,
            payload=payload,
            delivery=delivery,
            failure_alert=failure_alert,
            session_key=session_key,  # 新增：传入 session_key
            agent_id=agent_id,  # 新增：传入 agent_id
        )

        added_job = await self._cron_service.add_job(job)

        text = f"Created cron job: {added_job.name}\n"
        text += f"  ID: {job_id}\n"
        text += f"  Schedule: {self._format_schedule(normalized_config.get('schedule', {}))}\n"
        text += f"  Type: {'Isolated Agent' if session_target == 'isolated' else 'System Event'}"
        if delivery:
            mode_str = f" [{delivery.mode}]" if delivery.mode != "announce" else ""
            text += f"\n  Delivery: {delivery.channel}{mode_str}"
            if delivery.to:
                text += f" -> {delivery.to}"
        if failure_alert:
            text += f"\n  Failure alert: after {failure_alert.after_n_errors} errors"

        return _ok(text)

    async def _action_update(self, job_id: str | None, patch: dict[str, Any]) -> ToolResult:
        """Update existing job (full patch support matching TypeScript)."""
        if not job_id:
            return _err("jobId is required for update action")
        if not patch:
            return _err("patch object is required")

        try:
            updated_job = await self._cron_service.update_job(job_id, patch)
            return _ok(f"Updated job: {updated_job.name}\n  ID: {job_id}")
        except ValueError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"Failed to update job: {e}")

    async def _action_remove(self, job_id: str | None) -> ToolResult:
        """Remove cron job."""
        if not job_id:
            return _err("jobId is required for remove action")

        result = await self._cron_service.remove_job(job_id)
        if result.get("removed"):
            return _ok(f"Removed job: {job_id}")
        else:
            return _err(f"Job not found: {job_id}")

    async def _action_run(self, job_id: str | None, mode: str = "force") -> ToolResult:
        """Run job immediately (matches TypeScript run with due|force)."""
        if not job_id:
            return _err("jobId is required for run action")

        if mode not in ("due", "force"):
            mode = "force"

        try:
            result = await self._cron_service.run(job_id, mode=mode)
            if result.get("ran"):
                return _ok(f"Executed job: {job_id}")
            elif result.get("reason") == "not-due":
                return _ok(f"Job {job_id} is not due yet (use mode='force' to override)")
            else:
                return _err(f"Job not executed: {result}")
        except ValueError as e:
            return _err(str(e))

    async def _action_runs(self, job_id: str | None, limit: int = 20) -> ToolResult:
        """Get job run history (matches TypeScript runs action)."""
        if not job_id:
            return _err("jobId is required for runs action")

        from openclaw.cron.store import CronRunLog
        log_dir = self._cron_service.log_dir
        if not log_dir:
            return _ok("No run logs configured")

        run_log = CronRunLog(log_dir, job_id)
        entries = run_log.read(limit=limit)

        if not entries:
            return _ok(f"No run history for job {job_id}")

        text = f"Run history for {job_id} (last {len(entries)}):\n\n"
        for entry in reversed(entries):
            ts = entry.get("timestamp", "?")
            status = entry.get("status", "?")
            duration = entry.get("duration_ms", 0)
            error = entry.get("error")
            summary = entry.get("summary")

            text += f"  [{status}] {ts} ({duration}ms)"
            if error:
                text += f" - {error}"
            if summary:
                text += f"\n    {summary[:100]}"
            text += "\n"

        return _ok(text.strip())

    async def _action_wake(self, text: str, mode: str = "now") -> ToolResult:
        """Send wake event (matches TypeScript wake action)."""
        if not text.strip():
            return _err("text is required for wake action")

        if mode not in ("now", "next-heartbeat"):
            mode = "now"

        result = self._cron_service.wake(text=text, mode=mode)
        if result.get("ok"):
            return _ok(f"Wake event sent (mode={mode}): {text[:100]}")
        else:
            return _err("Failed to send wake event")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _format_schedule(schedule: dict[str, Any]) -> str:
        stype = schedule.get("type", "")
        if stype == "at":
            return f"One-time at {schedule.get('timestamp', '?')}"
        elif stype == "every":
            interval_ms = schedule.get("interval_ms", schedule.get("intervalMs", 0))
            if interval_ms >= 3_600_000:
                return f"Every {interval_ms / 3_600_000:.1f}h"
            elif interval_ms >= 60_000:
                return f"Every {interval_ms / 60_000:.0f}m"
            else:
                return f"Every {interval_ms / 1000:.0f}s"
        elif stype == "cron":
            expr = schedule.get("expression", "?")
            tz = schedule.get("timezone", "UTC")
            return f"Cron: {expr} ({tz})"
        return "Unknown schedule"


# ---------------------------------------------------------------------------
# Normalization helpers (matches TypeScript normalizeCronJobCreate)
# ---------------------------------------------------------------------------

def _normalize_schedule(config: dict[str, Any]):
    """Normalize schedule config to a schedule type."""
    from openclaw.cron.types import AtSchedule, EverySchedule, CronSchedule

    stype = config.get("type", config.get("kind", ""))
    if stype == "at":
        at_val = config.get("at") or config.get("timestamp") or ""
        return AtSchedule(at=at_val)
    elif stype == "every":
        every_ms = (
            config.get("every_ms")
            or config.get("everyMs")
            or config.get("interval_ms")
            or config.get("intervalMs")
            or 0
        )
        anchor_ms = config.get("anchor_ms") or config.get("anchorMs")
        return EverySchedule(every_ms=int(every_ms), anchor_ms=anchor_ms)
    elif stype == "cron":
        expr = config.get("expr") or config.get("expression") or ""
        tz = config.get("tz") or config.get("timezone") or "UTC"
        stagger_ms = config.get("stagger_ms") or config.get("staggerMs")
        return CronSchedule(
            expr=expr,
            tz=tz,
            stagger_ms=int(stagger_ms) if stagger_ms is not None else None,
        )
    return None


def _normalize_payload(config: dict[str, Any]):
    """Normalize payload config to a payload type."""
    from openclaw.cron.types import SystemEventPayload, AgentTurnPayload

    kind = config.get("kind", "")
    if kind == "systemEvent":
        return SystemEventPayload(text=config.get("text", ""))
    elif kind == "agentTurn":
        raw_fallbacks = config.get("fallbacks") or config.get("modelFallbacks")
        fallbacks: list[str] | None = None
        if isinstance(raw_fallbacks, list) and raw_fallbacks:
            fallbacks = [str(f) for f in raw_fallbacks if f]
        return AgentTurnPayload(
            message=config.get("message", config.get("prompt", "")),
            model=config.get("model"),
            timeout_seconds=(
                int(config["timeoutSeconds"]) if "timeoutSeconds" in config
                else int(config["timeout_seconds"]) if "timeout_seconds" in config
                else None
            ),
            fallbacks=fallbacks,
            light_context=bool(config.get("lightContext") or config.get("light_context")),
        )
    return None


def _parse_failure_destination(raw: Any) -> "CronFailureDestination | None":
    """Parse failure_destination dict to CronFailureDestination."""
    if not isinstance(raw, dict):
        return None
    from openclaw.cron.types import CronFailureDestination
    channel = raw.get("channel")
    to = raw.get("to")
    if channel or to:
        return CronFailureDestination(channel=channel, to=to)
    return None
