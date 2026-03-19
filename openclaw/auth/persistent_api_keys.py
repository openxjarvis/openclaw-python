"""
Persistent API key storage

Provides SQLite-based persistent storage for API keys.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from openclaw.config.paths import resolve_state_dir

logger = logging.getLogger(__name__)


@dataclass
class APIKey:
    """API key with metadata."""
    key_id: str
    name: str
    key_hash: str
    permissions: list[str]
    created_at: int
    expires_at: int | None = None
    last_used_at: int | None = None
    enabled: bool = True
    rate_limit: int | None = None
    metadata: dict[str, Any] | None = None
    
    def has_permission(self, permission: str) -> bool:
        """Check if key has permission."""
        return permission in self.permissions
    
    def is_expired(self) -> bool:
        """Check if key is expired."""
        if self.expires_at is None:
            return False
        return int(time.time()) > self.expires_at
    
    def is_valid(self) -> bool:
        """Check if key is valid (enabled and not expired)."""
        return self.enabled and not self.is_expired()
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "key_id": self.key_id,
            "name": self.name,
            "permissions": self.permissions,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used_at": self.last_used_at,
            "enabled": self.enabled,
            "rate_limit": self.rate_limit,
            "metadata": self.metadata or {},
        }


class PersistentAPIKeyStore:
    """
    Persistent API key storage using SQLite.
    
    Features:
    - SQLite backend for persistence
    - Key hashing (SHA-256)
    - Permission management
    - Expiration support
    - Rate limiting
    - Metadata storage
    """
    
    def __init__(self, db_path: Path | None = None):
        """
        Initialize persistent API key store.
        
        Args:
            db_path: Path to SQLite database (default: ~/.openclaw/api_keys.db)
        """
        if db_path is None:
            db_path = resolve_state_dir() / "api_keys.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_database()
        
        logger.info(f"Persistent API key store initialized: {self.db_path}")
    
    def _init_database(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    permissions TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    last_used_at INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    rate_limit INTEGER,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_key_hash ON api_keys(key_hash)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_enabled ON api_keys(enabled)
            """)
            conn.commit()
    
    def _hash_key(self, raw_key: str) -> str:
        """Hash API key using SHA-256."""
        return hashlib.sha256(raw_key.encode()).hexdigest()
    
    def create_key(
        self,
        name: str,
        permissions: list[str],
        expires_days: int | None = None,
        rate_limit: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Create a new API key.
        
        Args:
            name: Key name/description
            permissions: List of permissions (e.g., ["read", "write", "admin"])
            expires_days: Days until expiration (None = never)
            rate_limit: Rate limit (requests per minute)
            metadata: Additional metadata
        
        Returns:
            Raw API key (store this, it won't be shown again!)
        """
        # Generate key
        key_id = secrets.token_urlsafe(8)
        raw_key = f"clb_{secrets.token_urlsafe(32)}"
        key_hash = self._hash_key(raw_key)
        
        # Calculate expiration
        created_at = int(time.time())
        expires_at = None
        if expires_days is not None:
            expires_at = created_at + (expires_days * 24 * 3600)
        
        # Store in database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO api_keys (
                    key_id, name, key_hash, permissions, created_at,
                    expires_at, enabled, rate_limit, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key_id,
                    name,
                    key_hash,
                    json.dumps(permissions),
                    created_at,
                    expires_at,
                    1,
                    rate_limit,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()
        
        logger.info(f"Created API key: {key_id} ({name})")
        return raw_key
    
    def validate_key(self, raw_key: str) -> APIKey | None:
        """
        Validate an API key.
        
        Args:
            raw_key: Raw API key
        
        Returns:
            APIKey if valid, None if invalid
        """
        if not raw_key or not raw_key.startswith("clb_"):
            return None
        
        key_hash = self._hash_key(raw_key)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)
            )
            row = cursor.fetchone()
        
        if not row:
            return None
        
        api_key = APIKey(
            key_id=row["key_id"],
            name=row["name"],
            key_hash=row["key_hash"],
            permissions=json.loads(row["permissions"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            enabled=bool(row["enabled"]),
            rate_limit=row["rate_limit"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )
        
        if not api_key.is_valid():
            return None
        
        # Update last used
        self._update_last_used(api_key.key_id)
        
        return api_key
    
    def _update_last_used(self, key_id: str):
        """Update last used timestamp."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                (int(time.time()), key_id),
            )
            conn.commit()
    
    def revoke_key(self, key_id: str) -> bool:
        """
        Revoke (disable) an API key.
        
        Args:
            key_id: Key ID
        
        Returns:
            True if revoked, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE api_keys SET enabled = 0 WHERE key_id = ?", (key_id,)
            )
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Revoked API key: {key_id}")
                return True
        
        return False
    
    def delete_key(self, key_id: str) -> bool:
        """
        Permanently delete an API key.
        
        Args:
            key_id: Key ID
        
        Returns:
            True if deleted, False if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Deleted API key: {key_id}")
                return True
        
        return False
    
    def list_keys(self, include_disabled: bool = False) -> list[APIKey]:
        """
        List all API keys.
        
        Args:
            include_disabled: Include disabled keys
        
        Returns:
            List of API keys (without raw key values)
        """
        query = "SELECT * FROM api_keys"
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query)
            rows = cursor.fetchall()
        
        keys = []
        for row in rows:
            keys.append(
                APIKey(
                    key_id=row["key_id"],
                    name=row["name"],
                    key_hash=row["key_hash"],
                    permissions=json.loads(row["permissions"]),
                    created_at=row["created_at"],
                    expires_at=row["expires_at"],
                    last_used_at=row["last_used_at"],
                    enabled=bool(row["enabled"]),
                    rate_limit=row["rate_limit"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                )
            )
        
        return keys
    
    def update_key(
        self,
        key_id: str,
        name: str | None = None,
        permissions: list[str] | None = None,
        rate_limit: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Update an API key's properties.
        
        Args:
            key_id: Key ID
            name: New name
            permissions: New permissions
            rate_limit: New rate limit
            metadata: New metadata
        
        Returns:
            True if updated, False if not found
        """
        updates = []
        values = []
        
        if name is not None:
            updates.append("name = ?")
            values.append(name)
        
        if permissions is not None:
            updates.append("permissions = ?")
            values.append(json.dumps(permissions))
        
        if rate_limit is not None:
            updates.append("rate_limit = ?")
            values.append(rate_limit)
        
        if metadata is not None:
            updates.append("metadata = ?")
            values.append(json.dumps(metadata))
        
        if not updates:
            return False
        
        values.append(key_id)
        query = f"UPDATE api_keys SET {', '.join(updates)} WHERE key_id = ?"
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, values)
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Updated API key: {key_id}")
                return True
        
        return False
