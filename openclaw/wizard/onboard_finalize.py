"""Onboarding finalization - TUI/UI launch"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


async def launch_tui(gateway_url: str = "ws://localhost:18789") -> None:
    """Launch TUI application"""
    print("\n🚀 Launching Terminal UI...")

    try:
        from openclaw.tui import run_tui, TUIOptions
        from urllib.parse import urlparse

        parsed = urlparse(gateway_url)
        port = parsed.port or 18789
        options = TUIOptions(gateway_port=port)
        await run_tui(options)
    except Exception as e:
        logger.error(f"Failed to launch TUI: {e}")
        print(f"  ❌ Failed to launch TUI: {e}")


async def open_web_ui(port: int = 8080) -> None:
    """Open Web UI in browser"""
    print(f"\n🌐 Opening Web UI at http://localhost:{port}/...")
    
    url = f"http://localhost:{port}/"
    
    # Try to open in browser
    import webbrowser
    try:
        webbrowser.open(url)
        print(f"  ✅ Web UI opened in browser")
        print(f"  🔗 URL: {url}")
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")
        print(f"  ℹ️  Manually open: {url}")


async def finalize_onboarding(mode: str = "quickstart", skip_ui: bool = False) -> dict:
    """Finalize onboarding and optionally launch UI
    
    Args:
        mode: "quickstart" or "advanced"
        skip_ui: Skip UI launch prompt
        
    Returns:
        Dict with finalization result
    """
    print("\n" + "=" * 60)
    print("🎉 ONBOARDING COMPLETE!")
    print("=" * 60)
    
    if skip_ui:
        print("\n⏭️  Skipping UI launch")
        return {"ui_launched": False, "skipped": True}
    
    print("\n🎯 How do you want to interact with OpenClaw?")
    print("  1. Web UI (browser-based) - Recommended 🌐")
    print("  2. Terminal UI (TUI)")
    print("  3. CLI only (no UI)")
    print("  4. Later")
    
    if mode == "quickstart":
        choice = "1"  # Auto-select Web UI in quickstart (更稳定)
        print(f"\n⚡ QuickStart: Opening Web UI (option 1)")
    else:
        choice = input("\nSelect option [1-4]: ").strip()
    
    if choice == "1":
        # Open Web UI (Gateway 内置)
        print("\n🌐 Opening Web UI...")
        print("  📡 Gateway is running on ws://localhost:18789")
        print("  🌍 Web UI URL: http://localhost:18789")
        print()
        print("  ✨ The Gateway includes a built-in Web UI")
        print("  💡 Tip: Keep this terminal open to keep Gateway running")
        print()
        
        # 打开浏览器
        import webbrowser
        try:
            webbrowser.open("http://localhost:18789")
            print("  ✅ Browser opened successfully")
        except Exception as e:
            logger.error(f"Failed to open browser: {e}")
            print(f"  ℹ️  Please manually open: http://localhost:18789")
        
        print("\n  📖 Web UI Features:")
        print("     - Chat with your agent")
        print("     - View sessions & history")
        print("     - Manage agents & skills")
        print("     - Configure channels")
        print()
        print("  ⏹️  Press Ctrl+C to stop Gateway")
        
        # 等待用户按 Ctrl+C
        try:
            import signal
            print()
            signal.pause()  # Wait for interrupt
        except (KeyboardInterrupt, AttributeError):
            print("\n  👋 Gateway will continue running in background")
        
        return {"ui_launched": True, "ui_type": "web", "url": "http://localhost:18789"}
    
    elif choice == "2":
        # Launch TUI
        print("\n🚀 Starting Terminal UI...")
        print("  💡 Use Ctrl+D or type 'exit' to close TUI")
        print("  💡 Use /help for commands")
        
        # 添加连接检查
        print("\n  🔍 Checking gateway connection...")
        try:
            import websocket
            ws = websocket.create_connection("ws://localhost:18789", timeout=5)
            ws.close()
            print("  ✅ Gateway connected")
        except ImportError:
            print("  ⚠️  websocket-client not installed")
            print("  💡 Install: pip install websocket-client")
            return {"ui_launched": False, "error": "websocket_missing"}
        except Exception as e:
            print(f"  ⚠️  Gateway connection issue: {e}")
            print("  💡 Tip: Make sure gateway is running on port 18789")
            return {"ui_launched": False, "error": "gateway_not_reachable"}
        
        # Note: TUI will block until user exits
        try:
            await launch_tui()
        except KeyboardInterrupt:
            print("\n👋 TUI closed")
        except Exception as e:
            logger.error(f"TUI error: {e}")
            print(f"\n  ⚠️  TUI error: {e}")
            print("  💡 Try Web UI instead: http://localhost:18789")
        
        return {"ui_launched": True, "ui_type": "tui"}
    
    elif choice == "3":
        print("\n✅ CLI-only mode selected")
        print("   Use 'openclaw' commands to interact")
        print()
        print("   Gateway is running at: http://localhost:18789")
        print()
        print("   Examples:")
        print("     openclaw status")
        print("     openclaw agent run -m 'Hello!'")
        print("     openclaw agents list")
        print()
        print("   Open Web UI anytime:")
        print("     http://localhost:18789")
        
        return {"ui_launched": False, "mode": "cli"}
    
    else:
        print("\n⏭️  You can access OpenClaw later:")
        print()
        print("     Web UI:  http://localhost:18789  (recommended)")
        print("     TUI:     openclaw tui")
        print("     CLI:     openclaw --help")
        print()
        print("   Gateway URL: http://localhost:18789")
        
        return {"ui_launched": False, "mode": "later"}


__all__ = ["finalize_onboarding", "launch_tui", "open_web_ui"]
