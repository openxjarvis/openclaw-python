"""
Cross-channel identity linking.

Allows same user across platforms to share sessions.
Example: Telegram user A = Discord user B = canonical "user123"

Matches openclaw identity linking logic.
"""
from __future__ import annotations

import json
from pathlib import Path
from openclaw.config.paths import resolve_state_dir


def normalize_token(value: str | None) -> str:
    """Normalize string token to lowercase"""
    return (value or "").strip().lower()


def resolve_linked_peer_id(
    identity_links: dict[str, list[str]] | None,
    channel: str,
    peer_id: str
) -> str | None:
    """
    Resolve peer ID to canonical identity via links.
    
    Identity links map canonical identity to list of channel-specific IDs:
    {
        "user123": ["telegram:456", "discord:789", "slack:abc"],
        "user456": ["telegram:111", "whatsapp:222"]
    }
    
    Args:
        identity_links: Identity link mapping
        channel: Channel name
        peer_id: Peer ID to resolve
        
    Returns:
        Canonical peer ID if linked, None otherwise
    """
    if not identity_links:
        return None
    
    peer_id_trimmed = peer_id.strip()
    if not peer_id_trimmed:
        return None
    
    # Build candidate identities to check
    candidates = set()
    
    # Raw peer ID
    raw_candidate = normalize_token(peer_id_trimmed)
    if raw_candidate:
        candidates.add(raw_candidate)
    
    # Scoped peer ID (channel:peerId)
    channel_norm = normalize_token(channel)
    if channel_norm:
        scoped_candidate = normalize_token(f"{channel_norm}:{peer_id_trimmed}")
        if scoped_candidate:
            candidates.add(scoped_candidate)
    
    if not candidates:
        return None
    
    # Search for matching identity
    for canonical, ids in identity_links.items():
        canonical_name = canonical.strip()
        if not canonical_name:
            continue
        
        if not isinstance(ids, list):
            continue
        
        for identity_id in ids:
            normalized = normalize_token(identity_id)
            if normalized and normalized in candidates:
                return canonical_name
    
    return None


class IdentityLinkStore:
    """
    Manages identity links between channels.
    
    Stores bidirectional mappings between channel-specific identities
    and canonical identities to enable cross-channel session sharing.
    
    Usage:
        store = IdentityLinkStore(config_path)
        
        # Add link
        store.add_link("telegram:123", "discord:456", canonical="user_abc")
        
        # Resolve identity
        canonical = store.resolve_identity("telegram:123", "telegram")
        # Returns: "user_abc"
        
        # Get all identities for canonical
        identities = store.get_linked_identities("user_abc")
        # Returns: ["telegram:123", "discord:456"]
    """
    
    def __init__(self, config_path: Path | None = None):
        """
        Initialize identity link store.
        
        Args:
            config_path: Path to identity links JSON file
        """
        self._config_path = config_path or (resolve_state_dir() / "identity_links.json")
        self._links: dict[str, list[str]] = {}  # canonical -> [identities]
        self._reverse_index: dict[str, str] = {}  # identity -> canonical
        self._load()
    
    def _load(self):
        """Load identity links from file"""
        if not self._config_path.exists():
            return
        
        try:
            with open(self._config_path, 'r') as f:
                data = json.load(f)
                self._links = data.get('links', {})
                self._build_reverse_index()
        except Exception:
            pass
    
    def _save(self):
        """Save identity links to file"""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            'links': self._links
        }
        
        with open(self._config_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _build_reverse_index(self):
        """Build reverse index for fast lookups"""
        self._reverse_index = {}
        for canonical, identities in self._links.items():
            for identity in identities:
                normalized = normalize_token(identity)
                if normalized:
                    self._reverse_index[normalized] = canonical
    
    def add_link(
        self,
        identity1: str,
        identity2: str,
        canonical: str | None = None
    ):
        """
        Link two identities together.
        
        Args:
            identity1: First identity (e.g., "telegram:123")
            identity2: Second identity (e.g., "discord:456")
            canonical: Canonical identity name (auto-generated if None)
        """
        id1_norm = normalize_token(identity1)
        id2_norm = normalize_token(identity2)
        
        if not id1_norm or not id2_norm:
            return
        
        # Find existing canonical
        existing_canonical = self._reverse_index.get(id1_norm) or self._reverse_index.get(id2_norm)
        
        if not canonical:
            canonical = existing_canonical or id1_norm
        
        # Merge with existing links if canonical exists
        if canonical in self._links:
            identities = set(self._links[canonical])
            identities.add(id1_norm)
            identities.add(id2_norm)
            self._links[canonical] = list(identities)
        else:
            self._links[canonical] = [id1_norm, id2_norm]
        
        # Update reverse index
        self._reverse_index[id1_norm] = canonical
        self._reverse_index[id2_norm] = canonical
        
        self._save()
    
    def resolve_identity(self, identity: str, channel: str | None = None) -> str | None:
        """
        Get canonical identity for a channel-specific identity.
        
        Args:
            identity: Identity to resolve
            channel: Optional channel name
            
        Returns:
            Canonical identity if linked, None otherwise
        """
        return resolve_linked_peer_id(
            identity_links=self._links,
            channel=channel or "",
            peer_id=identity
        )
    
    def get_linked_identities(self, canonical: str) -> list[str]:
        """
        Get all identities linked to canonical identity.
        
        Args:
            canonical: Canonical identity
            
        Returns:
            List of linked identities
        """
        return self._links.get(canonical, [])
    
    def remove_link(self, identity: str):
        """
        Remove an identity from all links.
        
        Args:
            identity: Identity to remove
        """
        identity_norm = normalize_token(identity)
        canonical = self._reverse_index.get(identity_norm)
        
        if canonical and canonical in self._links:
            identities = self._links[canonical]
            identities = [i for i in identities if normalize_token(i) != identity_norm]
            
            if identities:
                self._links[canonical] = identities
            else:
                del self._links[canonical]
            
            self._build_reverse_index()
            self._save()
    
    def get_all_links(self) -> dict[str, list[str]]:
        """Get all identity links"""
        return self._links.copy()


__all__ = [
    "resolve_linked_peer_id",
    "IdentityLinkStore",
]
