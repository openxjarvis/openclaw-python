#!/usr/bin/env python3
"""验证 Cron 和 Logs 修复"""

import sys
import tempfile
from pathlib import Path

def test_format_delivery_message():
    """测试 format_delivery_message 的 None handling"""
    print("🧪 Testing format_delivery_message...")
    
    try:
        from openclaw.cron.isolated_agent.delivery import format_delivery_message
        
        # 创建简单的 mock job
        class MockJob:
            def __init__(self):
                self.id = "test-job"
                self.name = "测试任务"
        
        job = MockJob()
        
        # Test 1: None result
        msg1 = format_delivery_message(job, None)
        assert "no result available" in msg1
        print("  ✅ None result handled correctly")
        
        # Test 2: Error result
        msg2 = format_delivery_message(job, {"error": "Something went wrong"})
        assert "failed" in msg2
        print("  ✅ Error result formatted correctly")
        
        # Test 3: Success result
        msg3 = format_delivery_message(job, {"summary": "Task completed successfully"})
        assert "completed successfully" in msg3
        print("  ✅ Success result formatted correctly")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_logs_tail_format():
    """测试 logs.tail 返回格式"""
    print("\n🧪 Testing logs.tail format...")
    
    try:
        from openclaw.gateway.handlers import handle_logs_tail
        
        # 模拟 connection 对象
        class MockConnection:
            pass
        
        connection = MockConnection()
        
        # Test with default params
        result = None
        import asyncio
        result = asyncio.run(handle_logs_tail(connection, {}))
        
        # 验证返回格式
        required_fields = ["cursor", "size", "lines", "truncated", "reset", "file"]
        for field in required_fields:
            assert field in result, f"Missing field: {field}"
        
        print(f"  ✅ All required fields present: {required_fields}")
        
        # 验证字段类型
        assert isinstance(result["cursor"], int)
        assert isinstance(result["size"], int)
        assert isinstance(result["lines"], list)
        assert isinstance(result["truncated"], bool)
        assert isinstance(result["reset"], bool)
        assert isinstance(result["file"], str)
        
        print("  ✅ All field types correct")
        
        # Test with cursor param
        result2 = asyncio.run(handle_logs_tail(connection, {"cursor": 100, "limit": 50}))
        assert "cursor" in result2
        print("  ✅ Cursor parameter handled")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    print("=" * 60)
    print("🔧 Cron & Logs Fix Verification")
    print("=" * 60)
    print()
    
    results = {}
    
    # Test 1: format_delivery_message
    results['delivery_message'] = test_format_delivery_message()
    
    # Test 2: logs.tail format
    results['logs_tail'] = test_logs_tail_format()
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)
    
    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {check}")
    
    all_passed = all(results.values())
    
    print()
    if all_passed:
        print("🎉 All fixes verified!")
        print()
        print("✅ Fixed Issues:")
        print("  1. format_delivery_message handles None result")
        print("  2. logs.tail returns correct format")
        print()
        print("🔄 Next steps:")
        print("  1. Restart Gateway")
        print("  2. Test cron job delivery")
        print("  3. Check Web UI Logs page")
        print()
        return 0
    else:
        print("⚠️  Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
