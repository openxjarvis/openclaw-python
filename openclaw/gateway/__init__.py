"""
Gateway WebSocket server implementation

The Gateway provides:
1. ChannelManager - Manages channel plugins (Telegram, Discord, etc.)
2. WebSocket API - Serves external clients (UI, CLI, mobile)
3. Event Broadcasting - Broadcasts Agent events to all clients

Architecture:
    Gateway Server
        ├── ChannelManager (manages channel plugins)
        ├── WebSocket Server (for external clients)
        └── Event Broadcasting (Observer Pattern)
"""

try:
    from .channel_manager import (
        ChannelEventListener,
        ChannelManager,
        ChannelRuntimeEnv,
        ChannelState,
        discover_channel_plugins,
        load_channel_plugins,
    )
except ModuleNotFoundError:
    ChannelEventListener = None  # type: ignore[assignment]
    ChannelManager = None  # type: ignore[assignment]
    ChannelRuntimeEnv = None  # type: ignore[assignment]
    ChannelState = None  # type: ignore[assignment]

    def discover_channel_plugins(*args, **kwargs):  # type: ignore[override]
        raise ModuleNotFoundError("Channel runtime dependencies are missing.")

    def load_channel_plugins(*args, **kwargs):  # type: ignore[override]
        raise ModuleNotFoundError("Channel runtime dependencies are missing.")
from .protocol import (
    ErrorShape,
    EventFrame,
    RequestFrame,
    ResponseFrame,
)
try:
    from .server import GatewayConnection, GatewayServer
except ModuleNotFoundError:
    GatewayConnection = None  # type: ignore[assignment]
    GatewayServer = None  # type: ignore[assignment]

# New modules for 100% alignment
from .channel_health_monitor import ChannelHealthMonitor, ChannelHealthStatus
from .channel_health_policy import ChannelHealthPolicy, ChannelHealthAction, evaluate_channel_health_policy
from .openresponses_http import send_openresponse_http
from .net import BindMode, resolve_bind_host, get_local_ip, is_port_available
from .probe import ProbeResult, probe_http_endpoint, probe_websocket_endpoint

__all__ = [
    # Server
    "GatewayServer",
    "GatewayConnection",
    # Channel Manager
    "ChannelManager",
    "ChannelState",
    "ChannelRuntimeEnv",
    "ChannelEventListener",
    "discover_channel_plugins",
    "load_channel_plugins",
    # Protocol
    "RequestFrame",
    "ResponseFrame",
    "EventFrame",
    "ErrorShape",
    # Health monitoring
    "ChannelHealthMonitor",
    "ChannelHealthStatus",
    "ChannelHealthPolicy",
    "ChannelHealthAction",
    "evaluate_channel_health_policy",
    # HTTP and network
    "send_openresponse_http",
    "BindMode",
    "resolve_bind_host",
    "get_local_ip",
    "is_port_available",
    # Probe
    "ProbeResult",
    "probe_http_endpoint",
    "probe_websocket_endpoint",
]
