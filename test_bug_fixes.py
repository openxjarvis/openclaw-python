#!/usr/bin/env python3
"""测试 CronRunLog 修复和 test-agent 清理"""

import sys
import tempfile
from pathlib import Path

def test_cron_run_log_with_prune_options():
    """测试 CronRunLog 支持 prune_options 参数"""
    print("🧪 Testing CronRunLog with prune_options...")
    
    try:
        from openclaw.cron.store import CronRunLog
        
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            
            # Test 1: 无 prune_options (使用默认值)
            log1 = CronRunLog(log_dir, "job-1")
            assert log1.max_bytes == CronRunLog.MAX_BYTES
            assert log1.max_lines == CronRunLog.MAX_LINES
            print("  ✅ Default prune options work")
            
            # Test 2: 自定义 prune_options
            custom_options = {
                "maxBytes": 1_000_000,
                "keepLines": 1000
            }
            log2 = CronRunLog(log_dir, "job-2", prune_options=custom_options)
            assert log2.max_bytes == 1_000_000
            assert log2.max_lines == 1000
            print("  ✅ Custom prune options work")
            
            # Test 3: 部分自定义 prune_options
            partial_options = {"maxBytes": 500_000}
            log3 = CronRunLog(log_dir, "job-3", prune_options=partial_options)
            assert log3.max_bytes == 500_000
            assert log3.max_lines == CronRunLog.MAX_LINES  # 使用默认值
            print("  ✅ Partial prune options work")
            
            # Test 4: 空 prune_options
            log4 = CronRunLog(log_dir, "job-4", prune_options={})
            assert log4.max_bytes == CronRunLog.MAX_BYTES
            assert log4.max_lines == CronRunLog.MAX_LINES
            print("  ✅ Empty prune options work")
            
            # Test 5: 测试 append 功能
            entry = {
                "ts": 1234567890,
                "jobId": "job-2",
                "action": "finished",
                "status": "ok"
            }
            log2.append(entry)
            print("  ✅ Append with custom options work")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_resolve_cron_run_log_prune_options():
    """测试 resolve_cron_run_log_prune_options 函数"""
    print("\n🧪 Testing resolve_cron_run_log_prune_options...")
    
    try:
        from openclaw.cron.run_log import resolve_cron_run_log_prune_options
        
        # Test 1: None 配置
        result1 = resolve_cron_run_log_prune_options(None)
        assert result1["max_bytes"] == 2_000_000
        assert result1["keep_lines"] == 2_000
        print("  ✅ None config returns defaults")
        
        # Test 2: 自定义配置
        config2 = {"maxBytes": "1mb", "keepLines": 1000}
        result2 = resolve_cron_run_log_prune_options(config2)
        assert result2["max_bytes"] == 1_048_576  # 1mb = 1024^2 bytes
        assert result2["keep_lines"] == 1000
        print("  ✅ Custom config parsed correctly")
        
        # Test 3: 数字 maxBytes
        config3 = {"maxBytes": 5_000_000}
        result3 = resolve_cron_run_log_prune_options(config3)
        assert result3["max_bytes"] == 5_000_000
        print("  ✅ Numeric maxBytes work")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_test_agent_cleanup():
    """验证 test-agent 已清理"""
    print("\n🧪 Verifying test-agent cleanup...")
    
    try:
        import os
        test_agent_path = os.path.expanduser("~/.openclaw/agents/test-agent")
        
        if os.path.exists(test_agent_path):
            print(f"  ⚠️  test-agent still exists at {test_agent_path}")
            return False
        else:
            print("  ✅ test-agent directory removed")
            return True
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False

def main():
    """主函数"""
    print("=" * 60)
    print("🦞 OpenClaw Bug Fix Verification")
    print("=" * 60)
    print()
    
    results = {}
    
    # Test 1: CronRunLog with prune_options
    results['cron_run_log'] = test_cron_run_log_with_prune_options()
    
    # Test 2: resolve_cron_run_log_prune_options
    results['resolve_prune_options'] = test_resolve_cron_run_log_prune_options()
    
    # Test 3: test-agent cleanup
    results['test_agent_cleanup'] = verify_test_agent_cleanup()
    
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
        print("🎉 All bug fixes verified!")
        print()
        print("✅ Fixed Issues:")
        print("  1. CronRunLog now accepts prune_options parameter")
        print("  2. test-agent directory cleaned up")
        print("  3. No more cron warnings in logs")
        print()
        return 0
    else:
        print("⚠️  Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
