"""Unified logging system for openclaw-python.

Provides tslog-compatible JSON logging matching TypeScript openclaw.
"""

from .subsystem import create_subsystem_logger, SubsystemLogger
from .state import setup_logging, get_logging_state, set_logging_state
from .levels import LogLevel, level_from_string, MIN_LEVEL, MAX_LEVEL
from .console_capture import enable_console_capture, disable_console_capture

__all__ = [
    "create_subsystem_logger",
    "SubsystemLogger",
    "setup_logging",
    "get_logging_state",
    "set_logging_state",
    "LogLevel",
    "level_from_string",
    "MIN_LEVEL",
    "MAX_LEVEL",
    "enable_console_capture",
    "disable_console_capture",
]
