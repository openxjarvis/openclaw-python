"""
Unified Configuration System for OpenClaw

DEPRECATED: This module is deprecated. Please use openclaw.config.loader instead.

This module now acts as a compatibility shim that re-exports from the standard
loader to maintain backward compatibility.

Migration guide:
    OLD: from openclaw.config.unified import load_config
    NEW: from openclaw.config.loader import load_config

    OLD: from openclaw.config.unified import UnifiedConfig  
    NEW: from openclaw.config.schema import OpenClawConfig
"""
from __future__ import annotations

import warnings

# Issue deprecation warning when this module is imported
warnings.warn(
    "openclaw.config.unified is deprecated and will be removed in a future version. "
    "Use openclaw.config.loader and openclaw.config.schema instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from proper modules for backward compatibility
from openclaw.config.loader import load_config, save_config, get_config_path
from openclaw.config.schema import (
    OpenClawConfig,
    AgentConfig,
    GatewayConfig,
    ChannelsConfig,
)

# Aliases for backward compatibility
UnifiedConfig = OpenClawConfig
ConfigBuilder = OpenClawConfig  # ConfigBuilder is now just an alias to OpenClawConfig

__all__ = [
    "load_config",
    "save_config", 
    "get_config_path",
    "OpenClawConfig",
    "UnifiedConfig",  # Deprecated alias
    "ConfigBuilder",  # Deprecated alias
    "AgentConfig",
    "GatewayConfig",
    "ChannelsConfig",
]

