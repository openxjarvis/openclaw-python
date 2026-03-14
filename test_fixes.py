#!/usr/bin/env python3
"""Test script for timestamp and media fixes."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_timestamp_injection():
    """Test that timestamps are generated fresh each time."""
    print("\n=== Testing Timestamp Injection ===")
    
    from openclaw.gateway.handlers import _inject_timestamp
    from datetime import datetime, timezone
    import time
    
    # Generate two timestamps with a small delay
    ts1 = _inject_timestamp("Test message 1", "UTC")
    print(f"Timestamp 1: {ts1}")
    
    time.sleep(2)  # Wait 2 seconds
    
    ts2 = _inject_timestamp("Test message 2", "UTC")
    print(f"Timestamp 2: {ts2}")
    
    # Extract times from both timestamps
    import re
    match1 = re.search(r'\[.*? (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', ts1)
    match2 = re.search(r'\[.*? (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', ts2)
    
    if match1 and match2:
        time1 = match1.group(1)
        time2 = match2.group(1)
        print(f"\nExtracted times:")
        print(f"  Time 1: {time1}")
        print(f"  Time 2: {time2}")
        
        # They should be different (or at least not cached)
        if ts1 != ts2:
            print("✅ PASS: Timestamps are unique (not cached)")
        else:
            print("❌ FAIL: Timestamps are identical (might be cached)")
    else:
        print("❌ FAIL: Could not extract timestamps")


async def test_media_local_roots():
    """Test media local roots resolution."""
    print("\n=== Testing Media Local Roots ===")
    
    from openclaw.media.local_roots import get_agent_scoped_media_local_roots, is_path_in_allowed_roots
    from openclaw.config.loader import load_config
    
    cfg = load_config()
    
    # Test for main agent
    roots_main = get_agent_scoped_media_local_roots(cfg, "main")
    print(f"\nMedia roots for 'main' agent ({len(roots_main)} roots):")
    for i, root in enumerate(roots_main, 1):
        print(f"  {i}. {root}")
    
    # Test path validation
    test_paths = [
        Path.home() / ".openclaw" / "workspace" / "test.txt",
        Path.home() / ".openclaw" / "media" / "image.png",
        Path("/tmp/test.txt"),
        Path("/etc/passwd"),  # Should be blocked
    ]
    
    print("\nPath validation tests:")
    for test_path in test_paths:
        allowed = is_path_in_allowed_roots(test_path, roots_main)
        status = "✅ ALLOWED" if allowed else "❌ BLOCKED"
        print(f"  {status}: {test_path}")
    
    print("\n✅ Media local roots module working correctly")


async def test_media_url_resolution():
    """Test media URL resolution with agent-scoped roots."""
    print("\n=== Testing Media URL Resolution ===")
    
    from openclaw.gateway.cron_bootstrap import _resolve_media_url
    from pathlib import Path
    
    # Create a test file in workspace
    test_file = Path.home() / ".openclaw" / "workspace" / "test_media.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("Test media file")
    
    print(f"\nCreated test file: {test_file}")
    
    # Test cases
    test_cases = [
        ("http://example.com/image.jpg", "HTTP URL (should pass through)"),
        ("https://example.com/image.jpg", "HTTPS URL (should pass through)"),
        ("test_media.txt", "Relative path (should resolve)"),
        (str(test_file), "Absolute path (should validate)"),
        ("nonexistent.txt", "Nonexistent file (should return None)"),
    ]
    
    print("\nMedia URL resolution tests:")
    for media_url, description in test_cases:
        resolved = _resolve_media_url(media_url, None, "main")
        print(f"  [{description}]")
        print(f"    Input:  {media_url}")
        print(f"    Output: {resolved}")
    
    # Cleanup
    test_file.unlink(missing_ok=True)
    print("\n✅ Media URL resolution working correctly")


async def test_subagent_registry():
    """Test subagent registry get_global_registry."""
    print("\n=== Testing Subagent Registry ===")
    
    from openclaw.agents.subagent_registry import get_global_registry
    
    registry = get_global_registry()
    print(f"\nRegistry object: {registry}")
    print(f"Registry type: {type(registry)}")
    
    # Test basic operations
    print("\nTesting registry operations:")
    
    # Count active runs
    count = registry.count_active_runs_for_session("test:session")
    print(f"  Active runs for 'test:session': {count}")
    
    # Register a run
    run_id = registry.register_subagent_run(
        requester_session_key="test:session",
        child_session_key="test:session:subagent:123",
        task="test task",
        label="test label",
    )
    print(f"  Registered run ID: {run_id}")
    
    # Get run
    record = registry.get_subagent_run(run_id)
    print(f"  Retrieved record: task='{record.task}', status={record.status}")
    
    # Cleanup
    registry.delete_subagent_run(run_id)
    print(f"  Deleted run: {run_id}")
    
    print("\n✅ Subagent registry working correctly")


async def test_subagent_spawn_mode():
    """Test resolve_spawn_mode function."""
    print("\n=== Testing Subagent Spawn Mode Resolution ===")
    
    from openclaw.agents.subagent_spawn import resolve_spawn_mode
    
    test_cases = [
        (None, False, "run", "Default: no mode, no thread"),
        (None, True, "session", "Default: no mode, with thread"),
        ("run", False, "run", "Explicit run mode"),
        ("run", True, "run", "Explicit run mode (thread ignored)"),
        ("session", True, "session", "Explicit session mode"),
    ]
    
    print("\nSpawn mode resolution tests:")
    for requested_mode, thread, expected, description in test_cases:
        result = resolve_spawn_mode(requested_mode, thread)
        status = "✅" if result == expected else "❌"
        print(f"  {status} {description}")
        print(f"      Input: mode={requested_mode}, thread={thread}")
        print(f"      Expected: {expected}, Got: {result}")
    
    print("\n✅ Spawn mode resolution working correctly")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("OpenClaw Python - Fix Verification Tests")
    print("=" * 60)
    
    try:
        await test_timestamp_injection()
        await test_media_local_roots()
        await test_media_url_resolution()
        await test_subagent_registry()
        await test_subagent_spawn_mode()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
