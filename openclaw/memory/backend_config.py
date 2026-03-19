"""Memory backend configuration

Mirrors openclaw/src/memory/backend-config.ts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


MemoryBackend = Literal["builtin", "qmd", "remote"]


@dataclass
class MemoryBackendConfig:
    """Memory backend configuration"""
    
    backend: MemoryBackend = "builtin"
    """Backend type"""
    
    builtin: dict[str, Any] | None = None
    """Builtin backend config"""
    
    qmd: dict[str, Any] | None = None
    """QMD backend config"""
    
    remote: dict[str, Any] | None = None
    """Remote backend config"""


def resolve_memory_backend_config(
    config: dict[str, Any],
) -> MemoryBackendConfig:
    """Resolve memory backend configuration.
    
    Args:
        config: OpenClaw configuration
        
    Returns:
        Memory backend config
    """
    memory_config = config.get("memory", {})
    
    backend = memory_config.get("backend", "builtin")
    
    return MemoryBackendConfig(
        backend=backend,
        builtin=memory_config.get("builtin"),
        qmd=memory_config.get("qmd"),
        remote=memory_config.get("remote"),
    )


__all__ = [
    "MemoryBackend",
    "MemoryBackendConfig",
    "resolve_memory_backend_config",
]
