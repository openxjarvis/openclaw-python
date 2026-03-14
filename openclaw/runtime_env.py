"""
RuntimeEnv - Runtime Environment Abstraction

This module provides a unified RuntimeEnv abstraction that encapsulates
Agent Runtime and related dependencies, matching the TypeScript OpenClaw architecture.

Architecture:
    RuntimeEnv = AgentRuntime + SessionManager + ToolRegistry + Config

    Multiple RuntimeEnv instances can exist, each with different configurations.
    Channels can use different RuntimeEnv instances for isolation.

Usage:
    # Create RuntimeEnv
    env = RuntimeEnv(
        env_id="production",
        model="anthropic/claude-sonnet-4",
        workspace=Path("./workspace")
    )

    # Execute a turn
    async for event in env.execute_turn("session-123", "Hello!"):
        print(event)

    # Use in ChannelManager
    channel_manager.set_runtime_env("telegram", env)
"""
from __future__ import annotations


import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .agents.runtime import AgentRuntime, MultiProviderRuntime
from .agents.session import Session, SessionManager
from .agents.tools.base import AgentTool
from .agents.tools.registry import ToolRegistry
from .events import Event

logger = logging.getLogger(__name__)


@dataclass
class RuntimeEnv:
    """
    Runtime Environment

    Encapsulates Agent Runtime and related dependencies into a single
    reusable unit. This matches the TypeScript OpenClaw RuntimeEnv concept.

    Features:
    - Unified execution interface
    - Configuration isolation
    - Session management
    - Tool registry
    - Custom prompts and tools

    Example:
        env = RuntimeEnv(
            env_id="my-env",
            model="anthropic/claude-sonnet-4",
            workspace=Path("./workspace"),
            config={"temperature": 0.7}
        )

        async for event in env.execute_turn("session-1", "Hello"):
            print(event.data)
    """

    # Identity
    env_id: str

    # Core components (created lazily)
    _agent_runtime: AgentRuntime | None = field(default=None, repr=False)
    _session_manager: SessionManager | None = field(default=None, repr=False)
    _tool_registry: ToolRegistry | None = field(default=None, repr=False)

    # Configuration
    model: str = "anthropic/claude-sonnet-4-20250514"
    workspace: Path | None = None
    config: dict[str, Any] = field(default_factory=dict)

    # Optional overrides
    custom_tools: list[AgentTool] | None = None
    custom_prompt: str | None = None
    system_message: str | None = None

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    description: str = ""

    # Runtime state
    _initialized: bool = field(default=False, repr=False)

    def __post_init__(self):
        """Initialize after dataclass creation"""
        if self.workspace is None:
            self.workspace = Path("./workspace") / self.env_id

        # Ensure workspace exists
        self.workspace.mkdir(parents=True, exist_ok=True)

    @property
    def agent_runtime(self) -> AgentRuntime:
        """Get or create AgentRuntime"""
        if self._agent_runtime is None:
            self._agent_runtime = MultiProviderRuntime(model=self.model, **self.config)
            logger.info(f"Created AgentRuntime for env '{self.env_id}': {self.model}")
        return self._agent_runtime

    @property
    def session_manager(self) -> SessionManager:
        """Get or create SessionManager"""
        if self._session_manager is None:
            self._session_manager = SessionManager(self.workspace)
            logger.info(f"Created SessionManager for env '{self.env_id}'")
        return self._session_manager

    @property
    def tool_registry(self) -> ToolRegistry:
        """Get or create ToolRegistry"""
        if self._tool_registry is None:
            self._tool_registry = ToolRegistry()

            # Add custom tools if provided
            if self.custom_tools:
                for tool in self.custom_tools:
                    self._tool_registry.register(tool)

            logger.info(f"Created ToolRegistry for env '{self.env_id}'")
        return self._tool_registry

    async def execute_turn(
        self,
        session_id: str,
        message: str,
        tools: list[AgentTool] | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Event]:
        """
        Execute a conversation turn

        This is the main interface for executing Agent turns in this environment.

        Args:
            session_id: Session identifier
            message: User message
            tools: Optional tools (defaults to registry tools)
            max_tokens: Optional max tokens override

        Yields:
            Event: Agent events (text, thinking, tool use, etc.)

        Example:
            async for event in env.execute_turn("sess-1", "Hello"):
                if event.type == EventType.AGENT_TEXT:
                    print(event.data.get("text"))
        """
        # Get or create session
        session = self.session_manager.get_session(session_id)

        # Use provided tools or default to registry
        if tools is None and self._tool_registry:
            tools = list(self._tool_registry.get_all())

        # Build or use system prompt
        system_prompt = self.custom_prompt or self.system_message
        
        # If no custom prompt, build a proper one using the new architecture
        if not system_prompt:
            try:
                from .agents.system_prompt import build_agent_system_prompt
                from .agents.system_prompt_bootstrap import load_bootstrap_files, format_bootstrap_context
                from .agents.system_prompt_params import get_runtime_info
                
                # Build runtime info
                runtime_info = get_runtime_info(
                    agent_id=self.env_id,
                    model=self.model,
                    channel=None
                )
                
                # Load bootstrap files
                bootstrap_files = load_bootstrap_files(self.workspace)
                context_files = format_bootstrap_context(bootstrap_files)
                
                # Get tool names
                tool_names = [tool.name for tool in (tools or [])]
                
                # Build system prompt
                system_prompt = build_agent_system_prompt(
                    workspace_dir=self.workspace,
                    tool_names=tool_names,
                    prompt_mode="full",
                    runtime_info=runtime_info,
                    context_files=context_files,
                )
                
                logger.debug(f"Built system prompt for {session_id} ({len(system_prompt)} chars)")
            except Exception as e:
                logger.warning(f"Failed to build system prompt: {e}")
                system_prompt = None

        # Execute turn through AgentRuntime
        async for event in self.agent_runtime.run_turn(
            session=session,
            message=message,
            tools=tools,
            max_tokens=max_tokens or self.config.get("max_tokens"),
            system_prompt=system_prompt,
        ):
            yield event

    def get_session(self, session_id: str) -> Session:
        """Get or create a session"""
        return self.session_manager.get_session(session_id)

    def set_agent_runtime(self, runtime: AgentRuntime):
        """Set custom AgentRuntime"""
        self._agent_runtime = runtime
        logger.info(f"Set custom AgentRuntime for env '{self.env_id}'")

    def log(self, message: str, level: str = "info") -> None:
        """
        Unified logging method matching TypeScript runtime.log()
        
        Args:
            message: Message to log
            level: Log level (info, debug, warning, error)
        """
        log_method = getattr(logger, level, logger.info)
        log_method(f"[{self.env_id}] {message}")
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "env_id": self.env_id,
            "model": self.model,
            "workspace": str(self.workspace),
            "config": self.config,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
            "has_custom_tools": self.custom_tools is not None,
            "has_custom_prompt": self.custom_prompt is not None,
            "initialized": self._initialized,
        }

    def __repr__(self) -> str:
        return f"RuntimeEnv(env_id={self.env_id}, model={self.model})"


