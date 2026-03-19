"""
OpenClaw Python - Personal AI Assistant Platform

A Python implementation of the OpenClaw personal AI assistant platform.

Example usage:
    from openclaw import RuntimeEnv, Event, EventType
    from pathlib import Path

    # Create runtime environment
    env = RuntimeEnv(
        env_id="my-env",
        model="anthropic/claude-sonnet-4",
        workspace=Path("./workspace")
    )

    # Execute turn with unified events
    async for event in env.execute_turn("session-1", "Hello!"):
        if event.type == EventType.AGENT_TEXT:
            print(event.data.get("text", ""))
"""

__version__ = "0.8.2"
__author__ = "OpenClaw Contributors"

try:
    from .agents import AgentRuntime, Session, SessionManager
except ModuleNotFoundError:
    # Optional provider SDKs may be absent in lightweight/dev environments.
    AgentRuntime = None  # type: ignore[assignment]
    Session = None  # type: ignore[assignment]
    SessionManager = None  # type: ignore[assignment]

from .config import Settings, get_settings
from .config.loader import load_config, save_config, get_config_path
from .config.schema import OpenClawConfig

# Backward compatibility alias
ConfigBuilder = OpenClawConfig

# Refactored modules (v0.6.0+)
from .events import Event, EventBus, EventType, get_event_bus

try:
    from .gateway.api import MethodRegistry, get_method_registry
except ModuleNotFoundError:
    MethodRegistry = None  # type: ignore[assignment]

    def get_method_registry():  # type: ignore[override]
        raise ModuleNotFoundError(
            "Gateway API dependencies are missing. Install optional runtime dependencies."
        )

try:
    from .monitoring import get_health_check, get_metrics, setup_logging
except ModuleNotFoundError:
    def get_health_check():  # type: ignore[override]
        raise ModuleNotFoundError("Monitoring dependencies are missing.")

    def get_metrics():  # type: ignore[override]
        raise ModuleNotFoundError("Monitoring dependencies are missing.")

    def setup_logging(*args, **kwargs):  # type: ignore[override]
        raise ModuleNotFoundError("Monitoring dependencies are missing.")
try:
    from .runtime_env import RuntimeEnv, RuntimeEnvManager, get_runtime_env_manager
except ModuleNotFoundError:
    RuntimeEnv = None  # type: ignore[assignment]
    RuntimeEnvManager = None  # type: ignore[assignment]

    def get_runtime_env_manager():  # type: ignore[override]
        raise ModuleNotFoundError(
            "RuntimeEnv dependencies are missing. Install optional provider/tool SDKs."
        )

__all__ = [
    # Version
    "__version__",
    # Core (legacy)
    "AgentRuntime",
    "Session",
    "SessionManager",
    "get_settings",
    "Settings",
    "get_health_check",
    "get_metrics",
    "setup_logging",
    # Events (v0.6.0+)
    "Event",
    "EventType",
    "EventBus",
    "get_event_bus",
    # RuntimeEnv (v0.6.0+)
    "RuntimeEnv",
    "RuntimeEnvManager",
    "get_runtime_env_manager",
    # Configuration (v0.6.0+)
    "OpenClawConfig",
    "ConfigBuilder",
    # Gateway API (v0.6.0+)
    "MethodRegistry",
    "get_method_registry",
]
