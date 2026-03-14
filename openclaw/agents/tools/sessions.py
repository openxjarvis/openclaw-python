"""Session management tools"""

import logging
from typing import Any

from ..session import SessionManager
from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class SessionsListTool(AgentTool):
    """List all sessions with access control"""

    def __init__(
        self,
        session_manager: SessionManager,
        current_session_key: str | None = None,
        cfg: Any = None,
    ):
        super().__init__()
        self.name = "sessions_list"
        self.description = "List other sessions (incl. sub-agents) with filters. Access control applies based on agent-to-agent policy and session visibility settings."
        self.session_manager = session_manager
        self.current_session_key = current_session_key
        self.cfg = cfg

    def get_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """List sessions with visibility guard"""
        try:
            # Create visibility guard
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(self.cfg or {})
            visibility = resolve_session_tools_visibility(self.cfg or {})
            
            guard = await create_session_visibility_guard(
                action="list",
                requester_session_key=self.current_session_key or "main",
                visibility=visibility,
                a2a_policy=a2a_policy,
            )
            
            check = guard["check"]
            
            # List all sessions
            session_ids = self.session_manager.list_sessions()
            
            # Filter by access control
            accessible_sessions = []
            for session_id in session_ids:
                result = check(session_id)
                if result.allowed:
                    session = self.session_manager.get_session(session_id)
                    accessible_sessions.append({
                        "session_id": session_id,
                        "message_count": len(session.messages),
                        "last_message": (
                            session.messages[-1].timestamp if session.messages else None
                        ),
                    })
            
            # Format output
            if accessible_sessions:
                output = f"Found {len(accessible_sessions)} accessible session(s):\n\n"
                for info in accessible_sessions:
                    output += f"- **{info['session_id']}**: {info['message_count']} messages"
                    if info["last_message"]:
                        output += f" (last: {info['last_message']})"
                    output += "\n"
            else:
                output = "No accessible sessions found"

            return ToolResult(
                success=True,
                content=output,
                metadata={"sessions": accessible_sessions}
            )

        except Exception as e:
            logger.error(f"Sessions list error: {e}", exc_info=True)
            return ToolResult(success=False, content="", error=str(e))


