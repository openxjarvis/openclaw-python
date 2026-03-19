"""Plugin logger adapter

Mirrors openclaw/src/plugins/logger.ts
"""
from __future__ import annotations

import logging
from typing import Any, Protocol


class LoggerLike(Protocol):
    """Protocol for logger-like objects"""
    
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warn(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


class PluginLogger:
    """Plugin logger interface
    
    Provides consistent logging interface for plugins.
    """
    
    def __init__(self, logger: LoggerLike | None = None):
        self._logger = logger or logging.getLogger("openclaw.plugins")
    
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log info message"""
        self._logger.info(msg, *args, **kwargs)
    
    def warn(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log warning message"""
        self._logger.warn(msg, *args, **kwargs)
    
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log error message"""
        self._logger.error(msg, *args, **kwargs)
    
    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message"""
        if hasattr(self._logger, "debug"):
            self._logger.debug(msg, *args, **kwargs)


def create_plugin_loader_logger(logger: LoggerLike | None = None) -> PluginLogger:
    """Create a plugin logger from a logger-like object.
    
    Args:
        logger: Logger-like object or None (defaults to openclaw.plugins logger)
        
    Returns:
        PluginLogger instance
    """
    return PluginLogger(logger)


__all__ = ["PluginLogger", "LoggerLike", "create_plugin_loader_logger"]
