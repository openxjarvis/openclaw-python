"""Global logging state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import sys

from .levels import LogLevel, level_from_string


@dataclass
class LoggingState:
    """Global logging state."""
    
    console_level: LogLevel = LogLevel.INFO
    console_style: str = "pretty"  # pretty, compact, json
    console_timestamp_prefix: bool = False
    force_console_to_stderr: bool = False
    raw_console: Optional[any] = None
    file_logging_enabled: bool = True
    file_log_path: Optional[str] = None
    console_capture_enabled: bool = False


# Global state instance
_LOGGING_STATE = LoggingState()


def get_logging_state() -> LoggingState:
    """Get global logging state.
    
    Returns:
        Current logging state
    """
    return _LOGGING_STATE


def set_logging_state(**kwargs) -> None:
    """Update global logging state.
    
    Args:
        **kwargs: State fields to update
    """
    global _LOGGING_STATE
    
    for key, value in kwargs.items():
        if hasattr(_LOGGING_STATE, key):
            setattr(_LOGGING_STATE, key, value)


def get_console_settings() -> dict:
    """Get console logging settings.
    
    Returns:
        Dictionary with console settings
    """
    return {
        "level": _LOGGING_STATE.console_level,
        "style": _LOGGING_STATE.console_style
    }


def setup_logging(
    level: str = "INFO",
    console_style: str = "pretty",
    log_file: Optional[str] = None,
    enable_console_capture: bool = False,
) -> None:
    """Setup logging system.
    
    Args:
        level: Console log level (TRACE, DEBUG, INFO, WARN, ERROR, FATAL)
        console_style: Console output style (pretty, compact, json)
        log_file: Optional log file path
        enable_console_capture: Whether to capture print() statements
    """
    # Update state
    set_logging_state(
        console_level=level_from_string(level),
        console_style=console_style,
        file_logging_enabled=log_file is not None,
        file_log_path=log_file,
        console_capture_enabled=enable_console_capture,
    )
    
    # Enable console capture if requested
    if enable_console_capture and log_file:
        from .console_capture import enable_console_capture as enable_capture
        enable_capture(log_file)

