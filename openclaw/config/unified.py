"""
Unified Configuration System for OpenClaw

This module provides a unified, type-safe configuration system that replaces
the scattered configuration across multiple files.

Features:
- Pydantic models for validation
- Environment variable support
- JSON/YAML file loading
- Nested configuration
- Defaults and validation

Usage:
    # From file
    config = OpenClawConfig.from_file("openclaw.json")

    # From environment
    config = OpenClawConfig.from_env()

    # Programmatic
    config = OpenClawConfig(
        agent=AgentConfig(model="anthropic/claude-sonnet-4"),
        gateway=GatewayConfig(port=18789)
    )
"""
from __future__ import annotations


import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, validator

# =============================================================================
# Agent Configuration
# =============================================================================


class AgentModelConfig(BaseModel):
    """Agent model configuration"""

    model: str = "anthropic/claude-sonnet-4-20250514"
    max_tokens: int = 4000
    temperature: float = 0.7
    thinking_mode: str = "off"  # off, on, stream

    # Advanced features
    enable_context_management: bool = True
    enable_queuing: bool = False
    tool_format: str = "markdown"
    compaction_strategy: str = "keep_important"

    # Retry and failover
    max_retries: int = 3
    fallback_models: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """Complete agent configuration"""

    # Model settings
    model: AgentModelConfig | str = Field(default_factory=lambda: AgentModelConfig())

    # Workspace
    workspace: str = "~/.openclaw/workspace"

    # Tools
    tools_profile: str = "full"  # full, safe, minimal
    tools_allow: list[str] = Field(default_factory=list)
    tools_deny: list[str] = Field(default_factory=list)

    @validator("model", pre=True)
    def parse_model(cls, v):
        """Parse model string or dict"""
        if isinstance(v, str):
            return AgentModelConfig(model=v)
        elif isinstance(v, dict):
            return AgentModelConfig(**v)
        return v


# =============================================================================
# Channel Configuration
# =============================================================================


class SingleChannelConfig(BaseModel):
    """Configuration for a single channel"""

    enabled: bool = True

    # Connection settings (platform-specific)
    config: dict[str, Any] = Field(default_factory=dict)

    # Runtime environment
    runtime_env: str | None = None  # RuntimeEnv ID to use

    # Access control
    allow_from: list[str] = Field(default_factory=list)
    deny_from: list[str] = Field(default_factory=list)

    # Features
    auto_start: bool = True
    health_check_interval: int = 30  # seconds


class ChannelsConfig(BaseModel):
    """Configuration for all channels"""

    # Core channels
    telegram: SingleChannelConfig | None = None
    discord: SingleChannelConfig | None = None
    slack: SingleChannelConfig | None = None

    # Additional channels
    whatsapp: SingleChannelConfig | None = None
    signal: SingleChannelConfig | None = None
    matrix: SingleChannelConfig | None = None
    teams: SingleChannelConfig | None = None
    webchat: SingleChannelConfig | None = None

    # Global settings
    auto_start_all: bool = False
    max_concurrent: int = 10

    def get_enabled(self) -> dict[str, SingleChannelConfig]:
        """Get all enabled channels"""
        enabled = {}
        for name, config in self:
            if config and config.enabled:
                enabled[name] = config
        return enabled

    def __iter__(self):
        """Iterate over all channel configs"""
        for field_name in self.__fields__:
            if field_name in ["auto_start_all", "max_concurrent"]:
                continue
            value = getattr(self, field_name)
            if value is not None:
                yield field_name, value


# =============================================================================
# Gateway Configuration
# =============================================================================


class GatewaySecurityConfig(BaseModel):
    """Gateway security configuration"""

    auth_mode: str = "none"  # none, token, password
    auth_token: str | None = None
    auth_password: str | None = None

    # Rate limiting
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 100
    rate_limit_window: int = 60  # seconds

    # CORS
    cors_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class GatewayConfig(BaseModel):
    """Gateway server configuration"""

    # Server settings
    port: int = 8765
    bind: str = "loopback"  # loopback, all

    # Features
    auto_start_channels: bool = True
    auto_discover_channels: bool = False
    max_connections: int = 100

    # Security
    security: GatewaySecurityConfig = Field(default_factory=GatewaySecurityConfig)

    # WebSocket settings
    ping_interval: int = 30
    ping_timeout: int = 10

    @property
    def host(self) -> str:
        """Get host address from bind setting"""
        return "127.0.0.1" if self.bind == "loopback" else "0.0.0.0"


