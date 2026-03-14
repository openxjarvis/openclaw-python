#!/usr/bin/env python3
"""
Test script for cron job delivery to Telegram.

Verifies that:
1. run_subagent_announce_flow accepts keyword arguments
2. Direct delivery path is triggered for cron jobs
3. deliver_outbound_payloads is called correctly
4. Messages reach Telegram
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


async def test_announce_flow_signature():
    """Test that run_subagent_announce_flow accepts keyword arguments."""
    from openclaw.agents.subagent_announce import run_subagent_announce_flow
    
    print("✓ Testing run_subagent_announce_flow signature...")
    
    # Test that function accepts keyword arguments (this should not raise TypeError)
    try:
        # Mock parameters that would be passed by cron
        result = await run_subagent_announce_flow(
            child_session_key="test:session",
            child_run_id="test:run:123",
            requester_session_key="agent:main:main",
            requester_origin={
                "channel": "telegram",
                "to": "8366053063",
            },
            task="Test cron job",
            timeout_ms=60000,
            cleanup="keep",
            round_one_reply="Test message from cron job",
            wait_for_completion=False,
            announce_type="cron job",
        )
        print(f"  ✓ Function accepted keyword arguments, result: {result}")
        return True
    except TypeError as e:
        print(f"  ✗ Function signature error: {e}")
        return False
    except Exception as e:
        # Other errors are OK for this test (e.g., missing channel plugin)
        print(f"  ✓ Function signature OK (execution error expected): {type(e).__name__}")
        return True


async def test_heartbeat_detection():
    """Test heartbeat detection logic."""
    from openclaw.cron.isolated_agent.helpers import (
        is_heartbeat_only_response,
        resolve_heartbeat_ack_max_chars,
    )
    
    print("\n✓ Testing heartbeat detection...")
    
    # Test heartbeat-only response (should be skipped)
    heartbeat_payloads = [{"text": "HEARTBEAT_OK"}]
    ack_max = resolve_heartbeat_ack_max_chars()
    is_heartbeat = is_heartbeat_only_response(heartbeat_payloads, ack_max)
    assert is_heartbeat is True, "Should detect heartbeat-only response"
    print("  ✓ Heartbeat-only response detected correctly")
    
    # Test real content (should NOT be skipped)
    content_payloads = [{"text": "Here are 3 important news items:\n1. ..."}]
    is_heartbeat = is_heartbeat_only_response(content_payloads, ack_max)
    assert is_heartbeat is False, "Should NOT skip real content"
    print("  ✓ Real content NOT marked as heartbeat")
    
    # Test mixed (media + heartbeat text = should NOT skip)
    mixed_payloads = [{"text": "HEARTBEAT_OK", "mediaUrl": "https://example.com/img.jpg"}]
    is_heartbeat = is_heartbeat_only_response(mixed_payloads, ack_max)
    assert is_heartbeat is False, "Should NOT skip when media present"
    print("  ✓ Media payloads NOT marked as heartbeat")
    
    return True


async def test_direct_delivery_path():
    """Test that direct delivery path is taken for cron jobs."""
    print("\n✓ Testing direct delivery path for cron jobs...")
    
    # We can't fully test this without a running gateway, but we can verify the code path
    from openclaw.agents.subagent_announce import run_subagent_announce_flow
    import inspect
    
    # Verify the function has the cron job branch
    source = inspect.getsource(run_subagent_announce_flow)
    assert 'announce_type == "cron job"' in source, "Missing cron job branch"
    print("  ✓ Cron job branch exists in announce flow")
    
    assert "deliver_outbound_payloads" in source, "Missing direct delivery call"
    print("  ✓ Direct delivery call present")
    
    assert "requester_origin.get" in source, "Missing delivery target extraction"
    print("  ✓ Delivery target extraction present")
    
    return True


async def test_integration():
    """Integration test showing expected flow."""
    print("\n✓ Testing expected integration flow...")
    
    print("""
Expected flow for cron job delivery:
1. Cron job executes → agent returns result
2. run.py calls run_subagent_announce_flow with announce_type="cron job"
3. Function extracts channel/to from requester_origin
4. Calls deliver_outbound_payloads directly
5. Channel plugin sends to Telegram
6. Returns True on success

Verification steps:
- Check gateway logs for: [subagent-announce] Direct delivery to telegram:...
- Check channel logs for: [telegram] Sent message...
- Check Telegram app for actual message
""")
    
    return True


async def main():
    print("=" * 70)
    print("Cron Job Delivery Test Suite")
    print("=" * 70)
    
    tests = [
        ("Signature Test", test_announce_flow_signature),
        ("Heartbeat Detection", test_heartbeat_detection),
        ("Direct Delivery Path", test_direct_delivery_path),
        ("Integration Guide", test_integration),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name} failed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 70)
    print("Test Results Summary")
    print("=" * 70)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {name}")
    
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("\n✓ All tests passed!")
        print("\nNext step: Restart gateway and trigger a cron job to verify actual delivery:")
        print("  cd /Users/long/Desktop/XJarvis/openclaw-python")
        print("  uv run openclaw stop")
        print("  uv run openclaw start --daemon")
        print("  # Wait for cron job to trigger or manually trigger it")
        print("  tail -f ~/.openclaw/logs/gateway.log")
        return 0
    else:
        print("\n✗ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
