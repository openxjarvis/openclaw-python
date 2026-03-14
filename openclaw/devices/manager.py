"""
Device management service

Manages device pairing, tokens, and authentication.
"""

import logging
import secrets
import time
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DeviceAuthToken:
    """Device auth token - mirrors TS DeviceAuthToken"""
    token: str
    role: str
    scopes: List[str]
    created_at_ms: float
    rotated_at_ms: Optional[float] = None
    revoked_at_ms: Optional[float] = None
    last_used_at_ms: Optional[float] = None


@dataclass
class Device:
    """Device information - mirrors TS PairedDevice"""
    device_id: str
    public_key: str
    display_name: Optional[str] = None
    platform: Optional[str] = None
    device_family: Optional[str] = None
    client_id: Optional[str] = None
    client_mode: Optional[str] = None
    role: Optional[str] = None
    roles: Optional[List[str]] = None
    scopes: Optional[List[str]] = None
    approved_scopes: Optional[List[str]] = None
    remote_ip: Optional[str] = None
    tokens: Dict[str, DeviceAuthToken] = field(default_factory=dict)  # role -> token
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    approved_at_ms: float = field(default_factory=lambda: time.time() * 1000)
    label: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DevicePairRequest:
    """Device pairing request - mirrors TS DevicePairingPendingRequest"""
    request_id: str  # UUID - primary key for pending requests
    device_id: str
    public_key: str
    display_name: Optional[str] = None
    platform: Optional[str] = None
    device_family: Optional[str] = None
    client_id: Optional[str] = None
    client_mode: Optional[str] = None
    role: Optional[str] = None
    roles: Optional[List[str]] = None
    scopes: Optional[List[str]] = None
    remote_ip: Optional[str] = None
    silent: bool = False
    is_repair: bool = False
    ts: float = field(default_factory=lambda: time.time() * 1000)  # milliseconds
    status: str = "pending"  # pending | approved | rejected




