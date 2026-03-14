"""Timestamp formatting for tslog compatibility.

Aligned with TypeScript src/logging/timestamps.ts
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional


def format_local_iso_with_offset(dt: Optional[datetime] = None) -> str:
    """Format datetime with local timezone offset.
    
    Returns timestamp in format: YYYY-MM-DDTHH:mm:ss.SSS±HH:mm
    Example: "2026-03-14T02:49:13.184+08:00"
    
    Args:
        dt: Datetime to format (defaults to now)
    
    Returns:
        ISO timestamp with local timezone offset
    """
    if dt is None:
        dt = datetime.now()
    
    # Get local timezone offset
    # Try TZ environment variable first (like TypeScript process.env.TZ)
    tz_name = os.environ.get('TZ')
    
    if tz_name:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_name)
            dt = dt.replace(tzinfo=tz)
        except Exception:
            # Fall back to local timezone
            dt = dt.astimezone()
    else:
        # Use system local timezone
        dt = dt.astimezone()
    
    # Format: YYYY-MM-DDTHH:mm:ss.SSS±HH:mm
    # Get the offset
    offset = dt.utcoffset()
    if offset is None:
        offset_str = "+00:00"
    else:
        total_seconds = int(offset.total_seconds())
        hours, remainder = divmod(abs(total_seconds), 3600)
        minutes = remainder // 60
        sign = '+' if total_seconds >= 0 else '-'
        offset_str = f"{sign}{hours:02d}:{minutes:02d}"
    
    # Format the datetime part with milliseconds
    ms = dt.microsecond // 1000
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    return f"{base}.{ms:03d}{offset_str}"


def format_utc_iso(dt: Optional[datetime] = None) -> str:
    """Format datetime as UTC ISO timestamp.
    
    Returns timestamp in format: YYYY-MM-DDTHH:mm:ss.SSSZ
    Example: "2026-03-13T18:52:49.680Z"
    
    Args:
        dt: Datetime to format (defaults to now)
    
    Returns:
        ISO timestamp in UTC
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    
    # Format with milliseconds
    ms = dt.microsecond // 1000
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    
    return f"{base}.{ms:03d}Z"


def get_local_timezone_name() -> str:
    """Get local timezone name.
    
    Returns:
        Timezone name (e.g., "Asia/Shanghai")
    """
    tz_name = os.environ.get('TZ')
    if tz_name:
        return tz_name
    
    try:
        # Try to get system timezone
        import zoneinfo
        local_tz = datetime.now().astimezone().tzinfo
        if hasattr(local_tz, 'key'):
            return local_tz.key
    except Exception:
        pass
    
    return "UTC"
