"""
Enhanced logging for ClawdBot

This module provides backward compatibility with the new unified logging system.
All logging now uses tslog-compatible JSON format matching TypeScript openclaw.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from datetime import datetime

# Import from new unified logging system
from openclaw.logging import (
    setup_logging as new_setup_logging,
    create_subsystem_logger,
    get_logging_state,
)
from openclaw.logging.tslog_formatter import TslogFormatter
from openclaw.logging.levels import LogLevel, LOG_LEVEL_NAMES


class TslogHandler(logging.Handler):
    """Handler that outputs logs in tslog JSON format."""
    
    def __init__(self, log_file: str):
        super().__init__()
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._file_handle = open(self.log_file, 'a', encoding='utf-8')
        self._formatter = TslogFormatter()
    
    def emit(self, record: logging.LogRecord):
        """Emit a log record in tslog format."""
        try:
            # Map Python log level to our LogLevel
            level_map = {
                logging.DEBUG: LogLevel.DEBUG,
                logging.INFO: LogLevel.INFO,
                logging.WARNING: LogLevel.WARN,
                logging.ERROR: LogLevel.ERROR,
                logging.CRITICAL: LogLevel.FATAL,
            }
            level = level_map.get(record.levelno, LogLevel.INFO)
            
            # Extract subsystem from logger name
            # e.g., "openclaw.gateway.bootstrap" -> "gateway/bootstrap"
            logger_name = record.name
            if logger_name.startswith("openclaw."):
                subsystem = logger_name[9:].replace(".", "/")
            else:
                subsystem = logger_name.replace(".", "/")
            
            # Format message
            message = self.format(record)
            
            # Build metadata
            meta = {}
            if hasattr(record, 'extra'):
                meta.update(record.extra)
            
            # Write in tslog format
            json_line = self._formatter.format_log_entry(
                level=level,
                subsystem=subsystem if subsystem else None,
                message=message,
                meta=meta,
            )
            
            self._file_handle.write(json_line + '\n')
            self._file_handle.flush()
        except Exception:
            self.handleError(record)
    
    def close(self):
        """Close the file handle."""
        try:
            if self._file_handle:
                self._file_handle.close()
        finally:
            super().close()


def setup_logging(
    level: str = "INFO",
    format_type: str = "colored",  # "colored", "json", "simple"
    log_file: str | None = None,
    file_level: str = "DEBUG",
) -> None:
    """
    Setup logging configuration (backward compatibility wrapper).
    
    Now delegates to the new unified logging system which outputs
    tslog-compatible JSON to files and formatted text to console.
    
    Args:
        level: Console log level
        format_type: Log format type (maps to console_style)
        log_file: Optional log file path
        file_level: File log level (ignored, all levels logged to file)
    """
    # Map format_type to console_style
    style_map = {
        "colored": "pretty",
        "json": "json",
        "simple": "compact",
    }
    console_style = style_map.get(format_type, "pretty")
    
    # Configure root logger to use tslog format for file output
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler with simple formatting (colorized)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))
    
    # Simple console format (tslog-style subsystem display)
    console_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    console_handler.setFormatter(logging.Formatter(console_format))
    root_logger.addHandler(console_handler)
    
    # File handler with tslog JSON format
    if log_file:
        tslog_handler = TslogHandler(log_file)
        tslog_handler.setLevel(logging.DEBUG)  # All levels to file
        root_logger.addHandler(tslog_handler)
    
    # Also setup new unified logging system
    new_setup_logging(
        level=level,
        console_style=console_style,
        log_file=log_file,
        enable_console_capture=False,
    )
    
    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def get_logger(name: str):
    """Get a logger instance (backward compatibility).
    
    Now returns standard library logger which will use tslog format for file output.
    
    Args:
        name: Logger name
    
    Returns:
        Standard logging.Logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding context to logs (deprecated).
    
    This is kept for backward compatibility but not actively used
    in the new tslog-based system.
    """
    
    def __init__(self, logger, **context):
        self.logger = logger
        self.context = context
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def log_with_context(logger, **context) -> LogContext:
    """Create a logging context (deprecated).
    
    Kept for backward compatibility.
    """
    return LogContext(logger, **context)


# Legacy exports (deprecated but kept for compatibility)
class JSONFormatter(logging.Formatter):
    """Legacy JSON formatter (deprecated)."""
    
    def format(self, record: logging.LogRecord) -> str:
        import json
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        })


class ColoredFormatter(logging.Formatter):
    """Legacy colored formatter (deprecated)."""
    
    def format(self, record: logging.LogRecord) -> str:
        return f"{record.levelname} - {record.getMessage()}"
