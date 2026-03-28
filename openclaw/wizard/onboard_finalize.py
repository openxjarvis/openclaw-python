"""Onboarding finalization — mirrors TS src/wizard/onboarding.finalize.ts

Handles post-wizard steps: daemon install, health check with polling,
Control UI launch, TUI hatch, shell completion, and next-steps display.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def wait_for_gateway_reachable(
    port: int = 18789,
    token: Optional[str] = None,
    timeout: float = 30.0,
    interval: float = 1.0,
) -> bool:
    """Poll gateway health endpoint until reachable or timeout.

    Mirrors TS waitForGatewayReachable() from onboard-helpers.ts.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                resp = await client.get(f"http://127.0.0.1:{port}/health", headers=headers)
                if resp.status_code < 500:
                    return True
        except Exception:
            pass
        await asyncio.sleep(interval)
    return False


def resolve_control_ui_links(
    port: int = 18789,
    bind: str = "loopback",
    token: Optional[str] = None,
) -> dict[str, str]:
    """Build bind-aware Control UI URLs. Mirrors TS resolveControlUiLinks()."""
    if bind in ("lan", "auto", "tailnet"):
        import socket

        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = "0.0.0.0"
        base = f"http://{ip}:{port}"
    else:
        base = f"http://localhost:{port}"

    links: dict[str, str] = {"ui": base}
    if token:
        links["ui_with_token"] = f"{base}?token={token}"
    return links


def _detect_headless() -> bool:
    """Return True when running on a headless server (no DISPLAY / no macOS)."""
    import os
    import sys

    if sys.platform == "darwin":
        return False
    return not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


def format_ssh_hint(port: int = 18789) -> str:
    """SSH port-forwarding hint for headless servers."""
    return (
        f"  SSH tunnel: ssh -L {port}:localhost:{port} user@this-server\n"
        f"  Then open:  http://localhost:{port}"
    )


async def launch_tui(gateway_url: str = "ws://localhost:18789") -> None:
    """Launch TUI application."""
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


async def open_web_ui(port: int = 18789, token: Optional[str] = None) -> None:
    """Open Control UI in browser with bind-aware URL."""
    links = resolve_control_ui_links(port=port, token=token)
    url = links.get("ui_with_token") or links["ui"]

    print(f"\n🌐 Opening Control UI at {links['ui']}...")
    try:
        webbrowser.open(url)
        print("  ✅ Opened in browser")
    except Exception as e:
        logger.error(f"Failed to open browser: {e}")
        print(f"  ℹ️  Manually open: {url}")


def setup_shell_completion() -> None:
    """Install shell completion if not already present.

    Mirrors TS setupOnboardingShellCompletion().
    """
    try:
        from openclaw.cli.completion import install_completion

        install_completion(quiet=True)
        print("✓ Shell completion installed")
    except Exception:
        logger.debug("Shell completion setup skipped (non-fatal)")


async def finalize_onboarding(
    mode: str = "quickstart",
    skip_ui: bool = False,
    port: int = 18789,
    bind: str = "loopback",
    token: Optional[str] = None,
    gateway_running: bool = False,
    workspace_dir: Optional[Path] = None,
) -> dict:
    """Finalize onboarding and optionally launch UI.

    Mirrors TS finalizeOnboardingWizard() from onboarding.finalize.ts.
    """
    print("\n" + "=" * 60)
    print("🎉 ONBOARDING COMPLETE!")
    print("=" * 60)

    # Shell completion (mirrors TS finalize step)
    setup_shell_completion()

    # Wait for gateway if it should be running
    if gateway_running:
        print("\n⏳ Waiting for gateway to become reachable...")
        reachable = await wait_for_gateway_reachable(port=port, token=token, timeout=15)
        if reachable:
            print("✓ Gateway is reachable")
        else:
            print("⚠ Gateway not yet reachable — it may still be starting")

    # Control UI links
    links = resolve_control_ui_links(port=port, bind=bind, token=token)
    print(f"\n🌐 Control UI: {links['ui']}")

    # SSH hint for headless
    if _detect_headless():
        print(f"\n💡 Headless server detected:")
        print(format_ssh_hint(port))

    # BOOTSTRAP.md hatch message (mirrors TS "Wake up, my friend!")
    ws = workspace_dir or Path.home()
    bootstrap_path = ws / "BOOTSTRAP.md"
    if bootstrap_path.exists():
        print("\n🐣 Your agent has a BOOTSTRAP.md — send it a message to hatch!")

    if skip_ui:
        print("\n⏭️  Skipping UI launch")
        return {"ui_launched": False, "skipped": True}

    # Hatch choice (mirrors TS finalize hatch/web/tui/later)
    print("\n🎯 How do you want to interact with OpenClaw?")
    print("  1. Web UI (browser-based) — Recommended 🌐")
    print("  2. Terminal UI (TUI)")
    print("  3. CLI only")
    print("  4. Later")

    if mode == "quickstart":
        choice = "1"
        print(f"\n⚡ QuickStart: Opening Web UI")
    else:
        choice = input("\nSelect option [1-4]: ").strip() or "1"

    if choice == "1":
        await open_web_ui(port=port, token=token)

        print("\n  📖 Control UI Features:")
        print("     - Chat with your agent")
        print("     - View sessions & history")
        print("     - Manage agents & skills")
        print("     - Configure channels")
        print()
        print("  ⏹️  Press Ctrl+C to stop Gateway")

        try:
            import signal

            signal.pause()
        except (KeyboardInterrupt, AttributeError):
            print("\n  👋 Gateway will continue running in background")

        return {"ui_launched": True, "ui_type": "web", "url": links["ui"]}

    elif choice == "2":
        print("\n🚀 Starting Terminal UI...")
        try:
            await launch_tui(f"ws://localhost:{port}")
        except KeyboardInterrupt:
            print("\n👋 TUI closed")
        except Exception as e:
            print(f"\n  ⚠️  TUI error: {e}")
            print(f"  💡 Try Web UI instead: {links['ui']}")
        return {"ui_launched": True, "ui_type": "tui"}

    elif choice == "3":
        print("\n✅ CLI-only mode selected")
        print(f"   Control UI: {links['ui']}")
        return {"ui_launched": False, "mode": "cli"}

    else:
        print(f"\n⏭️  Access OpenClaw later at: {links['ui']}")
        return {"ui_launched": False, "mode": "later"}


__all__ = [
    "finalize_onboarding",
    "launch_tui",
    "open_web_ui",
    "wait_for_gateway_reachable",
    "resolve_control_ui_links",
    "setup_shell_completion",
    "format_ssh_hint",
]
