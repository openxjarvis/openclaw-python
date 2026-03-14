"""Gateway WebSocket server implementation

This is the main Gateway Server that:
1. Manages channel plugins via ChannelManager
2. Provides WebSocket API for external clients
3. Broadcasts events to connected clients (Observer Pattern)

Architecture:
    Gateway Server
        ├── ChannelManager (manages channel plugins)
        │       ├── Telegram Channel
        │       ├── Discord Channel
        │       └── ...
        │
        ├── WebSocket Server (for external clients)
        │       ├── Control UI
        │       ├── CLI tools
        │       └── Mobile apps
        │
        └── Event Broadcasting (Observer Pattern)
                └── Receives events from Agent Runtime
                    and broadcasts to all WebSocket clients
"""
from __future__ import annotations


import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# Use aiohttp for unified HTTP + WebSocket server (matches openclaw-ts)
from aiohttp import web, WSMsgType
import aiohttp

from openclaw.gateway.auth import (
    AuthRateLimiter,
    AuthMode,
    AuthMethod,
    authorize_gateway_connect,
    validate_auth_config,
)
from openclaw.gateway.authorization import AuthContext, authorize_gateway_method
from openclaw.gateway.device_auth import (
    DeviceIdentity,
    authorize_device_identity,
)
from openclaw.gateway.error_codes import (
    ErrorCode,
    InvalidRequestError,
    NotLinkedError,
    UnavailableError,
    error_shape,
)
from openclaw.gateway.protocol.validators import validate_method_params

from ..config import OpenClawConfig
from ..events import Event
from .channel_manager import ChannelManager, discover_channel_plugins
from .handlers import get_method_handler
from .node_registry import NodeRegistry
from .node_subscriptions import NodeSubscriptionManager
from .node_events import NodeEventHandler
from .protocol import ErrorShape, EventFrame, RequestFrame, ResponseFrame
from .protocol.frames import ConnectRequest, HelloResponse

logger = logging.getLogger(__name__)


