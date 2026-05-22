"""DM pairing system for secure channel access."""
import secrets
import time
from typing import Optional


class PairingManager:
    """Manages pairing codes for DM access control."""
    
    def __init__(self, allowlist_store=None):
        self.pending_codes: dict[str, dict] = {}
        self.allowlist_store = allowlist_store or AllowlistStore()
    
    def create_code(self, channel: str, user_id: str) -> str:
        """
        Create a new pairing code for a user.
        
        Args:
            channel: Channel name (e.g., 'telegram', 'discord')
            user_id: User identifier
            
        Returns:
            6-character pairing code
        """
        # Generate readable code (no ambiguous chars like O/0, I/1)
        code = ''.join(
            secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789')
            for _ in range(6)
        )
        
        # Store pending pairing
        self.pending_codes[code] = {
            'channel': channel,
            'user_id': user_id,
            'created_at': time.time(),
            'expires_at': time.time() + 300  # 5 minutes
        }
        
        return code
    
    async def approve(self, code: str) -> tuple[bool, str]:
        """
        Approve a pairing code and add user to allowlist.
        
        Args:
            code: The pairing code to approve
            
        Returns:
            (success, message)
        """
        if code not in self.pending_codes:
            return False, "Invalid or unknown code"
        
        pairing = self.pending_codes[code]
        
        # Check expiration
        if time.time() > pairing['expires_at']:
            del self.pending_codes[code]
            return False, "Code expired (5 minute limit)"
        
        # Add to allowlist
        try:
            await self.allowlist_store.add(
                pairing['channel'],
                pairing['user_id']
            )
        except Exception as e:
            return False, f"Failed to add to allowlist: {e}"
        
        # Remove from pending
        del self.pending_codes[code]
        
        return True, f"Approved: {pairing['channel']}:{pairing['user_id']}"
    
    def list_pending(self) -> list[dict]:
        """
        List all pending pairing requests.
        
        Returns:
            List of pending pairings with metadata
        """
        now = time.time()
        result = []
        
        for code, pairing in list(self.pending_codes.items()):
            # Remove expired codes
            if now > pairing['expires_at']:
                del self.pending_codes[code]
                continue
            
            result.append({
                'code': code,
                'channel': pairing['channel'],
                'user_id': pairing['user_id'],
                'expires_in': int(pairing['expires_at'] - now),
                'created_at': pairing['created_at']
            })
        
        return result
    
    def cleanup_expired(self) -> int:
        """
        Remove expired pairing codes.
        
        Returns:
            Number of codes removed
        """
        now = time.time()
        expired = [
            code for code, pairing in self.pending_codes.items()
            if now > pairing['expires_at']
        ]
        
        for code in expired:
            del self.pending_codes[code]
        
        return len(expired)


class AllowlistStore:
    """Stores and manages channel allowlists."""
    
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.allowlists: dict[str, set[str]] = {}
    
    async def add(self, channel: str, user_id: str) -> None:
        """Add user to channel allowlist."""
        if channel not in self.allowlists:
            self.allowlists[channel] = set()
        
        self.allowlists[channel].add(user_id)
        
        # TODO: Persist to config file
        if self.config_path:
            await self._save_to_config()
    
    async def remove(self, channel: str, user_id: str) -> bool:
        """Remove user from channel allowlist."""
        if channel not in self.allowlists:
            return False
        
        if user_id in self.allowlists[channel]:
            self.allowlists[channel].remove(user_id)
            
            # TODO: Persist to config file
            if self.config_path:
                await self._save_to_config()
            
            return True
        
        return False
    
    def is_allowed(self, channel: str, user_id: str) -> bool:
        """Check if user is in channel allowlist."""
        if channel not in self.allowlists:
            return False
        
        return user_id in self.allowlists[channel] or '*' in self.allowlists[channel]
    
    async def _save_to_config(self) -> None:
        """Save allowlists to config file."""
        # TODO: Implement config persistence
        pass