# ============================================================================
# RuntimeEnv Manager
# ============================================================================


class RuntimeEnvManager:
    """
    Manages multiple RuntimeEnv instances

    Features:
    - Create and register environments
    - Default environment
    - Environment lookup by ID

    Example:
        manager = RuntimeEnvManager()

        # Create envs
        prod_env = manager.create_env("production", "anthropic/claude-opus-4")
        dev_env = manager.create_env("development", "anthropic/claude-haiku")

        # Set default
        manager.set_default("production")

        # Get env
        env = manager.get_env("production")
    """

    def __init__(self):
        self._envs: dict[str, RuntimeEnv] = {}
        self._default_env_id: str | None = None

        logger.info("RuntimeEnvManager initialized")

    def create_env(
        self,
        env_id: str,
        model: str,
        workspace: Path | None = None,
        config: dict[str, Any] | None = None,
        **kwargs,
    ) -> RuntimeEnv:
        """
        Create a new RuntimeEnv

        Args:
            env_id: Unique environment ID
            model: LLM model to use
            workspace: Optional workspace path
            config: Optional configuration
            **kwargs: Additional RuntimeEnv parameters

        Returns:
            RuntimeEnv instance

        Example:
            env = manager.create_env(
                "telegram-bot",
                "anthropic/claude-sonnet-4",
                config={"temperature": 0.7}
            )
        """
        if env_id in self._envs:
            logger.warning(f"RuntimeEnv '{env_id}' already exists, returning existing")
            return self._envs[env_id]

        env = RuntimeEnv(
            env_id=env_id, model=model, workspace=workspace, config=config or {}, **kwargs
        )

        self._envs[env_id] = env

        # Set as default if this is the first env
        if self._default_env_id is None:
            self._default_env_id = env_id

        logger.info(f"Created RuntimeEnv: {env_id} (model={model})")
        return env

    def register_env(self, env: RuntimeEnv):
        """
        Register an existing RuntimeEnv

        Args:
            env: RuntimeEnv instance to register
        """
        self._envs[env.env_id] = env
        logger.info(f"Registered RuntimeEnv: {env.env_id}")

    def get_env(self, env_id: str) -> RuntimeEnv | None:
        """
        Get RuntimeEnv by ID

        Args:
            env_id: Environment ID

        Returns:
            RuntimeEnv or None if not found
        """
        return self._envs.get(env_id)

    def get_default_env(self) -> RuntimeEnv | None:
        """Get the default RuntimeEnv"""
        if self._default_env_id:
            return self._envs.get(self._default_env_id)
        return None

    def set_default(self, env_id: str):
        """
        Set the default RuntimeEnv

        Args:
            env_id: Environment ID to set as default
        """
        if env_id not in self._envs:
            raise ValueError(f"RuntimeEnv '{env_id}' not found")

        self._default_env_id = env_id
        logger.info(f"Set default RuntimeEnv to: {env_id}")

    def list_envs(self) -> list[str]:
        """List all registered environment IDs"""
        return list(self._envs.keys())

    def remove_env(self, env_id: str) -> bool:
        """
        Remove a RuntimeEnv

        Args:
            env_id: Environment ID to remove

        Returns:
            True if removed, False if not found
        """
        if env_id in self._envs:
            del self._envs[env_id]

            # Clear default if it was removed
            if self._default_env_id == env_id:
                self._default_env_id = None
                # Set new default to first available
                if self._envs:
                    self._default_env_id = list(self._envs.keys())[0]

            logger.info(f"Removed RuntimeEnv: {env_id}")
            return True

        return False

    def get_all_envs(self) -> dict[str, RuntimeEnv]:
        """Get all RuntimeEnv instances"""
        return self._envs.copy()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "default_env": self._default_env_id,
            "total_envs": len(self._envs),
            "envs": {env_id: env.to_dict() for env_id, env in self._envs.items()},
        }

    def __repr__(self) -> str:
        return f"RuntimeEnvManager(envs={len(self._envs)}, default={self._default_env_id})"


# ============================================================================
# Global RuntimeEnv Manager (optional)
# ============================================================================


_global_manager: RuntimeEnvManager | None = None


def get_runtime_env_manager() -> RuntimeEnvManager:
    """Get the global RuntimeEnvManager instance"""
    global _global_manager
    if _global_manager is None:
        _global_manager = RuntimeEnvManager()
    return _global_manager