class GatewayConnection:
    """Represents a single WebSocket connection (aiohttp WebSocketResponse)"""

    def __init__(
        self,
        websocket: web.WebSocketResponse,
        config: OpenClawConfig,
        gateway: "GatewayServer" = None,
        remote_addr: str = "",
        headers: dict[str, str] | None = None,
    ):
        self.websocket = websocket
        self.config = config
        self.gateway = gateway  # Reference to parent gateway server
        self.remote_addr = remote_addr  # Store remote address for logging
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.authenticated = False
        self.client_info: dict[str, Any] | None = None
        self.protocol_version = 1
        self.auth_context = AuthContext(role="operator", scopes=set())
        self.nonce: Optional[str] = None
        self.connect_challenge_sent = False
        # Per-connection UUID — used to track node registrations
        self.conn_id: str = str(uuid.uuid4())
        # Set to the node_id when this connection is a node (role="node")
        self.node_id: str | None = None

    async def send_response(
        self, request_id: str | int, payload: Any = None, error: ErrorShape | None = None
    ) -> None:
        """Send response frame (Gateway protocol format)"""
        # Use Gateway protocol ResponseFrame format
        response_frame = ResponseFrame(
            type="res",
            id=request_id,
            ok=error is None,
            payload=payload,
            error=error
        )
        # aiohttp: send_str instead of send
        await self.websocket.send_str(response_frame.model_dump_json())

    async def send_event(self, event: str, payload: Any = None) -> None:
        """Send event frame"""
        event_frame = EventFrame(event=event, payload=payload)
        # aiohttp: send_str instead of send
        await self.websocket.send_str(event_frame.model_dump_json())

    async def handle_message(self, message: str) -> None:
        """Handle incoming message"""
        try:
            data = json.loads(message)
            
            # Support both custom frame format and standard JSON-RPC 2.0
            if "jsonrpc" in data:
                # Standard JSON-RPC 2.0 format
                request = RequestFrame(
                    type="req",
                    id=data.get("id"),
                    method=data.get("method"),
                    params=data.get("params", {}),
                )
                await self.handle_request(request)
            elif data.get("type") == "req":
                # Custom frame format
                request = RequestFrame(**data)
                await self.handle_request(request)
            else:
                logger.warning(f"Unknown message format: {data}")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)

    async def handle_request(self, request: RequestFrame) -> None:
        """Handle request frame with authorization"""
        try:
            # Special handling for connect method
            if request.method == "connect":
                await self.handle_connect(request)
                return

            # Check authentication for other methods
            if not self.authenticated and request.method not in ("health", "ping"):
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="AUTH_REQUIRED",
                        message="Authentication required. Send 'connect' request first.",
                    ),
                )
                return

            # Check authorization (role/scope based)
            if not authorize_gateway_method(request.method, self.auth_context):
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="PERMISSION_DENIED",
                        message=f"Insufficient permissions for method '{request.method}'",
                    ),
                )
                logger.warning(
                    f"Permission denied: method={request.method}, "
                    f"role={self.auth_context.role}, "
                    f"scopes={self.auth_context.scopes}"
                )
                return

            # Get method handler
            handler = get_method_handler(request.method)
            if handler is None:
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="METHOD_NOT_FOUND", message=f"Method '{request.method}' not found"
                    ),
                )
                return

            # Validate parameters
            try:
                validated_params = validate_method_params(request.method, request.params or {})
                # Convert Pydantic model to dict if necessary
                if hasattr(validated_params, "model_dump"):
                    params_dict = validated_params.model_dump()
                else:
                    params_dict = request.params or {}
            except Exception as e:
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="INVALID_REQUEST",
                        message=f"Invalid parameters: {str(e)}"
                    ),
                )
                logger.warning(f"Parameter validation failed for {request.method}: {e}")
                return

            # Execute handler
            result = await handler(self, params_dict)
            await self.send_response(request.id, payload=result)

        except Exception as e:
            logger.error(f"Error handling request {request.method}: {e}", exc_info=True)
            await self.send_response(
                request.id, error=ErrorShape(code="INTERNAL_ERROR", message=str(e))
            )

    async def handle_connect(self, request: RequestFrame) -> None:
        """Handle connection handshake with authentication"""
        try:
            if self.authenticated:
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="INVALID_REQUEST",
                        message="connect is only valid as the first request",
                    ),
                )
                return

            connect_req = ConnectRequest(**(request.params or {}))

            protocol_version = 3
            if connect_req.maxProtocol < protocol_version or connect_req.minProtocol > protocol_version:
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="INVALID_REQUEST",
                        message="protocol mismatch",
                        details={"expectedProtocol": protocol_version},
                    ),
                )
                return
            negotiated_protocol = min(connect_req.maxProtocol, protocol_version)

            # Extract auth params
            auth_params = connect_req.auth or {}
            request_token = auth_params.get("token")
            request_password = auth_params.get("password")
            
            # Extract device identity if provided
            device_identity = None
            if connect_req.deviceIdentity:
                signed_at_raw = connect_req.deviceIdentity.get("signedAt", "")
                try:
                    signed_at_num = int(signed_at_raw)
                except Exception:
                    signed_at_num = None
                if signed_at_num is None or abs(int(time.time() * 1000) - signed_at_num) > 10 * 60 * 1000:
                    await self.send_response(
                        request.id,
                        error=ErrorShape(code="INVALID_REQUEST", message="device signature expired"),
                    )
                    return
                device_identity = DeviceIdentity(
                    id=connect_req.deviceIdentity.get("id", ""),
                    public_key=connect_req.deviceIdentity.get("publicKey", ""),
                    signature=connect_req.deviceIdentity.get("signature", ""),
                    signed_at=str(signed_at_raw),
                    nonce=connect_req.deviceIdentity.get("nonce")
                )
                # TS parity: verify declared device id derives from public key.
                derived_id = hashlib.sha256(device_identity.public_key.encode("utf-8")).hexdigest()[:32]
                if device_identity.id != derived_id:
                    await self.send_response(
                        request.id,
                        error=ErrorShape(code="INVALID_REQUEST", message="device identity mismatch"),
                    )
                    return
            
            # Get client IP (remote_addr from aiohttp request.remote)
            client_ip = self.remote_addr.split(":")[0] if self.remote_addr else None
            auth_cfg = getattr(self.config.gateway, "auth", None) if hasattr(self.config, "gateway") else None
            config_token = getattr(auth_cfg, "token", None) if auth_cfg else None
            config_password = getattr(auth_cfg, "password", None) if auth_cfg else None
            mode_raw = (getattr(auth_cfg, "mode", None) or "").lower() if auth_cfg else ""
            if mode_raw == "password":
                auth_mode = AuthMode.PASSWORD
            elif mode_raw == "trusted-proxy":
                auth_mode = AuthMode.TRUSTED_PROXY
            elif mode_raw == "none":
                auth_mode = AuthMode.NONE
            else:
                auth_mode = AuthMode.TOKEN

            auth_result = authorize_gateway_connect(
                auth_mode=auth_mode,
                config_token=config_token,
                config_password=config_password,
                request_token=request_token,
                request_password=request_password,
                allow_tailscale=bool(getattr(auth_cfg, "allow_tailscale", False)) if auth_cfg else False,
                client_ip=client_ip,
                trusted_proxies=getattr(self.config.gateway, "trusted_proxies", []),
                headers=self.headers,
                host_header=self.headers.get("host"),
                remote_addr=client_ip,
                trusted_proxy_config=(getattr(auth_cfg, "trusted_proxy", None) if auth_cfg else None),
                rate_limiter=getattr(self.gateway, "auth_rate_limiter", None),
                rate_limit_scope="shared-secret",
            )

            auth_result_ok = auth_result.ok
            auth_method = auth_result.method

            # If shared auth failed but device identity is provided, try device auth.
            if not auth_result_ok and device_identity and not auth_result.rate_limited:
                device_result = authorize_device_identity(
                    device_identity,
                    nonce=self.nonce,
                    require_nonce=True,
                )
                if device_result.ok:
                    auth_result_ok = True
                    auth_method = AuthMethod.DEVICE_TOKEN

            if not auth_result_ok:
                await self.send_response(
                    request.id,
                    error=ErrorShape(
                        code="AUTH_FAILED",
                        message=auth_result.reason or "Authentication failed",
                        retryable=True if auth_result.rate_limited else None,
                        retryAfterMs=auth_result.retry_after_ms,
                    ),
                )
                logger.warning(f"Authentication failed: {auth_result.reason}")
                return

            # Authentication successful
            self.client_info = connect_req.client
            self.protocol_version = negotiated_protocol
            self.authenticated = True
            
            # Set auth context with role and scopes
            role = connect_req.role or "operator"
            scopes = set(connect_req.scopes or [])
            
            # Default scopes for operator role
            if role == "operator" and not scopes:
                scopes = {
                    "operator.admin",
                    "operator.read",
                    "operator.write",
                    "operator.approvals",
                    "operator.pairing"
                }
            
            self.auth_context = AuthContext(
                role=role,
                scopes=scopes,
                user=getattr(auth_result, "user", None) if auth_result_ok and not auth_method == AuthMethod.LOCAL_DIRECT else None,
                device_id=device_identity.id if device_identity else None
            )

            # Register node connection in NodeRegistry when role == "node"
            if role == "node" and self.gateway is not None:
                client_dict = connect_req.client or {}
                if isinstance(client_dict, dict):
                    node_id = (
                        client_dict.get("instanceId")
                        or client_dict.get("id")
                        or (device_identity.id if device_identity else None)
                        or self.conn_id
                    )
                else:
                    node_id = getattr(client_dict, "instanceId", None) or getattr(client_dict, "id", None) or self.conn_id

                device_id = (device_identity.id if device_identity else None) or node_id
                capabilities = list(connect_req.caps or [])
                commands = list(connect_req.commands or [])
                platform = client_dict.get("platform") if isinstance(client_dict, dict) else None
                display_name = client_dict.get("displayName") if isinstance(client_dict, dict) else None

                self.node_id = node_id
                self.gateway.node_registry.register_node(
                    node_id=node_id,
                    conn_id=self.conn_id,
                    device_id=device_id,
                    capabilities=capabilities,
                    metadata={
                        "displayName": display_name,
                        "platform": platform,
                        "commands": commands,
                        "pathEnv": connect_req.pathEnv if hasattr(connect_req, "pathEnv") else None,
                        "remoteIp": self.remote_addr.split(":")[0] if self.remote_addr else None,
                    },
                    send_event=self.send_event,
                )
                logger.info(
                    f"Node registered: id={node_id!r} device={device_id!r} "
                    f"caps={capabilities} conn={self.conn_id!r}"
                )

            # Send hello response
            hello = HelloResponse(
                protocol=negotiated_protocol,
                server={
                    "name": "openclaw-python",
                    "version": "0.6.0",
                    "platform": "python"
                },
                features={
                    "agent": True,
                    "chat": True,
                    "sessions": True,
                    "channels": True,
                    "tools": True,
                    "cron": True,
                    "nodes": True,
                    "devices": True,
                },
                snapshot={
                    "sessions": [],
                    "channels": [],
                    "agents": []
                },
            )

            await self.send_response(request.id, payload=hello.model_dump())
            logger.info(
                f"Client connected: {self.client_info}, "
                f"protocol={negotiated_protocol}, "
                f"auth_method={auth_method}, "
                f"role={self.auth_context.role}"
            )

        except Exception as e:
            logger.error(f"Connect handshake failed: {e}", exc_info=True)
            await self.send_response(
                request.id, error=ErrorShape(code="HANDSHAKE_FAILED", message=str(e))
            )


