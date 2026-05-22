"""Gateway Bonjour/mDNS advertiser.

Mirrors TS openclaw/src/infra/bonjour.ts startGatewayBonjourAdvertiser().

Service type: _openclaw-gw._tcp (was incorrectly _openclaw._tcp in older Python builds).
TXT records: role, gatewayPort, lanHost, displayName, transport, sshPort,
             gatewayTls, gatewayTlsSha256, canvasPort, tailnetDns, cliPath.

Disable by setting OPENCLAW_DISABLE_BONJOUR=1.
"""
from __future__ import annotations

import logging
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Canonical mDNS service type — must match TS bonjour.ts type: "openclaw-gw"
GATEWAY_BONJOUR_SERVICE_TYPE = "_openclaw-gw._tcp.local."


@dataclass
class GatewayBonjourOpts:
    """Options for Bonjour advertising. Mirrors TS GatewayBonjourAdvertiseOpts."""

    gateway_port: int
    instance_name: Optional[str] = None
    ssh_port: int = 22
    gateway_tls_enabled: bool = False
    gateway_tls_fingerprint_sha256: Optional[str] = None
    canvas_port: Optional[int] = None
    tailnet_dns: Optional[str] = None
    cli_path: Optional[str] = None
    minimal: bool = False


def _safe_service_name(name: str) -> str:
    """Sanitize Bonjour service instance name."""
    cleaned = re.sub(r"[^a-zA-Z0-9 \-_().]", "", name).strip()
    return cleaned[:63] or "OpenClaw Gateway"


def _build_txt_records(opts: GatewayBonjourOpts, hostname: str, display_name: str) -> dict[str, str]:
    """Build TXT record dict. Mirrors TS gatewayTxt construction."""
    txt: dict[str, str] = {
        "role": "gateway",
        "gatewayPort": str(opts.gateway_port),
        "lanHost": f"{hostname}.local",
        "displayName": display_name,
        "transport": "gateway",
    }
    if not opts.minimal:
        txt["sshPort"] = str(opts.ssh_port)
    if opts.gateway_tls_enabled:
        txt["gatewayTls"] = "1"
        if opts.gateway_tls_fingerprint_sha256:
            txt["gatewayTlsSha256"] = opts.gateway_tls_fingerprint_sha256
    if opts.canvas_port and opts.canvas_port > 0:
        txt["canvasPort"] = str(opts.canvas_port)
    if opts.tailnet_dns and opts.tailnet_dns.strip():
        txt["tailnetDns"] = opts.tailnet_dns.strip()
    if not opts.minimal and opts.cli_path and opts.cli_path.strip():
        txt["cliPath"] = opts.cli_path.strip()
    return txt


class GatewayDiscovery:
    """Gateway Bonjour/mDNS advertiser.

    Mirrors TS startGatewayBonjourAdvertiser().

    Uses service type ``_openclaw-gw._tcp`` (TCP, mDNS .local domain).
    """

    def __init__(
        self,
        port: int = 18789,
        name: str = "OpenClaw Gateway",
        opts: Optional[GatewayBonjourOpts] = None,
    ):
        self.port = port
        self.name = name
        self._opts = opts or GatewayBonjourOpts(gateway_port=port)
        self.zeroconf = None
        self.service_info = None

    async def start(self) -> None:
        """Start advertising gateway via mDNS (Bonjour)."""
        if os.environ.get("OPENCLAW_DISABLE_BONJOUR", "").lower() in ("1", "true", "yes"):
            logger.debug("Bonjour disabled by OPENCLAW_DISABLE_BONJOUR env var")
            return

        try:
            from zeroconf import Zeroconf, ServiceInfo

            raw_hostname = socket.gethostname()
            hostname = (
                re.sub(r"\.local$", "", raw_hostname, flags=re.IGNORECASE)
                .split(".")[0]
                .strip() or "openclaw"
            )
            instance_name = (
                self._opts.instance_name.strip()
                if self._opts.instance_name and self._opts.instance_name.strip()
                else f"{hostname} (OpenClaw)"
            )
            display_name = _safe_service_name(instance_name)
            txt = _build_txt_records(self._opts, hostname, display_name)

            try:
                local_ip = socket.gethostbyname(raw_hostname)
            except Exception:
                local_ip = "127.0.0.1"

            self.service_info = ServiceInfo(
                GATEWAY_BONJOUR_SERVICE_TYPE,
                f"{_safe_service_name(instance_name)}.{GATEWAY_BONJOUR_SERVICE_TYPE}",
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties=txt,
                server=f"{hostname}.local.",
            )

            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service_info)

            logger.info(
                "Bonjour: advertising %s on port %d (service=%s)",
                display_name, self.port, GATEWAY_BONJOUR_SERVICE_TYPE,
            )

        except ImportError:
            logger.warning("zeroconf not installed — Bonjour discovery disabled. Install with: pip install zeroconf")
        except Exception as exc:
            logger.error("Failed to start Bonjour discovery: %s", exc)
    
    async def stop(self) -> None:
        """Stop advertising"""
        if self.zeroconf and self.service_info:
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
                logger.info("Gateway discovery stopped")
            except Exception as e:
                logger.error(f"Failed to stop discovery: {e}")


async def start_gateway_discovery(port: int = 18789, name: str = "OpenClaw Gateway") -> Optional[GatewayDiscovery]:
    """Start gateway discovery service
    
    Args:
        port: Gateway port
        name: Service name
        
    Returns:
        GatewayDiscovery instance or None if failed
    """
    discovery = GatewayDiscovery(port, name)
    await discovery.start()
    return discovery


__all__ = ["GatewayDiscovery", "start_gateway_discovery"]