# =============================================================================
# Monitoring Configuration
# =============================================================================


class MonitoringConfig(BaseModel):
    """Monitoring and observability configuration"""

    # Logging
    log_level: str = "INFO"
    log_format: str = "colored"  # colored, json, text
    log_file: str | None = None

    # Metrics
    metrics_enabled: bool = False
    metrics_port: int = 9090
    metrics_path: str = "/metrics"

    # Health checks
    health_check_enabled: bool = True
    health_check_interval: int = 30

    # Tracing
    tracing_enabled: bool = False
    tracing_endpoint: str | None = None


# =============================================================================
# RuntimeEnv Configuration
# =============================================================================


class RuntimeEnvConfigEntry(BaseModel):
    """Configuration for a single RuntimeEnv"""

    env_id: str
    model: str = "anthropic/claude-sonnet-4-20250514"
    workspace: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class RuntimeEnvsConfig(BaseModel):
    """Configuration for RuntimeEnv instances"""

    default_env: str = "production"
    envs: list[RuntimeEnvConfigEntry] = Field(default_factory=list)

    def get_env(self, env_id: str) -> RuntimeEnvConfigEntry | None:
        """Get environment by ID"""
        for env in self.envs:
            if env.env_id == env_id:
                return env
        return None


# =============================================================================
# Main Configuration
# =============================================================================


