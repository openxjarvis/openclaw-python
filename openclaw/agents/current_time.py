"""Current time formatting for agent prompts.

Fully aligned with TypeScript openclaw/src/agents/current-time.ts

Provides functionality to append formatted current date/time to agent prompts,
particularly for cron and heartbeat operations.
"""
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)


def resolve_current_date_time_line(
    config: dict[str, Any],
    now_ms: Optional[int] = None,
) -> str | None:
    """
    Resolve formatted current date/time line from config.
    
    Mirrors TS resolveCurrentDateTimeLine() from current-time.ts lines 26-43
    
    Args:
        config: Agent configuration dict
        now_ms: Current time in milliseconds (defaults to now)
        
    Returns:
        Formatted date/time string or None if timezone not configured
    """
    # Extract timezone from config
    timezone_str = None
    
    # Check agents.defaults.timezone
    if isinstance(config.get("agents"), dict):
        defaults = config["agents"].get("defaults", {})
        if isinstance(defaults, dict):
            timezone_str = defaults.get("timezone")
    
    if not timezone_str or not isinstance(timezone_str, str):
        return None
    
    timezone_str = timezone_str.strip()
    if not timezone_str:
        return None
    
    # Resolve timestamp
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)
    
    try:
        # Convert ms to seconds
        timestamp_seconds = now_ms / 1000.0
        
        # Parse timezone
        tz = ZoneInfo(timezone_str)
        dt = datetime.fromtimestamp(timestamp_seconds, tz=tz)
        
        # Format: "Current date/time: YYYY-MM-DD HH:MM:SS TZ"
        formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        return f"Current date/time: {formatted_time}"
    
    except Exception as e:
        logger.warning(f"Failed to format current time with timezone {timezone_str}: {e}")
        return None


def append_cron_style_current_time_line(
    prompt: str,
    config: dict[str, Any],
    now_ms: Optional[int] = None,
) -> str:
    """
    Append current date/time line to prompt (cron-style).
    
    Mirrors TS appendCronStyleCurrentTimeLine() from current-time.ts lines 45-52
    
    Args:
        prompt: Original prompt text
        config: Agent configuration dict
        now_ms: Current time in milliseconds (defaults to now)
        
    Returns:
        Prompt with appended time line, or original if time not available
    """
    resolved = resolve_current_date_time_line(config, now_ms)
    if resolved:
        return f"{prompt}\n\n{resolved}"
    return prompt


def format_current_time(
    timezone_str: str | None = None,
    now_ms: Optional[int] = None,
) -> str | None:
    """
    Format current time with optional timezone.
    
    Args:
        timezone_str: IANA timezone string (e.g., "America/New_York")
        now_ms: Current time in milliseconds (defaults to now)
        
    Returns:
        Formatted time string or None if timezone invalid
    """
    if not timezone_str:
        return None
    
    # Resolve timestamp
    if now_ms is None:
        import time
        now_ms = int(time.time() * 1000)
    
    try:
        timestamp_seconds = now_ms / 1000.0
        tz = ZoneInfo(timezone_str.strip())
        dt = datetime.fromtimestamp(timestamp_seconds, tz=tz)
        return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception as e:
        logger.warning(f"Failed to format time with timezone {timezone_str}: {e}")
        return None


__all__ = [
    "resolve_current_date_time_line",
    "append_cron_style_current_time_line",
    "format_current_time",
]
