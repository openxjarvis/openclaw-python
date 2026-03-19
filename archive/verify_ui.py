#!/usr/bin/env python3
"""验证 OpenClaw UI 和 Gateway 连接"""

import sys
import asyncio
import subprocess
from pathlib import Path

def check_gateway():
    """检查 Gateway 是否运行"""
    print("🔍 Checking Gateway status...")
    
    try:
        import requests
        response = requests.get("http://localhost:18789", timeout=5)
        print(f"  ✅ Gateway responding: {response.status_code}")
        return True
    except ImportError:
        print("  ⚠️  requests not installed, using curl")
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:18789"],
            capture_output=True,
            text=True
        )
        if result.stdout.strip() in ["200", "301", "302"]:
            print(f"  ✅ Gateway responding: {result.stdout.strip()}")
            return True
        else:
            print(f"  ❌ Gateway not responding: {result.stdout.strip()}")
            return False
    except Exception as e:
        print(f"  ❌ Gateway not accessible: {e}")
        return False

def check_web_ui():
    """检查 Web UI 文件"""
    print("\n🌐 Checking Web UI files...")
    
    static_dir = Path(__file__).parent / "openclaw" / "static" / "control-ui"
    
    if not static_dir.exists():
        print(f"  ❌ Static directory not found: {static_dir}")
        return False
    
    index_html = static_dir / "index.html"
    if index_html.exists():
        print(f"  ✅ index.html found")
        print(f"     Path: {index_html}")
    else:
        print(f"  ❌ index.html not found")
        return False
    
    return True

def check_websocket():
    """检查 WebSocket 连接"""
    print("\n🔌 Checking WebSocket connection...")
    
    try:
        import websocket
        ws = websocket.create_connection("ws://localhost:18789", timeout=5)
        ws.close()
        print("  ✅ WebSocket connection successful")
        return True
    except ImportError:
        print("  ⚠️  websocket-client not installed")
        print("     Install: pip install websocket-client")
        return False
    except Exception as e:
        print(f"  ❌ WebSocket connection failed: {e}")
        return False

async def test_finalize():
    """测试 finalize_onboarding 函数"""
    print("\n🧪 Testing finalize_onboarding...")
    
    try:
        from openclaw.wizard.onboard_finalize import finalize_onboarding
        
        # 测试 skip_ui=True
        result = await finalize_onboarding(mode="quickstart", skip_ui=True)
        
        if result.get("skipped"):
            print("  ✅ finalize_onboarding works (skip_ui=True)")
            return True
        else:
            print("  ❌ Unexpected result")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主函数"""
    print("=" * 60)
    print("🦞 OpenClaw UI & Gateway Verification")
    print("=" * 60)
    print()
    
    results = {}
    
    # 1. Gateway
    results['gateway'] = check_gateway()
    
    # 2. Web UI files
    results['web_ui_files'] = check_web_ui()
    
    # 3. WebSocket
    results['websocket'] = check_websocket()
    
    # 4. Finalize function
    results['finalize'] = asyncio.run(test_finalize())
    
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
        print("🎉 All checks passed!")
        print()
        print("🌐 Access Web UI at: http://localhost:18789")
        print()
        return 0
    else:
        print("⚠️  Some checks failed")
        print()
        print("💡 Troubleshooting:")
        
        if not results['gateway']:
            print("   - Start Gateway: uv run openclaw gateway run")
        
        if not results['websocket']:
            print("   - Install websocket-client: pip install websocket-client")
        
        if not results['web_ui_files']:
            print("   - Check openclaw/static/control-ui/ directory")
        
        print()
        return 1

if __name__ == "__main__":
    sys.exit(main())