class OpenClawConfig(BaseModel):
    """
    Unified OpenClaw configuration

    This replaces OpenClawConfig and provides a complete, type-safe
    configuration system for all OpenClaw components.

    Example:
        config = OpenClawConfig(
            agent=AgentConfig(
                model="anthropic/claude-sonnet-4"
            ),
            gateway=GatewayConfig(
                port=18789,
                auto_start_channels=True
            ),
            channels=ChannelsConfig(
                telegram=SingleChannelConfig(
                    enabled=True,
                    config={"bot_token": "..."}
                )
            )
        )
    """

    # Core components
    agent: AgentConfig = Field(default_factory=AgentConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)

    # Runtime environments
    runtime_envs: RuntimeEnvsConfig = Field(default_factory=RuntimeEnvsConfig)

    # Monitoring
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)

    # Optional features
    skills: dict[str, Any] = Field(default_factory=dict)
    plugins: dict[str, Any] = Field(default_factory=dict)

    # Metadata
    version: str = "0.6.0"

    # ==========================================================================
    # File I/O
    # ==========================================================================

    @classmethod
    def from_file(cls, path: str | Path) -> "OpenClawConfig":
        """
        Load configuration from JSON or YAML file

        Args:
            path: Path to config file

        Returns:
            OpenClawConfig instance

        Example:
            config = OpenClawConfig.from_file("openclaw.json")
        """
        path = Path(path).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            if path.suffix == ".json":
                data = json.load(f)
            elif path.suffix in [".yaml", ".yml"]:
                import yaml

                data = yaml.safe_load(f)
            else:
                raise ValueError(f"Unsupported file type: {path.suffix}")

        return cls(**data)

    @classmethod
    def from_env(cls, prefix: str = "OPENCLAW_") -> "OpenClawConfig":
        """
        Load configuration from environment variables

        Environment variable format:
            OPENCLAW_AGENT__MODEL=anthropic/claude-sonnet-4
            OPENCLAW_GATEWAY__PORT=18789
            OPENCLAW_CHANNELS__TELEGRAM__ENABLED=true

        Args:
            prefix: Environment variable prefix

        Returns:
            OpenClawConfig instance

        Example:
            config = OpenClawConfig.from_env()
        """
        # Build config dict from env vars
        config_dict: dict[str, Any] = {}

        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue

            # Remove prefix and split by __
            key_parts = key[len(prefix) :].lower().split("__")

            # Navigate dict and set value
            current = config_dict
            for part in key_parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Parse value
            last_key = key_parts[-1]
            if value.lower() in ["true", "false"]:
                current[last_key] = value.lower() == "true"
            elif value.isdigit():
                current[last_key] = int(value)
            else:
                current[last_key] = value

        return cls(**config_dict) if config_dict else cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OpenClawConfig":
        """Create from dictionary"""
        return cls(**data)

    def to_file(self, path: str | Path):
        """
        Save configuration to file

        Args:
            path: Output file path

        Example:
            config.to_file("openclaw.json")
        """
        path = Path(path).expanduser()

        with open(path, "w") as f:
            if path.suffix == ".json":
                json.dump(self.dict(), f, indent=2)
            elif path.suffix in [".yaml", ".yml"]:
                import yaml

                yaml.dump(self.dict(), f, default_flow_style=False)
            else:
                raise ValueError(f"Unsupported file type: {path.suffix}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return self.dict()

    # ==========================================================================
    # Convenience Methods
    # ==========================================================================

    def get_enabled_channels(self) -> dict[str, SingleChannelConfig]:
        """Get all enabled channels"""
        return self.channels.get_enabled()

    def get_runtime_env(self, env_id: str) -> RuntimeEnvConfigEntry | None:
        """Get RuntimeEnv configuration by ID"""
        return self.runtime_envs.get_env(env_id)

    def get_default_runtime_env(self) -> RuntimeEnvConfigEntry | None:
        """Get default RuntimeEnv configuration"""
        return self.runtime_envs.get_env(self.runtime_envs.default_env)


# =============================================================================
# Configuration Builder (Fluent API)
# =============================================================================


class ConfigBuilder:
    """
    Fluent API for building OpenClawConfig

    Example:
        config = (ConfigBuilder()
            .with_agent(model="anthropic/claude-sonnet-4")
            .with_gateway(port=18789)
            .with_channel("telegram", enabled=True, config={"bot_token": "..."})
            .build())
    """

    def __init__(self):
        self._data: dict[str, Any] = {}

    def with_agent(self, model: str | None = None, **kwargs) -> "ConfigBuilder":
        """Configure agent"""
        agent_data = kwargs.copy()
        if model:
            agent_data["model"] = model
        self._data["agent"] = agent_data
        return self

    def with_gateway(self, **kwargs) -> "ConfigBuilder":
        """Configure gateway"""
        self._data["gateway"] = kwargs
        return self

    def with_channel(self, channel_name: str, enabled: bool = True, **kwargs) -> "ConfigBuilder":
        """Configure a channel"""
        if "channels" not in self._data:
            self._data["channels"] = {}
        self._data["channels"][channel_name] = {"enabled": enabled, **kwargs}
        return self

    def with_monitoring(self, **kwargs) -> "ConfigBuilder":
        """Configure monitoring"""
        self._data["monitoring"] = kwargs
        return self

    def build(self) -> OpenClawConfig:
        """Build the configuration"""
        return OpenClawConfig(**self._data)


# =============================================================================
# Helper Functions
# =============================================================================


def load_config(path: str | Path | None = None) -> OpenClawConfig:
    """
    Load configuration from file or environment

    Priority:
    1. Provided path
    2. OPENCLAW_CONFIG environment variable
    3. ~/.openclaw/openclaw.json
    4. ./openclaw.json
    5. Default configuration

    Args:
        path: Optional config file path

    Returns:
        OpenClawConfig instance
    """
    # Try provided path
    if path:
        return OpenClawConfig.from_file(path)

    # Try environment variable
    env_path = os.getenv("OPENCLAW_CONFIG")
    if env_path:
        return OpenClawConfig.from_file(env_path)

    # Try standard locations
    standard_paths = [
        Path.home() / ".openclaw" / "openclaw.json",
        Path.home() / ".openclaw" / "openclaw.yaml",
        Path("openclaw.json"),
        Path("openclaw.yaml"),
    ]

    for std_path in standard_paths:
        if std_path.exists():
            return OpenClawConfig.from_file(std_path)

    # Try environment variables
    try:
        config = OpenClawConfig.from_env()
        # Check if any env vars were actually set
        if config.dict() != OpenClawConfig().dict():
            return config
    except Exception:
        pass

    # Return default
    return OpenClawConfig()


def create_default_config(path: str | Path):
    """
    Create a default configuration file

    Args:
        path: Output file path

    Example:
        create_default_config("~/.openclaw/openclaw.json")
    """
    config = OpenClawConfig()
    config.to_file(path)
    print(f"Created default configuration: {path}")