class SessionsHistoryTool(AgentTool):
    """Get session history with access control"""

    def __init__(
        self,
        session_manager: SessionManager,
        current_session_key: str | None = None,
        cfg: Any = None,
    ):
        super().__init__()
        self.name = "sessions_history"
        self.description = "Fetch history for another session/sub-agent. Use session_id to identify the target. Access control applies."
        self.session_manager = session_manager
        self.current_session_key = current_session_key
        self.cfg = cfg

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID to get history from"},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of messages to return",
                    "default": 50,
                },
            },
            "required": ["session_id"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Get session history with access control"""
        session_id = params.get("session_id", "")
        limit = params.get("limit", 50)

        if not session_id:
            return ToolResult(success=False, content="", error="session_id required")

        try:
            # Create visibility guard
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(self.cfg or {})
            visibility = resolve_session_tools_visibility(self.cfg or {})
            
            guard = await create_session_visibility_guard(
                action="history",
                requester_session_key=self.current_session_key or "main",
                visibility=visibility,
                a2a_policy=a2a_policy,
            )
            
            check = guard["check"]
            
            # Check access
            access_result = check(session_id)
            if not access_result.allowed:
                return ToolResult(
                    success=False,
                    content="",
                    error=access_result.error or "Access denied",
                )
            
            # Get session history
            session = self.session_manager.get_session(session_id)
            messages = session.get_messages(limit=limit)

            if not messages:
                return ToolResult(
                    success=True,
                    content=f"No messages in session '{session_id}'",
                    metadata={"session_id": session_id, "count": 0},
                )

            # Format messages
            output = f"Session '{session_id}' history ({len(messages)} messages):\n\n"
            for msg in messages:
                output += f"**{msg.role.upper()}** ({msg.timestamp}):\n{msg.content}\n\n"

            return ToolResult(
                success=True,
                content=output,
                metadata={"session_id": session_id, "count": len(messages)},
            )

        except Exception as e:
            logger.error(f"Sessions history error: {e}", exc_info=True)
            return ToolResult(success=False, content="", error=str(e))


class SessionsSendTool(AgentTool):
    """Send message to another session with access control"""

    def __init__(
        self,
        session_manager: SessionManager,
        current_session_key: str | None = None,
        cfg: Any = None,
    ):
        super().__init__()
        self.name = "sessions_send"
        self.description = "Send a message to another session/sub-agent. Use session_id to identify the target. Access control and agent-to-agent policy apply."
        self.session_manager = session_manager
        self.current_session_key = current_session_key
        self.cfg = cfg

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Target session ID"},
                "message": {"type": "string", "description": "Message content to send"},
                "from_session": {
                    "type": "string",
                    "description": "Source session ID",
                    "default": "system",
                },
            },
            "required": ["session_id", "message"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Send message to session with access control"""
        session_id = params.get("session_id", "")
        message = params.get("message", "")
        from_session = params.get("from_session", self.current_session_key or "system")

        if not session_id or not message:
            return ToolResult(success=False, content="", error="session_id and message required")

        try:
            # Create visibility guard
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(self.cfg or {})
            visibility = resolve_session_tools_visibility(self.cfg or {})
            
            guard = await create_session_visibility_guard(
                action="send",
                requester_session_key=self.current_session_key or "main",
                visibility=visibility,
                a2a_policy=a2a_policy,
            )
            
            check = guard["check"]
            
            # Check access
            access_result = check(session_id)
            if not access_result.allowed:
                return ToolResult(
                    success=False,
                    content="",
                    error=access_result.error or "Access denied",
                )
            
            # Get target session
            session = self.session_manager.get_session(session_id)

            # Add message as user message with metadata
            prefix = f"[From {from_session}] "
            session.add_user_message(prefix + message)

            return ToolResult(
                success=True,
                content=f"Message sent to session '{session_id}'",
                metadata={"session_id": session_id, "from_session": from_session},
            )

        except Exception as e:
            logger.error(f"Sessions send error: {e}", exc_info=True)
            return ToolResult(success=False, content="", error=str(e))


class SessionsSpawnTool(AgentTool):
    """Spawn a subagent session to run a task.

    Mirrors TS createSessionsSpawnTool() in sessions-spawn-tool.ts.
    Schema fields align with TS: task, label, agentId, model, thinking,
    runTimeoutSeconds, cleanup.
    """

    def __init__(self, session_manager: SessionManager | None = None, gateway: Any = None):
        super().__init__()
        self.name = "sessions_spawn"
        self.description = (
            "Spawn a subagent session to work on a task asynchronously. "
            "Returns a session key you can use to check status via sessions_status."
        )
        self.session_manager = session_manager
        self.gateway = gateway

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to work on.",
                },
                "label": {
                    "type": "string",
                    "description": "Human-readable label for the spawned session.",
                },
                "agentId": {
                    "type": "string",
                    "description": "Agent ID to use. Defaults to the current agent.",
                },
                "model": {
                    "type": "string",
                    "description": "Model override for the spawned session (e.g. 'anthropic:claude-sonnet-4').",
                },
                "thinking": {
                    "type": "string",
                    "description": "Thinking/reasoning mode override ('auto', 'none', or a number of tokens).",
                },
                "runTimeoutSeconds": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Timeout in seconds for the spawned run (default: 600).",
                },
                "cleanup": {
                    "type": "string",
                    "enum": ["delete", "keep"],
                    "description": "Whether to delete the session after the run completes. Default: 'keep'.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["run", "session"],
                    "description": "Spawn mode: 'run' (default, fire-and-forget) or 'session' (thread-bound, stays active).",
                },
                "thread": {
                    "type": "boolean",
                    "description": "Bind spawn to a thread (only for mode='session'). Default: false.",
                },
                "attachments": {
                    "type": "array",
                    "description": "Files to attach to the spawned session (inline content)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Filename for the attachment"
                            },
                            "content": {
                                "type": "string",
                                "description": "File content (utf8 text or base64)"
                            },
                            "encoding": {
                                "type": "string",
                                "enum": ["utf8", "base64"],
                                "description": "Content encoding (default: utf8)"
                            },
                            "mimeType": {
                                "type": "string",
                                "description": "MIME type of the file (optional)"
                            }
                        },
                        "required": ["name", "content"]
                    },
                    "maxItems": 50
                },
                "attachAs": {
                    "type": "object",
                    "description": "Attachment configuration",
                    "properties": {
                        "mountPath": {
                            "type": "string",
                            "description": "Directory path where attachments will be mounted (default: /workspace/attachments)"
                        }
                    }
                },
            },
            "required": ["task"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Spawn subagent session."""
        task = params.get("task")
        if not isinstance(task, str) or not task.strip():
            return ToolResult(success=False, content="", error="'task' is required")

        label = params.get("label") or ""
        agent_id = params.get("agentId")
        model = params.get("model")
        thinking = params.get("thinking")
        cleanup = params.get("cleanup", "keep")
        if cleanup not in ("delete", "keep"):
            cleanup = "keep"
        mode = params.get("mode", "run")
        if mode not in ("run", "session"):
            mode = "run"
        thread = bool(params.get("thread", False))
        run_timeout_seconds = params.get("runTimeoutSeconds")
        if run_timeout_seconds is not None:
            try:
                run_timeout_seconds = max(0, float(run_timeout_seconds))
            except (TypeError, ValueError):
                run_timeout_seconds = None
        
        # Parse attachments (matches TS sessions-spawn-tool.ts lines 112-119)
        attachments = params.get("attachments", [])
        attach_as = params.get("attachAs", {})
        mount_path = attach_as.get("mountPath", "attachments")

        # Resolve hook_runner from gateway or agent runtime
        hook_runner = None
        if self.gateway is not None:
            hook_runner = getattr(self.gateway, "_hook_runner", None)
        if hook_runner is None and self.session_manager is not None:
            hook_runner = getattr(self.session_manager, "_hook_runner", None)

        # Parent context for hooks
        parent_agent_id = agent_id
        parent_session_key: str | None = None
        if self.session_manager is not None and hasattr(self.session_manager, "current_session_key"):
            parent_session_key = self.session_manager.current_session_key

        spawning_event = {
            "task": task.strip(),
            "label": label or None,
            "agent_id": agent_id,
            "model": model,
        }
        spawning_ctx = {
            "agent_id": agent_id,
            "parent_agent_id": parent_agent_id,
            "parent_session_key": parent_session_key,
        }

        # Fire subagent_spawning hook (modifying — can block spawn)
        if hook_runner is not None and hook_runner.has_hooks("subagent_spawning"):
            try:
                spawning_result = await hook_runner.run_subagent_spawning(spawning_event, spawning_ctx)
                if isinstance(spawning_result, dict) and spawning_result.get("status") == "error":
                    error_msg = spawning_result.get("error") or "Subagent spawn blocked by plugin"
                    return ToolResult(success=False, content="", error=error_msg)
            except Exception as hook_exc:
                logger.warning("subagent_spawning hook error: %s", hook_exc)

        # Fire subagent_delivery_target hook (modifying — can override delivery origin)
        delivery_origin: dict | None = None
        if hook_runner is not None and hook_runner.has_hooks("subagent_delivery_target"):
            try:
                delivery_result = await hook_runner.run_subagent_delivery_target(spawning_event, spawning_ctx)
                if isinstance(delivery_result, dict):
                    delivery_origin = delivery_result.get("origin")
            except Exception as hook_exc:
                logger.warning("subagent_delivery_target hook error: %s", hook_exc)

        try:
            import time
            session_id = (
                label.lower().replace(" ", "-")[:32]
                if label
                else f"spawned-{int(time.time())}"
            )

            if self.gateway is not None:
                spawn_fn = getattr(self.gateway, "spawn_subagent", None)
                if callable(spawn_fn):
                    result = await spawn_fn(
                        task=task.strip(),
                        label=label or None,
                        agent_id=agent_id,
                        model=model,
                        thinking=thinking,
                        run_timeout_seconds=run_timeout_seconds,
                        cleanup=cleanup,
                        delivery_origin=delivery_origin,
                    )
                    if isinstance(result, dict):
                        session_key = result.get("sessionKey") or result.get("session_key") or session_id

                        # Fire subagent_spawned hook (void, parallel)
                        if hook_runner is not None and hook_runner.has_hooks("subagent_spawned"):
                            try:
                                await hook_runner.run_subagent_spawned(
                                    {
                                        "session_key": session_key,
                                        "agent_id": agent_id,
                                        "label": label or None,
                                        "task": task.strip(),
                                    },
                                    spawning_ctx,
                                )
                            except Exception as hook_exc:
                                logger.warning("subagent_spawned hook error: %s", hook_exc)

                        return ToolResult(
                            success=True,
                            content=f"Spawned subagent session '{session_key}'. Task: {task.strip()[:120]}",
                            metadata=result,
                        )

            if self.session_manager is not None:
                session = self.session_manager.get_session(session_id)
                session.add_user_message(task.strip())

                # Fire subagent_spawned hook (void, parallel)
                if hook_runner is not None and hook_runner.has_hooks("subagent_spawned"):
                    try:
                        await hook_runner.run_subagent_spawned(
                            {
                                "session_key": session_id,
                                "agent_id": agent_id,
                                "label": label or None,
                                "task": task.strip(),
                            },
                            spawning_ctx,
                        )
                    except Exception as hook_exc:
                        logger.warning("subagent_spawned hook error: %s", hook_exc)

                return ToolResult(
                    success=True,
                    content=f"Spawned session '{session_id}'",
                    metadata={
                        "sessionKey": session_id,
                        "agentId": agent_id,
                        "model": model,
                        "cleanup": cleanup,
                    },
                )

            return ToolResult(
                success=True,
                content=f"Session spawn queued: {session_id}",
                metadata={"sessionKey": session_id},
            )

        except Exception as exc:
            logger.error("sessions_spawn error: %s", exc, exc_info=True)
            return ToolResult(success=False, content="", error=str(exc))
