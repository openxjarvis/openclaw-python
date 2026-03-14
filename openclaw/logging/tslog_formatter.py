"""tslog-compatible JSON formatter.

Formats log entries to match TypeScript tslog JSON structure.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .levels import LogLevel, LOG_LEVEL_IDS, LOG_LEVEL_NAMES
from .timestamps import format_local_iso_with_offset, format_utc_iso
from .context import get_caller_context, get_python_version, get_hostname


class TslogFormatter:
    """Format log records to match TypeScript tslog format."""
    
    def __init__(self):
        """Initialize formatter."""
        self._runtime = "Python"
        self._runtime_version = get_python_version()
        self._hostname = get_hostname()
    
    def format_log_entry(
        self,
        level: LogLevel,
        subsystem: Optional[str],
        message: str,
        meta: Optional[dict] = None,
        extra_message_parts: Optional[list[str]] = None,
    ) -> str:
        """Format log entry as tslog JSON.
        
        Creates JSON structure matching TypeScript tslog:
        {
          "0": "{\"subsystem\":\"gateway/ws\"}",
          "1": "message text",
          "2": ...,  # additional message parts
          "_meta": { ... },
          "time": "2026-03-14T02:49:13.184+08:00"
        }
        
        Args:
            level: Log level
            subsystem: Subsystem name (e.g., "gateway/ws")
            message: Log message
            meta: Optional metadata dict
            extra_message_parts: Additional message parts for numbered keys
        
        Returns:
            JSON string for log entry
        """
        entry: dict[str, Any] = {}
        
        # Build numbered keys for message parts
        # Key "0" contains subsystem info as JSON string
        if subsystem:
            subsystem_json = json.dumps({"subsystem": subsystem})
            entry["0"] = subsystem_json
            
            # Message goes in "1"
            entry["1"] = message
            
            # Additional parts in "2", "3", etc.
            if extra_message_parts:
                for i, part in enumerate(extra_message_parts, start=2):
                    entry[str(i)] = part
        else:
            # No subsystem, message goes in "0"
            entry["0"] = message
            
            # Additional parts in "1", "2", etc.
            if extra_message_parts:
                for i, part in enumerate(extra_message_parts, start=1):
                    entry[str(i)] = part
        
        # Add metadata if provided as additional numbered keys
        if meta:
            # If meta contains simple values, add them as numbered keys
            # Complex meta goes into _meta
            pass
        
        # Build _meta structure
        caller_context = get_caller_context(skip_frames=2)
        
        meta_dict = {
            "runtime": self._runtime,
            "runtimeVersion": self._runtime_version,
            "hostname": self._hostname,
            "date": format_utc_iso(),
            "logLevelId": LOG_LEVEL_IDS[level],
            "logLevelName": LOG_LEVEL_NAMES[level],
            "path": caller_context,
        }
        
        # Add subsystem to name field (matches tslog format)
        if subsystem:
            meta_dict["name"] = json.dumps({"subsystem": subsystem})
            meta_dict["parentNames"] = ["openclaw"]
        else:
            meta_dict["name"] = "openclaw"
        
        # Add custom meta if provided
        if meta:
            for key, value in meta.items():
                if key not in meta_dict:
                    meta_dict[key] = value
        
        entry["_meta"] = meta_dict
        
        # Add local timezone timestamp
        entry["time"] = format_local_iso_with_offset()
        
        # Return as JSON string (single line)
        return json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
    
    def format_console_log_entry(
        self,
        level: LogLevel,
        message: str,
        meta: Optional[dict] = None,
    ) -> str:
        """Format console.log style entry (for captured print statements).
        
        Args:
            level: Log level
            message: Log message
            meta: Optional metadata
        
        Returns:
            JSON string for log entry
        """
        entry: dict[str, Any] = {
            "0": message,
        }
        
        caller_context = get_caller_context(skip_frames=3)
        
        meta_dict = {
            "runtime": self._runtime,
            "runtimeVersion": self._runtime_version,
            "hostname": self._hostname,
            "name": "openclaw",
            "date": format_utc_iso(),
            "logLevelId": LOG_LEVEL_IDS[level],
            "logLevelName": LOG_LEVEL_NAMES[level],
            "path": caller_context,
        }
        
        if meta:
            for key, value in meta.items():
                if key not in meta_dict:
                    meta_dict[key] = value
        
        entry["_meta"] = meta_dict
        entry["time"] = format_local_iso_with_offset()
        
        return json.dumps(entry, ensure_ascii=False, separators=(',', ':'))
