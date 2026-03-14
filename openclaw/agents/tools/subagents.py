"""Subagents management tool

Fully aligned with TypeScript openclaw/src/agents/tools/subagents-tool.ts

This tool allows agents to manage their spawned subagents:
- List active and recent subagent runs
- Kill subagents (with cascade to descendants)
- Steer subagents (interrupt and inject new message)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal

from .base import AgentToolBase, AgentToolResult, TextContent

logger = logging.getLogger(__name__)

# Constants (mirrors TS lines 37-44)
DEFAULT_RECENT_MINUTES = 30
MAX_RECENT_MINUTES = 24 * 60
MAX_STEER_MESSAGE_CHARS = 4_000
STEER_RATE_LIMIT_MS = 2_000
STEER_ABORT_SETTLE_TIMEOUT_MS = 5_000

# Global steer rate limit tracking
_steer_rate_limit: dict[str, int] = {}


def format_duration_compact(runtime_ms: int) -> str:
    """Format duration in compact form (mirrors TS shared/subagents-format.ts)"""
    if runtime_ms <= 0:
        return "0s"
    
    total_seconds = round(runtime_ms / 1000)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    if hours > 0:
        return f"{hours}h{minutes}m{seconds}s"
    if minutes > 0:
        return f"{minutes}m{seconds}s"
    return f"{seconds}s"


def format_token_usage_display(entry: dict[str, Any] | None) -> str:
    """Format token usage for display (mirrors TS shared/subagents-format.ts)"""
    if not entry:
        return ""
    
    input_tokens = entry.get("inputTokens", 0) or 0
    output_tokens = entry.get("outputTokens", 0) or 0
    
    if input_tokens == 0 and output_tokens == 0:
        return ""
    
    total = input_tokens + output_tokens
    if total >= 1000:
        return f"{total / 1000:.1f}k tok"
    return f"{total} tok"


def truncate_line(text: str, max_length: int) -> str:
    """Truncate line to max length"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def resolve_run_label(run: Any, fallback: str = "subagent") -> str:
    """Resolve display label for a run (mirrors TS resolveRunLabel lines 71-74)"""
    raw = (run.label or "").strip() or (run.task or "").strip()
    return raw or fallback


def resolve_run_status(run: Any) -> str:
    """Resolve display status for a run (mirrors TS resolveRunStatus lines 76-88)"""
    if not run.ended_at:
        return "running"
    
    status = (run.outcome or {}).get("status", "done")
    if status == "ok":
        return "done"
    if status == "error":
        return "failed"
    return status


def sort_runs(runs: list[Any]) -> list[Any]:
    """Sort runs by time (mirrors TS sortRuns lines 90-96)"""
    return sorted(
        runs,
        key=lambda r: r.started_at or r.created_at or 0,
        reverse=True,
    )


