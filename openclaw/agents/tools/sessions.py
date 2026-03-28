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
            # Dynamic config loading (mirrors TS sessions-list-tool.ts line 44)
            # Fallback to instance cfg if dynamic load fails
            cfg = self.cfg
            if not cfg:
                try:
                    from openclaw.config.loader import load_config
                    cfg = load_config(as_dict=True) or {}
                except Exception:
                    cfg = {}
            
            # Create visibility guard
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(cfg)
            visibility = resolve_session_tools_visibility(cfg)
            
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
            # Dynamic config loading (mirrors TS)
            cfg = self.cfg
            if not cfg:
                try:
                    from openclaw.config.loader import load_config
                    cfg = load_config(as_dict=True) or {}
                except Exception:
                    cfg = {}
            
            # Create visibility guard
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(cfg)
            visibility = resolve_session_tools_visibility(cfg)
            
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
    """Send message to another session with access control and A2A policy.
    
    Matches TS createSessionsSendTool in sessions-send-tool.ts.
    Supports label/agentId resolution, timeout control, and agent-to-agent messaging.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        current_session_key: str | None = None,
        cfg: Any = None,
        gateway: Any = None,
    ):
        super().__init__()
        self.name = "sessions_send"
        self.description = "Send a message into another session. Use sessionKey or label to identify the target."
        self.session_manager = session_manager
        self.current_session_key = current_session_key
        self.cfg = cfg
        self.gateway = gateway

    def get_schema(self) -> dict[str, Any]:
        """Schema matches TS SessionsSendToolSchema (lines 27-33)"""
        return {
            "type": "object",
            "properties": {
                "sessionKey": {
                    "type": "string",
                    "description": "Target session key (alternative to label)",
                },
                "label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 128,  # SESSION_LABEL_MAX_LENGTH
                    "description": "Session label to resolve (alternative to sessionKey)",
                },
                "agentId": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "description": "Agent ID when using label resolution",
                },
                "message": {
                    "type": "string",
                    "description": "Message content to send",
                },
                "timeoutSeconds": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Wait timeout in seconds (0=fire-and-forget, default=30)",
                },
            },
            "required": ["message"],
        }

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Send message with label/agentId resolution and A2A policy (matches TS lines 46-361)"""
        import uuid
        
        message = params.get("message", "").strip()
        if not message:
            return ToolResult(
                success=False,
                content="",
                error="message is required",
                metadata={"runId": str(uuid.uuid4()), "status": "error"},
            )

        try:
            # Load config (matches TS line 49)
            cfg = self.cfg
            if not cfg:
                try:
                    from openclaw.config.loader import load_config
                    cfg = load_config(as_dict=True) or {}
                except Exception:
                    cfg = {}
            
            # Create A2A policy and visibility (matches TS lines 57-61)
            from openclaw.agents.tools.sessions_access import (
                create_agent_to_agent_policy,
                create_session_visibility_guard,
                resolve_session_tools_visibility,
            )
            
            a2a_policy = create_agent_to_agent_policy(cfg)
            visibility = resolve_session_tools_visibility(cfg)
            requester_key = self.current_session_key or "agent:main:main"
            
            # Parse params (matches TS lines 63-72)
            session_key_param = params.get("sessionKey", "").strip() or None
            label_param = params.get("label", "").strip() or None
            agent_id_param = params.get("agentId", "").strip() or None
            
            if session_key_param and label_param:
                return ToolResult(
                    success=False,
                    content="",
                    error="Provide either sessionKey or label (not both).",
                    metadata={"runId": str(uuid.uuid4()), "status": "error"},
                )
            
            session_key = session_key_param
            
            # Label resolution (matches TS lines 75-151)
            if not session_key and label_param:
                # Extract requester agent ID
                from openclaw.routing.session_key import resolve_agent_id_from_session_key, normalize_agent_id
                requester_agent_id = resolve_agent_id_from_session_key(requester_key)
                requested_agent_id = normalize_agent_id(agent_id_param) if agent_id_param else None
                
                # Check A2A policy if cross-agent (matches TS lines 89-105)
                if requester_agent_id and requested_agent_id and requested_agent_id != requester_agent_id:
                    if not a2a_policy.get("enabled", False):
                        return ToolResult(
                            success=False,
                            content="",
                            error="Agent-to-agent messaging is disabled. Set tools.agentToAgent.enabled=true to allow cross-agent sends.",
                            metadata={"runId": str(uuid.uuid4()), "status": "forbidden"},
                        )
                    
                    # Check allow list
                    if not self._is_a2a_allowed(a2a_policy, requester_agent_id, requested_agent_id):
                        return ToolResult(
                            success=False,
                            content="",
                            error="Agent-to-agent messaging denied by tools.agentToAgent.allow.",
                            metadata={"runId": str(uuid.uuid4()), "status": "forbidden"},
                        )
                
                # Resolve label to session key (matches TS lines 107-150)
                try:
                    from openclaw.agents.internal_call import call_gateway_internal
                    resolve_params = {"label": label_param}
                    if requested_agent_id:
                        resolve_params["agentId"] = requested_agent_id
                    
                    resolved = await call_gateway_internal(
                        gateway=self.gateway,
                        method="sessions.resolve",
                        params=resolve_params,
                        timeout_ms=10_000,
                    )
                    session_key = resolved.get("key", "").strip() if resolved else ""
                    if not session_key:
                        return ToolResult(
                            success=False,
                            content="",
                            error=f"No session found with label: {label_param}",
                            metadata={"runId": str(uuid.uuid4()), "status": "error"},
                        )
                except Exception as e:
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"Session resolution failed: {str(e)}",
                        metadata={"runId": str(uuid.uuid4()), "status": "error"},
                    )
            
            if not session_key:
                return ToolResult(
                    success=False,
                    content="",
                    error="Either sessionKey or label is required",
                    metadata={"runId": str(uuid.uuid4()), "status": "error"},
                )
            
            # Visibility check (matches TS lines 160-213)
            guard = await create_session_visibility_guard(
                action="send",
                requester_session_key=requester_key,
                visibility=visibility,
                a2a_policy=a2a_policy,
            )
            
            check = guard["check"]
            access_result = check(session_key)
            if not access_result.allowed:
                return ToolResult(
                    success=False,
                    content="",
                    error=access_result.error or "Access denied",
                    metadata={
                        "runId": str(uuid.uuid4()),
                        "status": access_result.status or "forbidden",
                        "sessionKey": session_key,
                    },
                )
            
            # Parse timeout (matches TS lines 191-196)
            timeout_seconds_raw = params.get("timeoutSeconds")
            if isinstance(timeout_seconds_raw, (int, float)) and timeout_seconds_raw >= 0:
                timeout_seconds = max(0, int(timeout_seconds_raw))
            else:
                timeout_seconds = 30
            
            timeout_ms = timeout_seconds * 1000
            announce_timeout_ms = 30_000 if timeout_seconds == 0 else timeout_ms
            idempotency_key = str(uuid.uuid4())
            run_id = idempotency_key
            
            # Send message via gateway (matches TS lines 220-234)
            from openclaw.agents.internal_call import call_gateway_internal
            send_params = {
                "message": message,
                "sessionKey": session_key,
                "idempotencyKey": idempotency_key,
                "deliver": False,
                "channel": "internal",
                "lane": "nested",
                "inputProvenance": {
                    "kind": "inter_session",
                    "sourceSessionKey": self.current_session_key,
                    "sourceTool": "sessions_send",
                },
            }
            
            # Fire-and-forget mode (timeout=0) (matches TS lines 253-279)
            if timeout_seconds == 0:
                try:
                    response = await call_gateway_internal(
                        gateway=self.gateway,
                        method="agent",
                        params=send_params,
                        timeout_ms=10_000,
                    )
                    if response and isinstance(response.get("runId"), str):
                        run_id = response["runId"]
                    
                    return ToolResult(
                        success=True,
                        content=f"Message accepted for delivery to session '{session_key}'",
                        metadata={
                            "runId": run_id,
                            "status": "accepted",
                            "sessionKey": session_key,
                            "delivery": {"status": "pending", "mode": "announce"},
                        },
                    )
                except Exception as e:
                    return ToolResult(
                        success=False,
                        content="",
                        error=str(e),
                        metadata={"runId": run_id, "status": "error", "sessionKey": session_key},
                    )
            
            # Wait mode (timeout>0) (matches TS lines 282-358)
            try:
                response = await call_gateway_internal(
                    gateway=self.gateway,
                    method="agent",
                    params=send_params,
                    timeout_ms=10_000,
                )
                if response and isinstance(response.get("runId"), str):
                    run_id = response["runId"]
            except Exception as e:
                return ToolResult(
                    success=False,
                    content="",
                    error=str(e),
                    metadata={"runId": run_id, "status": "error", "sessionKey": session_key},
                )
            
            # Wait for completion (matches TS lines 302-324)
            try:
                wait_response = await call_gateway_internal(
                    gateway=self.gateway,
                    method="agent.wait",
                    params={"runId": run_id, "timeoutMs": timeout_ms},
                    timeout_ms=timeout_ms + 2000,
                )
                wait_status = wait_response.get("status") if wait_response else None
                wait_error = wait_response.get("error") if wait_response else None
            except Exception as e:
                error_msg = str(e)
                is_timeout = "timeout" in error_msg.lower() or "gateway timeout" in error_msg.lower()
                return ToolResult(
                    success=False,
                    content="",
                    error=error_msg,
                    metadata={
                        "runId": run_id,
                        "status": "timeout" if is_timeout else "error",
                        "sessionKey": session_key,
                    },
                )
            
            # Check wait status (matches TS lines 326-341)
            if wait_status == "timeout":
                return ToolResult(
                    success=False,
                    content="",
                    error=wait_error or "Timeout waiting for response",
                    metadata={"runId": run_id, "status": "timeout", "sessionKey": session_key},
                )
            if wait_status == "error":
                return ToolResult(
                    success=False,
                    content="",
                    error=wait_error or "agent error",
                    metadata={"runId": run_id, "status": "error", "sessionKey": session_key},
                )
            
            # Get reply from history (matches TS lines 343-350)
            try:
                history_response = await call_gateway_internal(
                    gateway=self.gateway,
                    method="chat.history",
                    params={"sessionKey": session_key, "limit": 50},
                    timeout_ms=10_000,
                )
                messages = history_response.get("messages", []) if history_response else []
                # Extract last assistant message
                reply = None
                for msg in reversed(messages):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            reply = content
                            break
            except Exception:
                reply = None
            
            return ToolResult(
                success=True,
                content=f"Message sent to session '{session_key}'. Reply: {reply or '(no reply)'}",
                metadata={
                    "runId": run_id,
                    "status": "ok",
                    "reply": reply,
                    "sessionKey": session_key,
                    "delivery": {"status": "pending", "mode": "announce"},
                },
            )

        except Exception as e:
            logger.error(f"Sessions send error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=str(e),
                metadata={"runId": str(uuid.uuid4()), "status": "error"},
            )
    
    def _is_a2a_allowed(self, a2a_policy: dict[str, Any], requester_agent_id: str, target_agent_id: str) -> bool:
        """Check if A2A is allowed by policy (matches TS AgentToAgentPolicy.isAllowed)"""
        allow_list = a2a_policy.get("allow", [])
        if not allow_list:
            return True  # No restrictions if allow list is empty
        
        # Check patterns: "agent1->agent2", "agent1->*", "*->agent2", "*->*"
        specific = f"{requester_agent_id}->{target_agent_id}"
        from_wildcard = f"{requester_agent_id}->*"
        to_wildcard = f"*->{target_agent_id}"
        all_wildcard = "*->*"
        
        return (
            specific in allow_list
            or from_wildcard in allow_list
            or to_wildcard in allow_list
            or all_wildcard in allow_list
        )


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
        """Spawn subagent session.
        
        Mirrors TypeScript sessions-spawn-tool.ts by directly calling spawn_subagent_direct.
        All hooks and logic are handled inside spawn_subagent_direct.
        """
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
        mount_path = attach_as.get("mountPath") if isinstance(attach_as, dict) else None

        try:
            # Import spawn_subagent_direct and related types
            from openclaw.agents.subagent_spawn import (
                spawn_subagent_direct,
                SpawnSubagentParams,
                SpawnSubagentContext,
            )
            from openclaw.config.loader import load_config
            
            cfg = load_config()
            
            # Build spawn params (mirrors TS sessions-spawn-tool.ts lines 151-169)
            spawn_params = SpawnSubagentParams(
                task=task.strip(),
                label=label or None,
                agentId=agent_id,
                model=model,
                thinking=thinking,
                runTimeoutSeconds=int(run_timeout_seconds) if run_timeout_seconds is not None else None,
                cleanup=cleanup,
                expectsCompletionMessage=True,
                mode=mode,
                thread=thread,
                sandbox="inherit",  # Default sandbox mode
                attachments=attachments if attachments else None,
                attachMountPath=mount_path,
            )
            
            # Build spawn context (mirrors TS sessions-spawn-tool.ts lines 170-180)
            # Get current session key from session_manager if available
            current_session_key = None
            if self.session_manager is not None and hasattr(self.session_manager, "current_session_key"):
                current_session_key = self.session_manager.current_session_key
            
            spawn_ctx = SpawnSubagentContext(
                agentSessionKey=current_session_key,
                agentChannel=None,  # Can be extracted from session context if needed
                agentAccountId=None,
                agentTo=None,
                agentThreadId=None,
                agentGroupId=None,
                agentGroupChannel=None,
                agentGroupSpace=None,
                requesterAgentIdOverride=None,
            )
            
            # Spawn subagent (mirrors TS sessions-spawn-tool.ts line 151)
            result = await spawn_subagent_direct(
                params=spawn_params,
                ctx=spawn_ctx,
                cfg=cfg,
                gateway=self.gateway,
            )
            
            # Handle result
            if result.status == "accepted":
                return ToolResult(
                    success=True,
                    content=f"Subagent spawned: {result.childSessionKey}",
                    metadata={
                        "sessionKey": result.childSessionKey,  # Primary field for backward compat
                        "childSessionKey": result.childSessionKey,
                        "runId": result.runId,
                        "mode": result.mode,
                        "note": result.note,
                        "status": "accepted",
                    }
                )
            elif result.status == "forbidden":
                return ToolResult(
                    success=False,
                    content="",
                    error=result.error or "Subagent spawn forbidden"
                )
            else:  # error
                return ToolResult(
                    success=False,
                    content="",
                    error=result.error or "Subagent spawn failed"
                )
                
        except Exception as e:
            logger.error(f"Subagent spawn error: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=f"Spawn failed: {str(e)}"
            )

