"""Console capture for logging print() statements.

Aligned with TypeScript src/logging/console.ts
"""

from __future__ import annotations

import sys
from typing import TextIO, Optional
from pathlib import Path

from .levels import LogLevel
from .tslog_formatter import TslogFormatter


# Suppressed prefixes (don't log these to file)
SUPPRESSED_PREFIXES = [
    "Closing session:",
    "Opening session:",
    "Removing old closed session:",
    "Session already closed",
    "Session already open",
]


class ConsoleCapture:
    """Capture console output and log to file."""
    
    def __init__(self, original_stream: TextIO, log_file_path: Optional[str]):
        """Initialize console capture.
        
        Args:
            original_stream: Original stdout/stderr
            log_file_path: Path to log file
        """
        self.original_stream = original_stream
        self.log_file_path = log_file_path
        self._tslog_formatter = TslogFormatter()
        self._file_handle = None
    
    def _get_file_handle(self):
        """Get or create file handle.
        
        Returns:
            File handle for writing
        """
        if self._file_handle:
            return self._file_handle
        
        if not self.log_file_path:
            return None
        
        log_path = Path(self.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._file_handle = open(log_path, 'a', encoding='utf-8')
        return self._file_handle
    
    def write(self, text: str) -> int:
        """Write text to both original stream and log file.
        
        Args:
            text: Text to write
        
        Returns:
            Number of characters written
        """
        # Always write to original stream
        result = self.original_stream.write(text)
        
        # Check if should suppress logging to file
        if self._should_suppress(text):
            return result
        
        # Write to file in tslog format
        if self.log_file_path and text.strip():
            file_handle = self._get_file_handle()
            if file_handle:
                try:
                    # Determine log level based on stream
                    level = LogLevel.ERROR if self.original_stream == sys.__stderr__ else LogLevel.INFO
                    
                    json_line = self._tslog_formatter.format_console_log_entry(
                        level=level,
                        message=text.rstrip('\n'),
                    )
                    file_handle.write(json_line + '\n')
                    file_handle.flush()
                except Exception:
                    pass
        
        return result
    
    def _should_suppress(self, text: str) -> bool:
        """Check if text should be suppressed from file logging.
        
        Args:
            text: Text to check
        
        Returns:
            True if should suppress
        """
        text_stripped = text.strip()
        for prefix in SUPPRESSED_PREFIXES:
            if text_stripped.startswith(prefix):
                return True
        return False
    
    def flush(self):
        """Flush streams."""
        self.original_stream.flush()
        if self._file_handle:
            self._file_handle.flush()
    
    def isatty(self) -> bool:
        """Check if original stream is a TTY.
        
        Returns:
            True if TTY
        """
        return self.original_stream.isatty()
    
    def __del__(self):
        """Clean up file handle."""
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass


_console_capture_enabled = False
_original_stdout = None
_original_stderr = None


def enable_console_capture(log_file_path: Optional[str] = None):
    """Enable console capture to log file.
    
    Patches sys.stdout and sys.stderr to also write to log file.
    
    Args:
        log_file_path: Path to log file
    """
    global _console_capture_enabled, _original_stdout, _original_stderr
    
    if _console_capture_enabled:
        return
    
    # Save original streams
    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    
    # Replace with capture wrappers
    sys.stdout = ConsoleCapture(_original_stdout, log_file_path)
    sys.stderr = ConsoleCapture(_original_stderr, log_file_path)
    
    _console_capture_enabled = True


def disable_console_capture():
    """Disable console capture and restore original streams."""
    global _console_capture_enabled, _original_stdout, _original_stderr
    
    if not _console_capture_enabled:
        return
    
    # Restore original streams
    if _original_stdout:
        sys.stdout = _original_stdout
    if _original_stderr:
        sys.stderr = _original_stderr
    
    _console_capture_enabled = False
    _original_stdout = None
    _original_stderr = None


def is_console_capture_enabled() -> bool:
    """Check if console capture is enabled.
    
    Returns:
        True if enabled
    """
    return _console_capture_enabled
