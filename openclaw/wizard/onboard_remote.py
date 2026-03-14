"""Remote gateway configuration during onboarding"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from openclaw.config.schema import OpenClawConfig, GatewayConfig
from openclaw.config.loader import save_config

logger = logging.getLogger(__name__)


async def probe_gateway(url: str, token: Optional[str] = None, timeout: int = 5) -> bool:
    """Probe if gateway is reachable
    
    Args:
        url: Gateway URL (ws://host:port or http://host:port)
        token: Gateway token
        timeout: Timeout in seconds
        
    Returns:
        True if gateway is reachable
    """
    # Convert ws:// to http:// for health check
    http_url = url.replace("ws://", "http://").replace("wss://", "https://")
    if not http_url.endswith("/health"):
        http_url = http_url.rstrip("/") + "/health"
    
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(http_url, headers=headers) as response:
                if response.status == 200:
                    return True
                return False
    except Exception as e:
        logger.debug(f"Gateway probe failed: {e}")
        return False


async def setup_remote_gateway() -> dict:
    """Configure remote gateway connection
    
    Returns:
        Dict with remote gateway config
    """
    print("\n" + "=" * 60)
    print("🌐 REMOTE GATEWAY CONFIGURATION")
    print("=" * 60)
    
    print("\n💡 Connect to an OpenClaw gateway running on another machine")
    print("   (e.g., server, cloud instance, another computer)")
    
    # Get remote URL
    print("\n📝 Remote Gateway URL:")
    print("   Examples:")
    print("     ws://192.168.1.100:18789")
    print("     ws://my-server.local:18789")
    print("     wss://openclaw.example.com:18789")
    
    url = input("\n🔗 Gateway URL: ").strip()
    if not url:
        print("❌ URL required")
        return {"configured": False, "error": "no_url"}
    
    # Get token
    token = input("🔑 Gateway token (leave empty if none): ").strip() or None
    
    # Probe gateway
    print(f"\n🔍 Probing gateway at {url}...")
    is_reachable = await probe_gateway(url, token)
    
    if is_reachable:
        print("  ✅ Gateway is reachable!")
    else:
        print("  ⚠️  Gateway not reachable (might be offline)")
        proceed = input("  Continue anyway? [y/N]: ").strip().lower()
        if proceed not in ["y", "yes"]:
            return {"configured": False, "cancelled": True}
    
    # Create config
    config = OpenClawConfig()
    config.gateway = GatewayConfig(
        mode="remote",
        remote_url=url,
        remote_token=token
    )
    
    # Save config
    try:
        save_config(config)
        print("\n✅ Remote gateway configured!")
        print(f"   URL: {url}")
        print(f"   Mode: remote")
        
        return {
            "configured": True,
            "url": url,
            "reachable": is_reachable
        }
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return {"configured": False, "error": str(e)}


__all__ = ["setup_remote_gateway", "probe_gateway"]
