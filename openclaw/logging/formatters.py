"""Log message formatters with color support."""

from __future__ import annotations

import sys
import os
from typing import Optional

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False

from .levels import LogLevel
from .timestamps import format_local_iso_with_offset


# Color mappings (fallback to no color if colorama not available)
SUBSYSTEM_COLORS = ["cyan", "green", "yellow", "blue", "magenta", "red"]

# Color overrides for specific subsystems
SUBSYSTEM_COLOR_OVERRIDES = {
    "gmail-watcher": "blue",
}


def supports_color() -> bool:
    """Check if terminal supports color output.
    
    Returns:
        True if colors are supported
    """
    if not HAS_COLOR:
        return False
    
    # Check NO_COLOR environment variable
    if os.environ.get("NO_COLOR"):
        return False
    
    # Check FORCE_COLOR
    force_color = os.environ.get("FORCE_COLOR", "").strip()
    if force_color and force_color != "0":
        return True
    
    # Check if stdout is a TTY
    return sys.stdout.isatty() or sys.stderr.isatty()


def get_color(color_name: str, text: str) -> str:
    """Apply color to text.
    
    Args:
        color_name: Color name (cyan, green, yellow, etc.)
        text: Text to colorize
    
    Returns:
        Colorized text or plain text if colors not supported
    """
    if not supports_color() or not HAS_COLOR:
        return text
    
    color_map = {
        "cyan": Fore.CYAN,
        "green": Fore.GREEN,
        "yellow": Fore.YELLOW,
        "blue": Fore.BLUE,
        "magenta": Fore.MAGENTA,
        "red": Fore.RED,
        "gray": Fore.LIGHTBLACK_EX,
    }
    
    color_code = color_map.get(color_name, "")
    if not color_code:
        return text
    
    return f"{color_code}{text}{Style.RESET_ALL}"


def pick_subsystem_color(subsystem: str) -> str:
    """Pick a color for subsystem based on hash.
    
    Args:
        subsystem: Subsystem name
    
    Returns:
        Color name
    """
    # Check for override
    if subsystem in SUBSYSTEM_COLOR_OVERRIDES:
        return SUBSYSTEM_COLOR_OVERRIDES[subsystem]
    
    # Hash subsystem name to pick color
    hash_val = 0
    for char in subsystem:
        hash_val = (hash_val * 31 + ord(char)) & 0xFFFFFFFF
    
    idx = abs(hash_val) % len(SUBSYSTEM_COLORS)
    return SUBSYSTEM_COLORS[idx]


def format_subsystem_for_console(subsystem: str) -> str:
    """Format subsystem name for console display.
    
    Simplifies long subsystem paths.
    
    Args:
        subsystem: Full subsystem path
    
    Returns:
        Simplified subsystem name
    """
    parts = subsystem.split("/")
    
    # Remove common prefixes
    prefixes_to_drop = ["gateway", "channels", "providers"]
    while parts and parts[0] in prefixes_to_drop:
        parts.pop(0)
    
    if not parts:
        return subsystem
    
    # Take last 2 segments max
    if len(parts) > 2:
        parts = parts[-2:]
    
    return "/".join(parts)


def get_level_color(level: LogLevel) -> str:
    """Get color for log level.
    
    Args:
        level: Log level
    
    Returns:
        Color name
    """
    if level in (LogLevel.ERROR, LogLevel.FATAL):
        return "red"
    elif level == LogLevel.WARN:
        return "yellow"
    elif level in (LogLevel.DEBUG, LogLevel.TRACE):
        return "gray"
    else:
        return "cyan"


def format_console_line(
    level: LogLevel,
    subsystem: str,
    message: str,
    style: str = "pretty",
    meta: Optional[dict] = None
) -> str:
    """Format log line for console output.
    
    Matches TypeScript src/logging/subsystem.ts console formatting.
    
    Args:
        level: Log level
        subsystem: Subsystem name
        message: Log message
        style: Output style (pretty, compact, json)
        meta: Optional metadata
    
    Returns:
        Formatted log line
    """
    display_subsystem = format_subsystem_for_console(subsystem)
    
    if style == "json":
        import json
        from .timestamps import format_local_iso_with_offset
        return json.dumps({
            "time": format_local_iso_with_offset(),
            "level": level.name.lower(),
            "subsystem": display_subsystem,
            "message": message,
            **(meta or {})
        })
    
    # Colorize components
    subsystem_color = pick_subsystem_color(display_subsystem)
    level_color = get_level_color(level)
    
    prefix = get_color(subsystem_color, f"[{display_subsystem}]")
    level_text = get_color(level_color, message)
    
    # Pretty style (TTY): HH:MM:SS [subsystem] message
    if style == "pretty":
        from datetime import datetime
        time_str = datetime.now().strftime("%H:%M:%S")
        time_colored = get_color("gray", time_str)
        return f"{time_colored} {prefix} {level_text}"
    
    # Compact style (non-TTY): full ISO timestamp [subsystem] message
    elif style == "compact":
        timestamp = format_local_iso_with_offset()
        return f"{timestamp} {prefix} {level_text}"
    
    # Default: just prefix and message
    else:
        return f"{prefix} {level_text}"
