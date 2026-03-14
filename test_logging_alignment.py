"""Test script to verify tslog format alignment."""

import json
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from openclaw.logging import (
    create_subsystem_logger,
    setup_logging,
)


def test_basic_logging():
    """Test basic logging output."""
    print("=" * 60)
    print("Testing OpenClaw Python Logging System")
    print("=" * 60)
    
    # Setup logging
    log_dir = Path.home() / ".openclaw" / "tmp"
    log_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = str(log_dir / f"openclaw-test-{date_str}.log")
    
    print(f"\nLog file: {log_file}")
    
    setup_logging(
        level="DEBUG",
        console_style="pretty",
        log_file=log_file,
    )
    
    # Create subsystem loggers
    gateway_logger = create_subsystem_logger("gateway")
    ws_logger = create_subsystem_logger("gateway/ws")
    channels_logger = create_subsystem_logger("gateway/channels/telegram")
    
    print("\n--- Testing console output (should show colored text) ---\n")
    
    # Test different log levels
    gateway_logger.info("Gateway starting up")
    gateway_logger.info("listening on ws://127.0.0.1:18789, ws://[::1]:18789 (PID 12345)")
    
    ws_logger.debug("Connection established", {"conn_id": "abc123"})
    ws_logger.info("⇄ res ✓ config.schema 97ms conn=4529b864…a550 id=0d0c1f7f…144d")
    
    channels_logger.info("connected")
    channels_logger.warn("connection unstable", {"attempts": 3})
    
    gateway_logger.error("Failed to start service", {"error": "Port already in use"})
    
    print("\n--- Log file created ---")
    print(f"Location: {log_file}")
    
    # Read and display first few lines of log file
    print("\n--- Sample log entries (JSON format) ---\n")
    try:
        with open(log_file, 'r') as f:
            lines = f.readlines()
            for i, line in enumerate(lines[:5], 1):
                try:
                    entry = json.loads(line)
                    print(f"Entry {i}:")
                    print(f"  Time: {entry.get('time')}")
                    print(f"  Level: {entry.get('_meta', {}).get('logLevelName')}")
                    print(f"  Subsystem: {entry.get('0')}")
                    print(f"  Message: {entry.get('1')}")
                    print()
                except json.JSONDecodeError:
                    print(f"Entry {i}: (invalid JSON)")
                    print()
    except Exception as e:
        print(f"Error reading log file: {e}")
    
    print("=" * 60)
    print("Verification complete!")
    print("=" * 60)
    print("\nExpected format:")
    print('  - "0" key contains subsystem as JSON string')
    print('  - "1" key contains main message')
    print('  - "time" uses local timezone with offset (e.g., +08:00)')
    print('  - "_meta" contains full metadata (runtime, path, etc.)')
    print("\nCompare with TypeScript log: /Users/long/Downloads/openclaw-2026-03-14.log")


if __name__ == "__main__":
    test_basic_logging()
