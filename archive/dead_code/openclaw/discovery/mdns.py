"""
mDNS/Bonjour service discovery

Enables automatic discovery of OpenClaw Gateway on local network.
"""

import logging
import socket
from typing import Optional

logger = logging.getLogger(__name__)


class MDNSService:
    """
    mDNS service discovery
    
    Advertises OpenClaw Gateway on local network using mDNS/Bonjour.
    """
    
    def __init__(
        self,
        port: int,
        service_name: str = "OpenClaw Gateway",
        version: str = "0.6.0"
    ):
        """
        Initialize mDNS service
        
        Args:
            port: Gateway port
            service_name: Service name
            version: Gateway version
        """
        self.port = port
        self.service_name = service_name
        self.version = version
        self.zeroconf: Optional[any] = None
        self.service_info: Optional[any] = None
    
    def start(self):
        """Start mDNS service advertisement"""
        try:
            from zeroconf import ServiceInfo, Zeroconf
            
            # Get local IP
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            
            # Create service info
            service_type = "_openclaw._tcp.local."
            full_name = f"{self.service_name}.{service_type}"
            
            self.service_info = ServiceInfo(
                service_type,
                full_name,
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={
                    "version": self.version,
                    "protocol": "3"
                },
                server=f"{hostname}.local."
            )
            
            # Start zeroconf
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service_info)
            
            logger.info(
                f"mDNS service started: {full_name} on {local_ip}:{self.port}"
            )
            
        except ImportError:
            logger.warning(
                "zeroconf library not installed, mDNS service disabled. "
                "Install with: pip install zeroconf"
            )
        except Exception as e:
            logger.error(f"Failed to start mDNS service: {e}")
    
    def stop(self):
        """Stop mDNS service advertisement"""
        if self.zeroconf and self.service_info:
            try:
                self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
                logger.info("mDNS service stopped")
            except Exception as e:
                logger.error(f"Error stopping mDNS service: {e}")
