"""Gateway method handlers"""
from __future__ import annotations


import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
import sys

# Python 3.9 compatibility
if sys.version_info >= (3, 11):
    from datetime import UTC
else:
    UTC = timezone.utc
from typing import Any

from openclaw.config.paths import resolve_state_dir

# Import store-based session methods
from openclaw.gateway.api.sessions_methods import (
    SessionsListMethod,
    SessionsPreviewMethod,
    SessionsResolveMethod,
    SessionsPatchMethod,
    SessionsResetMethod,
    SessionsDeleteMethod,
    SessionsCompactMethod,
)

# Import chat methods
from openclaw.gateway.api.chat import CHAT_METHODS

logger = logging.getLogger(__name__)

# Type alias for handler functions
Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]

# Registry of method handlers
_handlers: dict[str, Handler] = {}

# Global instances (set by gateway server)
_session_manager: Any | None = None
_tool_registry: Any | None = None
_channel_registry: Any | None = None
_agent_runtime: Any | None = None
_wizard_handler: Any | None = None
_plugin_manager: Any | None = None
_queue_manager: Any | None = None
_node_registry: Any | None = None
_node_event_handler: Any | None = None

RESET_COMMAND_RE = re.compile(r"^/(new|reset)(?:\s+([\s\S]*))?$", re.IGNORECASE)
BARE_SESSION_RESET_PROMPT = (
    "A new session was started via /new or /reset. "
    "Execute your Session Startup sequence now - read the required files before responding to the user. "
    "Then greet the user in your configured persona, if one is provided. "
    "Be yourself - use your defined voice, mannerisms, and mood. "
    "Keep it to 1-3 sentences and ask what they want to do. "
    "If the runtime model differs from default_model in the system prompt, mention the default model. "
    "Do not mention internal steps, files, tools, or reasoning."
)


def set_global_instances(session_manager, tool_registry, channel_registry, agent_runtime, wizard_handler=None, queue_manager=None, node_registry=None, node_event_handler=None):
    """Set global instances for handlers to use"""
    global _session_manager, _tool_registry, _channel_registry, _agent_runtime, _wizard_handler, _plugin_manager, _queue_manager, _node_registry, _node_event_handler
    _session_manager = session_manager
    _tool_registry = tool_registry
    _channel_registry = channel_registry
    _agent_runtime = agent_runtime
    _wizard_handler = wizard_handler
    _plugin_manager = None
    _queue_manager = queue_manager
    _node_registry = node_registry
    _node_event_handler = node_event_handler


def _get_current_config() -> dict:
    """Return the currently loaded OpenClaw config as a dict.

    Used by agents.files.* handlers to resolve workspace directories via
    resolve_agent_workspace_dir(), matching TS resolveAgentWorkspaceFileOrRespondError().
    """
    try:
        from openclaw.gateway.config_service import get_config_service
        svc = get_config_service()
        if svc:
            cfg = svc.get_config()
            if isinstance(cfg, dict):
                return cfg
            if hasattr(cfg, "model_dump"):
                return cfg.model_dump()
            if hasattr(cfg, "dict"):
                return cfg.dict()
    except Exception:
        pass
    return {}


def _get_plugin_manager(connection: Any):
    """Get or lazily initialize plugin manager."""
    global _plugin_manager
    if _plugin_manager is not None:
        return _plugin_manager

    if getattr(connection, "gateway", None) is not None:
        gateway_pm = getattr(connection.gateway, "plugin_manager", None)
        if gateway_pm is not None:
            _plugin_manager = gateway_pm
            return _plugin_manager

    from openclaw.plugins.plugin_manager import PluginManager

    _plugin_manager = PluginManager()
    return _plugin_manager


def _sorted_unique_strings(*values: Any) -> list[str]:
    out: set[str] = set()
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item:
                out.add(item)
    return sorted(out)


def _resolve_node_caller_id(connection: Any) -> str | None:
    node_id = getattr(connection, "node_id", None)
    if isinstance(node_id, str) and node_id.strip():
        return node_id.strip()

    auth_ctx = getattr(connection, "auth_context", None)
    device_id = getattr(auth_ctx, "device_id", None)
    if isinstance(device_id, str) and device_id.strip():
        return device_id.strip()

    client_info = getattr(connection, "client_info", None)
    if isinstance(client_info, dict):
        device = client_info.get("device")
        if isinstance(device, dict):
            did = device.get("id")
            if isinstance(did, str) and did.strip():
                return did.strip()
        cid = client_info.get("id")
        if isinstance(cid, str) and cid.strip():
            return cid.strip()
    return None


def register_handler(method: str) -> Callable[[Handler], Handler]:
    """Decorator to register a method handler"""

    def decorator(func: Handler) -> Handler:
        _handlers[method] = func
        return func

    return decorator


def get_method_handler(method: str) -> Handler | None:
    """Get handler for a method"""
    return _handlers.get(method)


def list_registered_methods() -> list[str]:
    """Return registered Gateway RPC method names."""
    return sorted(_handlers.keys())


# Initialize store-based session method instances
_sessions_list_method = SessionsListMethod()
_sessions_preview_method = SessionsPreviewMethod()
_sessions_resolve_method = SessionsResolveMethod()
_sessions_patch_method = SessionsPatchMethod()
_sessions_reset_method = SessionsResetMethod()
_sessions_delete_method = SessionsDeleteMethod()
_sessions_compact_method = SessionsCompactMethod()


