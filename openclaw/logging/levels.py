"""Log level definitions."""

from __future__ import annotations

from enum import IntEnum


class LogLevel(IntEnum):
    """Log levels aligned with TypeScript."""
    
    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    FATAL = 5
    SILENT = 999


# Minimum and maximum levels
MIN_LEVEL = LogLevel.TRACE
MAX_LEVEL = LogLevel.FATAL


# tslog level IDs (matches TypeScript tslog numbering)
# Note: Different from Python's default numbering
LOG_LEVEL_IDS = {
    LogLevel.FATAL: 0,
    LogLevel.ERROR: 1,
    LogLevel.DEBUG: 2,
    LogLevel.INFO: 3,
    LogLevel.WARN: 4,
    LogLevel.TRACE: 5,
    LogLevel.SILENT: 999,
}


# tslog level names
LOG_LEVEL_NAMES = {
    LogLevel.FATAL: "FATAL",
    LogLevel.ERROR: "ERROR",
    LogLevel.DEBUG: "DEBUG",
    LogLevel.INFO: "INFO",
    LogLevel.WARN: "WARN",
    LogLevel.TRACE: "TRACE",
    LogLevel.SILENT: "SILENT",
}


def level_from_string(level: str) -> LogLevel:
    """Convert string to LogLevel.
    
    Args:
        level: Level string (case-insensitive)
    
    Returns:
        LogLevel enum value
    """
    level_upper = level.upper()
    
    if level_upper == "TRACE":
        return LogLevel.TRACE
    elif level_upper == "DEBUG":
        return LogLevel.DEBUG
    elif level_upper == "INFO":
        return LogLevel.INFO
    elif level_upper == "WARN":
        return LogLevel.WARN
    elif level_upper == "ERROR":
        return LogLevel.ERROR
    elif level_upper == "FATAL":
        return LogLevel.FATAL
    elif level_upper == "SILENT":
        return LogLevel.SILENT
    else:
        return LogLevel.INFO


def should_log(current_level: LogLevel, min_level: LogLevel) -> bool:
    """Check if message at current level should be logged.
    
    Args:
        current_level: Level of the message
        min_level: Minimum level to log
    
    Returns:
        True if message should be logged
    """
    if min_level == LogLevel.SILENT:
        return False
    
    return current_level >= min_level
