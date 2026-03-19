"""Global state and utilities — mirrors src/globals.ts"""
from __future__ import annotations

import logging

# Global verbose flag
_global_verbose = False


def set_verbose(v: bool) -> None:
    """Set global verbose flag"""
    global _global_verbose
    _global_verbose = v


def is_verbose() -> bool:
    """Check if verbose mode is enabled"""
    return _global_verbose


def should_log_verbose() -> bool:
    """
    Check if verbose logging should be enabled.
    
    Returns True if:
    - Global verbose flag is set, OR
    - File log level is DEBUG or lower
    """
    if _global_verbose:
        return True
    
    # Check if any logger has DEBUG level enabled
    root_logger = logging.getLogger()
    return root_logger.isEnabledFor(logging.DEBUG)


def log_verbose(message: str) -> None:
    """Log a verbose message"""
    if not should_log_verbose():
        return
    
    try:
        logger = logging.getLogger("openclaw")
        logger.debug(message)
    except Exception:
        # Ignore logger failures
        pass
    
    if _global_verbose:
        print(f"[verbose] {message}")


def log_verbose_console(message: str) -> None:
    """Log verbose message to console only"""
    if not _global_verbose:
        return
    print(f"[verbose] {message}")
