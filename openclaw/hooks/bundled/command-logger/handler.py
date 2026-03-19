"""Command logger hook handler.

Logs all command events to a centralized audit file for debugging and monitoring.

Aligned with TypeScript openclaw/src/hooks/bundled/command-logger/handler.ts
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


async def log_command(event: Any) -> None:
    """Log all command events to a file.
    
    Args:
        event: The hook event
    """
    # Only trigger on command events
    if event.type != "command":
        return
    
    try:
        # Create log directory
        state_dir = resolve_state_dir()
        log_dir = state_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Append to command log file
        log_file = log_dir / "commands.log"
        log_entry = {
            "timestamp": event.timestamp.isoformat(),
            "action": event.action,
            "sessionKey": event.session_key,
            "senderId": event.context.get("senderId") or event.context.get("sender_id") or "unknown",
            "source": event.context.get("commandSource") or event.context.get("command_source") or "unknown",
        }
        log_line = json.dumps(log_entry) + "\n"
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as err:
        logger.error(f"[command-logger] Failed to log command: {err}")


# Default export (matches TS pattern)
default = log_command