class SubagentsTool(AgentToolBase[dict, dict]):
    """
    Tool for managing spawned sub-agents.
    
    Fully aligned with TS createSubagentsTool() from subagents-tool.ts lines 389-727
    
    Actions:
    - list: Show active and recent subagent runs
    - kill: Terminate subagent (with cascade to descendants)
    - steer: Interrupt and inject new message into running subagent
    """
    
    def __init__(self, *, agent_session_key: str | None = None, gateway: Any = None):
        """
        Initialize subagents tool.
        
        Args:
            agent_session_key: Current agent session key
            gateway: Gateway instance for RPC calls
        """
        self.agent_session_key = agent_session_key
        self.gateway = gateway
    
    @property
    def name(self) -> str:
        return "subagents"
    
    @property
    def label(self) -> str:
        return "Subagents"
    
    @property
    def description(self) -> str:
        return "List, kill, or steer spawned sub-agents for this requester session. Use this for sub-agent orchestration."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "kill", "steer"],
                    "description": "Action to perform",
                },
                "target": {
                    "type": "string",
                    "description": "Target subagent (for kill/steer): run ID, label, index, or 'all'",
                },
                "message": {
                    "type": "string",
                    "description": "Message to inject (for steer action)",
                },
                "recentMinutes": {
                    "type": "number",
                    "description": f"Minutes of history to show (default: {DEFAULT_RECENT_MINUTES}, max: {MAX_RECENT_MINUTES})",
                    "minimum": 1,
                },
            },
        }
    
    async def execute(
        self,
        tool_call_id: str,
        params: dict,
        signal: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult[dict]:
        """Execute subagents tool action"""
        action = params.get("action", "list")
        
        if action == "list":
            return await self._execute_list(params)
        elif action == "kill":
            return await self._execute_kill(params)
        elif action == "steer":
            return await self._execute_steer(params)
        else:
            return AgentToolResult(
                content=[TextContent(text=f"Unsupported action: {action}")],
                details={"status": "error", "error": "Unsupported action"},
            )
    
    async def _execute_list(self, params: dict) -> AgentToolResult[dict]:
        """List active and recent subagent runs (mirrors TS lines 410-466)"""
        from openclaw.config.unified import load_config
        from openclaw.agents.subagent_registry import get_global_registry
        
        cfg = load_config()
        registry = get_global_registry()
        
        # Resolve requester key
        requester = self._resolve_requester_key(cfg)
        requester_session_key = requester["requesterSessionKey"]
        
        # Get runs for this requester
        runs = registry.list_runs_for_requester(requester_session_key)
        runs = sort_runs(runs)
        
        recent_minutes = params.get("recentMinutes", DEFAULT_RECENT_MINUTES)
        recent_minutes = max(1, min(MAX_RECENT_MINUTES, int(recent_minutes)))
        
        now = int(time.time() * 1000)
        recent_cutoff = now - (recent_minutes * 60_000)
        
        # Build list entries
        active_entries = []
        recent_entries = []
        index = 1
        
        for run in runs:
            runtime_ms = now - (run.started_at or run.created_at)
            if run.ended_at:
                runtime_ms = run.ended_at - (run.started_at or run.created_at)
            
            # Get session entry for token counts
            session_entry = None
            if self.gateway and hasattr(self.gateway, "session_manager"):
                try:
                    session_entry = self.gateway.session_manager.get_session_entry(run.child_session_key)
                except Exception:
                    pass
            
            model_display = run.model or "model n/a"
            if "/" in model_display:
                model_display = model_display.split("/")[-1]
            
            usage_text = format_token_usage_display(session_entry)
            status = resolve_run_status(run)
            runtime = format_duration_compact(runtime_ms)
            label = truncate_line(resolve_run_label(run), 48)
            task = truncate_line(run.task.strip(), 72)
            
            usage_suffix = f", {usage_text}" if usage_text else ""
            task_suffix = f" - {task}" if task.lower() != label.lower() else ""
            line = f"{index}. {label} ({model_display}, {runtime}{usage_suffix}) {status}{task_suffix}"
            
            entry_data = {
                "index": index,
                "runId": run.run_id,
                "sessionKey": run.child_session_key,
                "label": label,
                "task": task,
                "status": status,
                "runtime": runtime,
                "runtimeMs": runtime_ms,
                "model": run.model,
            }
            
            if not run.ended_at:
                active_entries.append({"line": line, "view": entry_data})
            elif run.ended_at >= recent_cutoff:
                entry_data["endedAt"] = run.ended_at
                recent_entries.append({"line": line, "view": entry_data})
            
            index += 1
        
        # Build text output
        text_lines = ["active subagents:"]
        if not active_entries:
            text_lines.append("(none)")
        else:
            text_lines.extend([e["line"] for e in active_entries])
        
        text_lines.append("")
        text_lines.append(f"recent (last {recent_minutes}m):")
        if not recent_entries:
            text_lines.append("(none)")
        else:
            text_lines.extend([e["line"] for e in recent_entries])
        
        text = "\n".join(text_lines)
        
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "status": "ok",
                "action": "list",
                "requesterSessionKey": requester_session_key,
                "callerSessionKey": requester["callerSessionKey"],
                "callerIsSubagent": requester["callerIsSubagent"],
                "total": len(runs),
                "active": [e["view"] for e in active_entries],
                "recent": [e["view"] for e in recent_entries],
            },
        )
    
    async def _execute_kill(self, params: dict) -> AgentToolResult[dict]:
        """Kill subagent run (mirrors TS lines 468-566)"""
        from openclaw.config.unified import load_config
        from openclaw.agents.subagent_registry import get_global_registry
        # Removed import of non-existent stop_subagents_for_requester
        
        target = params.get("target")
        if not target or not target.strip():
            return AgentToolResult(
                content=[TextContent(text="Missing subagent target")],
                details={"status": "error", "action": "kill", "error": "Missing target"},
            )
        
        cfg = load_config()
        registry = get_global_registry()
        requester = self._resolve_requester_key(cfg)
        requester_session_key = requester["requesterSessionKey"]
        
        runs = registry.list_runs_for_requester(requester_session_key)
        runs = sort_runs(runs)
        
        # Kill all
        if target in ("all", "*"):
            killed_count = 0
            killed_labels = []
            
            for run in runs:
                if run.ended_at:
                    continue
                
                # Kill this run
                await self._kill_subagent_run(run, cfg)
                killed_count += 1
                killed_labels.append(resolve_run_label(run))
                
                # Cascade to descendants
                cascade_result = await self._cascade_kill_children(
                    run.child_session_key,
                    cfg,
                )
                killed_count += cascade_result["killed"]
                killed_labels.extend(cascade_result["labels"])
            
            text = (
                f"killed {killed_count} subagent{'s' if killed_count != 1 else ''}."
                if killed_count > 0
                else "no running subagents to kill."
            )
            
            return AgentToolResult(
                content=[TextContent(text=text)],
                details={
                    "status": "ok",
                    "action": "kill",
                    "target": "all",
                    "killed": killed_count,
                    "labels": killed_labels,
                },
            )
        
        # Kill specific target
        resolved = self._resolve_subagent_target(runs, target)
        if "error" in resolved:
            return AgentToolResult(
                content=[TextContent(text=resolved["error"])],
                details={
                    "status": "error",
                    "action": "kill",
                    "target": target,
                    "error": resolved["error"],
                },
            )
        
        run = resolved["entry"]
        
        # Kill this run
        await self._kill_subagent_run(run, cfg)
        
        # Cascade to descendants
        cascade_result = await self._cascade_kill_children(run.child_session_key, cfg)
        
        cascade_text = (
            f" (+ {cascade_result['killed']} descendant{'s' if cascade_result['killed'] != 1 else ''})"
            if cascade_result["killed"] > 0
            else ""
        )
        
        text = f"killed {resolve_run_label(run)}{cascade_text}."
        
        return AgentToolResult(
            content=[TextContent(text=text)],
            details={
                "status": "ok",
                "action": "kill",
                "target": target,
                "runId": run.run_id,
                "sessionKey": run.child_session_key,
                "label": resolve_run_label(run),
                "cascadeKilled": cascade_result["killed"],
                "cascadeLabels": cascade_result["labels"] if cascade_result["killed"] > 0 else None,
            },
        )
    
    async def _execute_steer(self, params: dict) -> AgentToolResult[dict]:
        """Steer (restart) subagent with new message (mirrors TS lines 567-720)"""
        from openclaw.config.unified import load_config
        from openclaw.agents.subagent_registry import get_global_registry
        
        target = params.get("target")
        message = params.get("message")
        
        if not target or not target.strip():
            return AgentToolResult(
                content=[TextContent(text="Missing subagent target")],
                details={"status": "error", "action": "steer", "error": "Missing target"},
            )
        
        if not message or not message.strip():
            return AgentToolResult(
                content=[TextContent(text="Missing steer message")],
                details={"status": "error", "action": "steer", "error": "Missing message"},
            )
        
        if len(message) > MAX_STEER_MESSAGE_CHARS:
            return AgentToolResult(
                content=[TextContent(text=f"Message too long ({len(message)} chars, max {MAX_STEER_MESSAGE_CHARS})")],
                details={
                    "status": "error",
                    "action": "steer",
                    "target": target,
                    "error": f"Message too long ({len(message)} chars, max {MAX_STEER_MESSAGE_CHARS})",
                },
            )
        
        cfg = load_config()
        registry = get_global_registry()
        requester = self._resolve_requester_key(cfg)
        requester_session_key = requester["requesterSessionKey"]
        
        runs = registry.list_runs_for_requester(requester_session_key)
        runs = sort_runs(runs)
        
        # Resolve target
        resolved = self._resolve_subagent_target(runs, target)
        if "error" in resolved:
            return AgentToolResult(
                content=[TextContent(text=resolved["error"])],
                details={
                    "status": "error",
                    "action": "steer",
                    "target": target,
                    "error": resolved["error"],
                },
            )
        
        run = resolved["entry"]
        
        # Check if already finished
        if run.ended_at:
            return AgentToolResult(
                content=[TextContent(text=f"{resolve_run_label(run)} is already finished.")],
                details={
                    "status": "done",
                    "action": "steer",
                    "target": target,
                    "runId": run.run_id,
                    "sessionKey": run.child_session_key,
                },
            )
        
        # Check self-steer (forbidden)
        if (
            requester["callerIsSubagent"]
            and requester["callerSessionKey"] == run.child_session_key
        ):
            return AgentToolResult(
                content=[TextContent(text="Subagents cannot steer themselves.")],
                details={
                    "status": "forbidden",
                    "action": "steer",
                    "target": target,
                    "runId": run.run_id,
                    "sessionKey": run.child_session_key,
                    "error": "Subagents cannot steer themselves.",
                },
            )
        
        # Rate limit check
        rate_key = f"{requester['callerSessionKey']}:{run.child_session_key}"
        now = int(time.time() * 1000)
        last_sent_at = _steer_rate_limit.get(rate_key, 0)
        
        if now - last_sent_at < STEER_RATE_LIMIT_MS:
            return AgentToolResult(
                content=[TextContent(text="Steer rate limit exceeded. Wait a moment before sending another steer.")],
                details={
                    "status": "rate_limited",
                    "action": "steer",
                    "target": target,
                    "runId": run.run_id,
                    "sessionKey": run.child_session_key,
                    "error": "Steer rate limit exceeded",
                },
            )
        
        _steer_rate_limit[rate_key] = now
        
        # Mark for steer restart (suppresses announce)
        registry.mark_subagent_run_for_steer_restart(run.run_id)
        
        # Get session ID
        session_id = None
        if self.gateway and hasattr(self.gateway, "session_manager"):
            try:
                entry = self.gateway.session_manager.get_session_entry(run.child_session_key)
                if isinstance(entry, dict):
                    session_id = entry.get("sessionId")
            except Exception:
                pass
        
        # Abort current run
        if session_id:
            try:
                from openclaw.agents.pi_embedded import abort_embedded_pi_run
                abort_embedded_pi_run(session_id)
            except Exception as e:
                logger.debug(f"Failed to abort run: {e}")
        
        # Wait for abort to settle (best effort)
        if self.gateway:
            try:
                from openclaw.gateway.handlers import handle_agent_wait
                
                mock_connection = type("MockConnection", (), {"gateway": self.gateway})()
                await handle_agent_wait(
                    mock_connection,
                    {
                        "runId": run.run_id,
                        "timeoutMs": STEER_ABORT_SETTLE_TIMEOUT_MS,
                    },
                )
            except Exception:
                pass
        
        # Launch new run with steer message
        import uuid
        idempotency_key = str(uuid.uuid4())
        run_id = idempotency_key
        
        if self.gateway:
            try:
                from openclaw.gateway.handlers import handle_agent
                from openclaw.agents.subagent_spawn import AGENT_LANE_SUBAGENT
                
                mock_connection = type("MockConnection", (), {"gateway": self.gateway})()
                
                response = await handle_agent(
                    mock_connection,
                    {
                        "message": message,
                        "sessionKey": run.child_session_key,
                        "sessionId": session_id,
                        "idempotencyKey": idempotency_key,
                        "deliver": False,
                        "channel": "internal",
                        "lane": AGENT_LANE_SUBAGENT,
                        "timeout": 0,
                    },
                )
                
                if isinstance(response, dict) and "runId" in response:
                    run_id = response["runId"]
            
            except Exception as err:
                # Restore normal announce behavior if steer fails
                registry.mark_subagent_run_for_steer_restart("")  # Clear suppression
                
                return AgentToolResult(
                    content=[TextContent(text=f"Failed to steer: {err}")],
                    details={
                        "status": "error",
                        "action": "steer",
                        "target": target,
                        "runId": run_id,
                        "sessionKey": run.child_session_key,
                        "sessionId": session_id,
                        "error": str(err),
                    },
                )
        
        # Replace run in registry
        registry.replace_subagent_run_after_steer(
            old_run_id=run.run_id,
            new_run_id=run_id,
            new_child_session_key=run.child_session_key,
        )
        
        return AgentToolResult(
            content=[TextContent(text=f"steered {resolve_run_label(run)}.")],
            details={
                "status": "accepted",
                "action": "steer",
                "target": target,
                "runId": run_id,
                "sessionKey": run.child_session_key,
                "sessionId": session_id,
                "mode": "restart",
                "label": resolve_run_label(run),
            },
        )
    
    def _resolve_requester_key(self, cfg: Any) -> dict[str, Any]:
        """
        Resolve requester key from agent session key.
        
        Mirrors TS resolveRequesterKey() lines 229-275
        """
        from openclaw.routing.session_key import (
            is_subagent_session_key,
            parse_agent_session_key,
        )
        from openclaw.agents.subagent_spawn import (
            resolve_main_session_alias,
            resolve_internal_session_key,
            get_subagent_depth_from_session_store,
        )
        
        main_data = resolve_main_session_alias(cfg)
        main_key = main_data["mainKey"]
        alias = main_data["alias"]
        
        caller_raw = (self.agent_session_key or "").strip() or alias
        caller_session_key = resolve_internal_session_key(caller_raw, alias, main_key)
        
        if not is_subagent_session_key(caller_session_key):
            return {
                "requesterSessionKey": caller_session_key,
                "callerSessionKey": caller_session_key,
                "callerIsSubagent": False,
            }
        
        # Check if this subagent can spawn children (orchestrator)
        caller_depth = get_subagent_depth_from_session_store(
            caller_session_key,
            cfg=cfg,
            gateway=self.gateway,
        )
        
        max_spawn_depth = 1
        if hasattr(cfg, "agents") and hasattr(cfg.agents, "defaults"):
            defaults = cfg.agents.defaults
            if hasattr(defaults, "subagents"):
                subagents_cfg = defaults.subagents
                if isinstance(subagents_cfg, dict):
                    max_spawn_depth = subagents_cfg.get("maxSpawnDepth", 1)
                elif hasattr(subagents_cfg, "maxSpawnDepth"):
                    max_spawn_depth = subagents_cfg.maxSpawnDepth or 1
        
        if caller_depth < max_spawn_depth:
            # Orchestrator: see own children
            return {
                "requesterSessionKey": caller_session_key,
                "callerSessionKey": caller_session_key,
                "callerIsSubagent": True,
            }
        
        # Leaf: walk up to parent
        if self.gateway and hasattr(self.gateway, "session_manager"):
            try:
                entry = self.gateway.session_manager.get_session_entry(caller_session_key)
                if isinstance(entry, dict):
                    spawned_by = (entry.get("spawnedBy") or "").strip()
                    if spawned_by:
                        return {
                            "requesterSessionKey": spawned_by,
                            "callerSessionKey": caller_session_key,
                            "callerIsSubagent": True,
                        }
            except Exception:
                pass
        
        return {
            "requesterSessionKey": caller_session_key,
            "callerSessionKey": caller_session_key,
            "callerIsSubagent": True,
        }
    
    def _resolve_subagent_target(
        self,
        runs: list[Any],
        token: str | None,
    ) -> dict[str, Any]:
        """
        Resolve subagent target from string token.
        
        Mirrors TS resolveSubagentTarget() lines 142-199
        
        Supports:
        - "last": most recent run
        - "1", "2", etc.: numeric index
        - "session-key": by session key
        - "label": by exact label or prefix
        - "run-id-prefix": by run ID prefix
        """
        trimmed = (token or "").strip()
        if not trimmed:
            return {"error": "Missing subagent target."}
        
        # Numeric order: active first, then recent
        recent_minutes = DEFAULT_RECENT_MINUTES
        recent_cutoff = int(time.time() * 1000) - (recent_minutes * 60_000)
        
        active = [r for r in runs if not r.ended_at]
        recent = [r for r in runs if r.ended_at and r.ended_at >= recent_cutoff]
        numeric_order = active + recent
        
        # "last"
        if trimmed == "last":
            if runs:
                return {"entry": runs[0]}
            return {"error": "No subagents found."}
        
        # Numeric index
        if trimmed.isdigit():
            idx = int(trimmed)
            if idx <= 0 or idx > len(numeric_order):
                return {"error": f"Invalid subagent index: {trimmed}"}
            return {"entry": numeric_order[idx - 1]}
        
        # Session key
        if ":" in trimmed:
            for run in runs:
                if run.child_session_key == trimmed:
                    return {"entry": run}
            return {"error": f"Unknown subagent session: {trimmed}"}
        
        # Label (exact match)
        lowered = trimmed.lower()
        exact_matches = [r for r in runs if resolve_run_label(r).lower() == lowered]
        
        if len(exact_matches) == 1:
            return {"entry": exact_matches[0]}
        if len(exact_matches) > 1:
            return {"error": f"Ambiguous subagent label: {trimmed}"}
        
        # Label (prefix match)
        prefix_matches = [r for r in runs if resolve_run_label(r).lower().startswith(lowered)]
        
        if len(prefix_matches) == 1:
            return {"entry": prefix_matches[0]}
        if len(prefix_matches) > 1:
            return {"error": f"Ambiguous subagent label prefix: {trimmed}"}
        
        # Run ID prefix
        run_id_matches = [r for r in runs if r.run_id.startswith(trimmed)]
        
        if len(run_id_matches) == 1:
            return {"entry": run_id_matches[0]}
        if len(run_id_matches) > 1:
            return {"error": f"Ambiguous subagent run id prefix: {trimmed}"}
        
        return {"error": f"Unknown subagent target: {trimmed}"}
    
    async def _kill_subagent_run(self, run: Any, cfg: Any):
        """Kill a single subagent run (mirrors TS killSubagentRun lines 277-317)"""
        from openclaw.agents.subagent_registry import get_global_registry
        
        if run.ended_at:
            return {"killed": False}
        
        child_session_key = run.child_session_key
        session_id = None
        
        # Get session ID
        if self.gateway and hasattr(self.gateway, "session_manager"):
            try:
                entry = self.gateway.session_manager.get_session_entry(child_session_key)
                if isinstance(entry, dict):
                    session_id = entry.get("sessionId")
            except Exception:
                pass
        
        # Abort run
        aborted = False
        if session_id:
            try:
                from openclaw.agents.pi_embedded import abort_embedded_pi_run
                aborted = abort_embedded_pi_run(session_id)
            except Exception as e:
                logger.debug(f"Failed to abort run: {e}")
        
        # Mark as terminated
        registry = get_global_registry()
        registry.mark_subagent_run_terminated(run.run_id, reason="killed")
        
        return {"killed": True, "sessionId": session_id}
    
    async def _cascade_kill_children(
        self,
        parent_child_session_key: str,
        cfg: Any,
        seen_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        Recursively kill all descendant subagents.
        
        Mirrors TS cascadeKillChildren() lines 319-365
        """
        from openclaw.agents.subagent_registry import get_global_registry
        
        registry = get_global_registry()
        child_runs = registry.list_runs_for_requester(parent_child_session_key)
        
        seen_keys = seen_keys or set()
        killed = 0
        labels = []
        
        for run in child_runs:
            child_key = (run.child_session_key or "").strip()
            if not child_key or child_key in seen_keys:
                continue
            
            seen_keys.add(child_key)
            
            if not run.ended_at:
                stop_result = await self._kill_subagent_run(run, cfg)
                if stop_result.get("killed"):
                    killed += 1
                    labels.append(resolve_run_label(run))
            
            # Recurse for grandchildren
            cascade_result = await self._cascade_kill_children(
                child_key,
                cfg,
                seen_keys,
            )
            killed += cascade_result["killed"]
            labels.extend(cascade_result["labels"])
        
        return {"killed": killed, "labels": labels}
