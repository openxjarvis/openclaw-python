"""Subsystem-based structured logging.

Aligned with TypeScript src/logging/subsystem.ts
"""

from __future__ import annotations

import sys
from typing import Optional, Protocol
from pathlib import Path

from .levels import LogLevel, should_log
from .state import get_console_settings, get_logging_state
from .formatters import format_console_line
from .tslog_formatter import TslogFormatter


class SubsystemLogger(Protocol):
    """Protocol for subsystem logger."""
    
    subsystem: str
    
    def trace(self, message: str, meta: Optional[dict] = None) -> None: ...
    def debug(self, message: str, meta: Optional[dict] = None) -> None: ...
    def info(self, message: str, meta: Optional[dict] = None) -> None: ...
    def warn(self, message: str, meta: Optional[dict] = None) -> None: ...
    def error(self, message: str, meta: Optional[dict] = None) -> None: ...
    def fatal(self, message: str, meta: Optional[dict] = None) -> None: ...
    def raw(self, message: str) -> None: ...
    def child(self, name: str) -> SubsystemLogger: ...


class SubsystemLoggerImpl:
    """Implementation of subsystem logger.
    
    Provides structured, colorized logging with subsystem tagging.
    Outputs to file in tslog JSON format.
    """
    
    def __init__(self, subsystem: str):
        """Initialize logger for subsystem.
        
        Args:
            subsystem: Subsystem name (e.g., "gateway/auth")
        """
        self.subsystem = subsystem
        self._tslog_formatter = TslogFormatter()
        self._file_handle = None
    
    def _get_file_handle(self):
        """Get or create file handle for logging.
        
        Returns:
            File handle for writing logs
        """
        if self._file_handle:
            return self._file_handle
        
        state = get_logging_state()
        if not state.file_logging_enabled or not state.file_log_path:
            return None
        
        log_path = Path(state.file_log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Open in append mode
        self._file_handle = open(log_path, 'a', encoding='utf-8')
        return self._file_handle
    
    def _emit(self, level: LogLevel, message: str, meta: Optional[dict] = None) -> None:
        """Emit log message.
        
        Args:
            level: Log level
            message: Log message
            meta: Optional metadata
        """
        state = get_logging_state()
        console_settings = get_console_settings()
        
        # Log to file in tslog JSON format
        if state.file_logging_enabled:
            file_handle = self._get_file_handle()
            if file_handle:
                try:
                    json_line = self._tslog_formatter.format_log_entry(
                        level=level,
                        subsystem=self.subsystem,
                        message=message,
                        meta=meta,
                    )
                    file_handle.write(json_line + '\n')
                    file_handle.flush()
                except Exception as e:
                    # Fallback to stderr if file logging fails
                    print(f"Logging error: {e}", file=sys.stderr)
        
        # Check if should log to console
        if not should_log(level, console_settings["level"]):
            return
        
        # Format and write to console
        formatted = format_console_line(
            level=level,
            subsystem=self.subsystem,
            message=message,
            style=console_settings["style"],
            meta=meta
        )
        
        # Write to appropriate stream
        stream = sys.stderr if state.force_console_to_stderr or level >= LogLevel.ERROR else sys.stdout
        
        print(formatted, file=stream)
    
    def trace(self, message: str, meta: Optional[dict] = None) -> None:
        """Log trace message."""
        self._emit(LogLevel.TRACE, message, meta)
    
    def debug(self, message: str, meta: Optional[dict] = None) -> None:
        """Log debug message."""
        self._emit(LogLevel.DEBUG, message, meta)
    
    def info(self, message: str, meta: Optional[dict] = None) -> None:
        """Log info message."""
        self._emit(LogLevel.INFO, message, meta)
    
    def warn(self, message: str, meta: Optional[dict] = None) -> None:
        """Log warning message."""
        self._emit(LogLevel.WARN, message, meta)
    
    def error(self, message: str, meta: Optional[dict] = None) -> None:
        """Log error message."""
        self._emit(LogLevel.ERROR, message, meta)
    
    def fatal(self, message: str, meta: Optional[dict] = None) -> None:
        """Log fatal message."""
        self._emit(LogLevel.FATAL, message, meta)
    
    def raw(self, message: str) -> None:
        """Log raw message without formatting.
        
        Args:
            message: Raw message text
        """
        state = get_logging_state()
        stream = sys.stderr if state.force_console_to_stderr else sys.stdout
        print(message, file=stream)
    
    def child(self, name: str) -> SubsystemLogger:
        """Create child logger with nested subsystem name.
        
        Args:
            name: Child subsystem name
        
        Returns:
            New SubsystemLogger for child subsystem
        """
        child_subsystem = f"{self.subsystem}/{name}"
        return create_subsystem_logger(child_subsystem)
    
    def __del__(self):
        """Clean up file handle on deletion."""
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass


def create_subsystem_logger(subsystem: str) -> SubsystemLogger:
    """Create a subsystem logger.
    
    Args:
        subsystem: Subsystem name (e.g., "gateway/auth")
    
    Returns:
        SubsystemLogger instance
    """
    return SubsystemLoggerImpl(subsystem)


# Example usage:
# logger = create_subsystem_logger("gateway/auth")
# logger.info("User authenticated", {"userId": "123"})
# logger.error("Authentication failed")