class GatewayServer:
    """
    Gateway WebSocket server

    This is the main entry point for OpenClaw Gateway, providing:
    1. ChannelManager - Manages all channel plugins (Telegram, Discord, etc.)
    2. WebSocket API - Serves external clients (UI, CLI, mobile)
    3. Event Broadcasting - Broadcasts Agent events to all clients

    Architecture follows TypeScript OpenClaw design:
    - Gateway contains ChannelManager
    - Channels are plugins inside Gateway (not external clients)
    - Gateway observes Agent Runtime for events
    - WebSocket is for external clients only

    Example:
        config = OpenClawConfig(...)
        gateway = GatewayServer(config, agent_runtime, session_manager)

        # Register channels
        gateway.channel_manager.register("telegram", EnhancedTelegramChannel)
        gateway.channel_manager.configure("telegram", {"bot_token": "..."})

        # Start gateway (starts WebSocket + all enabled channels)
        await gateway.start()
    """

    def __init__(
        self,
        config: OpenClawConfig,
        agent_runtime=None,
        session_manager=None,
        tools=None,
        system_prompt: str | None = None,
        auto_discover_channels: bool = False,
    ):
        """
        Initialize Gateway Server

        Args:
            config: Gateway configuration
            agent_runtime: AgentRuntime instance (shared with channels)
            session_manager: SessionManager for managing sessions
            tools: List of tools available to the agent
            system_prompt: Optional system prompt (skills, capabilities)
            auto_discover_channels: If True, auto-discover and register channel plugins
        """
        self.config = config
        self.connections: set[GatewayConnection] = set()
        self.running = False
        self.started_at: float | None = None
        self.agent_runtime = agent_runtime
        self.session_manager = session_manager
        # Normalise: accept ToolRegistry or plain list (mirrors TS plain array)
        if tools is None:
            self.tools = []
        elif hasattr(tools, "list_tools"):
            self.tools = tools.list_tools()
        else:
            self.tools = list(tools)
        self.system_prompt = system_prompt
        self.http_server = None
        self.http_server_task = None
        self.active_runs: dict[str, asyncio.Task] = {}  # Track active agent runs for abort
        self.auth_rate_limiter = AuthRateLimiter(limit=8, window_ms=60_000)
        
        # Initialize memory manager (lazy initialization)
        self._memory_manager = None
        self._sync_manager = None  # SyncManager started alongside memory manager
        
        # Initialize approval manager
        from openclaw.exec.approval_manager import ExecApprovalManager
        self.approval_manager = ExecApprovalManager()

        # Create ChannelManager
        self.channel_manager = ChannelManager(
            default_runtime=agent_runtime,
            session_manager=session_manager,
            tools=self.tools,
            system_prompt=self.system_prompt,
        )

        # Create NodeRegistry + supporting managers (mirrors TS gateway/node-registry.ts)
        self.node_registry = NodeRegistry()
        self.node_subscription_manager = NodeSubscriptionManager(
            get_node_send_fn=lambda node_id: (
                self.node_registry.get_node(node_id)._send_event
                if self.node_registry.get_node(node_id) is not None
                else None
            )
        )
        self.node_event_handler = NodeEventHandler(
            node_registry=self.node_registry,
            subscription_manager=self.node_subscription_manager,
            session_manager=session_manager,
            agent_runtime=agent_runtime,
        )
        # Wire subscription manager into registry for cleanup on disconnect
        self.node_registry.set_subscription_manager(self.node_subscription_manager)
        self.node_registry.set_event_handler(self.node_event_handler)

        # Register global handler instances so gateway handlers can access runtime
        from openclaw.gateway.handlers import set_global_instances
        from openclaw.agents.tools.registry import ToolRegistry
        if isinstance(tools, ToolRegistry):
            tool_registry = tools
        else:
            tool_registry = ToolRegistry()
            for t in (tools or []):
                tool_registry.register(t)
        set_global_instances(
            session_manager=session_manager,
            tool_registry=tool_registry,
            channel_registry=self.channel_manager,
            agent_runtime=agent_runtime,
            node_registry=self.node_registry,
            node_event_handler=self.node_event_handler,
        )

        # Register as observer if agent_runtime provided
        if agent_runtime:
            agent_runtime.add_event_listener(self.on_agent_event)
            logger.info("Gateway registered as Agent Runtime observer")

        # Listen for channel events to broadcast
        self.channel_manager.add_event_listener(self._on_channel_event)

        # Initialize wizard RPC handler
        from .wizard_rpc import WizardRPCHandler
        self.wizard_handler = WizardRPCHandler(self)

        # Auto-discover channel plugins if requested
        if auto_discover_channels:
            self._discover_and_register_channels()

        logger.info("GatewayServer initialized with ChannelManager")

    def _discover_and_register_channels(self) -> None:
        """Discover and register available channel plugins"""
        plugins = discover_channel_plugins()
        for channel_id, channel_class in plugins.items():
            self.channel_manager.register(channel_id, channel_class)
        logger.info(f"Auto-discovered {len(plugins)} channel plugins")

    async def _on_channel_event(
        self,
        event_type: str,
        channel_id: str,
        data: dict[str, Any],
    ) -> None:
        """
        Handle channel manager events

        Broadcasts channel lifecycle events to WebSocket clients.
        """
        await self.broadcast_event(
            "channel",
            {
                "event": event_type,
                "channel_id": channel_id,
                "data": data,
            },
        )

    async def on_agent_event(self, event: Event):
        """
        Observer callback: Agent Runtime automatically calls this for every event

        This implements the Observer Pattern where Gateway passively receives
        events instead of channels actively pushing to Gateway.

        Args:
            event: Unified Event from Agent Runtime
        """
        # Broadcast to all WebSocket clients using standardized format
        await self.broadcast_event("agent", event.to_dict())

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket upgrade and connection (aiohttp)"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        # Get remote address
        remote_addr = request.remote or "unknown"
        
        # Create connection
        connection = GatewayConnection(
            ws,
            self.config,
            gateway=self,
            remote_addr=remote_addr,
            headers={k: v for k, v in request.headers.items()},
        )
        self.connections.add(connection)

        try:
            logger.info(f"New WebSocket connection from {remote_addr}")

            # Broadcast presence: new peer joined
            try:
                await self.broadcast_event("presence", {
                    "event": "join",
                    "connections": len(self.connections),
                    "ts": int(time.time() * 1000),
                })
            except Exception:
                pass

            # Send connect challenge immediately
            connection.nonce = secrets.token_urlsafe(32)
            connection.connect_challenge_sent = True
            await connection.send_event("connect.challenge", {
                "nonce": connection.nonce,
                "timestamp": int(time.time() * 1000)
            })
            logger.debug(f"Sent connect.challenge with nonce")
            
            # Handle messages (aiohttp pattern)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await connection.handle_message(msg.data)
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break
        except Exception as e:
            logger.error(f"Connection error: {e}", exc_info=True)
        finally:
            self.connections.discard(connection)
            # Unregister node on disconnect
            if connection.node_id is not None:
                self.node_registry.unregister_node(connection.node_id)
                logger.info(f"Node unregistered on disconnect: id={connection.node_id!r}")
            logger.info(f"Connection closed: {remote_addr}")
            # Broadcast presence: peer left
            try:
                await self.broadcast_event("presence", {
                    "event": "leave",
                    "connections": len(self.connections),
                    "ts": int(time.time() * 1000),
                })
            except Exception:
                pass
        
        return ws

    async def broadcast_event(self, event: str, payload: Any = None) -> None:
        """Broadcast event to all connected clients"""
        disconnected = set()
        for connection in self.connections:
            try:
                await connection.send_event(event, payload)
            except Exception as e:
                logger.error(f"Failed to send event to connection: {e}")
                disconnected.add(connection)

        # Clean up disconnected connections
        self.connections -= disconnected
    
    def get_memory_manager(self):
        """Get or create memory manager (lazy initialization).

        Also starts SyncManager (file watcher + 30-min periodic sync) on first
        call — mirrors TS MemoryIndexManager.get() which wires up chokidar and
        the interval sync inside the constructor.
        """
        if self._memory_manager is None:
            try:
                from openclaw.memory.builtin_manager import BuiltinMemoryManager
                from openclaw.memory.sync_manager import SyncManager

                # Determine workspace directory
                workspace_dir = Path.home() / ".openclaw" / "workspace"
                if self.agent_runtime and hasattr(self.agent_runtime, 'workspace_dir'):
                    workspace_dir = Path(self.agent_runtime.workspace_dir)

                # Use default agent_id
                agent_id = "main"
                if self.config and hasattr(self.config, 'agent') and hasattr(self.config.agent, 'id'):
                    agent_id = self.config.agent.id

                self._memory_manager = BuiltinMemoryManager(
                    agent_id=agent_id,
                    workspace_dir=workspace_dir,
                    embedding_provider="openai",
                )
                logger.info(f"Memory manager initialized for agent '{agent_id}' at {workspace_dir}")

                # Start SyncManager — file watcher + periodic 30-min sync
                # Matches TS ensureWatcher() + ensureIntervalSync() in manager-sync-ops.ts
                sync_manager = SyncManager(
                    memory_manager=self._memory_manager,
                    workspace_path=workspace_dir,
                )
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(sync_manager.start())
                    else:
                        logger.debug("No running event loop — SyncManager will be started later")
                except Exception as sm_exc:
                    logger.warning("SyncManager could not be scheduled: %s", sm_exc)

                # Store sync_manager reference so it isn't GC'd
                self._sync_manager = sync_manager

                # Wire sync_manager into agent_runtime so per-turn export works
                if self.agent_runtime is not None and hasattr(self.agent_runtime, '_sync_manager'):
                    self.agent_runtime._sync_manager = sync_manager

            except Exception as e:
                logger.error(f"Failed to initialize memory manager: {e}", exc_info=True)
                return None

        return self._memory_manager

    def _get_assistant_name(self) -> str:
        """Resolve assistant name from config"""
        try:
            if self.config and hasattr(self.config, 'agent'):
                agent = self.config.agent
                if hasattr(agent, 'name') and agent.name:
                    return agent.name
        except Exception:
            pass
        return "OpenClaw"

    def _get_control_ui_config_script(self) -> str:
        """Generate config injection script (matches openclaw-ts)"""
        import json
        name = self._get_assistant_name()
        return f"""
    <script>
        window.__OPENCLAW_CONTROL_UI_BASE_PATH__ = "/";
        window.__OPENCLAW_ASSISTANT_NAME__ = {json.dumps(name)};
        window.__OPENCLAW_ASSISTANT_AVATAR__ = null;
    </script>
    """

    async def serve_bootstrap_config(self, request: web.Request) -> web.Response:
        """Serve /__openclaw/control-ui-config.json (matches openclaw-ts)"""
        return web.Response(
            text=__import__('json').dumps({
                "basePath": "/",
                "assistantName": self._get_assistant_name(),
                "assistantAvatar": None,
                "assistantAgentId": "main",
            }),
            content_type="application/json",
            headers={"Cache-Control": "no-cache"},
        )

    async def serve_control_ui(self, request: web.Request) -> web.Response:
        """Serve Control UI index.html"""
        ui_dir = Path(__file__).parent.parent.parent / "ui" / "dist"
        index_path = ui_dir / "index.html"
        
        if not index_path.exists():
            return web.Response(
                text="Control UI not built. Run: cd ui && npm install && npm run build",
                status=503,
                content_type="text/plain"
            )
        
        # Read and inject config
        html = index_path.read_text()
        config_script = self._get_control_ui_config_script()
        html = html.replace("</head>", f"{config_script}</head>")
        
        return web.Response(text=html, content_type="text/html")

    async def serve_control_ui_spa(self, request: web.Request) -> web.Response:
        """SPA fallback: serve static files or index.html"""
        ui_dir = Path(__file__).parent.parent.parent / "ui" / "dist"
        path = request.match_info.get('path', '')
        
        # Check if static file exists
        file_path = ui_dir / path
        if file_path.is_file() and file_path.exists():
            return web.FileResponse(file_path)
        
        # Fallback to index.html for SPA routing
        return await self.serve_control_ui(request)
    
    async def handle_root(self, request: web.Request) -> web.Response | web.WebSocketResponse:
        """Handle root path: WebSocket upgrade or Control UI"""
        # Check if this is a WebSocket upgrade request
        if request.headers.get('Upgrade', '').lower() == 'websocket':
            return await self.handle_websocket(request)
        
        # Otherwise, serve Control UI
        return await self.serve_control_ui(request)

    # ------------------------------------------------------------------
    # OpenAI / OpenResponses HTTP handlers
    # Mirrors TS openclaw/src/gateway/server-http-openai.ts
    # ------------------------------------------------------------------

    async def _handle_chat_completions_route(self, request: web.Request) -> web.Response:
        """Handle POST /v1/chat/completions (OpenAI-compatible)."""
        import json as _json
        from aiohttp import web as _web

        try:
            from openclaw.gateway.http.chat_completions import (
                ChatCompletionRequest,
                handle_chat_completions,
            )

            # Auth check
            auth_header = request.headers.get("Authorization", "")
            if not self._check_http_auth(auth_header):
                return _web.Response(
                    status=401,
                    content_type="application/json",
                    body=_json.dumps({"error": {"message": "Unauthorized", "type": "auth_error"}}).encode(),
                )

            body = await request.json()
            req_model = ChatCompletionRequest(**body)
            agent_id_header = request.headers.get("x-openclaw-agent-id")
            result = await handle_chat_completions(
                request=req_model,
                agent_runtime=self,
                authorization=auth_header,
                x_openclaw_agent_id=agent_id_header,
            )
            if hasattr(result, "__aiter__"):
                # Streaming SSE
                async def _stream():
                    async for chunk in result:
                        yield chunk.encode() if isinstance(chunk, str) else chunk

                return _web.Response(
                    status=200,
                    content_type="text/event-stream",
                    body=b"".join([c.encode() if isinstance(c, str) else c async for c in result]),
                )
            return _web.Response(
                status=200,
                content_type="application/json",
                body=_json.dumps(result if isinstance(result, dict) else result.dict()).encode(),
            )
        except Exception as exc:
            logger.error(f"/v1/chat/completions error: {exc}", exc_info=True)
            return _web.Response(
                status=500,
                content_type="application/json",
                body=_json.dumps({"error": {"message": str(exc), "type": "server_error"}}).encode(),
            )

    async def _handle_list_models_route(self, request: web.Request) -> web.Response:
        """Handle GET /v1/models (OpenAI-compatible model list).

        Returns the list of available models.  Mirrors TS GET /v1/models.
        """
        import json as _json
        from aiohttp import web as _web
        import time as _time

        auth_header = request.headers.get("Authorization", "")
        if not self._check_http_auth(auth_header):
            return _web.Response(
                status=401,
                content_type="application/json",
                body=_json.dumps({"error": {"message": "Unauthorized", "type": "auth_error"}}).encode(),
            )

        try:
            models = self._list_available_models()
            return _web.Response(
                status=200,
                content_type="application/json",
                body=_json.dumps({
                    "object": "list",
                    "data": [
                        {
                            "id": m,
                            "object": "model",
                            "created": int(_time.time()),
                            "owned_by": "openclaw",
                        }
                        for m in models
                    ],
                }).encode(),
            )
        except Exception as exc:
            logger.error(f"/v1/models error: {exc}", exc_info=True)
            return _web.Response(
                status=500,
                content_type="application/json",
                body=_json.dumps({"error": {"message": str(exc), "type": "server_error"}}).encode(),
            )

    async def _handle_responses_route(self, request: web.Request) -> web.Response:
        """Handle POST /v1/responses (OpenResponses-compatible).

        Mirrors TS POST /v1/responses.
        """
        import json as _json
        from aiohttp import web as _web

        auth_header = request.headers.get("Authorization", "")
        if not self._check_http_auth(auth_header):
            return _web.Response(
                status=401,
                content_type="application/json",
                body=_json.dumps({"error": {"message": "Unauthorized", "type": "auth_error"}}).encode(),
            )

        try:
            from openclaw.gateway.http.responses import handle_responses_request
            body = await request.json()
            agent_id_header = request.headers.get("x-openclaw-agent-id")
            result = await handle_responses_request(
                body=body,
                gateway=self,
                authorization=auth_header,
                agent_id_header=agent_id_header,
            )
            return _web.Response(
                status=200,
                content_type="application/json",
                body=_json.dumps(result if isinstance(result, dict) else result.dict()).encode(),
            )
        except NotImplementedError:
            return _web.Response(
                status=501,
                content_type="application/json",
                body=_json.dumps({"error": {"message": "Not implemented", "type": "not_implemented"}}).encode(),
            )
        except Exception as exc:
            logger.error(f"/v1/responses error: {exc}", exc_info=True)
            return _web.Response(
                status=500,
                content_type="application/json",
                body=_json.dumps({"error": {"message": str(exc), "type": "server_error"}}).encode(),
            )

    def _check_http_auth(self, auth_header: str) -> bool:
        """Validate HTTP bearer token against gateway auth config.

        Mirrors TS verifyBearerToken() in server-http-openai.ts.
        Returns True if auth passes or if auth is disabled.
        """
        try:
            gw_auth = getattr(getattr(self.config, "gateway", None), "auth", None)
            if gw_auth is None:
                return True
            mode = getattr(gw_auth, "mode", None) or (gw_auth.get("mode") if isinstance(gw_auth, dict) else None)
            if mode == "token":
                expected = getattr(gw_auth, "token", None) or (gw_auth.get("token") if isinstance(gw_auth, dict) else None)
                if not expected:
                    return True
                token = auth_header.removeprefix("Bearer ").strip()
                return token == expected
            if mode == "password":
                expected = getattr(gw_auth, "password", None) or (gw_auth.get("password") if isinstance(gw_auth, dict) else None)
                if not expected:
                    return True
                token = auth_header.removeprefix("Bearer ").strip()
                return token == expected
            return True
        except Exception:
            return True

    def _list_available_models(self) -> list[str]:
        """Return available model IDs for /v1/models response.

        Returns a list like ["openclaw:main", "openclaw:beta", ...] based on
        configured agents plus a generic "openclaw" fallback.
        """
        models: list[str] = ["openclaw"]
        try:
            agents_cfg = getattr(self.config, "agents", None)
            agent_list = []
            if isinstance(agents_cfg, dict):
                agent_list = agents_cfg.get("list", []) or []
            elif agents_cfg is not None:
                agent_list = getattr(agents_cfg, "list", []) or []
            for entry in agent_list:
                agent_id = (entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None))
                if agent_id:
                    models.append(f"openclaw:{agent_id}")
                    models.append(f"agent:{agent_id}")
        except Exception:
            pass
        return models

    async def start(self, start_channels: bool = True, enable_tls: bool = False, cert_path: Optional[str] = None, key_path: Optional[str] = None) -> None:
        """
        Start unified Gateway server (HTTP + WebSocket on single port)
        
        This implementation matches openclaw-ts architecture:
        - Single port serves both HTTP (Control UI) and WebSocket (Gateway API)
        - Uses aiohttp for HTTP Upgrade pattern

        Args:
            start_channels: If True, start all enabled channels
            enable_tls: If True, enable TLS/SSL
            cert_path: Path to TLS certificate file
            key_path: Path to TLS key file
        """
        host = "127.0.0.1" if self.config.gateway.bind == "loopback" else "0.0.0.0"
        port = self.config.gateway.port

        # Setup SSL context if TLS is enabled
        ssl_context = None
        if enable_tls:
            if not cert_path or not key_path:
                raise ValueError("TLS enabled but cert_path or key_path not provided")
            
            import ssl
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(cert_path, key_path)
            logger.info("TLS/SSL enabled for Gateway server")

        protocol = "wss" if enable_tls else "ws"
        logger.info(f"Starting unified Gateway server on {host}:{port} (TLS: {enable_tls})")
        self.running = True
        self.started_at = time.time()

        # Create aiohttp application
        app = web.Application()
        
        # Register routes (matches openclaw-ts architecture)
        # enable_web_ui defaults to True if not explicitly set to False
        ui_enabled = getattr(self.config.gateway, 'enable_web_ui', None)
        if ui_enabled is None:
            ui_enabled = True
        
        # ------------------------------------------------------------------
        # OpenAI-compatible HTTP endpoints
        # Enabled via config: gateway.http.endpoints.chatCompletions.enabled
        # and gateway.http.endpoints.responses.enabled
        # Mirrors TS openclaw/src/gateway/server-http-openai.ts
        # ------------------------------------------------------------------
        http_endpoints_cfg = (
            getattr(getattr(self.config, "gateway", None), "http", None) or {}
        )
        if isinstance(http_endpoints_cfg, dict):
            endpoints_cfg = http_endpoints_cfg.get("endpoints", {}) or {}
        else:
            _ep = getattr(http_endpoints_cfg, "endpoints", None)
            endpoints_cfg = _ep if isinstance(_ep, dict) else {}

        chat_completions_enabled = (
            endpoints_cfg.get("chatCompletions", {}) or {}
        ).get("enabled", False) if isinstance(endpoints_cfg, dict) else False

        responses_enabled = (
            endpoints_cfg.get("responses", {}) or {}
        ).get("enabled", False) if isinstance(endpoints_cfg, dict) else False

        if chat_completions_enabled:
            app.router.add_post('/v1/chat/completions', self._handle_chat_completions_route)
            app.router.add_get('/v1/models', self._handle_list_models_route)
            logger.info("Registered /v1/chat/completions and /v1/models")

        if responses_enabled:
            app.router.add_post('/v1/responses', self._handle_responses_route)
            logger.info("Registered /v1/responses")

        if ui_enabled:
            # Root handles both WebSocket upgrade and Control UI
            app.router.add_get('/', self.handle_root)
            # Dedicated WebSocket endpoint
            app.router.add_get('/ws', self.handle_websocket)
            # Bootstrap config JSON endpoint (matches openclaw-ts)
            app.router.add_get('/__openclaw/control-ui-config.json', self.serve_bootstrap_config)
            # SPA fallback for all other paths
            app.router.add_get('/{path:.*}', self.serve_control_ui_spa)
        else:
            # Only WebSocket endpoints
            app.router.add_get('/', self.handle_websocket)
            app.router.add_get('/ws', self.handle_websocket)
        
        logger.info(f"Routes registered: WebSocket on / and /ws, Control UI: {ui_enabled}")

        # Start periodic tick broadcaster (mirrors TS gateway tick heartbeat)
        TICK_INTERVAL_S = 5.0
        async def _tick_loop() -> None:
            while self.running:
                try:
                    await asyncio.sleep(TICK_INTERVAL_S)
                    if not self.running:
                        break
                    ts = int(time.time() * 1000)
                    await self.broadcast_event("tick", {"ts": ts})
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.debug("tick broadcast error: %s", exc)

        self._tick_task = asyncio.create_task(_tick_loop())
        logger.debug("Tick broadcaster started (interval=%.1fs)", TICK_INTERVAL_S)

        # Start all enabled channels
        if start_channels:
            channel_results = await self.channel_manager.start_all()
            started = sum(1 for v in channel_results.values() if v)
            logger.info(f"Started {started}/{len(channel_results)} channels")

        # Start aiohttp server
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, host, port, ssl_context=ssl_context)
        try:
            await site.start()
        except OSError as exc:
            from openclaw.gateway.error_codes import GatewayLockError
            raise GatewayLockError(host, port, cause=exc) from exc
        
        logger.info(f"✓ Gateway server running on http{'s' if enable_tls else ''}://{host}:{port}")
        logger.info(f"✓ Control UI available at http{'s' if enable_tls else ''}://{host}:{port}/")
        logger.info(f"✓ WebSocket endpoint: {protocol}://{host}:{port}/ws")
        logger.info(f"✓ ChannelManager: {len(self.channel_manager.list_running())} channels running")

        try:
            # Keep server running
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Gateway server task cancelled, cleaning up...")
            raise
        finally:
            await runner.cleanup()

    # Removed _start_http_server - unified server now handles HTTP + WebSocket on single port
    
    async def stop(self) -> None:
        """Stop the Gateway server gracefully"""
        logger.info("Stopping Gateway server gracefully...")
        self.running = False

        # Stop HTTP server if running
        if self.http_server_task:
            logger.debug("Stopping HTTP server...")
            try:
                self.http_server_task.cancel()
                await asyncio.wait_for(self.http_server_task, timeout=2.0)
            except asyncio.CancelledError:
                logger.debug("HTTP server task cancelled")
            except asyncio.TimeoutError:
                logger.warning("HTTP server stop timed out")
            except Exception as e:
                logger.error(f"Error stopping HTTP server: {e}")

        # Close all WebSocket connections first
        if self.connections:
            logger.debug(f"Closing {len(self.connections)} WebSocket connections...")
            close_tasks = []
            for connection in list(self.connections):
                try:
                    close_tasks.append(connection.websocket.close())
                except Exception as e:
                    logger.debug(f"Error preparing connection close: {e}")
            
            if close_tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*close_tasks, return_exceptions=True), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("WebSocket close timed out")
            
            self.connections.clear()

        # Stop all channels
        logger.debug("Stopping all channels...")
        try:
            await asyncio.wait_for(self.channel_manager.stop_all(), timeout=3.0)
            logger.debug("All channels stopped")
        except asyncio.TimeoutError:
            logger.warning("Channel stop timed out")
        except Exception as e:
            logger.error(f"Error stopping channels: {e}")

        logger.info("Gateway server stopped gracefully")
