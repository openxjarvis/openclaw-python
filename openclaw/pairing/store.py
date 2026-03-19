"""
Pairing request storage (aligned with TypeScript pairing-store.ts)

Manages persistent storage of pending pairing requests.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .codes import generate_pairing_code
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# Constants (matching TypeScript)
PAIRING_PENDING_TTL_MS = 60 * 60 * 1000  # 1 hour
PAIRING_PENDING_MAX = 3  # Max pending requests per channel

ChannelId = Literal["telegram", "discord", "slack", "signal", "sms"]


@dataclass
class PairingRequest:
    """Pairing request with code and metadata"""
    
    id: str
    """Unique request ID (typically user ID from channel)"""
    
    code: str
    """8-character pairing code"""
    
    created_at: str
    """ISO8601 timestamp when created"""
    
    last_seen_at: str
    """ISO8601 timestamp when last seen"""
    
    meta: dict[str, str] = field(default_factory=dict)
    """Additional metadata (username, etc.)"""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "code": self.code,
            "createdAt": self.created_at,
            "lastSeenAt": self.last_seen_at,
            "meta": self.meta,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PairingRequest:
        """Create from dictionary"""
        return cls(
            id=data["id"],
            code=data["code"],
            created_at=data["createdAt"],
            last_seen_at=data["lastSeenAt"],
            meta=data.get("meta", {}),
        )
    
    def is_expired(self) -> bool:
        """Check if request is expired (> 1 hour old)"""
        try:
            created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_ms = (now - created).total_seconds() * 1000
            return age_ms > PAIRING_PENDING_TTL_MS
        except Exception:
            return True


class PairingStore:
    """
    Persistent storage for pairing requests
    
    Stores pending pairing requests in JSON files:
    - ~/.openclaw/credentials/telegram-pairing.json
    - ~/.openclaw/credentials/discord-pairing.json
    - etc.
    """
    
    def __init__(self, channel: ChannelId):
        self.channel = channel
        self.store_path = self._resolve_store_path()
        self._ensure_directory()
    
    def _resolve_store_path(self) -> Path:
        """Resolve store file path for channel"""
        credentials_dir = resolve_state_dir() / "credentials"
        safe_channel = self._safe_channel_key(self.channel)
        return credentials_dir / f"{safe_channel}-pairing.json"
    
    def _safe_channel_key(self, channel: str) -> str:
        """Sanitize channel ID for use in filenames"""
        safe = channel.strip().lower()
        safe = safe.replace("/", "_").replace("\\", "_")
        safe = safe.replace("..", "_")
        
        if not safe or safe == "_":
            raise ValueError(f"Invalid channel ID: {channel}")
        
        return safe
    
    def _ensure_directory(self) -> None:
        """Ensure store directory exists"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    
    def _read_store(self) -> dict[str, Any]:
        """Read store from disk"""
        if not self.store_path.exists():
            return {"version": 1, "requests": []}
        
        try:
            with open(self.store_path, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"version": 1, "requests": []}
        except Exception as e:
            logger.warning(f"Failed to read pairing store: {e}")
            return {"version": 1, "requests": []}
    
    def _write_store(self, data: dict[str, Any]) -> None:
        """Write store to disk atomically"""
        import tempfile
        import os
        
        self._ensure_directory()
        
        # Write to temporary file first
        fd, tmp_path = tempfile.mkstemp(
            dir=self.store_path.parent,
            prefix=f"{self.store_path.name}.",
            suffix=".tmp"
        )
        
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            
            # Set permissions
            os.chmod(tmp_path, 0o600)
            
            # Atomic rename
            os.replace(tmp_path, self.store_path)
        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except:
                pass
            raise e
    
    def list_requests(self) -> list[PairingRequest]:
        """List all pending requests"""
        store = self._read_store()
        requests_data = store.get("requests", [])
        
        # Parse and filter expired
        requests = []
        for req_data in requests_data:
            try:
                req = PairingRequest.from_dict(req_data)
                if not req.is_expired():
                    requests.append(req)
            except Exception as e:
                logger.warning(f"Failed to parse pairing request: {e}")
        
        return requests
    
    def get_request(self, request_id: str) -> PairingRequest | None:
        """Get request by ID"""
        requests = self.list_requests()
        return next((r for r in requests if r.id == request_id), None)
    
    def get_or_create_request(
        self,
        request_id: str,
        meta: dict[str, str] | None = None,
    ) -> PairingRequest:
        """
        Get existing request or create new one
        
        Args:
            request_id: Unique request ID (user ID)
            meta: Optional metadata
            
        Returns:
            Pairing request
        """
        # Check existing
        existing = self.get_request(request_id)
        if existing:
            # Update last_seen_at
            existing.last_seen_at = datetime.now(timezone.utc).isoformat()
            self.update_request(existing)
            return existing
        
        # Create new request
        now = datetime.now(timezone.utc).isoformat()
        code = generate_pairing_code()
        
        request = PairingRequest(
            id=request_id,
            code=code,
            created_at=now,
            last_seen_at=now,
            meta=meta or {},
        )
        
        # Add to store
        self.add_request(request)
        
        return request
    
    def add_request(self, request: PairingRequest) -> None:
        """Add a pairing request"""
        store = self._read_store()
        requests = [
            PairingRequest.from_dict(r)
            for r in store.get("requests", [])
            if not PairingRequest.from_dict(r).is_expired()
        ]
        
        # Remove existing with same ID
        requests = [r for r in requests if r.id != request.id]
        
        # Enforce max pending
        if len(requests) >= PAIRING_PENDING_MAX:
            # Remove oldest
            requests.sort(key=lambda r: r.created_at)
            requests = requests[-(PAIRING_PENDING_MAX - 1):]
        
        # Add new request
        requests.append(request)
        
        # Save
        store["requests"] = [r.to_dict() for r in requests]
        self._write_store(store)
        
        logger.info(f"Added pairing request {request.id} with code {request.code}")
    
    def update_request(self, request: PairingRequest) -> None:
        """Update an existing request"""
        store = self._read_store()
        requests = [
            PairingRequest.from_dict(r)
            for r in store.get("requests", [])
        ]
        
        # Update matching request
        for i, r in enumerate(requests):
            if r.id == request.id:
                requests[i] = request
                break
        
        # Save
        store["requests"] = [r.to_dict() for r in requests]
        self._write_store(store)
    
    def remove_request(self, request_id: str) -> bool:
        """Remove a pairing request"""
        store = self._read_store()
        requests = [
            PairingRequest.from_dict(r)
            for r in store.get("requests", [])
        ]
        
        # Filter out matching request
        filtered = [r for r in requests if r.id != request_id]
        
        if len(filtered) == len(requests):
            return False  # Not found
        
        # Save
        store["requests"] = [r.to_dict() for r in filtered]
        self._write_store(store)
        
        logger.info(f"Removed pairing request {request_id}")
        return True
    
    def find_by_code(self, code: str) -> PairingRequest | None:
        """Find request by pairing code"""
        requests = self.list_requests()
        return next((r for r in requests if r.code.upper() == code.upper()), None)
    
    def clear_expired(self) -> int:
        """Remove all expired requests"""
        store = self._read_store()
        requests = [
            PairingRequest.from_dict(r)
            for r in store.get("requests", [])
        ]
        
        # Filter expired
        valid = [r for r in requests if not r.is_expired()]
        removed_count = len(requests) - len(valid)
        
        if removed_count > 0:
            store["requests"] = [r.to_dict() for r in valid]
            self._write_store(store)
            logger.info(f"Cleared {removed_count} expired pairing requests")
        
        return removed_count