class DeviceManager:
    """
    Device management service - mirrors TS device-pairing.ts
    
    Handles:
    - Device pairing with requestId-based workflow
    - Token generation and management per role
    - Device authentication
    """
    
    def __init__(self):
        """Initialize device manager"""
        self.devices: Dict[str, Device] = {}  # deviceId -> Device (paired devices)
        self.pending_pairs: Dict[str, DevicePairRequest] = {}  # requestId -> Request
        self.tokens_by_token: Dict[str, DeviceAuthToken] = {}  # token -> DeviceAuthToken (for quick lookup)
    
    def request_pairing(
        self,
        device_id: str,
        public_key: str,
        display_name: Optional[str] = None,
        platform: Optional[str] = None,
        device_family: Optional[str] = None,
        client_id: Optional[str] = None,
        client_mode: Optional[str] = None,
        role: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        remote_ip: Optional[str] = None,
        silent: bool = False,
    ) -> DevicePairRequest:
        """
        Request device pairing - mirrors TS requestDevicePairing
        
        Args:
            device_id: Device identifier
            public_key: Device public key
            display_name: Device display name
            platform: Device platform
            device_family: Device family
            client_id: Client identifier
            client_mode: Client mode
            role: Primary role for this request
            scopes: Requested scopes
            remote_ip: Remote IP address
            silent: Silent pairing (no broadcast)
            
        Returns:
            DevicePairRequest
        """
        import uuid
        
        # Check if device already paired (for repair)
        is_repair = device_id in self.devices
        
        # Check if existing pending request for this device
        existing = None
        for req in self.pending_pairs.values():
            if req.device_id == device_id:
                existing = req
                break
        
        if existing:
            # Merge with existing request
            if display_name:
                existing.display_name = display_name
            if platform:
                existing.platform = platform
            if device_family:
                existing.device_family = device_family
            if client_id:
                existing.client_id = client_id
            if client_mode:
                existing.client_mode = client_mode
            if role:
                existing.role = role
                if existing.roles:
                    existing.roles = list(set(existing.roles + [role]))
                else:
                    existing.roles = [role]
            if scopes:
                existing.scopes = scopes
            if remote_ip:
                existing.remote_ip = remote_ip
            existing.is_repair = is_repair
            existing.ts = time.time() * 1000
            logger.info(f"Device pairing request merged: {device_id} (requestId: {existing.request_id})")
            return existing
        
        # Create new request
        request = DevicePairRequest(
            request_id=str(uuid.uuid4()),
            device_id=device_id,
            public_key=public_key,
            display_name=display_name,
            platform=platform,
            device_family=device_family,
            client_id=client_id,
            client_mode=client_mode,
            role=role,
            roles=[role] if role else None,
            scopes=scopes,
            remote_ip=remote_ip,
            silent=silent,
            is_repair=is_repair,
        )
        
        self.pending_pairs[request.request_id] = request
        logger.info(f"Device pairing requested: {device_id} (requestId: {request.request_id})")
        
        # TODO: Broadcast device.pair.requested event
        
        return request
    
    def approve_pairing(
        self,
        request_id: str,
        label: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Approve device pairing - mirrors TS approveDevicePairing
        
        Args:
            request_id: Request UUID (not deviceId!)
            label: Optional device label
            
        Returns:
            {"requestId": str, "device": Device} or None if request not found
        """
        request = self.pending_pairs.get(request_id)
        if not request:
            logger.warning(f"No pending pair request with requestId: {request_id}")
            return None
        
        device_id = request.device_id
        now = time.time() * 1000
        
        # Get existing device if this is a repair
        existing = self.devices.get(device_id)
        
        # Merge roles
        roles = []
        if existing and existing.roles:
            roles.extend(existing.roles)
        if existing and existing.role:
            roles.append(existing.role)
        if request.roles:
            roles.extend(request.roles)
        if request.role:
            roles.append(request.role)
        roles = list(set(roles)) if roles else None
        
        # Merge scopes
        approved_scopes = []
        if existing:
            if existing.approved_scopes:
                approved_scopes.extend(existing.approved_scopes)
            elif existing.scopes:
                approved_scopes.extend(existing.scopes)
        if request.scopes:
            approved_scopes.extend(request.scopes)
        approved_scopes = list(set(approved_scopes)) if approved_scopes else None
        
        # Handle tokens per role
        tokens = existing.tokens.copy() if existing else {}
        role_for_token = request.role.strip() if request.role and request.role.strip() else None
        
        if role_for_token:
            existing_token = tokens.get(role_for_token)
            requested_scopes = request.scopes or []
            
            # Determine next scopes
            if requested_scopes:
                next_scopes = requested_scopes
            elif existing_token:
                next_scopes = existing_token.scopes
            elif approved_scopes:
                next_scopes = approved_scopes
            elif existing:
                next_scopes = existing.approved_scopes or existing.scopes or []
            else:
                next_scopes = []
            
            # Generate new token
            new_token = secrets.token_urlsafe(32)
            token_entry = DeviceAuthToken(
                token=new_token,
                role=role_for_token,
                scopes=next_scopes,
                created_at_ms=existing_token.created_at_ms if existing_token else now,
                rotated_at_ms=now if existing_token else None,
                last_used_at_ms=existing_token.last_used_at_ms if existing_token else None,
            )
            tokens[role_for_token] = token_entry
            
            # Update token lookup
            self.tokens_by_token[new_token] = token_entry
            
            # Remove old token from lookup if exists
            if existing_token:
                self.tokens_by_token.pop(existing_token.token, None)
        
        # Create or update device
        device = Device(
            device_id=device_id,
            public_key=request.public_key,
            display_name=request.display_name or (existing.display_name if existing else None),
            platform=request.platform or (existing.platform if existing else None),
            device_family=request.device_family or (existing.device_family if existing else None),
            client_id=request.client_id or (existing.client_id if existing else None),
            client_mode=request.client_mode or (existing.client_mode if existing else None),
            role=request.role or (existing.role if existing else None),
            roles=roles,
            scopes=request.scopes or (existing.scopes if existing else None),
            approved_scopes=approved_scopes,
            remote_ip=request.remote_ip or (existing.remote_ip if existing else None),
            tokens=tokens,
            created_at_ms=existing.created_at_ms if existing else now,
            approved_at_ms=now,
            label=label,
        )
        self.devices[device_id] = device
        
        # Update request status and remove from pending
        request.status = "approved"
        del self.pending_pairs[request_id]
        
        logger.info(f"Device pairing approved: {device_id} (requestId: {request_id})")
        
        # TODO: Broadcast device.pair.resolved event
        
        return {"requestId": request_id, "device": device}
    
    def reject_pairing(
        self,
        request_id: str,
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """
        Reject device pairing - mirrors TS rejectDevicePairing
        
        Args:
            request_id: Request UUID (not deviceId!)
            reason: Rejection reason
            
        Returns:
            {"requestId": str, "deviceId": str} or None if request not found
        """
        request = self.pending_pairs.get(request_id)
        if not request:
            logger.warning(f"No pending pair request with requestId: {request_id}")
            return None
        
        device_id = request.device_id
        request.status = "rejected"
        
        # Remove from pending
        del self.pending_pairs[request_id]
        
        logger.info(f"Device pairing rejected: {device_id} (requestId: {request_id}), reason: {reason}")
        
        # TODO: Broadcast device.pair.resolved event
        
        return {"requestId": request_id, "deviceId": device_id}
    
    def list_pairing(self) -> Dict[str, Any]:
        """
        List device pairing requests and paired devices - mirrors TS listDevicePairing
        
        Returns:
            {"pending": List[DevicePairRequest], "paired": List[Device]}
        """
        # Sort pending by timestamp (newest first)
        pending = sorted(
            self.pending_pairs.values(),
            key=lambda r: r.ts,
            reverse=True,
        )
        
        # Sort paired by approvedAtMs (newest first)
        paired = sorted(
            self.devices.values(),
            key=lambda d: d.approved_at_ms,
            reverse=True,
        )
        
        return {"pending": pending, "paired": paired}
    
    def list_devices(self) -> List[Dict[str, Any]]:
        """
        List all paired devices
        
        Returns:
            List of device info dictionaries
        """
        devices = []
        for device in self.devices.values():
            devices.append({
                "deviceId": device.device_id,
                "label": device.label,
                "pairedAt": device.created_at_ms,
                "approvedAt": device.approved_at_ms,
                "metadata": device.metadata
            })
        return devices
    
    def list_pending_pairs(self) -> List[Dict[str, Any]]:
        """
        List pending pairing requests
        
        Returns:
            List of pending pair requests
        """
        pairs = []
        for request in self.pending_pairs.values():
            pairs.append({
                "requestId": request.request_id,
                "deviceId": request.device_id,
                "requestedAt": request.ts,
                "status": request.status
            })
        return pairs
    
    def get_device(self, device_id: str) -> Optional[Device]:
        """
        Get device by ID
        
        Args:
            device_id: Device identifier
            
        Returns:
            Device or None
        """
        return self.devices.get(device_id)
    
    def get_pending_request(self, request_id: str) -> Optional[DevicePairRequest]:
        """
        Get pending pairing request by requestId
        
        Args:
            request_id: Request UUID
            
        Returns:
            DevicePairRequest or None
        """
        return self.pending_pairs.get(request_id)
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify device token - updated to match new structure
        
        Args:
            token: Device token
            
        Returns:
            {"deviceId": str, "role": str, "scopes": List[str]} or None if invalid
        """
        device_token = self.tokens_by_token.get(token)
        if not device_token:
            return None
        
        # Check if revoked
        if device_token.revoked_at_ms:
            return None
        
        # Find device to get deviceId
        device_id = None
        for dev in self.devices.values():
            if device_token.role in dev.tokens and dev.tokens[device_token.role].token == token:
                device_id = dev.device_id
                break
        
        if not device_id:
            return None
        
        # Update last used
        device_token.last_used_at_ms = time.time() * 1000
        
        return {
            "deviceId": device_id,
            "role": device_token.role,
            "scopes": device_token.scopes,
        }
    
    def rotate_token(
        self,
        device_id: str,
        role: str,
        scopes: Optional[List[str]] = None,
    ) -> Optional[DeviceAuthToken]:
        """
        Rotate device token - mirrors TS rotateDeviceToken
        
        Args:
            device_id: Device identifier
            role: Token role to rotate
            scopes: Optional new scopes (defaults to existing scopes)
            
        Returns:
            New DeviceAuthToken or None if device/role not found
        """
        device = self.devices.get(device_id)
        if not device:
            logger.warning(f"No paired device found: {device_id}")
            return None
        
        role_stripped = role.strip() if role and role.strip() else None
        if not role_stripped:
            logger.warning(f"Invalid role for token rotation: {role}")
            return None
        
        now = time.time() * 1000
        existing_token = device.tokens.get(role_stripped)
        
        # Determine scopes
        if scopes:
            next_scopes = scopes
        elif existing_token:
            next_scopes = existing_token.scopes
        elif device.approved_scopes:
            next_scopes = device.approved_scopes
        elif device.scopes:
            next_scopes = device.scopes
        else:
            next_scopes = []
        
        # Generate new token
        new_token = secrets.token_urlsafe(32)
        token_entry = DeviceAuthToken(
            token=new_token,
            role=role_stripped,
            scopes=next_scopes,
            created_at_ms=existing_token.created_at_ms if existing_token else now,
            rotated_at_ms=now,
            last_used_at_ms=existing_token.last_used_at_ms if existing_token else None,
        )
        
        # Update device tokens
        device.tokens[role_stripped] = token_entry
        
        # Update token lookup
        self.tokens_by_token[new_token] = token_entry
        
        # Remove old token from lookup
        if existing_token:
            self.tokens_by_token.pop(existing_token.token, None)
        
        logger.info(f"Device token rotated: device={device_id} role={role_stripped}")
        
        return token_entry
    
    def revoke_token(
        self,
        device_id: str,
        role: str,
    ) -> Optional[DeviceAuthToken]:
        """
        Revoke device token by deviceId and role - mirrors TS revokeDeviceToken
        
        Args:
            device_id: Device identifier
            role: Token role to revoke
            
        Returns:
            Revoked DeviceAuthToken or None if not found
        """
        device = self.devices.get(device_id)
        if not device:
            logger.warning(f"No paired device found: {device_id}")
            return None
        
        role_stripped = role.strip() if role and role.strip() else None
        if not role_stripped:
            logger.warning(f"Invalid role for token revocation: {role}")
            return None
        
        if role_stripped not in device.tokens:
            logger.warning(f"No token found for device {device_id} role {role_stripped}")
            return None
        
        # Get token entry
        token_entry = device.tokens[role_stripped]
        
        # Mark as revoked
        now = time.time() * 1000
        token_entry.revoked_at_ms = now
        
        # Remove from quick lookup
        self.tokens_by_token.pop(token_entry.token, None)
        
        logger.info(f"Device token revoked: device={device_id} role={role_stripped}")
        
        return token_entry
    
    def remove_device(self, device_id: str) -> Optional[Dict[str, str]]:
        """
        Remove paired device - mirrors TS removePairedDevice
        
        Args:
            device_id: Device identifier
            
        Returns:
            {"deviceId": str} or None if not found
        """
        if device_id not in self.devices:
            logger.warning(f"No paired device found: {device_id}")
            return None
        
        device = self.devices[device_id]
        
        # Remove all tokens from lookup
        for token_entry in device.tokens.values():
            self.tokens_by_token.pop(token_entry.token, None)
        
        # Remove device
        del self.devices[device_id]
        
        logger.info(f"Device removed: {device_id}")
        
        return {"deviceId": device_id}


# Global device manager instance
_device_manager: Optional[DeviceManager] = None


def get_device_manager() -> DeviceManager:
    """Get global device manager instance"""
    global _device_manager
    if _device_manager is None:
        _device_manager = DeviceManager()
    return _device_manager


def set_device_manager(manager: DeviceManager):
    """Set global device manager instance"""
    global _device_manager
    _device_manager = manager