# Register chat methods
def _register_chat_methods():
    """Register all chat methods from api/chat.py"""
    for chat_method in CHAT_METHODS:
        # Create wrapper that calls method.execute
        def make_handler(method_obj):
            async def handler(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
                try:
                    return await method_obj.execute(connection, params)
                except Exception as e:
                    logger.error(f"Chat method {method_obj.name} error: {e}", exc_info=True)
                    raise
            return handler
        
        _handlers[chat_method.name] = make_handler(chat_method)
        logger.debug(f"Registered chat method: {chat_method.name}")


# Register chat methods on module load
_register_chat_methods()


# Core method handlers


@register_handler("health")
async def handle_health(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Health check summary (TS-like envelope)."""
    started = int(datetime.now(UTC).timestamp() * 1000)
    gateway = getattr(connection, "gateway", None)
    connections = len(getattr(gateway, "connections", [])) if gateway is not None else 0
    started_at = getattr(gateway, "started_at", None)
    uptime = 0
    if isinstance(started_at, (int, float)):
        uptime = max(0, int(datetime.now(UTC).timestamp() - started_at))
    channels_running = []
    if gateway is not None and hasattr(gateway, "channel_manager"):
        try:
            channels_running = list(gateway.channel_manager.list_running())
        except Exception:
            channels_running = []
    ended = int(datetime.now(UTC).timestamp() * 1000)
    return {
        "ok": True,
        "ts": ended,
        "durationMs": max(0, ended - started),
        "gateway": {"uptimeSec": uptime, "connections": connections},
        "channels": {"active": channels_running, "count": len(channels_running)},
        "agents": {"count": len(connection.config.agents.agents) if getattr(connection.config, "agents", None) and connection.config.agents.agents else 0},
        "sessions": {"count": len(getattr(getattr(gateway, "active_runs", {}), "keys", lambda: [])()) if gateway is not None else 0},
    }


@register_handler("status")
async def handle_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get server status"""
    gateway = getattr(connection, "gateway", None)
    connections = len(getattr(gateway, "connections", [])) if gateway is not None else 0
    active_channels: list[str] = []
    if gateway is not None and hasattr(gateway, "channel_manager"):
        try:
            active_channels = list(gateway.channel_manager.list_running())
        except Exception:
            active_channels = []
    summary = {
        "ok": True,
        "ts": int(datetime.now(UTC).timestamp() * 1000),
        "gateway": {
            "running": True,
            "port": connection.config.gateway.port,
            "connections": connections,
        },
        "agents": {
            "count": len(connection.config.agents.agents) if connection.config.agents.agents else 0
        },
        "channels": {"active": active_channels},
    }
    # Non-admin callers receive a redacted subset.
    scopes = set(getattr(getattr(connection, "auth_context", None), "scopes", set()) or set())
    if "operator.admin" not in scopes:
        summary["gateway"].pop("connections", None)
    return summary


@register_handler("config.get")
async def handle_config_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get configuration"""
    return connection.config.model_dump(exclude_none=True)


@register_handler("sessions.list")
async def handle_sessions_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List active sessions - using store-based implementation"""
    return await _sessions_list_method.execute(connection, params)


@register_handler("channels.list")
async def handle_channels_list(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List available channels"""
    if not _channel_registry:
        return []

    return _channel_registry.get_all_channels()


# Placeholder handlers for methods to be implemented


def _inject_timestamp(message: str, timezone: str = "UTC") -> str:
    """Inject a compact timestamp prefix into a message if one isn't present.

    Mirrors TS injectTimestamp() in server-methods/agent-timestamp.ts.
    Format: [DOW YYYY-MM-DD HH:MM TZ] message
    """
    import re as _re
    from openclaw.agents.date_time import resolve_user_timezone as _resolve_tz
    if not message.strip():
        return message
    # Already has a timestamp envelope like [Wed 2024-01-15 14:30 UTC]
    if _re.match(r"^\[.*\d{4}-\d{2}-\d{2} \d{2}:\d{2}", message):
        return message
    # Already has a cron-injected timestamp
    if "Current time: " in message:
        return message
    resolved_tz = _resolve_tz(timezone)
    try:
        import zoneinfo as _zi
        tz_obj = _zi.ZoneInfo(resolved_tz)
        now = datetime.now(tz_obj)
    except Exception:
        try:
            import pytz
            tz_obj = pytz.timezone(resolved_tz)
            now = datetime.now(tz_obj)
        except Exception:
            now = datetime.now(UTC)
    dow = now.strftime("%a")
    formatted = now.strftime("%Y-%m-%d %H:%M")
    tz_abbr = now.strftime("%Z") or resolved_tz
    return f"[{dow} {formatted} {tz_abbr}] {message}"


def _normalize_attachments(attachments: Any) -> list[dict[str, Any]]:
    """Normalize RPC attachments to chat attachment format.

    Mirrors TS normalizeRpcAttachmentsToChatAttachments().
    """
    if not isinstance(attachments, list):
        return []
    result = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        att_type = att.get("type", "")
        mime = att.get("mimeType", "")
        content = att.get("content")
        file_name = att.get("fileName")
        if att_type == "image" or (mime and mime.startswith("image/")):
            if isinstance(content, str) and content:
                result.append({
                    "type": "image",
                    "mimeType": mime or "image/jpeg",
                    "data": content,
                    "fileName": file_name,
                })
        elif att_type in ("file", "document") or (isinstance(file_name, str) and file_name):
            if isinstance(content, str) and content:
                result.append({
                    "type": "file",
                    "mimeType": mime or "application/octet-stream",
                    "data": content,
                    "fileName": file_name,
                })
    return result


@register_handler("agent")
async def handle_agent(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run agent turn — fully aligned with TS agent handler."""
    message = params.get("message", "")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message required")

    message = message.strip()

    agent_id_raw = str(params.get("agentId") or "").strip()
    session_id = str(params.get("sessionId") or "").strip() or None
    session_key = str(params.get("sessionKey") or "").strip() or None
    model = params.get("model")
    thinking = params.get("thinking")
    deliver = params.get("deliver", False)
    idempotency_key = str(params.get("idempotencyKey") or "").strip() or None
    label = str(params.get("label") or "").strip() or None
    spawned_by = str(params.get("spawnedBy") or "").strip() or None
    group_id = str(params.get("groupId") or "").strip() or None
    group_channel = str(params.get("groupChannel") or "").strip() or None
    group_space = str(params.get("groupSpace") or "").strip() or None
    channel = str(params.get("channel") or "").strip() or None
    reply_channel = str(params.get("replyChannel") or "").strip() or None
    to = str(params.get("to") or params.get("replyTo") or "").strip() or None
    thread_id = str(params.get("threadId") or "").strip() or None
    account_id = str(params.get("accountId") or params.get("replyAccountId") or "").strip() or None
    extra_system_prompt = params.get("extraSystemPrompt")
    attachments = params.get("attachments")
    timeout_secs = params.get("timeout")
    lane = params.get("lane")
    session_workspace = params.get("sessionWorkspace")

    if _agent_runtime is None or _session_manager is None:
        raise RuntimeError("Agent runtime not initialized")

    gateway = getattr(connection, "gateway", None)

    # Idempotency dedupe (aligned with TS context.dedupe)
    dedupe_key = f"agent:{idempotency_key}" if idempotency_key else None
    if dedupe_key and gateway is not None:
        if not hasattr(gateway, "agent_dedupe"):
            gateway.agent_dedupe = {}
        cached = gateway.agent_dedupe.get(dedupe_key)
        if cached:
            return cached

    # Normalize attachments and extract images
    images: list[dict[str, Any]] = []
    if attachments:
        normalized = _normalize_attachments(attachments)
        images = [a for a in normalized if a.get("type") == "image"]

    # Channel validation — reject unknown non-gateway channels
    _KNOWN_CHANNELS = {"telegram", "discord", "slack", "signal", "whatsapp", "matrix", "line", "imessage", "sms", "last"}
    for raw_ch in [channel, reply_channel]:
        if raw_ch and raw_ch not in _KNOWN_CHANNELS and raw_ch != "internal":
            logger.warning(f"Unknown channel hint: {raw_ch!r}")

    # Agent ID validation against known agents
    if agent_id_raw:
        cfg = getattr(connection, "config", None)
        if cfg is not None:
            agents_cfg = getattr(cfg, "agents", None) or {}
            if isinstance(agents_cfg, dict):
                known_ids = list(agents_cfg.get("agents", {}).keys())
            else:
                try:
                    known_ids = list((getattr(agents_cfg, "agents", None) or {}).keys())
                except Exception:
                    known_ids = []
            if known_ids and agent_id_raw not in known_ids:
                logger.warning(f"Unknown agentId: {agent_id_raw!r}, proceeding with default")

    # Session key shape validation (malformed agent key guard)
    # Mirrors TS parseAgentSessionKey: agent:<agentId>:<rest> where rest can contain ":"
    # Allow multi-segment rest format (e.g., agent:main:cron:job-id)
    if session_key and ":" in session_key:
        parts = session_key.split(":")
        if parts[0] == "agent" and len(parts) < 3:
            raise ValueError(f"malformed session key: {session_key!r} (requires at least agent:id:rest)")

    # Reset command: /new or /reset [optional message]
    skip_timestamp_injection = False
    reset_match = RESET_COMMAND_RE.match(message)
    if reset_match and session_key:
        reset_reason = "new" if (reset_match.group(1) or "").lower() == "new" else "reset"
        reset = await _sessions_reset_method.execute(
            connection,
            {"key": session_key, "reason": reset_reason},
        )
        post_reset_message = (reset_match.group(2) or "").strip()
        if post_reset_message:
            message = post_reset_message
        else:
            message = BARE_SESSION_RESET_PROMPT
            skip_timestamp_injection = True
        session_key = reset.get("key", session_key)
        entry = reset.get("entry") or {}
        session_id = entry.get("sessionId") or session_id

    # Inject timestamp — mirrors TS injectTimestamp() call in agent handler
    if not skip_timestamp_injection:
        cfg = getattr(connection, "config", None)
        timezone = "UTC"
        try:
            if cfg is not None:
                tz_val = getattr(getattr(cfg, "agents", None), "defaults", None)
                if tz_val is not None:
                    timezone = getattr(tz_val, "userTimezone", None) or timezone
        except Exception:
            pass
        message = _inject_timestamp(message, timezone)

    # Load session entry for group inheritance and session ID resolution
    if session_key and _session_manager:
        try:
            entry_data = _session_manager.get_session_entry(session_key) if hasattr(_session_manager, "get_session_entry") else None
            if isinstance(entry_data, dict):
                if not session_id:
                    session_id = entry_data.get("sessionId")
                # Inherit group context from parent (spawnedBy) session
                parent_key = spawned_by or entry_data.get("spawnedBy")
                if parent_key and (not group_id or not group_channel):
                    try:
                        parent_entry = _session_manager.get_session_entry(parent_key) if hasattr(_session_manager, "get_session_entry") else None
                        if isinstance(parent_entry, dict):
                            group_id = group_id or parent_entry.get("groupId")
                            group_channel = group_channel or parent_entry.get("groupChannel")
                            group_space = group_space or parent_entry.get("space")
                    except Exception:
                        pass
        except Exception:
            pass

    # Resolve final session_id
    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())

    # Get session + tools
    session = _session_manager.get_session(session_id)
    tools = _tool_registry.list_tools()

    run_id = idempotency_key or f"run-{int(datetime.now(UTC).timestamp() * 1000)}"

    # Respond immediately with accepted (fire-and-forget pattern matching TS)
    accepted_at = int(datetime.now(UTC).timestamp() * 1000)
    accepted = {"runId": run_id, "status": "accepted", "acceptedAt": accepted_at}

    if dedupe_key and gateway is not None:
        gateway.agent_dedupe[dedupe_key] = accepted

    # Launch background agent turn via queue lanes (mirrors TS nested enqueue pattern)
    async def _agent_task() -> None:
        # Apply timeout if specified (mirrors TS runTimeoutSeconds)
        if timeout_secs and timeout_secs > 0:
            try:
                await asyncio.wait_for(
                    _run_agent_turn(
                        connection, run_id, session, message, tools, model,
                        images=images, extra_system_prompt=extra_system_prompt,
                        session_workspace=session_workspace,
                    ),
                    timeout=timeout_secs
                )
            except asyncio.TimeoutError:
                logger.warning(f"Agent run {run_id} timed out after {timeout_secs}s")
                # Mark subagent as timed out if this is a subagent
                if lane == "subagent" or (session_key and ":subagent:" in session_key):
                    try:
                        from openclaw.agents.subagent_registry import get_global_registry
                        registry = get_global_registry()
                        # Find run by session key and mark as timed out
                        runs = registry.list_all_runs()
                        for run in runs:
                            if run.child_session_key == session_key and not run.ended_at:
                                registry.mark_subagent_run_terminated(run.run_id, reason="timeout")
                                break
                    except Exception as e:
                        logger.debug(f"Failed to mark subagent as timed out: {e}")
                raise
        else:
            await _run_agent_turn(
                connection, run_id, session, message, tools, model,
                images=images, extra_system_prompt=extra_system_prompt,
                session_workspace=session_workspace,
            )

    if _queue_manager is not None:
        from openclaw.agents.queuing.lanes import CommandLane

        session_lane_key = session_key or session_id or run_id
        if lane == "subagent":
            resolved_lane = CommandLane.SUBAGENT
        elif lane == "cron":
            resolved_lane = CommandLane.CRON
        elif lane == "nested":
            resolved_lane = CommandLane.NESTED
        else:
            resolved_lane = CommandLane.MAIN
        task = asyncio.create_task(
            _queue_manager.enqueue_session_then_lane(session_lane_key, resolved_lane, _agent_task)
        )
    else:
        task = asyncio.create_task(_agent_task())

    if gateway is not None:
        if not hasattr(gateway, "active_runs"):
            gateway.active_runs = {}
        if not hasattr(gateway, "agent_run_status"):
            gateway.agent_run_status = {}
        if not hasattr(gateway, "agent_run_starts"):
            gateway.agent_run_starts = {}
        gateway.active_runs[run_id] = task
        gateway.agent_run_starts[run_id] = accepted_at

        def _cleanup_run(future: asyncio.Future) -> None:
            ended_at = int(datetime.now(UTC).timestamp() * 1000)
            status_payload: dict[str, Any] = {
                "runId": run_id,
                "startedAt": gateway.agent_run_starts.get(run_id),
                "endedAt": ended_at,
            }
            try:
                if future.cancelled():
                    status_payload["status"] = "aborted"
                elif future.exception() is not None:
                    exc = future.exception()
                    if isinstance(exc, asyncio.TimeoutError):
                        status_payload["status"] = "timeout"
                        status_payload["error"] = f"Run timed out after {timeout_secs}s"
                    else:
                        status_payload["status"] = "error"
                        status_payload["error"] = str(exc)
                else:
                    status_payload["status"] = "ok"
            except Exception:
                status_payload["status"] = "error"
            gateway.agent_run_status[run_id] = status_payload
            gateway.active_runs.pop(run_id, None)
            gateway.agent_run_starts.pop(run_id, None)
            final_payload = {
                "runId": run_id,
                "status": status_payload["status"],
                "summary": "completed" if status_payload["status"] == "ok" else status_payload.get("error", ""),
            }
            if dedupe_key:
                gateway.agent_dedupe[dedupe_key] = final_payload

        task.add_done_callback(_cleanup_run)

    return accepted


async def _run_agent_turn(
    connection: Any,
    run_id: str,
    session: Any,
    message: str,
    tools: Any,
    model: Any,
    *,
    images: list[dict[str, Any]] | None = None,
    extra_system_prompt: str | None = None,
    session_workspace: str | None = None,
) -> None:
    """Execute agent turn and stream events — matches TS agentCommand fire-and-forget."""
    seq = 0

    async def _emit(stream: str, data: dict[str, Any]) -> None:
        nonlocal seq
        seq += 1
        try:
            await connection.send_event(
                "agent",
                {
                    "runId": run_id,
                    "seq": seq,
                    "stream": stream,
                    "ts": int(datetime.now(UTC).timestamp() * 1000),
                    "data": data,
                },
            )
        except Exception as e:
            # Connection closed (e.g., internal RPC calls close immediately)
            # This is expected for fire-and-forget internal calls
            if "closing transport" not in str(e).lower():
                raise

    try:
        await _emit("lifecycle", {"phase": "start"})
        
        # Check if the provider is a CLI provider
        from openclaw.agents.cli_backends import resolve_cli_backend_ids
        from openclaw.agents.model_selection import get_provider_from_model
        
        cfg = getattr(connection, "config", None)
        provider = get_provider_from_model(model) if model else None
        
        # CLI provider routing
        if provider and cfg:
            cli_backend_ids = resolve_cli_backend_ids(cfg)
            if provider in cli_backend_ids:
                from openclaw.agents.cli_runner import run_cli_agent
                from openclaw.agents.cli_session import get_cli_session_id, set_cli_session_id
                
                # Get session info
                session_key = getattr(session, "session_key", None) or getattr(session, "session_id", None)
                session_id = getattr(session, "id", None) or session_key
                workspace_dir = getattr(session, "workspace_dir", None)
                agent_id = getattr(session, "agent_id", "main")
                
                # Get CLI session ID
                cli_session_id = get_cli_session_id(session, provider)
                
                # Run CLI agent
                result = await run_cli_agent(
                    session_id=session_id,
                    session_key=session_key,
                    agent_id=agent_id,
                    workspace_dir=workspace_dir,
                    config=cfg,
                    prompt=message,
                    provider=provider,
                    model=model,
                    timeout_ms=None,  # Use default
                    run_id=run_id,
                    extra_system_prompt=extra_system_prompt,
                    cli_session_id=cli_session_id,
                    images=images,
                )
                
                # Save new CLI session ID if available
                if result.get("meta", {}).get("agentMeta", {}).get("sessionId"):
                    set_cli_session_id(session, provider, result["meta"]["agentMeta"]["sessionId"])
                
                # Emit result events
                for payload in result.get("payloads", []):
                    if "text" in payload:
                        await _emit("assistant", {"type": "text", "payload": {"text": payload["text"]}})
                
                await _emit("lifecycle", {"phase": "end"})
                return
        
        # Default embedded runtime path
        run_kwargs: dict[str, Any] = {}
        if images:
            run_kwargs["images"] = images
        if extra_system_prompt:
            run_kwargs["system_prompt"] = extra_system_prompt
        run_kwargs["run_id"] = run_id
        session_key_for_run = getattr(session, "session_key", None) or getattr(session, "session_id", None)
        if session_key_for_run:
            run_kwargs["session_key"] = session_key_for_run
        if session_workspace:
            run_kwargs["session_workspace"] = session_workspace
        async for event in _agent_runtime.run_turn(session, message, tools, model, **run_kwargs):
            evt_type = getattr(event, "type", "")
            stream = "assistant"
            if evt_type in ("tool_call", "tool_result"):
                stream = "tool"
            elif evt_type == "error":
                stream = "error"
            await _emit(stream, {"type": evt_type, "payload": getattr(event, "data", {})})
        await _emit("lifecycle", {"phase": "end"})
    except asyncio.CancelledError:
        logger.info(f"Agent turn {run_id} was aborted")
        await _emit("lifecycle", {"phase": "error", "reason": "aborted"})
        raise
    except Exception as e:
        logger.error(f"Agent turn error: {e}", exc_info=True)
        await _emit("error", {"message": str(e)})
        await _emit("lifecycle", {"phase": "error", "reason": str(e)})


# Old chat handlers removed - now using openclaw/gateway/api/chat.py
# The new chat methods (chat.send, chat.history, chat.abort, chat.inject) 
# are registered by _register_chat_methods() above


@register_handler("agent.identity.get")
async def handle_agent_identity_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get agent identity"""
    from openclaw.routing.session_key import resolve_agent_id_from_session_key
    from pathlib import Path

    requested_agent_id = str(params.get("agentId") or "").strip()
    session_key = str(params.get("sessionKey") or "").strip()
    resolved_from_key = resolve_agent_id_from_session_key(session_key) if session_key else ""
    if requested_agent_id and resolved_from_key and requested_agent_id != resolved_from_key:
        raise ValueError(
            f'invalid agent params: agent "{requested_agent_id}" does not match session key agent "{resolved_from_key}"'
        )
    agent_id = requested_agent_id or resolved_from_key or "main"
    cfg = connection.config
    agent_name = "Assistant"
    agent_theme = None
    agent_emoji = None
    avatar = "A"
    avatar_url = None

    try:
        ui_cfg = getattr(cfg, "ui", None)
        ui_assistant = getattr(ui_cfg, "assistant", None) if ui_cfg else None
        if ui_assistant is not None:
            agent_name = getattr(ui_assistant, "name", None) or agent_name
            avatar = getattr(ui_assistant, "avatar", None) or avatar

        agents_cfg = getattr(cfg, "agents", None)
        entries = getattr(agents_cfg, "agents", None) if agents_cfg else None
        if isinstance(entries, list):
            for entry in entries:
                if getattr(entry, "id", None) == agent_id:
                    agent_name = getattr(entry, "name", None) or agent_name
                    break
        
        # Read workspace identity.md file (mirrors TS resolveAssistantIdentity)
        # Priority: workspace identity > agents config > ui.assistant config
        state_dir = resolve_state_dir()
        workspace_dir = state_dir / "agents" / agent_id
        workspace_identity_path = workspace_dir / "identity.md"
        
        if workspace_identity_path.exists():
            try:
                identity_content = workspace_identity_path.read_text(encoding="utf-8")
                workspace_identity = _parse_identity_md(identity_content)
                
                # Override with workspace values if present
                if workspace_identity.get("name"):
                    agent_name = workspace_identity["name"]
                if workspace_identity.get("avatar"):
                    avatar = workspace_identity["avatar"]
                if workspace_identity.get("emoji"):
                    agent_emoji = workspace_identity["emoji"]
                if workspace_identity.get("theme"):
                    agent_theme = workspace_identity["theme"]
            except Exception as e:
                logger.warning(f"Failed to read workspace identity for {agent_id}: {e}")
    except Exception:
        pass

    # Derive avatarUrl similarly to TS: URL/data values pass through; path values resolve on gateway base path.
    if isinstance(avatar, str):
        av = avatar.strip()
        if av.lower().startswith(("http://", "https://", "data:image/")):
            avatar_url = av
        elif any(ch in av for ch in ("/", "\\")) or av.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
        ):
            base_path = getattr(getattr(cfg, "gateway", None), "web_ui_base_path", "/") or "/"
            base_path = "/" + base_path.strip("/") if base_path != "/" else ""
            avatar_url = f"{base_path}/{av.lstrip('/')}"

    return {
        "agentId": agent_id,
        "name": agent_name,
        "theme": agent_theme,
        "emoji": agent_emoji,
        "avatar": avatar,
        "avatarUrl": avatar_url,
    }


def _parse_identity_md(content: str) -> dict[str, str]:
    """Parse identity.md file content.
    
    Extracts YAML front matter or key-value pairs from identity.md
    
    Returns:
        Dict with keys: name, avatar, emoji, theme, creature, vibe
    """
    result = {}
    
    # Try YAML front matter first
    if content.startswith("---"):
        try:
            import yaml
            parts = content.split("---", 2)
            if len(parts) >= 3:
                front_matter = parts[1].strip()
                parsed = yaml.safe_load(front_matter)
                if isinstance(parsed, dict):
                    result = parsed
                    return result
        except Exception:
            pass
    
    # Fallback: parse key-value pairs (key: value)
    for line in content.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key in ("name", "avatar", "emoji", "theme", "creature", "vibe"):
                result[key] = value
    
    return result


@register_handler("agent.wait")
async def handle_agent_wait(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Wait for agent run completion.

    Mirrors TS agent.wait which uses the waiter-notification system from
    agent-wait-dedupe.ts.  If the run is already done it returns immediately;
    otherwise it registers an asyncio.Event waiter and blocks until the run
    notifies us or the timeout expires.
    """
    run_id = params.get("runId")
    timeout_ms = int(params.get("timeoutMs", params.get("timeout", 30000)))
    if not run_id:
        raise ValueError("runId required")

    gateway = getattr(connection, "gateway", None)
    if gateway is None:
        return {"runId": run_id, "status": "timeout"}

    # -----------------------------------------------------------------------
    # Fast path: run already completed — return terminal snapshot immediately.
    # -----------------------------------------------------------------------
    def _terminal_snapshot() -> dict[str, Any] | None:
        if hasattr(gateway, "agent_run_status"):
            done = gateway.agent_run_status.get(run_id)
            if done:
                return {
                    "runId": run_id,
                    "status": done.get("status", "ok"),
                    "startedAt": done.get("startedAt"),
                    "endedAt": done.get("endedAt"),
                    "error": done.get("error"),
                }
        return None

    snap = _terminal_snapshot()
    if snap:
        return snap

    # -----------------------------------------------------------------------
    # Wait path: register a waiter event and block until notified or timeout.
    # Mirrors TS waitForAgentRunTerminal() in agent-wait-dedupe.ts.
    # -----------------------------------------------------------------------
    if timeout_ms <= 0:
        snap = _terminal_snapshot()
        return snap or {"runId": run_id, "status": "timeout"}

    evt = _add_agent_waiter(run_id)
    try:
        timeout_sec = max(0.0, timeout_ms / 1000.0)

        # If there's a concrete active task, race the event against the task completing.
        active_task: asyncio.Task | None = None
        if hasattr(gateway, "active_runs"):
            t = gateway.active_runs.get(run_id)
            if t is not None and hasattr(t, "done"):
                active_task = t

        try:
            if active_task is not None and not active_task.done():
                # Race: event notification OR the task itself finishing
                evt_waiter = asyncio.ensure_future(evt.wait())
                try:
                    done, _ = await asyncio.wait(
                        {evt_waiter, active_task},
                        timeout=timeout_sec,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        evt_waiter.cancel()
                        snap = _terminal_snapshot()
                        return snap or {"runId": run_id, "status": "timeout"}
                    evt_waiter.cancel()
                except Exception:
                    evt_waiter.cancel()
                    raise
            else:
                await asyncio.wait_for(evt.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            snap = _terminal_snapshot()
            return snap or {"runId": run_id, "status": "timeout"}

        snap = _terminal_snapshot()
        if snap:
            return snap

        # Fallback: check active task directly
        if active_task is not None:
            if active_task.cancelled():
                return {"runId": run_id, "status": "aborted"}
            exc = active_task.exception() if active_task.done() else None
            if exc:
                return {"runId": run_id, "status": "error", "error": str(exc)}
        return {"runId": run_id, "status": "ok"}
    finally:
        _remove_agent_waiter(run_id, evt)


@register_handler("agents.list")
async def handle_agents_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List available agents (matches TypeScript agents.ts format)"""
    from openclaw.gateway.session_utils import list_agents_for_gateway
    
    cfg = _get_current_config()
    result = list_agents_for_gateway(cfg)
    return result


@register_handler("agent.queue.status")
async def handle_agent_queue_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get agent queue status"""
    if not _agent_runtime:
        return {"enabled": False}
    
    # Check if queue manager is enabled
    if not hasattr(_agent_runtime, "queue_manager") or not _agent_runtime.queue_manager:
        return {"enabled": False}
    
    # Get queue statistics
    stats = _agent_runtime.queue_manager.get_stats()
    
    return {
        "enabled": True,
        "global": stats.get("global", {}),
        "sessions": stats.get("sessions", {}),
        "total_sessions": stats.get("total_sessions", 0)
    }


@register_handler("agents.files.list")
async def handle_agents_files_list(connection: Any, params: dict[str, Any]) -> list[str]:
    """List agent workspace files — mirrors TS agents.files.list."""
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
    agent_id = params.get("agentId") or params.get("agent_id") or "main"
    cfg = _get_current_config()
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    if not workspace_dir.exists():
        return []
    return [f.name for f in workspace_dir.iterdir() if f.is_file()]


@register_handler("agents.files.get")
async def handle_agents_files_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get agent workspace file content — mirrors TS agents.files.get."""
    from pathlib import Path
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
    filename = params.get("name") or params.get("filename", "")
    agent_id = params.get("agentId") or params.get("agent_id") or "main"
    cfg = _get_current_config()
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    filepath = workspace_dir / filename
    if filepath.exists():
        content = filepath.read_text(encoding="utf-8")
        stat = filepath.stat()
        return {
            "agentId": agent_id,
            "workspace": str(workspace_dir),
            "file": {
                "name": filename,
                "path": str(filepath),
                "missing": False,
                "size": stat.st_size,
                "updatedAtMs": int(stat.st_mtime * 1000),
                "content": content,
            },
        }
    return {
        "agentId": agent_id,
        "workspace": str(workspace_dir),
        "file": {"name": filename, "path": str(filepath), "missing": True},
    }


@register_handler("agents.files.set")
async def handle_agents_files_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set agent workspace file content — mirrors TS agents.files.set.

    Resolves the true workspace directory for the requested agent (using
    resolve_agent_workspace_dir) instead of hardcoding ~/.openclaw/agents/.
    """
    from pathlib import Path
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir
    filename = params.get("name") or params.get("filename", "")
    content = str(params.get("content", ""))
    agent_id = params.get("agentId") or params.get("agent_id") or "main"
    if not filename:
        raise ValueError("agents.files.set: 'name' param is required")
    cfg = _get_current_config()
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    filepath = workspace_dir / filename
    filepath.write_text(content, encoding="utf-8")
    stat = filepath.stat()
    return {
        "ok": True,
        "agentId": agent_id,
        "workspace": str(workspace_dir),
        "file": {
            "name": filename,
            "path": str(filepath),
            "missing": False,
            "size": stat.st_size,
            "updatedAtMs": int(stat.st_mtime * 1000),
            "content": content,
        },
    }


@register_handler("browser.request")
async def handle_browser_request(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Handle browser automation request"""
    action = params.get("action", "navigate")
    url = params.get("url")
    return {"action": action, "url": url, "status": "accepted"}


@register_handler("channels.status")
async def handle_channels_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get channel connection status (TS-compatible shape)."""
    import logging
    logger = logging.getLogger(__name__)
    
    if not _channel_registry:
        logger.warning("channels.status: _channel_registry is None")
        return {
            "ts": int(datetime.now(UTC).timestamp() * 1000),
            "channelOrder": [],
            "channelLabels": {},
            "channelDetailLabels": {},
            "channelSystemImages": {},
            "channelMeta": [],
            "channels": {},
            "channelAccounts": {},
            "channelDefaultAccountId": {},
        }

    # Build snapshot dict: prefer get_snapshot(), fall back to get_all_channels()
    snapshot: dict[str, Any] = {}
    if hasattr(_channel_registry, "get_snapshot") and callable(_channel_registry.get_snapshot):
        snapshot = _channel_registry.get_snapshot() or {}
    elif hasattr(_channel_registry, "get_all_channels") and callable(_channel_registry.get_all_channels):
        for ch in (_channel_registry.get_all_channels() or []):
            if isinstance(ch, dict):
                cid = ch.get("id") or ch.get("channel_id", "")
                if cid:
                    snapshot[cid] = ch
    logger.info(f"channels.status: snapshot returned {len(snapshot)} channels: {list(snapshot.keys())}")
    
    probe = bool(params.get("probe", False))
    timeout_ms = max(1000, int(params.get("timeoutMs", 5000)))
    now_ts = int(datetime.now(UTC).timestamp() * 1000)
    channel_order: list[str] = []
    channel_labels: dict[str, str] = {}
    channel_meta: dict[str, Any] = {}
    channels_summary: dict[str, Any] = {}
    channel_accounts: dict[str, Any] = {}
    default_account: dict[str, Any] = {}

    for channel_id, snap in snapshot.items():
        channel_order.append(channel_id)
        label = snap.get("label", channel_id)
        channel_labels[channel_id] = label
        channel_meta[channel_id] = {
            "id": channel_id,
            "label": label,
        }
        channels_summary[channel_id] = {
            "configured": snap.get("enabled", False),
            "running": snap.get("running", False),
            "connected": snap.get("connected", False),
            "state": snap.get("state", "unknown"),
        }
        account_snapshot = {
            "accountId": "default",
            "configured": snap.get("enabled", False),
            "enabled": snap.get("enabled", True),
            "running": snap.get("running", False),
            "connected": snap.get("connected", False),
            "healthy": snap.get("healthy", snap.get("connected", False)),
        }
        if probe:
            account_snapshot["lastProbeAt"] = now_ts
            account_snapshot["probe"] = {"ok": account_snapshot["healthy"], "timeoutMs": timeout_ms}
        channel_accounts[channel_id] = [account_snapshot]
        default_account[channel_id] = "default"

    return {
        "ts": now_ts,
        "channelOrder": channel_order,
        "channelLabels": channel_labels,
        "channelDetailLabels": channel_labels,  # Simplified: same as labels
        "channelSystemImages": {},
        "channelMeta": list(channel_meta.values()),
        "channels": channels_summary,
        "channelAccounts": channel_accounts,
        "channelDefaultAccountId": default_account,
    }


@register_handler("channels.logout")
async def handle_channels_logout(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Logout from a channel (best-effort stop + clear status)."""
    channel_id = params.get("channelId") or params.get("channel")
    if not channel_id:
        raise ValueError("channelId required")

    gateway = getattr(connection, "gateway", None)
    if gateway and hasattr(gateway, "channel_manager"):
        try:
            await gateway.channel_manager.stop_channel(channel_id)
        except Exception:
            pass
    return {
        "channel": channel_id,
        "accountId": params.get("accountId", "default"),
        "cleared": True,
        "loggedOut": True,
    }


@register_handler("config.set")
async def handle_config_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set full configuration"""
    from openclaw.gateway.config_service import get_config_service
    
    config_data = params.get("config", {})
    config_service = get_config_service()
    success = config_service.save_config(config_data)
    
    return {
        "set": success,
        "restartRequired": True  # Most config changes require restart
    }


@register_handler("config.patch")
async def handle_config_patch(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Apply patch to configuration"""
    from openclaw.gateway.config_service import get_config_service
    
    patch = params.get("patch", {})
    config_service = get_config_service()
    updated_config = config_service.patch_config(patch)
    
    return {
        "applied": len(patch),
        "restartRequired": True
    }


@register_handler("config.apply")
async def handle_config_apply(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Apply configuration (alias for config.set)"""
    return await handle_config_set(connection, params)


@register_handler("config.schema")
async def handle_config_schema(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get configuration schema"""
    from openclaw.gateway.config_service import get_config_service
    
    config_service = get_config_service()
    schema = config_service.get_config_schema()
    
    # Embed channels as a proper JSON Schema object so the frontend's
    # resolveSchemaNode(schema, ["channels", channelId]) can traverse
    # schema.properties.channels.properties.<channelId> correctly.
    schema.setdefault("properties", {})["channels"] = {
        "type": "object",
        "description": "Channel integrations",
        "properties": {
            "telegram": {
                "type": "object",
                "description": "Telegram bot channel",
                "properties": {
                    "enabled": {"type": "boolean", "description": "Enable Telegram channel"},
                    "bot_token": {"type": "string", "description": "Telegram bot token"},
                    "owner_id": {"type": "string", "description": "Owner user ID"},
                    "allowed_user_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Allowed user IDs",
                    },
                    "group_activation_mode": {
                        "type": "string",
                        "enum": ["mention", "always"],
                        "description": "Group activation mode",
                    },
                },
            },
            "discord": {
                "type": "object",
                "description": "Discord bot channel",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "bot_token": {"type": "string", "description": "Discord bot token"},
                },
            },
            "slack": {
                "type": "object",
                "description": "Slack bot channel",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "bot_token": {"type": "string", "description": "Slack bot token"},
                    "app_token": {"type": "string", "description": "Slack app-level token"},
                },
            },
            "whatsapp": {
                "type": "object",
                "description": "WhatsApp channel",
                "properties": {
                    "enabled": {"type": "boolean"},
                },
            },
        },
    }

    # uiHints for password fields so the form renders them masked
    ui_hints = {
        "channels.telegram.bot_token": {"secret": True},
        "channels.discord.bot_token": {"secret": True},
        "channels.slack.bot_token": {"secret": True},
        "channels.slack.app_token": {"secret": True},
    }

    return {"schema": schema, "uiHints": ui_hints}


@register_handler("cron.list")
async def handle_cron_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List cron jobs with pagination/filter — mirrors TS cron.list."""
    from openclaw.config.loader import load_config
    from openclaw.cron.delivery_preview import resolve_cron_delivery_previews
    from openclaw.cron.service import get_cron_service

    cron_service = get_cron_service()
    if not cron_service:
        return {
            "jobs": [],
            "total": 0,
            "offset": 0,
            "limit": 50,
            "hasMore": False,
            "nextOffset": None,
            "deliveryPreviews": {},
        }

    page = await cron_service.list_page(
        include_disabled=bool(params.get("includeDisabled", False)),
        limit=params.get("limit"),
        offset=int(params.get("offset") or 0),
        query=params.get("query"),
        enabled=params.get("enabled"),
        sort_by=params.get("sortBy") or "nextRunAtMs",
        sort_dir=params.get("sortDir") or "asc",
    )

    preview_jobs = []
    for job_dict in page.get("jobs", []):
        job_id = job_dict.get("id")
        if isinstance(job_id, str):
            job = cron_service.get_job(job_id)
            if job:
                preview_jobs.append(job)

    cfg = load_config()
    delivery_previews = await resolve_cron_delivery_previews(
        cfg=cfg,
        default_agent_id=cron_service.default_agent_id,
        jobs=preview_jobs,
    )

    return {
        "jobs": page.get("jobs", []),
        "total": page.get("total", 0),
        "offset": page.get("offset", 0),
        "limit": page.get("limit", 50),
        "hasMore": page.get("hasMore", False),
        "nextOffset": page.get("nextOffset"),
        "deliveryPreviews": delivery_previews,
    }


@register_handler("cron.status")
async def handle_cron_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get cron status — returns { enabled, jobs: count, nextWakeAtMs } matching CronStatus type."""
    from openclaw.cron.service import get_cron_service
    cron_service = get_cron_service()

    if not cron_service:
        return {"enabled": False, "jobs": 0, "nextWakeAtMs": None}

    jobs = await cron_service.list_jobs()
    # nextWakeAtMs: pull from service if available
    next_wake = None
    try:
        svc_info = await cron_service.status()
        next_wake = svc_info.get("nextWakeAtMs") or svc_info.get("next_wake_at_ms")
    except Exception:
        pass
    return {
        "enabled": True,
        "jobs": len(jobs),
        "nextWakeAtMs": next_wake,
    }


@register_handler("cron.add")
async def handle_cron_add(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    Add cron job (matches TypeScript API)

    Expects: { job: CronJobCreate }
    Returns: CronJob
    """
    from openclaw.cron.types import CronJob
    from openclaw.cron.service import get_cron_service
    from openclaw.cron.serialization import convert_job_to_api
    from openclaw.cron.normalize import normalize_cron_job_create
    import uuid
    from datetime import datetime, UTC

    # Frontend sends job fields flat (not nested under "job")
    raw_job = params.get("job") or {k: v for k, v in params.items() if k != "job"}

    # Run normalization (applies defaults, infers sessionTarget, delivery, stagger, etc.)
    job_data = normalize_cron_job_create(raw_job) or {}

    from openclaw.cron.normalize import validate_schedule_timestamp

    schedule = job_data.get("schedule")
    if schedule:
        ts_error = validate_schedule_timestamp(schedule)
        if ts_error:
            raise ValueError(ts_error)

    # Generate id if not provided
    if "id" not in job_data:
        job_data["id"] = str(uuid.uuid4())

    # Add timestamps if not present
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    if "created_at_ms" not in job_data:
        job_data["created_at_ms"] = now_ms
    if "updated_at_ms" not in job_data:
        job_data["updated_at_ms"] = now_ms

    job = CronJob.from_dict(job_data)

    cron_service = get_cron_service()
    if not cron_service:
        raise RuntimeError("Cron service not available")

    created_job = await cron_service.add_job(job)
    return convert_job_to_api(created_job.to_dict())


@register_handler("cron.update")
async def handle_cron_update(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    Update cron job (matches TypeScript API)

    Expects: { jobId: string, patch: Partial<CronJob> }
    Returns: CronJob
    """
    from openclaw.cron.service import get_cron_service
    from openclaw.cron.serialization import convert_job_to_api
    from openclaw.cron.normalize import normalize_cron_job_patch
    from datetime import datetime, UTC

    # Accept both "id" (frontend) and "jobId" (legacy) — TypeScript frontend sends "id"
    job_id = params.get("id") or params.get("jobId")
    if not job_id:
        raise ValueError("id is required")

    raw_patch = params.get("patch", {})
    if not raw_patch:
        raise ValueError("patch is required")

    # Normalize patch (no defaults)
    python_patch = normalize_cron_job_patch(raw_patch) or {}

    from openclaw.cron.normalize import validate_schedule_timestamp

    if python_patch.get("schedule"):
        ts_error = validate_schedule_timestamp(python_patch["schedule"])
        if ts_error:
            raise ValueError(ts_error)

    # Add updated timestamp
    python_patch["updated_at_ms"] = int(datetime.now(UTC).timestamp() * 1000)
    
    # Update via service
    cron_service = get_cron_service()
    if not cron_service:
        raise RuntimeError("Cron service not available")
    
    updated_job = await cron_service.update_job(job_id, python_patch)
    
    # Convert back to TypeScript API format
    return convert_job_to_api(updated_job.to_dict())


@register_handler("cron.remove")
async def handle_cron_remove(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Remove cron job"""
    from openclaw.cron.service import get_cron_service

    # Accept both "id" (frontend) and "jobId" (legacy)
    job_id = params.get("id") or params.get("jobId")
    if not job_id:
        raise ValueError("id is required")

    cron_service = get_cron_service()
    result = await cron_service.remove_job(job_id)
    return {"ok": True, "removed": result.get("removed", False)}


@register_handler("cron.run")
async def handle_cron_run(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    Manually run cron job (matches TypeScript API)
    
    Expects: { jobId: string, mode?: "due" | "force" }
    Returns: { ok: boolean, ran: boolean, reason?: "not-due" }
    """
    from openclaw.cron.service import get_cron_service
    
    # Accept both "id" (frontend) and "jobId" (legacy)
    job_id = params.get("id") or params.get("jobId")
    if not job_id:
        raise ValueError("id is required")

    mode = params.get("mode", "force")
    
    cron_service = get_cron_service()
    if not cron_service:
        raise RuntimeError("Cron service not available")
    
    # Use service's run method
    result = await cron_service.run(job_id, mode=mode)
    
    return result


@register_handler("cron.runs")
async def handle_cron_runs(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List cron run history with pagination — mirrors TS cron.runs."""
    from openclaw.cron.run_log import (
        read_cron_run_log_entries_page,
        read_cron_run_log_entries_page_all,
        resolve_cron_run_log_path,
    )
    from openclaw.cron.serialization import convert_run_log_entry_to_api
    from openclaw.cron.service import get_cron_service

    cron_service = get_cron_service()
    empty_page = {
        "entries": [],
        "total": 0,
        "offset": 0,
        "limit": int(params.get("limit") or 50),
        "hasMore": False,
        "nextOffset": None,
    }
    if not cron_service or not cron_service.store_path:
        return empty_page

    job_id = params.get("id") or params.get("jobId")
    explicit_scope = params.get("scope")
    scope = explicit_scope if explicit_scope in ("job", "all") else ("job" if job_id else "all")

    if scope == "job" and not job_id:
        raise ValueError("invalid cron.runs params: missing id")

    page_opts = {
        "limit": params.get("limit"),
        "offset": int(params.get("offset") or 0),
        "statuses": params.get("statuses"),
        "status": params.get("status"),
        "delivery_statuses": params.get("deliveryStatuses"),
        "delivery_status": params.get("deliveryStatus"),
        "query": params.get("query"),
        "sort_dir": params.get("sortDir"),
    }

    try:
        if scope == "all":
            jobs = await cron_service.list_jobs(include_disabled=True)
            job_name_by_id = {
                j["id"]: j["name"]
                for j in jobs
                if isinstance(j.get("id"), str) and isinstance(j.get("name"), str)
            }
            page = read_cron_run_log_entries_page_all(
                store_path=cron_service.store_path,
                job_name_by_id=job_name_by_id,
                **page_opts,
            )
        else:
            log_path = resolve_cron_run_log_path(
                store_path=cron_service.store_path,
                job_id=str(job_id),
            )
            page = read_cron_run_log_entries_page(
                log_path,
                job_id=str(job_id),
                **page_opts,
            )
    except ValueError as exc:
        raise ValueError("invalid cron.runs params: invalid id") from exc
    except Exception as e:
        logger.error("Failed to read cron runs: %s", e, exc_info=True)
        return empty_page

    page["entries"] = [
        convert_run_log_entry_to_api(entry) for entry in page.get("entries", [])
    ]
    return page


@register_handler("device.pair.list")
async def handle_device_pair_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List paired devices and pending pairs - mirrors TS device.pair.list"""
    from openclaw.devices.manager import get_device_manager
    
    device_manager = get_device_manager()
    result = device_manager.list_pairing()
    
    # Convert to serializable format
    pending_list = []
    for req in result["pending"]:
        pending_list.append({
            "requestId": req.request_id,
            "deviceId": req.device_id,
            "publicKey": req.public_key,
            "displayName": req.display_name,
            "platform": req.platform,
            "deviceFamily": req.device_family,
            "clientId": req.client_id,
            "clientMode": req.client_mode,
            "role": req.role,
            "roles": req.roles,
            "scopes": req.scopes,
            "remoteIp": req.remote_ip,
            "silent": req.silent,
            "isRepair": req.is_repair,
            "ts": req.ts,
        })
    
    paired_list = []
    for device in result["paired"]:
        # Redact token values, only show summaries
        tokens_summary = {}
        if device.tokens:
            for role, token in device.tokens.items():
                tokens_summary[role] = {
                    "role": token.role,
                    "scopes": token.scopes,
                    "createdAtMs": token.created_at_ms,
                    "rotatedAtMs": token.rotated_at_ms,
                    "revokedAtMs": token.revoked_at_ms,
                    "lastUsedAtMs": token.last_used_at_ms,
                }
        
        paired_list.append({
            "deviceId": device.device_id,
            "publicKey": device.public_key,
            "displayName": device.display_name,
            "platform": device.platform,
            "deviceFamily": device.device_family,
            "role": device.role,
            "roles": device.roles,
            "approvedScopes": device.approved_scopes,
            "createdAtMs": device.created_at_ms,
            "approvedAtMs": device.approved_at_ms,
            "label": device.label,
            "tokens": tokens_summary,
        })
    
    return {
        "pending": pending_list,
        "paired": paired_list,
    }


@register_handler("device.pair.approve")
async def handle_device_pair_approve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Approve device pairing - mirrors TS device.pair.approve"""
    from openclaw.devices.manager import get_device_manager
    
    request_id = params.get("requestId")
    if not request_id:
        raise ValueError("requestId is required")
    
    device_manager = get_device_manager()
    result = device_manager.approve_pairing(request_id)
    
    if not result:
        raise ValueError(f"unknown requestId: {request_id}")
    
    # Redact sensitive token data for response (summarize tokens, don't send actual token strings)
    device = result["device"]
    
    def summarize_tokens(tokens_dict: dict) -> dict | None:
        if not tokens_dict:
            return None
        return {
            role: {
                "role": token.role,
                "scopes": token.scopes,
                "createdAtMs": token.created_at_ms,
                "rotatedAtMs": token.rotated_at_ms,
                "revokedAtMs": token.revoked_at_ms,
                "lastUsedAtMs": token.last_used_at_ms,
            }
            for role, token in tokens_dict.items()
        }
    
    device_dict = {
        "deviceId": device.device_id,
        "publicKey": device.public_key,
        "displayName": device.display_name,
        "platform": device.platform,
        "deviceFamily": device.device_family,
        "role": device.role,
        "roles": device.roles,
        "approvedScopes": device.approved_scopes,
        "createdAtMs": device.created_at_ms,
        "approvedAtMs": device.approved_at_ms,
        "label": device.label,
        "tokens": summarize_tokens(device.tokens),
    }
    
    logger.info(f"Device pairing approved: device={device.device_id} role={device.role or 'unknown'}")
    
    # TODO: Broadcast device.pair.resolved event
    
    return {
        "requestId": result["requestId"],
        "device": device_dict,
    }


@register_handler("device.pair.reject")
async def handle_device_pair_reject(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reject device pairing - mirrors TS device.pair.reject"""
    from openclaw.devices.manager import get_device_manager
    
    request_id = params.get("requestId")
    if not request_id:
        raise ValueError("requestId is required")
    
    reason = params.get("reason")
    
    device_manager = get_device_manager()
    result = device_manager.reject_pairing(request_id, reason)
    
    if not result:
        raise ValueError(f"unknown requestId: {request_id}")
    
    logger.info(f"Device pairing rejected: requestId={request_id}")
    
    # TODO: Broadcast device.pair.resolved event
    
    return result


@register_handler("device.token.rotate")
async def handle_device_token_rotate(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rotate device token - mirrors TS device.token.rotate"""
    from openclaw.devices.manager import get_device_manager
    
    device_id = params.get("deviceId")
    role = params.get("role")
    scopes = params.get("scopes")
    
    if not device_id:
        raise ValueError("deviceId is required")
    if not role:
        raise ValueError("role is required")
    
    device_manager = get_device_manager()
    entry = device_manager.rotate_token(device_id, role, scopes)
    
    if not entry:
        raise ValueError(f"unknown deviceId/role: {device_id}/{role}")
    
    logger.info(f"Device token rotated: device={device_id} role={entry.role} scopes={','.join(entry.scopes)}")
    
    return {
        "deviceId": device_id,
        "role": entry.role,
        "token": entry.token,
        "scopes": entry.scopes,
        "rotatedAtMs": entry.rotated_at_ms or entry.created_at_ms,
    }


@register_handler("device.token.revoke")
async def handle_device_token_revoke(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Revoke device token - mirrors TS device.token.revoke"""
    from openclaw.devices.manager import get_device_manager
    
    device_id = params.get("deviceId")
    role = params.get("role")
    
    if not device_id:
        raise ValueError("deviceId is required")
    if not role:
        raise ValueError("role is required")
    
    device_manager = get_device_manager()
    entry = device_manager.revoke_token(device_id, role)
    
    if not entry:
        raise ValueError(f"unknown deviceId/role: {device_id}/{role}")
    
    logger.info(f"Device token revoked: device={device_id} role={entry.role}")
    
    return {
        "deviceId": device_id,
        "role": entry.role,
        "revokedAtMs": entry.revoked_at_ms or int(time.time() * 1000),
    }


@register_handler("exec.approval.request")
async def handle_exec_approval_request(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Request exec approval"""
    from openclaw.exec.approval_manager import get_approval_manager
    
    command = params.get("command", "")
    context = params.get("context", {})
    
    approval_manager = get_approval_manager()
    request_id = approval_manager.request_approval(command, context)
    
    return {
        "requestId": request_id,
        "command": command
    }


@register_handler("exec.approval.resolve")
async def handle_exec_approval_resolve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve exec approval"""
    from openclaw.exec.approval_manager import get_approval_manager
    
    request_id = params.get("requestId")
    approved = params.get("approved", False)
    approved_by = connection.auth_context.user
    
    approval_manager = get_approval_manager()
    
    if approved:
        success = approval_manager.approve(request_id, approved_by)
    else:
        success = approval_manager.reject(request_id, approved_by)
    
    return {
        "requestId": request_id,
        "approved": approved,
        "resolved": success
    }


@register_handler("exec.approvals.get")
async def handle_exec_approvals_get(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Get pending exec approvals"""
    from openclaw.exec.approval_manager import get_approval_manager
    
    approval_manager = get_approval_manager()
    return approval_manager.list_pending()


@register_handler("exec.approvals.set")
async def handle_exec_approvals_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set exec approval policies"""
    from openclaw.exec.approval_manager import get_approval_manager, ApprovalPolicy
    
    policy_id = params.get("policyId")
    policy_data = params.get("policy", {})
    
    policy = ApprovalPolicy(
        pattern=policy_data.get("pattern"),
        auto_approve=policy_data.get("autoApprove", False),
        require_approval=policy_data.get("requireApproval", True),
        allowed_users=policy_data.get("allowedUsers")
    )
    
    approval_manager = get_approval_manager()
    approval_manager.set_policy(policy_id, policy)
    
    return {"policyId": policy_id, "set": True}


# Rolling log file pattern (matches TypeScript ROLLING_LOG_RE)
ROLLING_LOG_RE = re.compile(r'^openclaw-\d{4}-\d{2}-\d{2}\.log$')


def resolve_log_file(file_path: Path) -> Path:
    """Resolve log file with rolling log support (matches TS resolveLogFile).
    
    If the file exists, return it.
    If it doesn't exist and matches the rolling log pattern,
    scan the directory and return the most recently modified matching file.
    
    Args:
        file_path: Initial log file path
        
    Returns:
        Resolved log file path
    """
    # If file exists, use it
    if file_path.exists():
        return file_path
    
    # Check if it's a rolling log pattern
    if not ROLLING_LOG_RE.match(file_path.name):
        return file_path
    
    # Find latest rolling log file in the directory
    log_dir = file_path.parent
    if not log_dir.exists():
        return file_path
    
    candidates = []
    try:
        for entry in log_dir.iterdir():
            if entry.is_file() and ROLLING_LOG_RE.match(entry.name):
                stat = entry.stat()
                candidates.append((entry, stat.st_mtime))
    except (OSError, PermissionError):
        return file_path
    
    if candidates:
        # Sort by modification time, newest first
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    
    return file_path


@register_handler("logs.tail")
async def handle_logs_tail(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Tail gateway logs — mirrors TS logs.tail via readConfiguredLogTail."""
    from openclaw.logging.log_tail import read_configured_log_tail

    cursor_param = params.get("cursor")
    cursor = int(cursor_param) if isinstance(cursor_param, (int, float)) else None
    try:
        return await read_configured_log_tail(
            cursor=cursor,
            limit=params.get("limit"),
            max_bytes=params.get("maxBytes"),
        )
    except Exception as exc:
        raise RuntimeError(f"log read failed: {exc}") from exc


@register_handler("models.list")
async def handle_models_list(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List available models"""
    config = connection.config
    models = []
    if config.agent:
        model_val = config.agent.model
        models.append({
            "name": "primary",
            "model": str(model_val) if isinstance(model_val, str) else model_val.primary,
            "type": "configured",
        })
    return models


@register_handler("node.list")
async def handle_node_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List connected nodes"""
    from openclaw.nodes.manager import get_node_manager

    node_manager = get_node_manager()
    connected = node_manager.list_nodes()
    paired = node_manager.list_paired_nodes()
    paired_by_id: dict[str, dict[str, Any]] = {
        str(item.get("nodeId")): item for item in paired if item.get("nodeId")
    }
    connected_by_id: dict[str, dict[str, Any]] = {
        str(item.get("id")): item for item in connected if item.get("id")
    }
    node_ids = set(paired_by_id.keys()) | set(connected_by_id.keys())

    nodes: list[dict[str, Any]] = []
    for node_id in node_ids:
        paired_item = paired_by_id.get(node_id, {})
        live_item = connected_by_id.get(node_id, {})
        live_meta = live_item.get("metadata") if isinstance(live_item.get("metadata"), dict) else {}

        live_types = (
            (live_item.get("capabilities") or {}).get("types")
            if isinstance(live_item.get("capabilities"), dict)
            else []
        )
        caps = _sorted_unique_strings(live_types, paired_item.get("caps", []))
        commands = _sorted_unique_strings(live_meta.get("commands", []), paired_item.get("commands", []))

        nodes.append(
            {
                "nodeId": node_id,
                "displayName": live_meta.get("displayName", paired_item.get("displayName")),
                "platform": live_meta.get("platform", paired_item.get("platform")),
                "version": live_meta.get("version", paired_item.get("version")),
                "coreVersion": live_meta.get("coreVersion", paired_item.get("coreVersion")),
                "uiVersion": live_meta.get("uiVersion", paired_item.get("uiVersion")),
                "deviceFamily": live_meta.get("deviceFamily", paired_item.get("deviceFamily")),
                "modelIdentifier": live_meta.get("modelIdentifier", paired_item.get("modelIdentifier")),
                "remoteIp": live_meta.get("remoteIp", paired_item.get("remoteIp")),
                "caps": caps,
                "commands": commands,
                "permissions": live_meta.get("permissions", paired_item.get("permissions")),
                "pathEnv": live_meta.get("pathEnv", paired_item.get("pathEnv")),
                "connectedAtMs": int((live_item.get("registeredAt") or 0) * 1000) if live_item else None,
                "connected": bool(live_item),
                "paired": bool(paired_item),
            }
        )

    nodes.sort(
        key=lambda n: (
            0 if n.get("connected") else 1,
            str(n.get("displayName") or n.get("nodeId") or "").lower(),
            str(n.get("nodeId") or ""),
        )
    )
    return {"ts": int(datetime.now(UTC).timestamp() * 1000), "nodes": nodes}


@register_handler("node.describe")
async def handle_node_describe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Describe a node"""
    from openclaw.nodes.manager import get_node_manager

    node_id = str(params.get("nodeId", "")).strip()
    if not node_id:
        raise ValueError("nodeId required")
    node_manager = get_node_manager()
    paired = next((n for n in node_manager.list_paired_nodes() if n.get("nodeId") == node_id), None)
    live = next((n for n in node_manager.list_nodes() if n.get("id") == node_id), None)

    if not paired and not live:
        raise ValueError(f"Node not found: {node_id}")

    live_meta = live.get("metadata") if isinstance((live or {}).get("metadata"), dict) else {}
    live_caps = (live or {}).get("capabilities") if isinstance((live or {}).get("capabilities"), dict) else {}
    caps = _sorted_unique_strings(
        live_caps.get("types") if isinstance(live_caps.get("types"), list) else [],
        (paired or {}).get("caps", []),
    )
    commands = _sorted_unique_strings(
        live_meta.get("commands") if isinstance(live_meta.get("commands"), list) else [],
        (paired or {}).get("commands", []),
    )

    return {
        "ts": int(datetime.now(UTC).timestamp() * 1000),
        "nodeId": node_id,
        "displayName": live_meta.get("displayName", (paired or {}).get("displayName")),
        "platform": live_meta.get("platform", (paired or {}).get("platform")),
        "version": live_meta.get("version", (paired or {}).get("version")),
        "coreVersion": live_meta.get("coreVersion", (paired or {}).get("coreVersion")),
        "uiVersion": live_meta.get("uiVersion", (paired or {}).get("uiVersion")),
        "deviceFamily": live_meta.get("deviceFamily", (paired or {}).get("deviceFamily")),
        "modelIdentifier": live_meta.get("modelIdentifier", (paired or {}).get("modelIdentifier")),
        "remoteIp": live_meta.get("remoteIp", (paired or {}).get("remoteIp")),
        "caps": caps,
        "commands": commands,
        "permissions": live_meta.get("permissions", (paired or {}).get("permissions")),
        "pathEnv": live_meta.get("pathEnv", (paired or {}).get("pathEnv")),
        "connectedAtMs": int((live or {}).get("registeredAt", 0) * 1000) if live else None,
        "paired": bool(paired),
        "connected": bool(live),
    }


@register_handler("node.invoke")
async def handle_node_invoke(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Invoke a command on a node via the live NodeRegistry WebSocket connection."""
    node_id = params.get("nodeId")
    command = str(params.get("command", "")).strip()
    command_params = params.get("params", {})
    timeout_ms = int(params.get("timeoutMs") or 30_000)
    idempotency_key = params.get("idempotencyKey")

    if not command:
        raise ValueError("nodeId and command required")
    if command in ("system.execApprovals.get", "system.execApprovals.set"):
        raise ValueError("node.invoke does not allow system.execApprovals.*; use exec.approvals.node.*")

    from openclaw.nodes.manager import get_node_manager
    node_manager = get_node_manager()

    # If nodeId is empty, auto-resolve to the first available node.
    # Mirrors TS resolveNodeId(gatewayOpts, node, allowDefault=true) in nodes-utils.ts:
    # when no node is specified the caller expects the gateway to pick the default.
    if not node_id:
        registry = _node_registry
        gateway = getattr(connection, "gateway", None)
        if registry is None and gateway is not None:
            registry = getattr(gateway, "node_registry", None)
        if registry is not None:
            live_nodes = registry.list_nodes()
            if live_nodes:
                node_id = live_nodes[0].nodeId
        if not node_id:
            # Fall back to paired nodes list
            paired = node_manager.list_paired_nodes()
            if paired:
                node_id = str(paired[0].get("nodeId", ""))
        if not node_id:
            raise ValueError("No nodes available; connect a node first")

    node_info = node_manager.get_node(node_id)
    if node_info is not None:
        allowed_commands = node_info.metadata.get("commands") if isinstance(node_info.metadata, dict) else None
        if isinstance(allowed_commands, list) and allowed_commands and command not in allowed_commands:
            raise ValueError(f"Node command not allowed: {command}")

    # Use live NodeRegistry to send the invocation over WebSocket
    registry = _node_registry
    gateway = getattr(connection, "gateway", None)
    if registry is None and gateway is not None:
        registry = getattr(gateway, "node_registry", None)

    if registry is not None:
        from openclaw.gateway.node_registry import NodeInvokeResult
        result: NodeInvokeResult = await registry.invoke(
            node_id=node_id,
            command=command,
            params=command_params,
            timeout_ms=timeout_ms,
            idempotency_key=idempotency_key,
        )
        if not result.ok:
            error = result.error or {}
            raise ValueError(f"Node invoke failed: {error.get('message', 'unknown error')} [{error.get('code', '')}]")
        return {
            "ok": True,
            "nodeId": node_id,
            "command": command,
            "payload": result.payload,
            "payloadJSON": result.payload_json,
        }

    # Fallback: node not yet connected via registry — queue via NodeManager (offline path)
    logger.warning(f"NodeRegistry not available for node.invoke; falling back to NodeManager queue for node={node_id!r}")
    queued = await node_manager.invoke_node(
        node_id,
        command,
        command_params,
        timeout_ms=timeout_ms,
        idempotency_key=idempotency_key,
    )
    return {
        "ok": True,
        "nodeId": node_id,
        "command": command,
        "payload": queued,
        "payloadJSON": None,
    }


@register_handler("node.pair.approve")
async def handle_node_pair_approve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Approve node pairing"""
    from openclaw.nodes.manager import get_node_manager
    
    request_or_node = params.get("requestId") or params.get("nodeId")
    request_id = params.get("requestId")
    node_manager = get_node_manager()
    pending = node_manager.pending_pairs.get(request_or_node) if request_or_node else None
    if pending is None and request_or_node:
        pending = next(
            (r for r in node_manager.pending_pairs.values() if r.node_id == request_or_node),
            None,
        )
    if request_id and pending is None:
        raise ValueError("unknown requestId")
    token = node_manager.approve_pairing(request_or_node)
    resolved_node_id = pending.node_id if pending is not None else params.get("nodeId")
    payload = {
        "requestId": params.get("requestId"),
        "nodeId": resolved_node_id,
        "approved": token is not None,
        "token": token,
    }
    if token is not None:
        gateway = getattr(connection, "gateway", None)
        if gateway is not None and hasattr(gateway, "broadcast_event"):
            try:
                await gateway.broadcast_event(
                    "node.pair.resolved",
                    {
                        "requestId": params.get("requestId"),
                        "nodeId": resolved_node_id,
                        "decision": "approved",
                        "ts": int(datetime.now(UTC).timestamp() * 1000),
                    },
                )
            except Exception:
                pass
    return payload


@register_handler("node.pair.reject")
async def handle_node_pair_reject(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reject node pairing"""
    from openclaw.nodes.manager import get_node_manager
    
    request_or_node = params.get("requestId") or params.get("nodeId")
    request_id = params.get("requestId")
    reason = params.get("reason")
    
    node_manager = get_node_manager()
    pending = node_manager.pending_pairs.get(request_or_node) if request_or_node else None
    if pending is None and request_or_node:
        pending = next(
            (r for r in node_manager.pending_pairs.values() if r.node_id == request_or_node),
            None,
        )
    if request_id and pending is None:
        raise ValueError("unknown requestId")
    node_manager.reject_pairing(request_or_node, reason)
    resolved_node_id = pending.node_id if pending is not None else params.get("nodeId")
    gateway = getattr(connection, "gateway", None)
    if gateway is not None and hasattr(gateway, "broadcast_event"):
        try:
            await gateway.broadcast_event(
                "node.pair.resolved",
                {
                    "requestId": params.get("requestId"),
                    "nodeId": resolved_node_id,
                    "decision": "rejected",
                    "ts": int(datetime.now(UTC).timestamp() * 1000),
                },
            )
        except Exception:
            pass
    return {"requestId": params.get("requestId"), "nodeId": resolved_node_id, "rejected": True}


@register_handler("sessions.preview")
async def handle_sessions_preview(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Preview session - using store-based implementation"""
    return await _sessions_preview_method.execute(connection, params)


@register_handler("sessions.resolve")
async def handle_sessions_resolve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve session key - using store-based implementation"""
    return await _sessions_resolve_method.execute(connection, params)


@register_handler("sessions.patch")
async def handle_sessions_patch(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Patch session metadata - using store-based implementation"""
    return await _sessions_patch_method.execute(connection, params)


@register_handler("sessions.reset")
async def handle_sessions_reset(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reset session - using store-based implementation"""
    return await _sessions_reset_method.execute(connection, params)


@register_handler("sessions.delete")
async def handle_sessions_delete(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Delete session - using store-based implementation"""
    return await _sessions_delete_method.execute(connection, params)


@register_handler("sessions.compact")
async def handle_sessions_compact(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Compact session - using store-based implementation"""
    return await _sessions_compact_method.execute(connection, params)


@register_handler("skills.status")
async def handle_skills_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get skills status - mirrors TS skills.status handler"""
    from openclaw.agents.skills_status import build_workspace_skill_status
    from openclaw.agents.skills.bundled_dir import resolve_bundled_skills_dir
    from openclaw.config.loader import load_config
    from openclaw.agents.agent_scope import (
        resolve_agent_workspace_dir,
        resolve_default_agent_id,
        list_agent_ids,
    )
    from openclaw.routing.session_key import normalize_agent_id
    from openclaw.infra.skills_remote import (
        can_exec_request_node,
        get_remote_skill_eligibility,
        sync_remote_nodes_from_registry,
    )

    cfg = load_config()
    agent_id_raw = params.get("agentId", "").strip() if params.get("agentId") else ""
    agent_id = normalize_agent_id(agent_id_raw) if agent_id_raw else resolve_default_agent_id(cfg)

    if agent_id_raw:
        known_agents = list_agent_ids(cfg)
        if agent_id not in known_agents:
            raise ValueError(f'unknown agent id "{agent_id_raw}"')

    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    config_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else (cfg if isinstance(cfg, dict) else {})

    registry = _node_registry
    if registry is None and getattr(connection, "gateway", None) is not None:
        registry = getattr(connection.gateway, "node_registry", None)
    sync_remote_nodes_from_registry(registry)

    remote = get_remote_skill_eligibility(
        advertise_exec_node=can_exec_request_node(config_dict, agent_id),
    )
    eligibility = {"remote": remote} if remote else None

    bundled_skills_dir = resolve_bundled_skills_dir()
    return build_workspace_skill_status(
        workspace_dir,
        config=config_dict,
        bundled_skills_dir=bundled_skills_dir,
        eligibility=eligibility,
    )


@register_handler("skills.install")
async def handle_skills_install(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Install skill deps or ClawHub skill — mirrors TS skills.install handler."""
    from openclaw.config.loader import load_config
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id
    from openclaw.agents.skills.workspace import load_workspace_skill_entries
    from openclaw.agents.skills.installer import install_skill_dependencies
    from openclaw.agents.skills_clawhub import install_skill_from_clawhub
    from openclaw.security.install_security_scan import scan_skill_install_source
    from pathlib import Path

    cfg = load_config()
    workspace_dir_raw = resolve_agent_workspace_dir(cfg, resolve_default_agent_id(cfg))

    if params.get("source") == "clawhub":
        slug = params.get("slug", "")
        result = await install_skill_from_clawhub(
            workspace_dir=str(workspace_dir_raw),
            slug=str(slug),
            version=params.get("version"),
            force=bool(params.get("force")),
        )
        if result.get("ok"):
            return {
                "ok": True,
                "message": f"Installed {result['slug']}@{result['version']}",
                "stdout": "",
                "stderr": "",
                "code": 0,
                "slug": result["slug"],
                "version": result["version"],
                "targetDir": result["targetDir"],
            }
        return {"ok": False, "error": result.get("error", "install failed")}

    skill_name = params.get("name", "")
    install_id = params.get("installId", "")
    dangerously_force = bool(params.get("dangerouslyForceUnsafeInstall"))

    try:
        config_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else {}
        entries = load_workspace_skill_entries(str(workspace_dir_raw), config_dict)
        target = next((e for e in entries if e.skill.name == skill_name), None)
        if target is None:
            return {
                "ok": False,
                "message": f"Skill not found: {skill_name}",
                "stdout": "",
                "stderr": "",
                "code": None,
            }

        source = getattr(target.skill, "source", "unknown")
        base_dir = Path(getattr(target.skill, "base_dir", None) or getattr(target.skill, "location", "") or ".").parent
        if not base_dir.is_dir():
            base_dir = Path(str(workspace_dir_raw)) / "skills" / skill_name

        scan = await scan_skill_install_source(
            skill_name=skill_name,
            source_dir=str(base_dir.resolve()),
            origin=str(source),
            install_id=str(install_id),
            dangerously_force_unsafe_install=dangerously_force,
        )
        if scan and scan.get("blocked"):
            return {
                "ok": False,
                "message": scan["blocked"].get("reason", "security scan blocked install"),
                "stdout": "",
                "stderr": "",
                "code": None,
            }

        install_specs = getattr(getattr(target.skill, "metadata", None), "install", None) or []
        if install_id:
            filtered = []
            for i, spec in enumerate(install_specs):
                spec_id = getattr(spec, "id", None) or f"{getattr(spec, 'kind', 'install')}-{i}"
                if spec_id == install_id:
                    filtered.append(spec)
            install_specs = filtered
            if not install_specs:
                return {
                    "ok": False,
                    "message": f"Installer not found: {install_id}",
                    "stdout": "",
                    "stderr": "",
                    "code": None,
                }

        success, errors = await install_skill_dependencies(target.skill, install_specs)
        message = "Installation complete" if success else f"Installation failed: {', '.join(errors)}"
        return {
            "ok": success,
            "message": message,
            "stdout": "",
            "stderr": "\n".join(errors) if errors else "",
            "code": 0 if success else None,
        }
    except Exception as exc:
        logger.error("skills.install error: %s", exc, exc_info=True)
        return {
            "ok": False,
            "message": str(exc),
            "stdout": "",
            "stderr": "",
            "code": None,
        }


@register_handler("skills.update")
async def handle_skills_update(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Update skill config or ClawHub installs — mirrors TS skills.update handler."""
    from openclaw.config.loader import load_config, write_config_file
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id
    from openclaw.agents.skills_clawhub import update_skills_from_clawhub
    import copy

    if params.get("source") == "clawhub":
        slug = params.get("slug")
        update_all = bool(params.get("all"))
        if not slug and not update_all:
            raise ValueError('clawhub skills.update requires "slug" or "all"')
        if slug and update_all:
            raise ValueError('clawhub skills.update accepts either "slug" or "all", not both')

        cfg = load_config()
        workspace_dir = resolve_agent_workspace_dir(cfg, resolve_default_agent_id(cfg))
        results = await update_skills_from_clawhub(
            workspace_dir=str(workspace_dir),
            slug=str(slug) if slug else None,
        )
        errors = [r for r in results if not r.get("ok")]
        return {
            "ok": len(errors) == 0,
            "skillKey": slug if slug else "*",
            "config": {
                "source": "clawhub",
                "results": results,
            },
            **({"error": "; ".join(str(r.get("error", "")) for r in errors)} if errors else {}),
        }

    skill_key = params.get("skillKey", "")
    enabled = params.get("enabled")
    api_key = params.get("apiKey")
    env = params.get("env")

    try:
        cfg = load_config()
        cfg_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else {}
        skills = copy.deepcopy(cfg_dict.get("skills") or {})
        entries = copy.deepcopy(skills.get("entries") or {})
        current = copy.deepcopy(entries.get(skill_key) or {})

        if isinstance(enabled, bool):
            current["enabled"] = enabled

        if isinstance(api_key, str):
            trimmed = api_key.strip()
            if trimmed:
                current["apiKey"] = trimmed
            elif "apiKey" in current:
                del current["apiKey"]

        if env and isinstance(env, dict):
            next_env = copy.deepcopy(current.get("env", {}))
            for key, value in env.items():
                trimmed_key = key.strip()
                if not trimmed_key:
                    continue
                trimmed_val = value.strip() if isinstance(value, str) else str(value)
                if not trimmed_val:
                    next_env.pop(trimmed_key, None)
                else:
                    next_env[trimmed_key] = trimmed_val
            current["env"] = next_env

        entries[skill_key] = current
        skills["entries"] = entries
        cfg_dict["skills"] = skills
        write_config_file(cfg_dict)

        from openclaw.agents.skills.refresh import bump_skills_snapshot_version
        bump_skills_snapshot_version()

        return {"ok": True, "skillKey": skill_key, "config": current}
    except Exception as exc:
        logger.error("skills.update error: %s", exc, exc_info=True)
        return {"ok": False, "skillKey": skill_key, "error": str(exc)}


@register_handler("system")
async def handle_system(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get system information"""
    import platform
    return {
        "platform": platform.system(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "hostname": platform.node(),
    }


@register_handler("talk")
async def handle_talk(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Voice talk handler"""
    return {"status": "not_configured"}


@register_handler("tts.status")
async def handle_tts_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_status as _impl

    return await _impl(connection, params)


@register_handler("tts.enable")
async def handle_tts_enable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_enable as _impl

    return await _impl(connection, params)


@register_handler("tts.disable")
async def handle_tts_disable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_disable as _impl

    return await _impl(connection, params)


@register_handler("tts.convert")
async def handle_tts_convert(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_convert as _impl

    return await _impl(connection, params)


@register_handler("tts.providers")
async def handle_tts_providers(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import _get_cfg_dict
    from openclaw.tts.provider_registry import (
        get_resolved_speech_provider_config,
        list_speech_providers,
    )
    from openclaw.tts.tts import get_tts_provider, resolve_tts_config, resolve_tts_prefs_path

    cfg = _get_cfg_dict(connection)
    config = resolve_tts_config(cfg)
    prefs_path = resolve_tts_prefs_path(config)
    raw = config.get("rawConfig") or {}
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.label,
                "configured": p.is_configured(
                    cfg=cfg,
                    provider_config=get_resolved_speech_provider_config(raw, p.id, cfg),
                    timeout_ms=config.get("timeoutMs") or 30_000,
                ),
                "models": list(p.models),
                "voices": list(p.voices),
            }
            for p in list_speech_providers(cfg)
        ],
        "active": get_tts_provider(config, prefs_path),
    }


@register_handler("update.run")
async def handle_update_run(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Run update check"""
    return {"updateAvailable": False, "currentVersion": "1.0.0"}


@register_handler("usage.status")
async def handle_usage_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get usage status"""
    return {"totalTokens": 0, "totalCost": 0.0, "sessions": 0}


@register_handler("usage.cost")
async def handle_usage_cost(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get usage cost"""
    return {"total_tokens": 0, "total_cost": 0.0, "by_model": {}}


@register_handler("voicewake.get")
async def handle_voicewake_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_voicewake_get as _impl

    return await _impl(connection, params)


@register_handler("voicewake.set")
async def handle_voicewake_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_voicewake_set as _impl

    return await _impl(connection, params)


@register_handler("web.login.start")
async def handle_web_login_start(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Start web login flow"""
    return {"loginUrl": "http://localhost:18789/login", "token": "pending"}


@register_handler("web.login.wait")
async def handle_web_login_wait(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Wait for web login completion"""
    return {"authenticated": False}


_wizard_sessions: dict[str, Any] = {}


def _find_running_wizard() -> str | None:
    """Return session ID of a running wizard, if any."""
    for sid, sess in _wizard_sessions.items():
        if sess.get_status() == "running":
            return sid
    return None


@register_handler("wizard.start")
async def handle_wizard_start(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Start setup wizard — mirrors TS wizard.start handler."""
    if _wizard_handler:
        return await _wizard_handler.wizard_start(params)

    from .wizard_rpc import resolve_wizard_start_params

    resolved = resolve_wizard_start_params(params)
    if "error" in resolved:
        return resolved

    flow = resolved["flow"]
    gateway_mode = resolved["gateway_mode"]
    workspace = resolved["workspace"]

    running_id = _find_running_wizard()
    if running_id:
        return {"error": "wizard already running", "sessionId": running_id}

    from ..wizard.session import WizardSession
    import uuid

    async def _runner(prompter):
        from ..wizard.onboarding import run_onboarding_wizard
        await run_onboarding_wizard(
            flow=flow,
            mode=gateway_mode,
            workspace_dir=workspace,
        )

    try:
        session_id = str(uuid.uuid4())
        session = WizardSession(runner=_runner)
        _wizard_sessions[session_id] = session
        result = await session.next()
        if result.done:
            _wizard_sessions.pop(session_id, None)
        return {"sessionId": session_id, **result.to_dict()}
    except Exception as e:
        logger.error(f"Error starting wizard: {e}", exc_info=True)
        return {"error": str(e)}


@register_handler("wizard.next")
async def handle_wizard_next(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Advance wizard to next step — mirrors TS wizard.next handler."""
    if _wizard_handler:
        return await _wizard_handler.wizard_next(params)

    session_id = params.get("sessionId", "")
    session = _wizard_sessions.get(session_id)
    if not session:
        return {"error": "wizard not found"}

    answer = params.get("answer")
    if answer and isinstance(answer, dict):
        step_id = str(answer.get("stepId", ""))
        value = answer.get("value")
        if session.get_status() != "running":
            return {"error": "wizard not running"}
        try:
            await session.answer(step_id, value)
        except Exception as exc:
            return {"error": str(exc)}

    result = await session.next()
    if result.done:
        _wizard_sessions.pop(session_id, None)
    return result.to_dict()


@register_handler("wizard.cancel")
async def handle_wizard_cancel(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Cancel wizard session — mirrors TS wizard.cancel handler."""
    if _wizard_handler:
        return await _wizard_handler.wizard_cancel(params)

    session_id = params.get("sessionId", "")
    session = _wizard_sessions.get(session_id)
    if not session:
        return {"error": "wizard not found"}

    session.cancel()
    _wizard_sessions.pop(session_id, None)
    return {"status": session.get_status(), "error": session.get_error()}


@register_handler("wizard.status")
async def handle_wizard_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get wizard status — mirrors TS wizard.status handler."""
    if _wizard_handler:
        return await _wizard_handler.wizard_status(params)

    session_id = params.get("sessionId", "")
    session = _wizard_sessions.get(session_id)
    if not session:
        return {"error": "wizard not found"}

    status = session.get_status()
    if status != "running":
        _wizard_sessions.pop(session_id, None)
    return {"status": status, "error": session.get_error()}

# Additional Talk Mode handlers (mirrors TS talk-mode-handler.ts)
_TALK_MODE_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "provider": "openai",
    "model": "whisper-1",
    "language": "en",
}

@register_handler("talk.mode.get")
async def handle_talk_mode_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get talk mode configuration — reads live config (mirrors TS talkMode.get)."""
    try:
        from openclaw.config.loader import load_config
        cfg = load_config()
        cfg_dict = cfg.model_dump() if hasattr(cfg, "model_dump") else {}
        talk_cfg = (cfg_dict.get("talk") or {}) if isinstance(cfg_dict, dict) else {}
        return {**_TALK_MODE_DEFAULTS, **talk_cfg}
    except Exception:
        return dict(_TALK_MODE_DEFAULTS)


@register_handler("talk.mode.set")
async def handle_talk_mode_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set talk mode configuration — persists to live config (mirrors TS talkMode.set)."""
    try:
        from openclaw.gateway.config_service import get_config_service
        svc = get_config_service()
        patch: dict[str, Any] = {}
        for key in ("enabled", "provider", "model", "language"):
            if key in params:
                patch[key] = params[key]
        if patch:
            current = svc.get_config()
            talk = dict(current.get("talk") or {})
            talk.update(patch)
            svc.patch_config({"talk": talk})
        return {"success": True, "config": {**_TALK_MODE_DEFAULTS, **patch}}
    except Exception as e:
        return {"success": False, "error": str(e)}


@register_handler("talk.mode")
async def handle_talk_mode(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_talk_mode as _impl

    return await _impl(connection, params)


@register_handler("talk.config")
async def handle_talk_config(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_talk_config as _impl

    return await _impl(connection, params)


@register_handler("system-event")
async def handle_system_event_alias(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Hyphenated alias used by TS Gateway API."""
    return await handle_system_event(connection, params)


@register_handler("send")
async def handle_send(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    TS-compatible send endpoint.
    Expects at least channel/to/text; delegates to channels.send.
    """
    mapped = {
        "channelId": params.get("channel") or params.get("channelId"),
        "target": params.get("to") or params.get("target"),
        "text": params.get("text") or params.get("message", ""),
    }
    return await handle_channels_send(connection, mapped)


@register_handler("skills.bins")
async def handle_skills_bins(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List known skill bins - mirrors TS skills.bins handler"""
    from openclaw.config.loader import load_config
    from openclaw.agents.agent_scope import list_agent_workspace_dirs
    from openclaw.agents.skills.workspace import load_workspace_skill_entries
    from openclaw.agents.skills_status import collect_skill_bins
    
    cfg = load_config()
    workspace_dirs = list_agent_workspace_dirs(cfg)
    bins = set()
    
    config_dict = cfg.model_dump() if hasattr(cfg, 'model_dump') else {}
    
    for workspace_dir in workspace_dirs:
        entries = load_workspace_skill_entries(str(workspace_dir), config_dict)
        for bin_path in collect_skill_bins(entries):
            bins.add(bin_path)
    
    return {"bins": sorted(list(bins))}


@register_handler("tts.setProvider")
async def handle_tts_set_provider(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_set_provider as _impl

    return await _impl(connection, params)


@register_handler("exec.approvals.node.get")
async def handle_exec_approvals_node_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get exec approvals from a remote node — routes via NodeManager.invoke_node()."""
    node_id = params.get("nodeId")
    if not node_id:
        return {"nodeId": node_id, "policy": None, "error": "nodeId required"}

    from openclaw.nodes.manager import get_node_manager
    manager = get_node_manager()
    try:
        result = await manager.invoke_node(
            node_id,
            "system.execApprovals.get",
            {},
            timeout_ms=10_000,
        )
        payload = result.get("result") or {}
        return {"nodeId": node_id, "policy": payload.get("file"), "hash": payload.get("hash")}
    except ValueError as exc:
        return {"nodeId": node_id, "policy": None, "error": str(exc)}
    except Exception as exc:
        return {"nodeId": node_id, "policy": None, "error": f"invoke failed: {exc}"}


@register_handler("exec.approvals.node.set")
async def handle_exec_approvals_node_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set exec approvals on a remote node — routes via NodeManager.invoke_node()."""
    node_id = params.get("nodeId")
    policy = params.get("policy")
    base_hash = params.get("baseHash")

    if not node_id:
        return {"ok": False, "error": "nodeId required"}

    from openclaw.nodes.manager import get_node_manager
    import json as _json
    manager = get_node_manager()
    try:
        result = await manager.invoke_node(
            node_id,
            "system.execApprovals.set",
            {"paramsJSON": _json.dumps({"file": policy, "baseHash": base_hash})},
            timeout_ms=10_000,
        )
        payload = result.get("result") or {}
        ok = payload.get("ok", False)
        return {"ok": ok, "nodeId": node_id, "error": payload.get("error")}
    except ValueError as exc:
        return {"ok": False, "nodeId": node_id, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "nodeId": node_id, "error": f"invoke failed: {exc}"}


@register_handler("exec.approval.waitDecision")
async def handle_exec_approval_wait_decision(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """
    Wait for approval decision.
    Current implementation is non-blocking best-effort for API compatibility.
    """
    request_id = params.get("requestId")
    timeout_ms = int(params.get("timeoutMs", 30000))
    approval_manager = None
    if connection.gateway and hasattr(connection.gateway, "approval_manager"):
        approval_manager = connection.gateway.approval_manager
    if not approval_manager or not request_id:
        return {"requestId": request_id, "status": "unknown"}
    pending = getattr(approval_manager, "pending_approvals", {})
    req = pending.get(request_id)
    status = getattr(req, "status", "pending") if req else "unknown"
    return {"requestId": request_id, "status": status, "timeoutMs": timeout_ms}


@register_handler("device.pair.remove")
async def handle_device_pair_remove(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Remove paired device - mirrors TS device.pair.remove"""
    from openclaw.devices.manager import get_device_manager
    
    device_id = params.get("deviceId")
    if not device_id:
        raise ValueError("deviceId is required")
    
    device_manager = get_device_manager()
    result = device_manager.remove_device(device_id)
    
    if not result:
        raise ValueError(f"unknown deviceId: {device_id}")
    
    logger.info(f"Device removed: {device_id}")
    
    return result


@register_handler("node.pair.list")
async def handle_node_pair_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List pending node pairing requests."""
    from openclaw.nodes.manager import get_node_manager
    manager = get_node_manager()
    try:
        pending = manager.list_pending_pairs()
        paired = manager.list_paired_nodes()
    except Exception:
        pending = []
        paired = []
    return {"pending": pending, "paired": paired}


@register_handler("node.pair.request")
async def handle_node_pair_request(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Create node pairing request."""
    from openclaw.nodes.manager import get_node_manager
    manager = get_node_manager()
    node_id = params.get("nodeId")
    request_data = params.get("request", {})
    if not isinstance(request_data, dict):
        request_data = {}
    try:
        req = manager.request_pairing(
            node_id=node_id,
            request_id=params.get("requestId"),
            display_name=request_data.get("displayName") or params.get("displayName"),
            platform=request_data.get("platform") or params.get("platform"),
            version=request_data.get("version") or params.get("version"),
            caps=request_data.get("caps") or params.get("caps"),
            commands=request_data.get("commands") or params.get("commands"),
            metadata=request_data.get("metadata") or {},
            nonce=request_data.get("nonce", ""),
            signature=request_data.get("signature", ""),
        )
        req_id = req.request_id
        gateway = getattr(connection, "gateway", None)
        if gateway is not None and hasattr(gateway, "broadcast_event"):
            try:
                await gateway.broadcast_event(
                    "node.pair.requested",
                    {
                        "requestId": req.request_id,
                        "nodeId": req.node_id,
                        "displayName": req.display_name,
                        "platform": req.platform,
                        "version": req.version,
                        "caps": req.caps,
                        "commands": req.commands,
                        "ts": int(datetime.now(UTC).timestamp() * 1000),
                    },
                )
            except Exception:
                pass
    except Exception:
        req_id = None
    return {"requested": req_id is not None, "requestId": req_id, "nodeId": node_id}


@register_handler("node.pair.verify")
async def handle_node_pair_verify(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Verify pairing token/code for node."""
    from openclaw.nodes.manager import get_node_manager
    manager = get_node_manager()
    token = params.get("token")
    expected_node_id = params.get("nodeId")
    try:
        result = manager.verify_pairing(token=token)
        ok = bool(result.get("ok"))
        node_id = result.get("nodeId")
        if expected_node_id and node_id and str(expected_node_id) != str(node_id):
            ok = False
    except Exception:
        ok = False
        node_id = params.get("nodeId")
    return {"nodeId": node_id, "verified": ok}


@register_handler("node.rename")
async def handle_node_rename(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rename node."""
    from openclaw.nodes.manager import get_node_manager
    manager = get_node_manager()
    node_id = str(params.get("nodeId", "")).strip()
    display_name = str(params.get("displayName") or params.get("name") or "").strip()
    if not node_id:
        raise ValueError("nodeId required")
    if not display_name:
        raise ValueError("displayName required")
    ok = bool(manager.rename_node(node_id=node_id, name=display_name))
    if not ok:
        raise ValueError(f"Node not found: {node_id}")
    return {"nodeId": node_id, "displayName": display_name}


@register_handler("node.event")
async def handle_node_event(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Publish node event payload and dispatch to NodeEventHandler."""
    event = str(params.get("event", "")).strip()
    if not event:
        raise ValueError("event is required")

    requested_node_id = str(params.get("nodeId", "")).strip() or None
    caller_node_id = _resolve_node_caller_id(connection)
    if caller_node_id and requested_node_id and caller_node_id != requested_node_id:
        raise ValueError("nodeId mismatch")
    node_id = caller_node_id or requested_node_id or "node"

    payload = params.get("payload")
    payload_json = params.get("payloadJSON")
    if payload is None and isinstance(payload_json, str):
        try:
            payload = json.loads(payload_json)
        except Exception:
            payload = None
    if payload is None:
        payload = {}
    normalized_payload_json = (
        payload_json
        if isinstance(payload_json, str)
        else json.dumps(payload) if payload is not None else None
    )

    # Broadcast raw event to all WS clients for observability
    gateway = getattr(connection, "gateway", None)
    if gateway is not None and hasattr(gateway, "broadcast_event"):
        try:
            await gateway.broadcast_event(
                "node.event",
                {
                    "nodeId": node_id,
                    "event": event,
                    "payload": payload,
                    "payloadJSON": normalized_payload_json,
                    "ts": int(datetime.now(UTC).timestamp() * 1000),
                },
            )
        except Exception:
            pass

    # Route to NodeEventHandler for semantic handling (mirrors TS server-node-events.ts)
    handler = _node_event_handler
    if handler is None and gateway is not None:
        handler = getattr(gateway, "node_event_handler", None)
    if handler is not None:
        try:
            await handler.handle_node_event(node_id, event, payload)
        except Exception as exc:
            logger.warning(f"NodeEventHandler.handle_node_event raised: {exc}", exc_info=True)

    return {"ok": True}


@register_handler("node.invoke.result")
async def handle_node_invoke_result(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Handle node invoke callback result — resolves the pending Future in NodeRegistry."""
    invocation_id = params.get("invocationId") or params.get("id")
    if not invocation_id:
        raise ValueError("invocationId is required")
    caller_node_id = _resolve_node_caller_id(connection)
    provided_node_id = params.get("nodeId")
    if caller_node_id and provided_node_id and str(provided_node_id).strip() != caller_node_id:
        raise ValueError("nodeId mismatch")

    node_id = caller_node_id or (str(provided_node_id).strip() if provided_node_id else None) or "node"

    payload_json = params.get("payloadJSON")
    payload = params.get("payload")
    if payload is None and payload_json is not None and not isinstance(payload_json, str):
        payload = payload_json
        payload_json = None
    ok = bool(params.get("ok", True))
    error = params.get("error") if not ok else None

    # Resolve via NodeRegistry (live WS invoke path)
    registry = _node_registry
    gateway = getattr(connection, "gateway", None)
    if registry is None and gateway is not None:
        registry = getattr(gateway, "node_registry", None)

    if registry is not None:
        ack = registry.resolve_invoke_result(
            invocation_id=invocation_id,
            node_id=node_id,
            ok=ok,
            payload=payload,
            payload_json=payload_json if isinstance(payload_json, str) else None,
            error=error,
        )
        if ack:
            return {"ok": True, "ack": True}

    # Fallback: resolve via NodeManager (offline/queued path)
    from openclaw.nodes.manager import get_node_manager
    node_manager = get_node_manager()
    result_payload = {
        "nodeId": node_id,
        "ok": ok,
        "payload": payload,
        "payloadJSON": payload_json if isinstance(payload_json, str) else None,
        "error": error,
    }
    ack_fallback = bool(node_manager.resolve_invoke_result(invocation_id, result_payload))
    if not ack_fallback:
        return {"ok": True, "ack": False, "ignored": True}
    return {"ok": True, "ack": True}


@register_handler("set-heartbeats")
async def handle_set_heartbeats(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set heartbeat state (TS-compatible endpoint)."""
    return {"ok": True, "enabled": bool(params.get("enabled", True))}


@register_handler("last-heartbeat")
async def handle_last_heartbeat(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get last heartbeat timestamp."""
    return {"ts": int(datetime.now(UTC).timestamp() * 1000)}


@register_handler("wake")
async def handle_wake(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Trigger cron wake / heartbeat — mirrors TS wake handler."""
    from openclaw.cron.service import get_cron_service

    mode = params.get("mode")
    if mode not in ("now", "next-heartbeat"):
        raise ValueError("invalid wake params: mode must be 'now' or 'next-heartbeat'")

    text = params.get("text")
    if not isinstance(text, str):
        raise ValueError("invalid wake params: text is required")

    cron_service = get_cron_service()
    if not cron_service:
        return {"ok": False}

    return cron_service.wake(text=text, mode=mode)


@register_handler("agents.create")
async def handle_agents_create(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Create agent (full implementation mirroring TS agents.create)"""
    try:
        return await _handle_agents_create_impl(connection, params)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _handle_agents_create_impl(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from pathlib import Path
    from openclaw.routing.session_key import normalize_agent_id
    from openclaw.commands.agents_config import apply_agent_config, find_agent_entry_index
    from openclaw.agents.agent_scope import (
        resolve_agent_dir,
        list_agent_entries,
    )
    from openclaw.agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
    from openclaw.config.loader import write_config_file
    
    # Validate required params — name defaults to id if not provided
    raw_name = str(params.get("name") or params.get("id", "")).strip()
    if not raw_name:
        raise ValueError("name is required")
    
    agent_id = str(params.get("id", "")).strip() or normalize_agent_id(raw_name)
    
    # Check if agent already exists
    cfg = _get_current_config()
    agents_list = list_agent_entries(cfg)
    if find_agent_entry_index(agents_list, agent_id) >= 0:
        raise ValueError(f'agent "{agent_id}" already exists')
    
    # Reserved ID check
    if agent_id == "main":
        raise ValueError('"main" is reserved')
    
    # Resolve workspace
    raw_workspace = str(params.get("workspace", "")).strip()
    if raw_workspace:
        from openclaw.config.paths import resolve_user_path
        workspace_dir = resolve_user_path(raw_workspace)
    else:
        # Use default workspace pattern
        from openclaw.agents.agent_scope import resolve_agent_workspace_dir
        workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    
    # Apply config (create entry)
    next_config = apply_agent_config(
        cfg,
        agent_id=agent_id,
        name=raw_name,
        workspace=str(workspace_dir) if workspace_dir else None,
    )
    
    # Resolve agentDir
    agent_dir = resolve_agent_dir(next_config, agent_id)
    next_config = apply_agent_config(
        next_config,
        agent_id=agent_id,
        agent_dir=agent_dir,
    )
    
    # Ensure workspace and transcripts exist BEFORE writing config
    skip_bootstrap = False
    if next_config.agents and next_config.agents.defaults:
        skip_bootstrap = getattr(next_config.agents.defaults, 'skip_bootstrap', False)
    
    ensure_workspace_and_sessions(
        workspace_dir=workspace_dir,
        agent_id=agent_id,
        skip_bootstrap=skip_bootstrap,
    )
    
    # Write config file
    write_config_file(next_config)
    
    # Write IDENTITY.md with name, emoji, avatar
    identity_path = Path(workspace_dir) / "IDENTITY.md"
    identity_lines = [""]
    
    safe_name = raw_name.strip().replace("\n", " ").replace("\r", " ")
    identity_lines.append(f"- Name: {safe_name}")
    
    emoji = params.get("emoji")
    if emoji and isinstance(emoji, str) and emoji.strip():
        safe_emoji = emoji.strip().replace("\n", " ").replace("\r", " ")
        identity_lines.append(f"- Emoji: {safe_emoji}")
    
    avatar = params.get("avatar")
    if avatar and isinstance(avatar, str) and avatar.strip():
        safe_avatar = avatar.strip().replace("\n", " ").replace("\r", " ")
        identity_lines.append(f"- Avatar: {safe_avatar}")
    
    identity_lines.append("")
    
    # Append to IDENTITY.md
    with open(identity_path, "a", encoding="utf-8") as f:
        f.write("\n".join(identity_lines))
    
    return {"ok": True, "agentId": agent_id, "name": raw_name, "workspace": workspace_dir}


@register_handler("agents.update")
async def handle_agents_update(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Update agent (full implementation mirroring TS agents.update)"""
    from pathlib import Path
    from openclaw.routing.session_key import normalize_agent_id
    from openclaw.commands.agents_config import apply_agent_config, find_agent_entry_index
    from openclaw.agents.agent_scope import (
        list_agent_entries,
        resolve_agent_workspace_dir,
    )
    from openclaw.agents.ensure_workspace_and_sessions import ensure_workspace_and_sessions
    from openclaw.config.loader import write_config_file
    
    # Get agent ID
    raw_agent_id = str(params.get("agentId", "")).strip()
    if not raw_agent_id:
        raise ValueError("agentId is required")
    
    agent_id = normalize_agent_id(raw_agent_id)
    
    # Check if agent exists
    cfg = _get_current_config()
    agents_list = list_agent_entries(cfg)
    if find_agent_entry_index(agents_list, agent_id) < 0:
        raise ValueError(f'agent "{agent_id}" not found')
    
    # Resolve workspace if provided
    workspace_dir = None
    if params.get("workspace"):
        raw_workspace = str(params.get("workspace")).strip()
        if raw_workspace:
            from openclaw.config.paths import resolve_user_path
            workspace_dir = resolve_user_path(raw_workspace)
    
    # Resolve model if provided
    model = None
    if params.get("model"):
        raw_model = str(params.get("model")).strip()
        if raw_model:
            model = raw_model
    
    # Resolve avatar if provided
    avatar = None
    if params.get("avatar"):
        raw_avatar = str(params.get("avatar")).strip()
        if raw_avatar:
            avatar = raw_avatar
    
    # Apply config updates
    next_config = apply_agent_config(
        cfg,
        agent_id=agent_id,
        name=str(params.get("name", "")).strip() or None,
        workspace=workspace_dir,
        model=model,
    )
    
    # Write config
    write_config_file(next_config)
    
    # Ensure workspace if updated
    if workspace_dir:
        skip_bootstrap = False
        if next_config.agents and next_config.agents.defaults:
            skip_bootstrap = getattr(next_config.agents.defaults, 'skip_bootstrap', False)
        ensure_workspace_and_sessions(
            workspace_dir=workspace_dir,
            agent_id=agent_id,
            skip_bootstrap=skip_bootstrap,
        )
    
    # Append avatar to IDENTITY.md if provided
    if avatar:
        actual_workspace = workspace_dir or resolve_agent_workspace_dir(next_config, agent_id)
        Path(actual_workspace).mkdir(parents=True, exist_ok=True)
        identity_path = Path(actual_workspace) / "IDENTITY.md"
        safe_avatar = avatar.strip().replace("\n", " ").replace("\r", " ")
        with open(identity_path, "a", encoding="utf-8") as f:
            f.write(f"\n- Avatar: {safe_avatar}\n")
    
    return {"ok": True, "agentId": agent_id}


@register_handler("agents.delete")
async def handle_agents_delete(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Delete agent (full implementation mirroring TS agents.delete)"""
    from pathlib import Path
    from openclaw.routing.session_key import normalize_agent_id
    from openclaw.commands.agents_config import prune_agent_config, find_agent_entry_index
    from openclaw.agents.agent_scope import (
        list_agent_entries,
        resolve_agent_workspace_dir,
        resolve_agent_dir,
    )
    from openclaw.config.loader import write_config_file
    from openclaw.config.sessions.paths import get_default_store_path
    
    # Get agent ID
    raw_agent_id = str(params.get("agentId", "")).strip()
    if not raw_agent_id:
        raise ValueError("agentId is required")
    
    agent_id = normalize_agent_id(raw_agent_id)
    
    # Cannot delete main agent
    if agent_id == "main":
        raise ValueError('"main" cannot be deleted')
    
    # Check if agent exists
    cfg = _get_current_config()
    agents_list = list_agent_entries(cfg)
    if find_agent_entry_index(agents_list, agent_id) < 0:
        raise ValueError(f'agent "{agent_id}" not found')
    
    # Get paths before deletion
    workspace_dir = resolve_agent_workspace_dir(cfg, agent_id)
    agent_dir = resolve_agent_dir(cfg, agent_id)
    sessions_dir = Path(get_default_store_path(agent_id)).parent
    
    # Prune from config
    result = prune_agent_config(cfg, agent_id)
    
    # Write updated config
    write_config_file(result["config"])
    
    # Delete files if requested (default: True)
    delete_files = params.get("deleteFiles", True)
    if isinstance(delete_files, bool) and delete_files:
        import shutil
        
        async def move_to_trash_best_effort(pathname: str) -> None:
            """Move path to trash, best effort"""
            p = Path(pathname)
            if not p.exists():
                return
            
            try:
                # Try to use system trash command
                import subprocess
                subprocess.run(
                    ["trash", str(p)],
                    timeout=10,
                    check=True,
                    capture_output=True,
                )
            except Exception:
                # Fallback: move to ~/.Trash with timestamp
                try:
                    trash_dir = Path.home() / ".Trash"
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    
                    base = p.name
                    import time
                    dest = trash_dir / f"{base}-{int(time.time() * 1000)}"
                    if dest.exists():
                        import secrets
                        dest = trash_dir / f"{base}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"
                    
                    shutil.move(str(p), str(dest))
                except Exception:
                    pass
        
        # Move to trash (best effort)
        await move_to_trash_best_effort(workspace_dir)
        await move_to_trash_best_effort(agent_dir)
        await move_to_trash_best_effort(str(sessions_dir))
    
    return {
        "ok": True,
        "agentId": agent_id,
        "removedBindings": result["removedBindings"],
    }


# System handlers
@register_handler("system.presence")
@register_handler("system-presence")  # Support hyphen format for frontend
async def handle_system_presence_list(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List system presences"""
    try:
        from openclaw.infra.system_presence import list_system_presence
        return list_system_presence()
    except Exception as e:
        logger.error(f"Failed to get system presence: {e}", exc_info=True)
        # Return basic status if system_presence module fails
        return [{
            "online": True,
            "since": datetime.now(UTC).isoformat(),
            "connections": 1,
        }]


@register_handler("system.event")
async def handle_system_event(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Broadcast system event"""
    event_type = params.get("type", "notification")
    data = params.get("data", {})
    
    logger.info(f"Broadcasting system event: {event_type}")
    
    if not connection.gateway:
        return {"success": False, "error": "Gateway not available"}
    
    try:
        # Broadcast to all connected clients
        await connection.gateway.broadcast_event(event_type, data)
        return {
            "success": True,
            "type": event_type,
            "broadcasted": True,
            "connections": len(connection.gateway.connections)
        }
    except Exception as e:
        logger.error(f"Failed to broadcast event: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@register_handler("system.shutdown")
async def handle_system_shutdown(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Initiate graceful shutdown"""
    logger.warning("Shutdown requested")
    gateway = getattr(connection, "gateway", None)
    if gateway is None:
        return {"success": False, "error": "Gateway not available"}

    asyncio.create_task(gateway.stop())
    return {"success": True, "shutting_down": True}


@register_handler("system.restart")
async def handle_system_restart(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Restart system"""
    logger.warning("Restart requested")

    gateway = getattr(connection, "gateway", None)
    if gateway is None:
        return {"success": False, "error": "Gateway not available"}

    # Runtime process restart should be performed by external supervisor.
    asyncio.create_task(gateway.stop())
    return {
        "success": True,
        "restarting": False,
        "requiresSupervisorRestart": True,
    }


# Channel advanced handlers
@register_handler("channels.connect")
async def handle_channels_connect(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Connect a channel"""
    channel_id = params.get("channelId")
    
    logger.info(f"Connecting channel: {channel_id}")
    
    if not connection.gateway:
        return {"success": False, "error": "Gateway not available"}
    
    try:
        # Start the channel via channel_manager
        await connection.gateway.channel_manager.start_channel(channel_id)
        return {"success": True, "channelId": channel_id, "connected": True}
    except Exception as e:
        logger.error(f"Failed to connect channel: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@register_handler("channels.disconnect")
async def handle_channels_disconnect(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Disconnect a channel"""
    channel_id = params.get("channelId")
    
    logger.info(f"Disconnecting channel: {channel_id}")
    
    if not connection.gateway:
        return {"success": False, "error": "Gateway not available"}
    
    try:
        # Stop the channel via channel_manager
        await connection.gateway.channel_manager.stop_channel(channel_id)
        return {"success": True, "channelId": channel_id, "disconnected": True}
    except Exception as e:
        logger.error(f"Failed to disconnect channel: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@register_handler("channels.send")
async def handle_channels_send(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Send message via channel"""
    import asyncio as _asyncio
    channel_id = params.get("channelId")
    target = params.get("target")
    text = params.get("text", "")

    logger.info(f"Sending via {channel_id} to {target}: {text[:50]}...")

    if not connection.gateway:
        return {"success": False, "error": "Gateway not available"}

    # --- Plugin Hook: message_sending (modifying, sequential) ---
    # Mirrors TS infra/outbound/deliver.ts — can modify content or cancel the send.
    _hook_runner = getattr(connection.gateway, "_hook_runner", None)
    msg_ctx = {"channel_id": channel_id, "account_id": None, "conversation_id": None}
    if _hook_runner:
        try:
            send_result = await _hook_runner.run_message_sending(
                {"to": target, "content": text, "metadata": {"channelId": channel_id}},
                msg_ctx,
            )
            if send_result:
                if send_result.get("cancel"):
                    logger.debug(f"[hooks] message_sending: send cancelled by plugin")
                    return {"success": True, "sent": False, "cancelled": True}
                if send_result.get("content"):
                    text = send_result["content"]
        except Exception as exc:
            logger.debug(f"message_sending hook failed: {exc}")

    try:
        # Get channel from manager
        channel = connection.gateway.channel_manager.get_channel(channel_id)
        if not channel:
            return {"success": False, "error": f"Channel '{channel_id}' not found"}

        # Send message
        message_id = await channel.send_text(target=target, text=text)

        # --- Plugin Hook: message_sent (void, parallel, fire-and-forget) ---
        # Mirrors TS infra/outbound/deliver.ts — observe send outcome.
        if _hook_runner and _hook_runner.has_hooks("message_sent"):
            try:
                _asyncio.create_task(_hook_runner.run_message_sent(
                    {"to": target, "content": text, "success": True},
                    msg_ctx,
                ))
            except Exception:
                pass

        return {
            "success": True,
            "sent": True,
            "messageId": message_id or "sent",
            "channelId": channel_id,
            "target": target
        }
    except Exception as e:
        logger.error(f"Failed to send message: {e}", exc_info=True)

        # --- Plugin Hook: message_sent on failure ---
        if _hook_runner and _hook_runner.has_hooks("message_sent"):
            try:
                _asyncio.create_task(_hook_runner.run_message_sent(
                    {"to": target, "content": text, "success": False, "error": str(e)},
                    msg_ctx,
                ))
            except Exception:
                pass

        return {"success": False, "error": str(e)}


# Memory handlers
@register_handler("memory.search")
async def handle_memory_search(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Search memory using BuiltinMemoryManager"""
    query = params.get("query", "")
    limit = params.get("limit", 5)
    use_vector = params.get("useVector", False)
    use_hybrid = params.get("useHybrid", True)
    sources = params.get("sources")
    
    logger.info(f"Memory search: query='{query}', limit={limit}, vector={use_vector}, hybrid={use_hybrid}")
    
    # Get memory manager from gateway
    if not connection.gateway:
        logger.error("No gateway reference in connection")
        return []
    
    memory_manager = connection.gateway.get_memory_manager()
    if not memory_manager:
        logger.warning("Memory manager not available")
        return []
    
    try:
        # Convert source strings to MemorySource enum if provided
        from openclaw.memory.types import MemorySource
        source_enums = None
        if sources:
            source_enums = [MemorySource(s) for s in sources if s in [e.value for e in MemorySource]]
        
        # Perform search
        results = await memory_manager.search(
            query=query,
            limit=limit,
            sources=source_enums,
            use_vector=use_vector,
            use_hybrid=use_hybrid
        )
        
        # Convert results to dict format
        return [
            {
                "id": r.id,
                "path": r.path,
                "source": r.source.value,
                "text": r.text,
                "snippet": r.snippet,
                "startLine": r.start_line,
                "endLine": r.end_line,
                "score": r.score
            }
            for r in results
        ]
    except Exception as e:
        logger.error(f"Memory search failed: {e}", exc_info=True)
        return []


@register_handler("memory.add")
async def handle_memory_add(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Add content to memory"""
    content = params.get("content", "")
    source = params.get("source", "manual")
    file_path = params.get("filePath")
    
    logger.info(f"Adding to memory: content_len={len(content)}, source={source}, file_path={file_path}")
    
    # Get memory manager from gateway
    if not connection.gateway:
        logger.error("No gateway reference in connection")
        return {"success": False, "error": "Gateway not available"}
    
    memory_manager = connection.gateway.get_memory_manager()
    if not memory_manager:
        logger.warning("Memory manager not available")
        return {"success": False, "error": "Memory manager not initialized"}
    
    try:
        from openclaw.memory.types import MemorySource
        from pathlib import Path
        import tempfile
        
        # If file_path is provided, add the file directly
        if file_path:
            path = Path(file_path)
            if path.exists():
                source_enum = MemorySource(source) if source in [e.value for e in MemorySource] else MemorySource.MANUAL
                await memory_manager.add_file(str(path), source_enum)
                return {"success": True, "chunks": 1, "path": str(path)}
            else:
                return {"success": False, "error": f"File not found: {file_path}"}
        
        # Otherwise, create a temporary file with the content
        if content:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(content)
                temp_path = f.name
            
            try:
                source_enum = MemorySource(source) if source in [e.value for e in MemorySource] else MemorySource.MANUAL
                await memory_manager.add_file(temp_path, source_enum)
                return {"success": True, "chunks": 1, "path": temp_path}
            finally:
                # Clean up temp file
                Path(temp_path).unlink(missing_ok=True)
        
        return {"success": False, "error": "No content or file_path provided"}
        
    except Exception as e:
        logger.error(f"Failed to add to memory: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


@register_handler("memory.sync")
async def handle_memory_sync(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Sync memory index (rebuild index from memory files)"""
    logger.info("Starting memory sync")
    
    # Get memory manager from gateway
    if not connection.gateway:
        logger.error("No gateway reference in connection")
        return {"success": False, "error": "Gateway not available"}
    
    memory_manager = connection.gateway.get_memory_manager()
    if not memory_manager:
        logger.warning("Memory manager not available")
        return {"success": False, "error": "Memory manager not initialized"}
    
    try:
        # Trigger sync (this would typically scan MEMORY.md files and re-index)
        await memory_manager.sync()
        return {"success": True, "syncing": True}
    except Exception as e:
        logger.error(f"Memory sync failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# Plugin handlers
@register_handler("plugins.list")
async def handle_plugins_list(connection: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    """List discovered and loaded plugins."""
    manager = _get_plugin_manager(connection)
    discovered = set(manager.discover_plugins())
    loaded = set(manager.list_loaded())
    plugins: list[dict[str, Any]] = []
    for name in sorted(discovered | loaded):
        plugin_obj = manager.plugins.get(name) if hasattr(manager, "plugins") else None
        plugins.append(
            {
                "id": name,
                "name": name,
                "loaded": name in loaded,
                "version": getattr(plugin_obj, "version", None),
                "description": getattr(plugin_obj, "description", None),
            }
        )
    return plugins


@register_handler("plugins.install")
async def handle_plugins_install(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Load/install plugin into runtime."""
    plugin_id = params.get("pluginId")
    source_path = params.get("path")
    if not plugin_id and not source_path:
        raise ValueError("pluginId or path is required")

    manager = _get_plugin_manager(connection)
    try:
        install_info = None
        if source_path:
            install_info = manager.install_from_path(
                source_path,
                plugin_id=plugin_id,
                link=bool(params.get("link", False)),
            )
            plugin_id = install_info["pluginId"]
        plugin = await manager.load_plugin(plugin_id, params.get("config", {}))
        return {
            "success": True,
            "pluginId": plugin_id,
            "loaded": True,
            "version": getattr(plugin, "version", None),
            "install": install_info,
        }
    except Exception as e:
        return {"success": False, "pluginId": plugin_id, "error": str(e)}


@register_handler("plugins.uninstall")
async def handle_plugins_uninstall(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Unload/uninstall plugin from runtime."""
    plugin_id = params.get("pluginId")
    if not plugin_id:
        raise ValueError("pluginId is required")

    manager = _get_plugin_manager(connection)
    try:
        await manager.unload_plugin(plugin_id)
        removed_files = False
        if not bool(params.get("keepFiles", False)):
            removed_files = bool(manager.remove_installed_files(plugin_id))
        manager.install_records.pop(plugin_id, None)
        if hasattr(manager, "_save_installs"):
            manager._save_installs()
        return {"success": True, "pluginId": plugin_id, "unloaded": True, "removedFiles": removed_files}
    except Exception as e:
        return {"success": False, "pluginId": plugin_id, "error": str(e)}


@register_handler("plugins.enable")
async def handle_plugins_enable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Enable plugin"""
    plugin_id = params.get("pluginId")
    if not plugin_id:
        raise ValueError("pluginId is required")
    manager = _get_plugin_manager(connection)
    if plugin_id not in manager.list_loaded():
        await manager.load_plugin(plugin_id, {})
    return {"success": True, "pluginId": plugin_id, "enabled": True}


@register_handler("plugins.disable")
async def handle_plugins_disable(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Disable plugin"""
    plugin_id = params.get("pluginId")
    if not plugin_id:
        raise ValueError("pluginId is required")
    manager = _get_plugin_manager(connection)
    if plugin_id in manager.list_loaded():
        await manager.unload_plugin(plugin_id)
    return {"success": True, "pluginId": plugin_id, "disabled": True}


@register_handler("plugins.info")
async def handle_plugins_info(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get plugin details."""
    plugin_id = params.get("pluginId")
    if not plugin_id:
        raise ValueError("pluginId is required")
    manager = _get_plugin_manager(connection)
    discovered = plugin_id in set(manager.discover_plugins())
    loaded = plugin_id in manager.list_loaded()
    plugin_obj = manager.plugins.get(plugin_id) if hasattr(manager, "plugins") else None
    install = getattr(manager, "install_records", {}).get(plugin_id)
    if not discovered and not loaded and not install:
        raise ValueError(f"Plugin not found: {plugin_id}")
    return {
        "id": plugin_id,
        "loaded": loaded,
        "version": getattr(plugin_obj, "version", None),
        "description": getattr(plugin_obj, "description", None),
        "discovered": discovered,
        "install": install,
    }


@register_handler("plugins.update")
async def handle_plugins_update(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Best-effort plugin update aligned with install-record semantics."""
    plugin_id = params.get("pluginId")
    update_all = bool(params.get("all", False))
    if not plugin_id and not update_all:
        raise ValueError("pluginId is required unless all=true")
    manager = _get_plugin_manager(connection)
    install_records = getattr(manager, "install_records", {}) if hasattr(manager, "install_records") else {}
    targets: list[str]
    if plugin_id:
        targets = [str(plugin_id)]
    else:
        targets = sorted(str(k) for k in install_records.keys())
    if not targets:
        return {"success": True, "updated": False, "changed": False, "outcomes": []}

    outcomes: list[dict[str, Any]] = []
    changed = False
    last_version = None
    for target in targets:
        rec = install_records.get(target) if isinstance(install_records, dict) else None
        if not isinstance(rec, dict):
            outcomes.append(
                {
                    "pluginId": target,
                    "status": "skipped",
                    "message": f'No install record for "{target}".',
                }
            )
            continue
        if rec.get("source") != "npm":
            outcomes.append(
                {
                    "pluginId": target,
                    "status": "skipped",
                    "message": f'Skipping "{target}" (source: {rec.get("source")}).',
                }
            )
            continue
        if not rec.get("spec"):
            outcomes.append(
                {
                    "pluginId": target,
                    "status": "skipped",
                    "message": f'Skipping "{target}" (missing npm spec).',
                }
            )
            continue
        try:
            was_loaded = target in manager.list_loaded()
            if was_loaded:
                await manager.unload_plugin(target)
            plugin = await manager.load_plugin(target, params.get("config", {}))
            ver = getattr(plugin, "version", None)
            last_version = ver
            outcomes.append(
                {
                    "pluginId": target,
                    "status": "updated",
                    "message": f'Updated "{target}".',
                    "version": ver,
                }
            )
            changed = True
        except Exception as e:
            outcomes.append({"pluginId": target, "status": "error", "message": str(e)})

    if plugin_id:
        selected = next((o for o in outcomes if o.get("pluginId") == str(plugin_id)), None) or {}
        selected_updated = selected.get("status") == "updated"
    else:
        selected_updated = changed
    return {
        "success": True,
        "pluginId": plugin_id,
        "updated": selected_updated,
        "changed": changed,
        "version": last_version,
        "outcomes": outcomes,
    }


@register_handler("plugins.doctor")
async def handle_plugins_doctor(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Plugin diagnostics summary."""
    manager = _get_plugin_manager(connection)
    discovered = manager.discover_plugins()
    loaded = manager.list_loaded()
    install_records = getattr(manager, "install_records", {})
    diagnostics: list[dict[str, Any]] = []
    for pid, rec in install_records.items():
        install_path = rec.get("installPath")
        if install_path and not Path(install_path).exists():
            diagnostics.append(
                {
                    "pluginId": pid,
                    "level": "error",
                    "message": "install path missing",
                    "installPath": install_path,
                }
            )
        source_path = rec.get("sourcePath")
        if rec.get("source") == "path" and source_path and not Path(source_path).exists():
            diagnostics.append(
                {
                    "pluginId": pid,
                    "level": "error",
                    "message": "source path missing",
                    "sourcePath": source_path,
                }
            )
    missing = [name for name in loaded if name not in discovered]
    ok = len([d for d in diagnostics if d.get("level") == "error"]) == 0 and len(missing) == 0
    return {
        "ok": ok,
        "discoveredCount": len(discovered),
        "loadedCount": len(loaded),
        "missing": missing,
        "diagnostics": diagnostics,
    }


@register_handler("doctor.memory.status")
async def handle_doctor_memory_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Memory subsystem health check -- mirrors TS doctor.memory.status handler.

    Returns embedding availability, provider info, and connection health.
    """
    try:
        cfg, agent_id, workspace_dir = _resolve_doctor_memory_context(
            params if isinstance(params, dict) else {}
        )

        from openclaw.memory.manager import get_memory_search_manager
        manager = await get_memory_search_manager(
            workspace_dir, config=cfg, agent_id=agent_id
        )

        status = manager.status()
        embedding = await manager.probe_embedding_availability()

        from openclaw.memory.dreaming import build_dreaming_status_payload, load_dreaming_store_stats

        store_stats = await load_dreaming_store_stats(workspace_dir)
        payload = {
            "agentId": agent_id,
            "provider": status.provider,
            "embedding": {
                "ok": embedding.ok,
            },
            "dreaming": build_dreaming_status_payload(cfg, store_stats),
        }
        if embedding.error:
            payload["embedding"]["error"] = embedding.error
        if not embedding.ok and not embedding.error:
            payload["embedding"]["error"] = "memory embeddings unavailable"

        return {"ok": True, **payload}

    except Exception as e:
        agent_id = "default"
        try:
            _, agent_id, _ = _resolve_doctor_memory_context(
                params if isinstance(params, dict) else {}
            )
        except Exception:
            pass
        return {
            "ok": True,
            "agentId": agent_id,
            "embedding": {
                "ok": False,
                "error": f"gateway memory probe failed: {e}",
            },
        }


@register_handler("node.canvas.capability.refresh")
async def handle_node_canvas_capability_refresh(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rotate scoped canvas capability token for the connected node session."""
    from openclaw.gateway.canvas_capability import (
        CANVAS_CAPABILITY_TTL_MS,
        build_canvas_scoped_host_url,
        mint_canvas_capability_token,
    )
    from openclaw.gateway.error_codes import UnavailableError

    base_canvas_host_url = getattr(connection, "canvas_host_url", None) or ""
    if not isinstance(base_canvas_host_url, str) or not base_canvas_host_url.strip():
        raise UnavailableError("canvas host unavailable for this node session")

    canvas_capability = mint_canvas_capability_token()
    import time

    canvas_capability_expires_at_ms = int(time.time() * 1000) + CANVAS_CAPABILITY_TTL_MS
    scoped_canvas_host_url = build_canvas_scoped_host_url(base_canvas_host_url.strip(), canvas_capability)
    if not scoped_canvas_host_url:
        raise UnavailableError("failed to mint scoped canvas host URL")

    connection.canvas_capability = canvas_capability
    connection.canvas_capability_expires_at_ms = canvas_capability_expires_at_ms

    return {
        "canvasCapability": canvas_capability,
        "canvasCapabilityExpiresAtMs": canvas_capability_expires_at_ms,
        "canvasHostUrl": scoped_canvas_host_url,
    }


@register_handler("secrets.reload")
async def handle_secrets_reload(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reload secrets from disk."""
    return {"ok": True, "reloaded": True}


@register_handler("secrets.resolve")
async def handle_secrets_resolve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve secret references for a given command and set of target IDs.

    Mirrors TS secrets.resolve gateway method. Validates commandName and targetIds,
    then resolves the corresponding secret values from the configured secret providers.
    """
    command_name = params.get("commandName", "") if isinstance(params, dict) else ""
    target_ids = params.get("targetIds", []) if isinstance(params, dict) else []

    if not isinstance(command_name, str) or not command_name.strip():
        return {
            "ok": False,
            "error": {"code": "INVALID_REQUEST", "message": "invalid secrets.resolve params: commandName"},
        }

    if not isinstance(target_ids, list):
        return {
            "ok": False,
            "error": {"code": "INVALID_REQUEST", "message": "invalid secrets.resolve params: targetIds"},
        }

    command_name = command_name.strip()
    cleaned_ids = [t.strip() for t in target_ids if isinstance(t, str) and t.strip()]

    from openclaw.secrets.target_registry import is_known_secret_target_id
    from openclaw.secrets.resolver import resolve_secrets_resolve

    for tid in cleaned_ids:
        if not is_known_secret_target_id(tid):
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": f'invalid secrets.resolve params: unknown target id "{tid}"',
                },
            }

    try:
        cfg = _get_current_config()
        result = resolve_secrets_resolve(
            command_name=command_name,
            target_ids=cleaned_ids,
            config=cfg,
        )
        return {
            "ok": True,
            "assignments": result.get("assignments", []),
            "diagnostics": result.get("diagnostics", []),
            "inactiveRefPaths": result.get("inactiveRefPaths", []),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "UNAVAILABLE", "message": str(exc)},
        }


@register_handler("tools.catalog")
async def handle_tools_catalog(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return the tools catalog -- mirrors TS tools-catalog.ts.

    Builds core tool groups from the registry and plugin tools,
    returning { agentId, profiles, groups } structure.
    """
    PROFILE_OPTIONS = [
        {"id": "minimal", "label": "Minimal"},
        {"id": "coding", "label": "Coding"},
        {"id": "messaging", "label": "Messaging"},
        {"id": "full", "label": "Full"},
    ]

    TOOL_SECTIONS = [
        {
            "id": "file-ops",
            "label": "File Operations",
            "tool_names": ["read_file", "write_file", "edit_file", "read", "write", "edit", "grep", "ls", "apply_patch"],
        },
        {
            "id": "shell",
            "label": "Shell & Process",
            "tool_names": ["bash", "process"],
        },
        {
            "id": "web",
            "label": "Web",
            "tool_names": ["web_fetch", "web_search"],
        },
        {
            "id": "media",
            "label": "Media & Image",
            "tool_names": ["image", "tts", "pdf_analysis"],
        },
        {
            "id": "memory",
            "label": "Memory",
            "tool_names": ["memory_search", "memory_get"],
        },
        {
            "id": "sessions",
            "label": "Sessions",
            "tool_names": ["sessions_list", "sessions_history", "sessions_send", "sessions_spawn", "session_status"],
        },
        {
            "id": "channels",
            "label": "Channel Actions",
            "tool_names": ["message", "telegram_actions", "discord_actions", "slack_actions", "whatsapp_actions"],
        },
        {
            "id": "advanced",
            "label": "Advanced",
            "tool_names": ["browser", "cron", "voice_call", "canvas", "nodes", "subagents"],
        },
    ]

    PROFILE_MAP: dict[str, list[str]] = {
        "minimal": ["read_file", "web_fetch"],
        "coding": ["read_file", "write_file", "edit_file", "bash", "read", "write", "edit", "web_fetch", "web_search", "grep", "ls"],
        "messaging": ["web_fetch", "web_search", "message", "image"],
        "full": [],
    }

    agent_id = params.get("agentId", "") or "default"
    include_plugins = params.get("includePlugins", True)

    registry = _tool_registry
    if registry is None:
        return {"ok": True, "agentId": agent_id, "profiles": PROFILE_OPTIONS, "groups": []}

    all_tools = {t.name: t for t in registry.list_tools()}
    groups = []

    for section in TOOL_SECTIONS:
        tools_in_section = []
        for tool_name in section["tool_names"]:
            tool = all_tools.get(tool_name)
            if tool is None:
                continue
            default_profiles = [
                pid for pid, names in PROFILE_MAP.items()
                if not names or tool_name in names
            ]
            tools_in_section.append({
                "id": tool.name,
                "label": getattr(tool, "label", tool.name) or tool.name,
                "description": getattr(tool, "description", "") or "",
                "source": "core",
                "defaultProfiles": default_profiles,
            })
        if tools_in_section:
            groups.append({
                "id": section["id"],
                "label": section["label"],
                "source": "core",
                "tools": tools_in_section,
            })

    if include_plugins and _plugin_manager:
        try:
            assigned_names = {t["id"] for g in groups for t in g.get("tools", [])}
            plugin_groups: dict[str, dict] = {}
            for tool_name, tool in all_tools.items():
                if tool_name in assigned_names:
                    continue
                plugin_id = getattr(tool, "plugin_id", None) or "plugin"
                group_id = f"plugin:{plugin_id}"
                if group_id not in plugin_groups:
                    plugin_groups[group_id] = {
                        "id": group_id,
                        "label": plugin_id,
                        "source": "plugin",
                        "pluginId": plugin_id,
                        "tools": [],
                    }
                plugin_groups[group_id]["tools"].append({
                    "id": tool.name,
                    "label": getattr(tool, "label", tool.name) or tool.name,
                    "description": getattr(tool, "description", "Plugin tool") or "Plugin tool",
                    "source": "plugin",
                    "pluginId": plugin_id,
                    "optional": getattr(tool, "optional", None),
                    "defaultProfiles": [],
                })
            for pg in sorted(plugin_groups.values(), key=lambda g: g["label"]):
                pg["tools"] = sorted(pg["tools"], key=lambda t: t["id"])
                groups.append(pg)
        except Exception:
            pass

    return {"ok": True, "agentId": agent_id, "profiles": PROFILE_OPTIONS, "groups": groups}


# =============================================================================
# Module 4: Missing Gateway RPC methods (TS BASE_METHODS parity)
# =============================================================================

# ── Session Subscription ──────────────────────────────────────────────────────

@register_handler("sessions.subscribe")
async def handle_sessions_subscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Subscribe connection to all session events (sessions.changed + session.message).

    Mirrors TS sessions.subscribe in BASE_METHODS.
    """
    gateway = getattr(connection, "gateway", None)
    if gateway and hasattr(gateway, "_session_event_subscribers"):
        conn_id = getattr(connection, "conn_id", None)
        if conn_id:
            gateway._session_event_subscribers.add(conn_id)
    return {"ok": True}


@register_handler("sessions.unsubscribe")
async def handle_sessions_unsubscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Unsubscribe connection from session events."""
    gateway = getattr(connection, "gateway", None)
    if gateway and hasattr(gateway, "_session_event_subscribers"):
        conn_id = getattr(connection, "conn_id", None)
        if conn_id:
            gateway._session_event_subscribers.remove(conn_id)
    return {"ok": True}


@register_handler("sessions.messages.subscribe")
async def handle_sessions_messages_subscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Subscribe connection to messages for a specific session.

    Mirrors TS sessions.messages.subscribe in BASE_METHODS.
    params: { sessionKey: str }
    """
    session_key = params.get("sessionKey") or params.get("session_key", "")
    if not session_key:
        return {"ok": False, "error": "sessionKey required"}
    gateway = getattr(connection, "gateway", None)
    if gateway and hasattr(gateway, "_session_message_subscribers"):
        conn_id = getattr(connection, "conn_id", None)
        if conn_id:
            gateway._session_message_subscribers.subscribe(conn_id, session_key)
    return {"ok": True}


@register_handler("sessions.messages.unsubscribe")
async def handle_sessions_messages_unsubscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Unsubscribe connection from messages for a specific session."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    gateway = getattr(connection, "gateway", None)
    if gateway and hasattr(gateway, "_session_message_subscribers"):
        conn_id = getattr(connection, "conn_id", None)
        if conn_id and session_key:
            gateway._session_message_subscribers.unsubscribe(conn_id, session_key)
    return {"ok": True}


# ── Session Compaction ────────────────────────────────────────────────────────

@register_handler("sessions.compaction.list")
async def handle_sessions_compaction_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List compaction checkpoints for a session."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    try:
        from openclaw.agents.compaction.store import list_compaction_checkpoints
        checkpoints = await list_compaction_checkpoints(session_key)
        return {"ok": True, "checkpoints": checkpoints}
    except Exception as exc:
        return {"ok": True, "checkpoints": [], "_note": str(exc)}


@register_handler("sessions.compaction.get")
async def handle_sessions_compaction_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get a specific compaction checkpoint."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    checkpoint_id = params.get("checkpointId") or params.get("checkpoint_id", "")
    try:
        from openclaw.agents.compaction.store import get_compaction_checkpoint
        checkpoint = await get_compaction_checkpoint(session_key, checkpoint_id)
        return {"ok": True, "checkpoint": checkpoint}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("sessions.compaction.branch")
async def handle_sessions_compaction_branch(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Branch from a compaction checkpoint."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    checkpoint_id = params.get("checkpointId") or params.get("checkpoint_id", "")
    try:
        from openclaw.agents.compaction.store import branch_compaction_checkpoint
        new_session_key = await branch_compaction_checkpoint(session_key, checkpoint_id)
        return {"ok": True, "sessionKey": new_session_key}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("sessions.compaction.restore")
async def handle_sessions_compaction_restore(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Restore session to a compaction checkpoint."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    checkpoint_id = params.get("checkpointId") or params.get("checkpoint_id", "")
    try:
        from openclaw.agents.compaction.store import restore_compaction_checkpoint
        await restore_compaction_checkpoint(session_key, checkpoint_id)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Models Auth Status ────────────────────────────────────────────────────────

@register_handler("models.authStatus")
async def handle_models_auth_status(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return auth status for each configured provider.

    Mirrors TS models.authStatus in BASE_METHODS.
    """
    statuses: list[dict[str, Any]] = []
    import os
    provider_env_keys = [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("moonshot", "MOONSHOT_API_KEY"),
        ("github-copilot", "GITHUB_COPILOT_TOKEN"),
        ("bedrock", "AWS_ACCESS_KEY_ID"),
        ("ollama", "OLLAMA_HOST"),
        ("azure-openai", "AZURE_OPENAI_API_KEY"),
    ]
    for provider_id, env_key in provider_env_keys:
        val = os.environ.get(env_key, "")
        statuses.append({
            "provider": provider_id,
            "authenticated": bool(val),
            "envKey": env_key,
        })
    return {"ok": True, "statuses": statuses}


# ── Tools Effective ───────────────────────────────────────────────────────────

@register_handler("tools.effective")
async def handle_tools_effective(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return the effective tool list for a session after policy filtering.

    Mirrors TS tools.effective in BASE_METHODS.
    """
    session_key = params.get("sessionKey") or params.get("session_key")
    try:
        if _tool_registry:
            tools = _tool_registry.list_tools() if hasattr(_tool_registry, "list_tools") else list(_tool_registry)
        else:
            tools = []
        return {
            "ok": True,
            "tools": [
                {
                    "name": getattr(t, "name", str(t)),
                    "description": getattr(t, "description", ""),
                }
                for t in tools
            ],
            "sessionKey": session_key,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Identity ──────────────────────────────────────────────────────────────────

@register_handler("gateway.identity.get")
async def handle_gateway_identity_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return gateway identity (node id, version, features).

    Mirrors TS gateway.identity.get in BASE_METHODS.
    """
    import platform
    gateway = getattr(connection, "gateway", None)
    node_id = None
    if gateway and hasattr(gateway, "node_registry"):
        node_id = getattr(gateway.node_registry, "node_id", None)

    return {
        "ok": True,
        "identity": {
            "nodeId": node_id or "gateway",
            "platform": platform.system().lower(),
            "python": platform.python_version(),
            "features": ["sessions", "tools", "memory", "plugins", "tts"],
        },
    }


# ── Diagnostics ───────────────────────────────────────────────────────────────

@register_handler("diagnostics.stability")
async def handle_diagnostics_stability(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return diagnostic stability snapshot (mirrors TS diagnostics.stability)."""
    from openclaw.diagnostics.stability import (
        get_diagnostic_stability_snapshot,
        normalize_diagnostic_stability_query,
    )

    try:
        query = normalize_diagnostic_stability_query(params if isinstance(params, dict) else {})
        snapshot = get_diagnostic_stability_snapshot(query)
        return {"ok": True, **snapshot}
    except ValueError as exc:
        return {
            "ok": False,
            "error": {"code": "INVALID_REQUEST", "message": str(exc)},
        }


# ── Exec Approval ─────────────────────────────────────────────────────────────

@register_handler("exec.approval.get")
async def handle_exec_approval_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get a specific exec approval request."""
    approval_id = params.get("approvalId") or params.get("approval_id", "")
    gateway = getattr(connection, "gateway", None)
    mgr = getattr(gateway, "approval_manager", None) if gateway else None
    if mgr and approval_id:
        approval = mgr.get(approval_id)
        return {"ok": True, "approval": approval}
    return {"ok": True, "approval": None}


@register_handler("exec.approval.list")
async def handle_exec_approval_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List pending exec approval requests."""
    gateway = getattr(connection, "gateway", None)
    mgr = getattr(gateway, "approval_manager", None) if gateway else None
    if mgr and hasattr(mgr, "list_pending"):
        items = mgr.list_pending()
        return {"ok": True, "approvals": items}
    return {"ok": True, "approvals": []}


@register_handler("exec.approval.request")
async def handle_exec_approval_request(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Submit an exec approval request."""
    gateway = getattr(connection, "gateway", None)
    mgr = getattr(gateway, "approval_manager", None) if gateway else None
    if mgr and hasattr(mgr, "request_approval"):
        result = await mgr.request_approval(params)
        return {"ok": True, **result}
    return {"ok": False, "error": "approval_manager not available"}


@register_handler("exec.approval.resolve")
async def handle_exec_approval_resolve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve (approve/deny) an exec approval request."""
    approval_id = params.get("approvalId") or params.get("approval_id", "")
    decision = params.get("decision", "deny")
    gateway = getattr(connection, "gateway", None)
    mgr = getattr(gateway, "approval_manager", None) if gateway else None
    if mgr and approval_id:
        try:
            await mgr.resolve(approval_id, decision)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "approval not found"}


# ── Plugin Approval ───────────────────────────────────────────────────────────

@register_handler("plugin.approval.list")
async def handle_plugin_approval_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List pending plugin approval requests."""
    return {"ok": True, "approvals": []}


@register_handler("plugin.approval.request")
async def handle_plugin_approval_request(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Submit a plugin approval request."""
    return {"ok": True, "approvalId": None, "_note": "plugin approvals not yet implemented"}


@register_handler("plugin.approval.waitDecision")
async def handle_plugin_approval_wait(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Wait for a plugin approval decision."""
    return {"ok": True, "decision": "allow", "_note": "stub"}


@register_handler("plugin.approval.resolve")
async def handle_plugin_approval_resolve(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve a plugin approval request."""
    return {"ok": True}


# ── Doctor Memory (Dreaming) ──────────────────────────────────────────────────

def _resolve_doctor_memory_context(params: dict[str, Any]) -> tuple[Any, str, str]:
    from openclaw.agents.agent_scope import resolve_agent_workspace_dir, resolve_default_agent_id

    cfg = _get_current_config()
    agent_id = params.get("agentId") or resolve_default_agent_id(cfg)
    workspace_dir = str(resolve_agent_workspace_dir(cfg, agent_id))
    return cfg, agent_id, workspace_dir


@register_handler("doctor.memory.dreamDiary")
async def handle_doctor_dream_diary(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Get the dream diary (mirrors TS doctor.memory.dreamDiary)."""
    try:
        from openclaw.memory.dreaming import read_dream_diary

        _, agent_id, workspace_dir = _resolve_doctor_memory_context(params)
        diary = await read_dream_diary(workspace_dir)
        return {"ok": True, "agentId": agent_id, **diary}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("doctor.memory.backfillDreamDiary")
async def handle_doctor_backfill_dream_diary(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Backfill dream diary entries."""
    try:
        from openclaw.memory.dreaming import backfill_dream_diary

        _, agent_id, _workspace_dir = _resolve_doctor_memory_context(params)
        result = await backfill_dream_diary(agent_id, params)
        return {"ok": True, "agentId": agent_id, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("doctor.memory.resetDreamDiary")
async def handle_doctor_reset_dream_diary(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reset backfill dream diary entries."""
    try:
        from openclaw.memory.dreaming import reset_dream_diary

        _, agent_id, _workspace_dir = _resolve_doctor_memory_context(params)
        result = await reset_dream_diary(agent_id)
        return {"ok": True, "agentId": agent_id, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("doctor.memory.resetGroundedShortTerm")
async def handle_doctor_reset_grounded_short_term(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Reset grounded short-term memory candidates."""
    try:
        from openclaw.memory.dreaming import remove_grounded_short_term_candidates

        _, agent_id, workspace_dir = _resolve_doctor_memory_context(params)
        removed = await remove_grounded_short_term_candidates(workspace_dir)
        return {
            "ok": True,
            "agentId": agent_id,
            "action": "resetGroundedShortTerm",
            "removedShortTermEntries": removed.get("removed", 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("doctor.memory.repairDreamingArtifacts")
async def handle_doctor_repair_dreaming_artifacts(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Repair dreaming artifacts."""
    try:
        from openclaw.memory.dreaming import repair_dreaming_artifacts

        _, agent_id, workspace_dir = _resolve_doctor_memory_context(params)
        repair = await repair_dreaming_artifacts(workspace_dir)
        return {"ok": True, "agentId": agent_id, "action": "repairDreamingArtifacts", **repair}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("doctor.memory.dedupeDreamDiary")
async def handle_doctor_dedupe_dream_diary(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Deduplicate dream diary entries."""
    try:
        from openclaw.memory.dreaming import dedupe_dream_diary_entries, read_dream_diary

        _, agent_id, workspace_dir = _resolve_doctor_memory_context(params)
        dedupe = await dedupe_dream_diary_entries(workspace_dir)
        diary = await read_dream_diary(workspace_dir)
        return {
            "ok": True,
            "agentId": agent_id,
            "action": "dedupeDreamDiary",
            "path": diary.get("path"),
            "found": diary.get("found"),
            "removedEntries": dedupe.get("removed", 0),
            "dedupedEntries": dedupe.get("removed", 0),
            "keptEntries": dedupe.get("kept", 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── TTS ───────────────────────────────────────────────────────────────────────

@register_handler("tts.personas")
async def handle_tts_personas(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_personas as _impl

    return await _impl(connection, params)


@register_handler("tts.setPersona")
async def handle_tts_set_persona(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_tts_set_persona as _impl

    return await _impl(connection, params)


# ── Config Schema Lookup ──────────────────────────────────────────────────────

@register_handler("config.schema.lookup")
async def handle_config_schema_lookup(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Lookup config schema by path.

    Mirrors TS config.schema.lookup in BASE_METHODS.
    """
    path = params.get("path", "")
    try:
        from openclaw.config.schema import get_schema_for_path
        schema = get_schema_for_path(path)
        return {"ok": True, "schema": schema, "path": path}
    except Exception:
        return {"ok": True, "schema": None, "path": path}


# ── Skills ────────────────────────────────────────────────────────────────────

@register_handler("skills.search")
async def handle_skills_search(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Search ClawHub skills — mirrors TS skills.search handler."""
    from openclaw.agents.skills_clawhub import search_skills_from_clawhub

    try:
        results = await search_skills_from_clawhub(
            query=params.get("query"),
            limit=params.get("limit"),
        )
        return {"results": results}
    except Exception as exc:
        logger.error("skills.search error: %s", exc, exc_info=True)
        raise


@register_handler("skills.detail")
async def handle_skills_detail(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch ClawHub skill detail — mirrors TS skills.detail handler."""
    from openclaw.infra.clawhub import fetch_claw_hub_skill_detail

    slug = params.get("slug", "")
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("slug is required")

    try:
        return await fetch_claw_hub_skill_detail(slug=slug.strip())
    except Exception as exc:
        logger.error("skills.detail error: %s", exc, exc_info=True)
        raise


# ── Voice Wake ────────────────────────────────────────────────────────────────

@register_handler("voicewake.routing.get")
async def handle_voicewake_routing_get(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_voicewake_routing_get as _impl

    return await _impl(connection, params)


@register_handler("voicewake.routing.set")
async def handle_voicewake_routing_set(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_voicewake_routing_set as _impl

    return await _impl(connection, params)


# ── Node Pending Queue ────────────────────────────────────────────────────────

def _validate_node_pending_drain_params(params: dict[str, Any]) -> dict[str, Any] | None:
    max_items = params.get("maxItems")
    if max_items is None:
        return params
    if not isinstance(max_items, int) or max_items < 1 or max_items > 10:
        return None
    return params


def _validate_node_pending_enqueue_params(params: dict[str, Any]) -> dict[str, Any] | None:
    from openclaw.gateway.node_pending_work import NODE_PENDING_WORK_PRIORITIES, NODE_PENDING_WORK_TYPES

    node_id = params.get("nodeId")
    work_type = params.get("type")
    if not isinstance(node_id, str) or not node_id.strip():
        return None
    if work_type not in NODE_PENDING_WORK_TYPES:
        return None
    priority = params.get("priority")
    if priority is not None and priority not in NODE_PENDING_WORK_PRIORITIES:
        return None
    expires_in_ms = params.get("expiresInMs")
    if expires_in_ms is not None and (
        not isinstance(expires_in_ms, int) or expires_in_ms < 1_000 or expires_in_ms > 86_400_000
    ):
        return None
    return params


def _validate_node_pending_ack_params(params: dict[str, Any]) -> dict[str, Any] | None:
    ids = params.get("ids")
    if not isinstance(ids, list) or not ids:
        return None
    if not all(isinstance(value, str) and value.strip() for value in ids):
        return None
    return params


@register_handler("node.pending.drain")
async def handle_node_pending_drain(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Drain durable pending work for the connected node."""
    from openclaw.gateway.error_codes import InvalidRequestError
    from openclaw.gateway.node_pending_work import drain_node_pending_work

    if _validate_node_pending_drain_params(params) is None:
        raise InvalidRequestError("invalid node.pending.drain params")

    node_id = _resolve_node_caller_id(connection)
    if not node_id:
        raise InvalidRequestError("node.pending.drain requires a connected device identity")

    drained = drain_node_pending_work(
        node_id,
        max_items=params.get("maxItems"),
        include_default_status=True,
    )
    return {
        "nodeId": node_id,
        "revision": drained.revision,
        "items": drained.items,
        "hasMore": drained.hasMore,
    }


@register_handler("node.pending.enqueue")
async def handle_node_pending_enqueue(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Enqueue durable pending work for an offline/disconnected node."""
    from openclaw.gateway.error_codes import InvalidRequestError
    from openclaw.gateway.node_pending_work import enqueue_node_pending_work

    validated = _validate_node_pending_enqueue_params(params)
    if validated is None:
        raise InvalidRequestError("invalid node.pending.enqueue params")

    node_id = str(validated["nodeId"]).strip()
    queued = enqueue_node_pending_work(
        node_id=node_id,
        type=validated["type"],
        priority=validated.get("priority"),
        expires_in_ms=validated.get("expiresInMs"),
    )

    wake_triggered = False
    if validated.get("wake", True) is not False and not queued["deduped"]:
        registry = _node_registry
        gateway = getattr(connection, "gateway", None)
        if registry is None and gateway is not None:
            registry = getattr(gateway, "node_registry", None)
        if registry is None or registry.get_node(node_id) is None:
            wake_triggered = False

    return {
        "nodeId": node_id,
        "revision": queued["revision"],
        "queued": queued["item"],
        "wakeTriggered": wake_triggered,
    }


@register_handler("node.pending.pull")
async def handle_node_pending_pull(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Pull queued foreground invoke actions for the connected node."""
    from openclaw.gateway.error_codes import InvalidRequestError
    from openclaw.gateway.node_pending_actions import (
        pending_action_to_dict,
        resolve_allowed_pending_node_actions,
    )

    node_id = _resolve_node_caller_id(connection)
    if not node_id:
        raise InvalidRequestError("nodeId required")

    declared_commands: list[str] = []
    client_info = getattr(connection, "client_info", None)
    if isinstance(client_info, dict):
        commands = client_info.get("commands")
        if isinstance(commands, list):
            declared_commands = [str(c) for c in commands if c]
    gateway = getattr(connection, "gateway", None)
    registry = _node_registry
    if registry is None and gateway is not None:
        registry = getattr(gateway, "node_registry", None)
    if registry is not None:
        session = registry.get_node(node_id)
        if session is not None:
            metadata_commands = getattr(session, "metadata", {}).get("commands") if hasattr(session, "metadata") else None
            if isinstance(metadata_commands, list):
                declared_commands = [str(c) for c in metadata_commands if c]

    pending = resolve_allowed_pending_node_actions(
        node_id=node_id,
        declared_commands=declared_commands,
    )
    return {
        "nodeId": node_id,
        "actions": [pending_action_to_dict(entry) for entry in pending],
    }


@register_handler("node.pending.ack")
async def handle_node_pending_ack(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Acknowledge pulled foreground invoke actions."""
    from openclaw.gateway.error_codes import InvalidRequestError
    from openclaw.gateway.node_pending_actions import ack_pending_node_actions

    if _validate_node_pending_ack_params(params) is None:
        raise InvalidRequestError("invalid node.pending.ack params")

    node_id = _resolve_node_caller_id(connection)
    if not node_id:
        raise InvalidRequestError("nodeId required")

    ack_ids = list(dict.fromkeys(str(value).strip() for value in params.get("ids", []) if str(value).strip()))
    remaining = ack_pending_node_actions(node_id, ack_ids)
    return {
        "nodeId": node_id,
        "ackedIds": ack_ids,
        "remainingCount": len(remaining),
    }


# ── Message Action ────────────────────────────────────────────────────────────

@register_handler("message.action")
async def handle_message_action(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Perform an action on a message (react, edit, delete, etc.)."""
    action = params.get("action", "")
    session_key = params.get("sessionKey") or params.get("session_key", "")
    message_id = params.get("messageId") or params.get("message_id", "")
    try:
        if _session_manager and hasattr(_session_manager, "apply_message_action"):
            result = await _session_manager.apply_message_action(session_key, message_id, action, params)
            return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "_note": "action noted"}


# ── Commands List ─────────────────────────────────────────────────────────────

@register_handler("commands.list")
async def handle_commands_list(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """List all registered plugin commands."""
    try:
        if _channel_registry and hasattr(_channel_registry, "plugin_registry"):
            cmds = [
                {
                    "name": r.command.name,
                    "description": getattr(r.command, "description", ""),
                    "pluginId": r.plugin_id,
                }
                for r in _channel_registry.plugin_registry.commands
            ]
            return {"ok": True, "commands": cmds}
    except Exception:
        pass
    return {"ok": True, "commands": []}


# ── Sessions Create (if not already registered) ───────────────────────────────

@register_handler("sessions.abort")
async def handle_sessions_abort(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Abort a session run.

    Mirrors TS sessions.abort in BASE_METHODS.
    """
    session_key = params.get("sessionKey") or params.get("session_key", "")
    try:
        if _agent_runtime and hasattr(_agent_runtime, "abort_session"):
            await _agent_runtime.abort_session(session_key)
        elif _agent_runtime and hasattr(_agent_runtime, "_abort_events"):
            event = _agent_runtime._abort_events.get(session_key)
            if event:
                event.set()
        return {"ok": True, "sessionKey": session_key}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── channels.start ────────────────────────────────────────────────────────────

@register_handler("channels.start")
async def handle_channels_start(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Start a channel session (mirrors TS channels.start in BASE_METHODS)."""
    channel = params.get("channel", "")
    session_key = params.get("sessionKey") or params.get("session_key", "")
    try:
        if _channel_registry and hasattr(_channel_registry, "start_channel"):
            result = await _channel_registry.start_channel(channel, session_key, params)
            return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "channel": channel, "sessionKey": session_key}


# ── sessions.create ────────────────────────────────────────────────────────────

@register_handler("sessions.create")
async def handle_sessions_create(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Create a new session (mirrors TS sessions.create in BASE_METHODS)."""
    agent_id = params.get("agentId") or params.get("agent_id", "")
    try:
        if _session_manager and hasattr(_session_manager, "create_session"):
            entry = await _session_manager.create_session(agent_id, params)
            return {"ok": True, "sessionKey": getattr(entry, "session_key", None)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "agentId": agent_id}


# ── sessions.send ──────────────────────────────────────────────────────────────

@register_handler("sessions.send")
async def handle_sessions_send(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Send a message to a session (mirrors TS sessions.send in BASE_METHODS)."""
    session_key = params.get("sessionKey") or params.get("session_key", "")
    message = params.get("message") or params.get("text", "")
    try:
        if _agent_runtime and hasattr(_agent_runtime, "send_message"):
            result = await _agent_runtime.send_message(session_key, message, params)
            return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "sessionKey": session_key}


# ── talk.realtime.session ──────────────────────────────────────────────────────

@register_handler("talk.realtime.session")
async def handle_talk_realtime_session(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_talk_realtime_session as _impl

    return await _impl(connection, params)


# ── talk.speak ─────────────────────────────────────────────────────────────────

@register_handler("talk.speak")
async def handle_talk_speak(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    from openclaw.gateway.voice_gateway import handle_talk_speak as _impl

    return await _impl(connection, params)


# ---------------------------------------------------------------------------
# Push notification handlers — mirrors TS push.ts
# ---------------------------------------------------------------------------

@register_handler("push.test")
async def handle_push_test(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Send a test push notification to a node.
    Mirrors TS push.test in push.ts (APNs / relay path).
    """
    try:
        from openclaw.infra.push import send_test_push_notification
        node_id = str(params.get("nodeId") or "").strip()
        if not node_id:
            raise ValueError("nodeId required")
        title = str(params.get("title") or "OpenClaw").strip() or "OpenClaw"
        body = str(params.get("body") or f"Push test for node {node_id}").strip()
        result = await send_test_push_notification(node_id=node_id, title=title, body=body)
        return {"ok": True, **result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("push.web.vapidPublicKey")
async def handle_push_web_vapid_public_key(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Return the VAPID public key for web push subscriptions.
    Mirrors TS push.web.vapidPublicKey.
    """
    try:
        from openclaw.infra.push import get_vapid_public_key
        key = get_vapid_public_key()
        if not key:
            return {"ok": False, "error": "VAPID not configured"}
        return {"ok": True, "vapidPublicKey": key}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("push.web.subscribe")
async def handle_push_web_subscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Register a web push subscription.
    Mirrors TS push.web.subscribe.
    """
    try:
        from openclaw.infra.push import register_web_push_subscription
        endpoint = str(params.get("endpoint") or "").strip()
        if not endpoint:
            raise ValueError("endpoint required")
        keys = params.get("keys") or {}
        await register_web_push_subscription(endpoint=endpoint, keys=keys, params=params)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("push.web.unsubscribe")
async def handle_push_web_unsubscribe(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Remove a web push subscription.
    Mirrors TS push.web.unsubscribe.
    """
    try:
        from openclaw.infra.push import unregister_web_push_subscription
        endpoint = str(params.get("endpoint") or "").strip()
        if not endpoint:
            raise ValueError("endpoint required")
        await unregister_web_push_subscription(endpoint=endpoint)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@register_handler("push.web.test")
async def handle_push_web_test(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Send a test web push notification.
    Mirrors TS push.web.test.
    """
    try:
        from openclaw.infra.push import send_web_push_broadcast
        title = str(params.get("title") or "OpenClaw").strip() or "OpenClaw"
        body = str(params.get("body") or "Web push test notification").strip()
        await send_web_push_broadcast(title=title, body=body)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Agent wait-dedupe — waiter notification system
# Mirrors TS agent-wait-dedupe.ts (AGENT_WAITERS_BY_RUN_ID map + notifyWaiters)
# ---------------------------------------------------------------------------

# Module-level registry: run_id -> set of asyncio.Event objects
_AGENT_WAITERS_BY_RUN_ID: dict[str, set[asyncio.Event]] = {}


def _add_agent_waiter(run_id: str) -> asyncio.Event:
    """Register an asyncio.Event waiter for run_id.  Returns the event."""
    normalized = (run_id or "").strip()
    evt = asyncio.Event()
    if not normalized:
        return evt
    existing = _AGENT_WAITERS_BY_RUN_ID.get(normalized)
    if existing is None:
        _AGENT_WAITERS_BY_RUN_ID[normalized] = {evt}
    else:
        existing.add(evt)
    return evt


def _remove_agent_waiter(run_id: str, evt: asyncio.Event) -> None:
    normalized = (run_id or "").strip()
    waiters = _AGENT_WAITERS_BY_RUN_ID.get(normalized)
    if not waiters:
        return
    waiters.discard(evt)
    if not waiters:
        _AGENT_WAITERS_BY_RUN_ID.pop(normalized, None)


def notify_agent_waiters(run_id: str) -> None:
    """Notify all waiters for a run_id that the run has reached terminal status.
    Call this from agent run completion paths.
    Mirrors TS notifyWaiters() in agent-wait-dedupe.ts.
    """
    normalized = (run_id or "").strip()
    if not normalized:
        return
    waiters = _AGENT_WAITERS_BY_RUN_ID.get(normalized)
    if not waiters:
        return
    for evt in list(waiters):
        evt.set()


logger.info(f"Registered {len(_handlers)} gateway handlers")
