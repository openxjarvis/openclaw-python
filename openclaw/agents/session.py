"""
Session management for agent conversations
"""
from __future__ import annotations


import json
import logging
import threading
import time
import uuid as _uuid_module
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

from openclaw.agents.session_ids import generate_session_id, looks_like_session_id
from openclaw.agents.session_entry import SessionEntry, SessionStore
from openclaw.config.paths import resolve_state_dir
from openclaw.routing.session_key import (
    build_agent_main_session_key,
    build_agent_peer_session_key,
    normalize_agent_id,
    parse_agent_session_key,
)

logger = logging.getLogger(__name__)

# Cache TTL in seconds (matches TypeScript 45s)
SESSION_STORE_CACHE_TTL = 45.0


class Message(BaseModel):
    """A single message in a conversation.

    ``content`` accepts either a plain string or a list of typed content blocks
    (text, tool_use, tool_result, image) exactly as the Anthropic / OpenAI APIs
    return them.  This mirrors the TS ``Message.content: ContentBlock[]`` union.
    """

    model_config = {"arbitrary_types_allowed": True}

    role: str  # "user", "assistant", "system", "tool"
    content: str | list[Any]  # str OR list of content blocks
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # For tool results
    images: list[str] | None = None  # Image URLs or paths
    # JSONL DAG fields — set by Session._save()
    id: str | None = None          # Entry UUID (full v4)
    parent_id: str | None = None   # Previous entry UUID (for compaction DAG)

    def to_api_format(self) -> dict[str, Any]:
        """Convert to API format for LLM calls"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls

        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id

        if self.name:
            msg["name"] = self.name

        return msg


class _HistoryProxy:
    """List-like proxy over Session.messages that returns dicts for item access.

    This allows:
    - ``session.history.append({"role": "user", "content": "hello"})``
    - ``session.history[0]["content"]``
    - ``len(session.history)``
    - ``for msg in session.history``
    """

    __slots__ = ("_session",)

    def __init__(self, session: "Session") -> None:
        self._session = session

    def __len__(self) -> int:
        return len(self._session.messages)

    def __iter__(self):
        for msg in self._session.messages:
            yield msg if isinstance(msg, dict) else msg.model_dump()

    def __getitem__(self, index):
        msg = self._session.messages[index]
        return msg if isinstance(msg, dict) else msg.model_dump()

    def append(self, item) -> None:
        if isinstance(item, Message):
            self._session.messages.append(item)
        elif isinstance(item, dict):
            try:
                self._session.messages.append(Message(**item))
            except Exception:
                self._session.messages.append(item)  # type: ignore[arg-type]
        else:
            self._session.messages.append(item)  # type: ignore[arg-type]

    def __eq__(self, other) -> bool:
        if isinstance(other, list):
            return list(self) == other
        return NotImplemented


class Session(BaseModel):
    """
    Manages a conversation session with persistence
    """

    session_id: str
    workspace_dir: Path
    session_key: str | None = None  # Optional session key for reference
    agent_id: str | None = None     # Agent identifier (optional, for display)
    messages: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Private override for sessions directory (used for workspace-scoped sessions)
    _sessions_dir_override: Path | None = PrivateAttr(default=None)
    # Thread-safe write lock for sync _save() calls
    _write_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        session_id: str | None = None,
        workspace_dir: Path | None = None,
        session_key: str | None = None,
        *,
        sessions_dir_override: Path | None = None,
        agent_id: str | None = None,
        **kwargs,
    ):
        """
        Initialize session with UUID.

        Args:
            session_id: Session UUID or simple key.  Auto-generated if omitted.
            workspace_dir: Workspace directory.
            session_key: Optional session key for reference.
            agent_id: Optional agent identifier stored on the session.
        """
        import uuid as _uuid_mod
        if session_id is None:
            session_id = str(_uuid_mod.uuid4())
        if workspace_dir is None:
            workspace_dir = resolve_state_dir() / "workspace"
        super().__init__(
            session_id=session_id,
            workspace_dir=workspace_dir,
            session_key=session_key,
            agent_id=agent_id,
            **kwargs,
        )

        # Set sessions directory override for workspace-scoped sessions
        if sessions_dir_override is not None:
            self._sessions_dir_override = sessions_dir_override

        # Validate UUID format (warning only — non-UUID keys allowed for simplicity)
        import uuid as uuid_module
        try:
            uuid_module.UUID(session_id)
        except ValueError:
            logger.debug(f"session_id is not a valid UUID: {session_id} (non-UUID keys are allowed)")
        
        # Create sessions directory
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Load from JSONL (v3) or legacy .json if present
        if (self._session_file.exists() or self._session_file_legacy.exists()) and not self.messages:
            self._load()

    @property
    def history(self) -> "_HistoryProxy":
        """Alias for .messages — returns a proxy that supports dict-style access
        and .append() that writes back to self.messages.
        """
        return _HistoryProxy(self)

    @history.setter
    def history(self, value: list) -> None:
        """Accept list of dicts or Message objects."""
        parsed = []
        for item in value:
            if isinstance(item, Message):
                parsed.append(item)
            elif isinstance(item, dict):
                try:
                    parsed.append(Message(**item))
                except Exception:
                    parsed.append(item)
            else:
                parsed.append(item)
        self.messages = parsed  # type: ignore[assignment]

    @property
    def _sessions_dir(self) -> Path:
        """
        Get sessions directory.

        If a sessions_dir_override was set, use that.
        Otherwise use workspace_dir / ".sessions" (portable, predictable).
        """
        if self._sessions_dir_override is not None:
            sessions_dir = self._sessions_dir_override
        else:
            sessions_dir = self.workspace_dir / ".sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    @property
    def _session_file(self) -> Path:
        """Session JSONL transcript path.  Extension is ``.jsonl`` (v3 format)."""
        return self._sessions_dir / f"{self.session_id}.jsonl"

    @property
    def _session_file_legacy(self) -> Path:
        """Old ``.json`` path for backward-compat migration check."""
        return self._sessions_dir / f"{self.session_id}.json"

    def add_message(self, role: str, content: str | list[Any], **kwargs) -> Message:
        """Add a message to the session"""
        msg = Message(role=role, content=content, **kwargs)
        self.messages.append(msg)
        self.updated_at = datetime.now(UTC).isoformat()
        self._save()
        return msg

    def add_user_message(self, content: str, images: list | None = None) -> Message:
        """Add a user message with optional images (mirrors TS addUserMessage)."""
        msg = self.add_message("user", content)
        if images:
            msg.images = images
        return msg

    def add_assistant_message(
        self, content: str, tool_calls: list[dict[str, Any]] | None = None
    ) -> Message:
        """Add an assistant message"""
        return self.add_message("assistant", content, tool_calls=tool_calls)

    def add_system_message(self, content: str) -> Message:
        """Add a system message"""
        return self.add_message("system", content)

    def add_tool_message(self, tool_call_id: str, content: str, name: str | None = None) -> Message:
        """Add a tool result message"""
        return self.add_message("tool", content, tool_call_id=tool_call_id, name=name)

    def get_messages(self, limit: int | None = None) -> list[Message]:
        """Get messages, optionally limited to last N"""
        if limit is None:
            return self.messages
        return self.messages[-limit:]

    def get_messages_for_api(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Get messages in API format"""
        messages = self.get_messages(limit)
        return [msg.to_api_format() for msg in messages]

    def clear(self) -> None:
        """Clear all messages and rewrite the JSONL file with an empty header."""
        self.messages = []
        self.updated_at = datetime.now(UTC).isoformat()
        try:
            with self._write_lock:
                # Truncate and rewrite — JSONL is append-only so we must rewrite
                header: dict[str, Any] = {
                    "type": "session",
                    "version": 3,
                    "id": self.session_id,
                    "timestamp": self.created_at,
                }
                if self.workspace_dir:
                    header["cwd"] = str(self.workspace_dir)
                self._session_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._session_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps(header, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to clear session %s: %s", self.session_id, e)

    def set_metadata(self, key: str, value: Any) -> None:
        """Set metadata value"""
        self.metadata[key] = value
        self._save_metadata()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata value"""
        return self.metadata.get(key, default)

    # ── Append-only JSONL persistence (v3 format, mirrors TS session-manager.ts) ─────

    def _save_metadata(self) -> None:
        """Append a metadata snapshot entry to the JSONL transcript."""
        try:
            self._ensure_session_header()
            entry: dict[str, Any] = {
                "type": "metadata",
                "id": str(_uuid_module.uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "data": self.metadata,
            }
            with self._write_lock:
                with open(self._session_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error("Failed to save metadata for session %s: %s", self.session_id, e)

    def _ensure_session_header(self) -> None:
        """Write v3 session header if the JSONL file does not exist yet."""
        if self._session_file.exists():
            return
        header: dict[str, Any] = {
            "type": "session",
            "version": 3,
            "id": self.session_id,
            "timestamp": self.created_at,
        }
        if self.workspace_dir:
            header["cwd"] = str(self.workspace_dir)
        if self.session_key:
            header["sessionKey"] = self.session_key
        if self.agent_id:
            header["agentId"] = self.agent_id
        try:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._session_file, "w", encoding="utf-8") as f:
                f.write(json.dumps(header, default=str) + "\n")
        except Exception as exc:
            logger.error("Failed to write session header: %s", exc)

    def _read_last_entry_id(self) -> str | None:
        """Return the ``id`` field of the last JSONL line (for parentId chain)."""
        if not self._session_file.exists():
            return None
        try:
            buf = b""
            with open(self._session_file, "rb") as f:
                f.seek(0, 2)
                pos = f.tell()
                while pos > 0:
                    read_size = min(512, pos)
                    pos -= read_size
                    f.seek(pos)
                    chunk = f.read(read_size)
                    buf = chunk + buf
                    lines = buf.split(b"\n")
                    for line in reversed(lines):
                        stripped = line.strip()
                        if stripped:
                            entry = json.loads(stripped.decode("utf-8", errors="replace"))
                            return entry.get("id")
        except Exception:
            pass
        return None

    def _save(self) -> None:
        """
        Append the most recently added message to the JSONL transcript.

        On first call (file doesn't exist) a v3 session header line is written
        first.  Each message entry carries a full UUIDv4 ``id`` and a
        ``parentId`` pointing to the previous entry, forming a DAG that the
        compaction algorithm traverses — mirrors TS session-manager.ts.

        A SessionWriteLock is acquired before any write to prevent concurrent
        corruption.
        """
        try:
            self._ensure_session_header()

            # Determine which message to persist (only the last one, newly added)
            if not self.messages:
                return
            msg = self.messages[-1]
            msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg) if isinstance(msg, dict) else {}

            entry_id = str(_uuid_module.uuid4())
            parent_id = self._read_last_entry_id()

            entry: dict[str, Any] = {
                "type": "message",
                "id": entry_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "message": msg_dict,
            }
            if parent_id:
                entry["parentId"] = parent_id

            with self._write_lock:
                with open(self._session_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, default=str) + "\n")

            # Stamp the message with its DAG ids so callers can read them back
            if hasattr(msg, "__dict__"):
                try:
                    object.__setattr__(msg, "id", entry_id)
                    if parent_id:
                        object.__setattr__(msg, "parent_id", parent_id)
                except Exception:
                    pass

            self.updated_at = datetime.now(UTC).isoformat()
        except Exception as e:
            logger.error("Failed to append message to session %s: %s", self.session_id, e)

    def _save_all(self) -> None:
        """Rewrite the entire JSONL file with the header and all current messages.

        Use this when the history has been replaced en-masse (e.g. via the
        history setter or save_session).  Incremental callers should use
        _save() instead to avoid O(n) rewrites on every message.
        """
        try:
            header: dict[str, Any] = {
                "type": "session",
                "version": 3,
                "id": self.session_id,
                "timestamp": self.created_at,
            }
            if self.workspace_dir:
                header["cwd"] = str(self.workspace_dir)
            if self.session_key:
                header["sessionKey"] = self.session_key
            if self.agent_id:
                header["agentId"] = self.agent_id

            lines: list[str] = [json.dumps(header, default=str)]
            prev_id: str | None = None
            for msg in self.messages:
                msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg) if isinstance(msg, dict) else {}
                entry_id = str(_uuid_module.uuid4())
                entry: dict[str, Any] = {
                    "type": "message",
                    "id": entry_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "message": msg_dict,
                }
                if prev_id:
                    entry["parentId"] = prev_id
                lines.append(json.dumps(entry, default=str))
                prev_id = entry_id

            if self.metadata:
                meta_entry: dict[str, Any] = {
                    "type": "metadata",
                    "id": str(_uuid_module.uuid4()),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "data": self.metadata,
                }
                lines.append(json.dumps(meta_entry, default=str))

            with self._write_lock:
                self._session_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._session_file, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")

            self.updated_at = datetime.now(UTC).isoformat()
        except Exception as e:
            logger.error("Failed to save_all session %s: %s", self.session_id, e)

    def _load(self) -> None:
        """
        Load session state from the JSONL transcript.

        Handles both new JSONL (v3) files and legacy JSON files for migration.
        """
        # Migration: old .json file without a .jsonl counterpart
        legacy = self._session_file_legacy
        if not self._session_file.exists() and legacy.exists():
            try:
                with open(legacy) as f:
                    data = json.load(f)
                loaded_msgs = []
                for m in data.get("messages", []):
                    if isinstance(m, dict):
                        try:
                            loaded_msgs.append(Message(**m))
                        except Exception:
                            loaded_msgs.append(m)
                    else:
                        loaded_msgs.append(m)
                self.messages = loaded_msgs  # type: ignore[assignment]
                self.metadata = data.get("metadata", {})
                self.created_at = data.get("created_at", self.created_at)
                self.updated_at = data.get("updated_at", self.updated_at)
                if data.get("session_key") and not self.session_key:
                    self.session_key = data["session_key"]
                if data.get("agent_id") and not self.agent_id:
                    self.agent_id = data["agent_id"]
            except Exception as e:
                logger.error("Failed to load legacy session %s: %s", self.session_id, e)
            return

        # JSONL path
        if not self._session_file.exists():
            return
        try:
            loaded_msgs: list[Message] = []
            with open(self._session_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    entry_type = entry.get("type")
                    if entry_type == "session":
                        # Header — restore session_key / agent_id if present.
                        # NOTE: Do NOT restore cwd from old session files.
                        # TS never reads back cwd from JSONL to override workspace_dir.
                        # Doing so causes stale per-session slug paths to pollute workspace.
                        if entry.get("sessionKey") and not self.session_key:
                            self.session_key = entry["sessionKey"]
                        if entry.get("agentId") and not self.agent_id:
                            self.agent_id = entry["agentId"]
                    elif entry_type == "metadata":
                        # Metadata snapshot — merge into current metadata
                        data = entry.get("data")
                        if isinstance(data, dict):
                            self.metadata.update(data)
                    elif entry_type == "message":
                        msg_data = entry.get("message")
                        if isinstance(msg_data, dict):
                            try:
                                msg = Message(**msg_data)
                            except Exception:
                                msg = msg_data  # type: ignore[assignment]
                            # Restore DAG ids
                            if isinstance(msg, Message):
                                try:
                                    msg.id = entry.get("id")
                                    msg.parent_id = entry.get("parentId")
                                except Exception:
                                    pass
                            loaded_msgs.append(msg)
            self.messages = loaded_msgs  # type: ignore[assignment]
        except Exception as e:
            logger.error("Failed to load JSONL session %s: %s", self.session_id, e)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return {
            "sessionId": self.session_id,
            "messageCount": len(self.messages),
            "messages": [msg.model_dump() for msg in self.messages],
            "metadata": self.metadata,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class SessionManager:
    """
    Manages multiple sessions with enhanced session key support
    
    New features:
    - Session key format (agent:id:channel:group:id)
    - UUID v4 session IDs with validation
    - DM scope modes (main, per-peer, per-channel-peer, per-account-channel-peer)
    - Agent ID normalization
    - Session key to session ID mapping
    """

    def __init__(
        self,
        workspace_dir: Path | None = None,
        agent_id: str = "main",
        base_dir: Path | None = None,
    ):
        """
        Initialize session manager

        Args:
            workspace_dir: Base directory for session storage (legacy, still used for fallback)
            agent_id: Agent identifier (default: "main")
            base_dir: Override the openclaw home directory (useful for testing isolation)
        """
        self.workspace_dir = Path(workspace_dir) if workspace_dir is not None else resolve_state_dir() / "workspace"
        self.agent_id = normalize_agent_id(agent_id)
        self._sessions: dict[str, Session] = {}

        # Create workspace directory
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Canonical path: ~/.openclaw/agents/{agentId}/sessions/ (matches TS)
        # base_dir allows tests to use a tmp directory instead of the real ~/.openclaw
        openclaw_home = Path(base_dir) if base_dir is not None else resolve_state_dir()
        self._sessions_dir = openclaw_home / "agents" / self.agent_id / "sessions"
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        
        # Support both old (session_map.json) and new (sessions.json) formats
        self._sessions_file = self._sessions_dir / "sessions.json"
        self._legacy_session_map_file = self._sessions_dir / "session_map.json"
        
        # Keep references for migration reads (read-only, no mkdir)
        self._legacy_sessions_dir = self.workspace_dir / ".sessions"
        self._legacy_sessions_file = self._legacy_sessions_dir / "sessions.json"
        self._legacy_legacy_map_file = self._legacy_sessions_dir / "session_map.json"
        
        # Session store caching with mtime-based invalidation
        self._session_store: SessionStore | None = None
        self._session_store_loaded_at: float = 0.0
        self._session_store_file_mtime: float | None = None
        
        # Lock file for concurrent access protection
        self._lock_file = self._sessions_file.with_suffix(".json.lock")

        # Load initial session store
        self._session_store = self._load_session_store()
    
    @property
    def sessions_dir(self) -> Path:
        """Public accessor for sessions directory"""
        return self._sessions_dir
    
    def _acquire_lock(self, timeout: float = 10.0) -> bool:
        """
        Acquire file lock for session store access.
        
        Args:
            timeout: Maximum time to wait for lock (seconds)
            
        Returns:
            True if lock acquired
        """
        start_time = time.time()
        
        while True:
            try:
                # Try to create lock file exclusively
                self._lock_file.touch(exist_ok=False)
                logger.debug("Acquired session store lock")
                return True
            except FileExistsError:
                # Lock file exists - check if it's stale (>30s old)
                try:
                    lock_age = time.time() - self._lock_file.stat().st_mtime
                    if lock_age > 30.0:
                        # Stale lock - remove and retry
                        logger.warning(f"Removing stale lock file (age={lock_age:.1f}s)")
                        self._lock_file.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    # Lock was removed between check and stat
                    continue
                
                # Check timeout
                if time.time() - start_time > timeout:
                    logger.error(f"Failed to acquire lock after {timeout}s")
                    return False
                
                # Wait and retry
                time.sleep(0.1)
    
    def _release_lock(self) -> None:
        """Release file lock."""
        try:
            self._lock_file.unlink(missing_ok=True)
            logger.debug("Released session store lock")
        except Exception as e:
            logger.warning(f"Failed to release lock: {e}")
    
    def _is_cache_valid(self) -> bool:
        """
        Check if cached session store is still valid.
        
        Validates based on:
        1. Time since last load (45s TTL)
        2. File modification time
        
        Returns:
            True if cache is valid
        """
        if self._session_store is None:
            return False
        
        # Check TTL
        age = time.time() - self._session_store_loaded_at
        if age > SESSION_STORE_CACHE_TTL:
            logger.debug(f"Cache expired (age={age:.1f}s > TTL={SESSION_STORE_CACHE_TTL}s)")
            return False
        
        # Check file mtime
        if self._sessions_file.exists():
            try:
                current_mtime = self._sessions_file.stat().st_mtime
                if self._session_store_file_mtime is None or current_mtime != self._session_store_file_mtime:
                    logger.debug("Cache invalidated by file modification")
                    return False
            except OSError as e:
                logger.warning(f"Failed to check file mtime: {e}")
                return False
        
        return True
    
    def _get_session_store(self) -> SessionStore:
        """
        Get session store with caching.
        
        Returns:
            SessionStore instance
        """
        # Check cache validity
        if self._is_cache_valid():
            return self._session_store
        
        # Reload from disk
        logger.debug("Reloading session store from disk")
        if not self._acquire_lock(timeout=10.0):
            logger.warning("Failed to acquire lock, using stale cache")
            return self._session_store or SessionStore(__root__={})
        
        try:
            self._session_store = self._load_session_store()
            return self._session_store
        finally:
            self._release_lock()

    def _load_session_store(self) -> SessionStore:
        """Load session store, migrating from legacy formats and locations if needed."""
        # Update cache metadata
        self._session_store_loaded_at = time.time()
        
        # Try loading new format from new location first
        if self._sessions_file.exists():
            try:
                # Record file mtime
                self._session_store_file_mtime = self._sessions_file.stat().st_mtime
                
                with open(self._sessions_file) as f:
                    data = json.load(f)
                logger.info(f"Loaded sessions.json from {self._sessions_file}")
                return SessionStore.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load sessions.json: {e}")
        
        # Check old location for sessions.json
        if hasattr(self, '_legacy_sessions_file') and self._legacy_sessions_file.exists():
            try:
                logger.info(f"Migrating sessions.json from {self._legacy_sessions_file} to {self._sessions_file}")
                with open(self._legacy_sessions_file) as f:
                    data = json.load(f)
                store = SessionStore.from_dict(data)
                # Save to new location
                self._save_session_store(store)
                logger.info(f"Migrated sessions.json to new location")
                return store
            except Exception as e:
                logger.warning(f"Failed to migrate sessions.json: {e}")
        
        # Fall back to legacy session_map.json format in new location
        if self._legacy_session_map_file.exists():
            try:
                logger.info("Migrating from legacy session_map.json to sessions.json")
                with open(self._legacy_session_map_file) as f:
                    legacy_map = json.load(f)
                
                # Convert legacy {key: id} to {key: SessionEntry}
                store = SessionStore(root={})
                now = int(datetime.now(UTC).timestamp() * 1000)
                
                for session_key, session_id in legacy_map.items():
                    entry = SessionEntry(
                        sessionId=session_id,
                        updatedAt=now,
                    )
                    store.set(session_key, entry)
                
                # Save in new format
                self._save_session_store(store)
                logger.info(f"Migrated {len(legacy_map)} sessions to new format")
                
                return store
            except Exception as e:
                logger.warning(f"Failed to migrate session map: {e}")
        
        # Fall back to legacy session_map.json in old location
        if hasattr(self, '_legacy_legacy_map_file') and self._legacy_legacy_map_file.exists():
            try:
                logger.info(f"Migrating from legacy location: {self._legacy_legacy_map_file}")
                with open(self._legacy_legacy_map_file) as f:
                    legacy_map = json.load(f)
                
                # Convert legacy {key: id} to {key: SessionEntry}
                store = SessionStore(root={})
                now = int(datetime.now(UTC).timestamp() * 1000)
                
                for session_key, session_id in legacy_map.items():
                    entry = SessionEntry(
                        sessionId=session_id,
                        updatedAt=now,
                    )
                    store.set(session_key, entry)
                
                # Save in new format and location
                self._save_session_store(store)
                logger.info(f"Migrated {len(legacy_map)} sessions from old location to new format")
                
                return store
            except Exception as e:
                logger.warning(f"Failed to migrate from old location: {e}")
        
        # Return empty store
        return SessionStore(root={})

    
    def _save_session_store(self, store: SessionStore | None = None):
        """Save session store in new format with file locking."""
        if store is None:
            store = self._session_store
        
        if not self._acquire_lock(timeout=10.0):
            logger.error("Failed to acquire lock for save")
            return
        
        try:
            with open(self._sessions_file, "w") as f:
                json.dump(store.to_dict(), f, indent=2)
            
            # Update cache metadata
            self._session_store_loaded_at = time.time()
            self._session_store_file_mtime = self._sessions_file.stat().st_mtime
            logger.debug("Saved session store")
        except Exception as e:
            logger.error(f"Failed to save session store: {e}")
        finally:
            self._release_lock()

    def generate_session_id(self) -> str:
        """Generate a new UUID v4 session ID."""
        return generate_session_id()
    
    def validate_session_id(self, session_id: str) -> bool:
        """Validate session ID format (UUID v4)."""
        return looks_like_session_id(session_id)

    def get_or_create_session(
        self,
        session_id: str | None = None,
        session_key: str | None = None,
        channel: str | None = None,
        peer_kind: str | None = None,
        peer_id: str | None = None,
        dm_scope: str = "main",
    ) -> Session:
        """
        Get existing session or create new one with session key support.
        
        Args:
            session_id: Direct session ID (legacy, optional)
            session_key: Session key (agent:id:..., optional)
            channel: Channel name (telegram, discord, etc.)
            peer_kind: Peer type (dm, group, channel)
            peer_id: Peer identifier
            dm_scope: DM scope mode (main, per-peer, per-channel-peer, per-account-channel-peer)
        
        Returns:
            Session instance
        """
        # Build session key if not provided
        if session_key is None and channel and peer_kind and peer_id:
            session_key = build_agent_peer_session_key(
                self.agent_id,
                channel,
                peer_kind,
                peer_id,
                dm_scope=dm_scope
            )
        elif session_key is None and session_id is None:
            session_key = build_agent_main_session_key(self.agent_id)
        
        # Look up or create session ID using SessionStore (with caching)
        store = self._get_session_store()
        logger.info(f"get_or_create_session: session_key={session_key}, existing_keys={list(store.keys())}")
        
        entry = store.get(session_key) if session_key else None
        
        if entry:
            session_id = entry.sessionId
            logger.info(f"Found existing session: {session_key} -> {session_id}")
            # Update timestamp
            entry.updatedAt = int(datetime.now(UTC).timestamp() * 1000)
            self._save_session_store()
        else:
            # Generate new session ID if not provided or invalid
            if session_id is None or not self.validate_session_id(session_id):
                session_id = self.generate_session_id()
            
            # Create new SessionEntry
            if session_key:
                now = int(datetime.now(UTC).timestamp() * 1000)
                entry = SessionEntry(
                    sessionId=session_id,
                    updatedAt=now,
                )
                store.set(session_key, entry)
                self._session_store = store  # Update cache
                self._save_session_store()
                logger.info(f"Created NEW session: {session_key} -> {session_id}, store now has {len(store.keys())} keys")
        
        # Get or create session instance
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(
                session_id,
                self.workspace_dir,
                session_key=session_key,
                sessions_dir_override=self._sessions_dir,
            )
        
        return self._sessions[session_id]

    def create_session(
        self,
        session_key: str | None = None,
        *,
        agent_id: str | None = None,
    ) -> "Session":
        """Create (or get if already exists) a session by session_key.

        Saves the session immediately so it can be re-loaded later.
        """
        import uuid as _uuid
        if session_key:
            # Use session_key as session_id for simple sessions so they can
            # be retrieved by key via get_session(session_key).
            sid = session_key
        else:
            sid = f"{agent_id or 'session'}-{_uuid.uuid4().hex[:8]}"

        if sid in self._sessions:
            return self._sessions[sid]

        # Create a fresh session — always empty (this is "create", not "load or create").
        session = Session(
            session_id=sid,
            workspace_dir=self.workspace_dir,
            session_key=session_key or sid,
            agent_id=agent_id,
            sessions_dir_override=self._sessions_dir,
        )
        # Discard any messages that may have been loaded from a stale disk file
        session.messages = []

        self._sessions[sid] = session
        session._save()
        return session

    def save_session(self, session: "Session") -> None:
        """Persist a session to disk (full rewrite — ensures all messages are written)."""
        session._save_all()

    def get_or_create(self, session_id: str | None = None, session_key: str | None = None) -> "Session":
        """
        Backward-compatible get-or-create entrypoint.

        Behavior:
        - If `session_key` is provided (or `session_id` looks like an agent key),
          resolve via session store and return UUID-backed session.
        - Otherwise create/load a local workspace-scoped simple session.
        """
        candidate_key = (session_key or "").strip()
        if not candidate_key and isinstance(session_id, str) and session_id.strip().startswith("agent:"):
            candidate_key = session_id.strip()

        if candidate_key:
            return self.get_or_create_session(session_key=candidate_key)

        if not session_id:
            raise ValueError("session_id cannot be empty")

        sid = session_id.strip()
        if not sid:
            raise ValueError("session_id cannot be empty")

        if sid in self._sessions:
            return self._sessions[sid]

        # Simple (non-agent-key) sessions — store in workspace_dir/.sessions/
        # so they are portable and workspace-scoped (mirrors TS local storage).
        session = Session(
            session_id=sid,
            workspace_dir=self.workspace_dir,
            session_key=sid,
            # No sessions_dir_override → uses workspace_dir/.sessions
        )
        self._sessions[sid] = session
        # Persist immediately so list_sessions() can discover it.
        session._save()
        return session

    def get_or_create_session_by_key(self, session_key: str) -> Session:
        """
        Get or create session using session key (simpler wrapper).
        
        This method queries the session store for the UUID associated with the key,
        creates a new UUID if the key doesn't exist, and returns the Session object.
        
        Args:
            session_key: Session key (e.g., "agent:main:telegram:dm:8366053063")
            
        Returns:
            Session instance using UUID
        """
        return self.get_or_create_session(session_key=session_key)

    def get_session(self, session_id: str) -> "Session":
        """
        Get or create a session by session_id (or session_key).

        Auto-creates if not found (after scanning disk).  Loads from disk if a
        saved file exists with that id.

        Args:
            session_id: Session UUID, simple id, or session key.

        Returns:
            Session instance (always non-None).
        """
        # 1. Check in-memory cache by session_id
        if session_id in self._sessions:
            return self._sessions[session_id]

        # 2. Check in-memory cache by session_key
        for s in self._sessions.values():
            if s.session_key == session_id:
                return s

        # 3. Try loading from disk by session_id stem (loads prior messages)
        # Check .jsonl first (new format), then .json (legacy)
        candidate_file_jsonl = self._sessions_dir / f"{session_id}.jsonl"
        candidate_file_json = self._sessions_dir / f"{session_id}.json"
        if candidate_file_jsonl.exists() or candidate_file_json.exists():
            session = Session(
                session_id,
                self.workspace_dir,
                sessions_dir_override=self._sessions_dir,
            )
            self._sessions[session_id] = session
            return session

        # 4. Scan disk files and match by session_key field (header line of JSONL or JSON)
        if self._sessions_dir.exists():
            for f in list(self._sessions_dir.glob("*.jsonl")) + list(self._sessions_dir.glob("*.json")):
                if f.name in ("session_map.json", "sessions.json"):
                    continue
                try:
                    import json as _json
                    first_line = f.read_text().splitlines()[0] if f.suffix == ".jsonl" else f.read_text()
                    data = _json.loads(first_line)
                    # JSONL header uses "sessionKey"; legacy JSON uses "session_key"
                    key_val = data.get("sessionKey") or data.get("session_key")
                    if key_val == session_id:
                        loaded_id = data.get("id") or data.get("session_id") or f.stem
                        session = Session(
                            loaded_id,
                            self.workspace_dir,
                            sessions_dir_override=self._sessions_dir,
                        )
                        self._sessions[loaded_id] = session
                        return session
                except Exception:
                    continue

        # 5. Auto-create: session doesn't exist on disk → create new
        session = Session(
            session_id,
            self.workspace_dir,
            sessions_dir_override=self._sessions_dir,
        )
        self._sessions[session_id] = session
        return session

    def list_sessions(self) -> list[str]:
        """
        List all session keys/IDs created via get_or_create().

        Combines on-disk files in workspace_dir/.sessions with any in-memory
        sessions whose session_id is their own key (i.e. created by get_or_create).

        Returns:
            Sorted list of session keys.
        """
        disk_sessions: set[str] = set()
        # Scan canonical sessions dir (agent-key sessions) — both .jsonl and .json
        if self._sessions_dir.exists():
            for f in list(self._sessions_dir.glob("*.jsonl")) + list(self._sessions_dir.glob("*.json")):
                if f.name not in ("session_map.json", "sessions.json"):
                    disk_sessions.add(f.stem)

        # Also scan workspace_dir/.sessions (simple/workspace-scoped sessions)
        legacy_dir = self.workspace_dir / ".sessions"
        if legacy_dir.exists() and legacy_dir != self._sessions_dir:
            for f in list(legacy_dir.glob("*.jsonl")) + list(legacy_dir.glob("*.json")):
                if f.name not in ("session_map.json", "sessions.json"):
                    disk_sessions.add(f.stem)

        # Include in-memory simple sessions (where session_id == their key)
        for sid, session in self._sessions.items():
            if session.session_id == sid:
                disk_sessions.add(sid)

        return sorted(disk_sessions)
    
    def get_session_key_for_id(self, session_id: str) -> str | None:
        """Get session key for given session ID."""
        store = self._get_session_store()
        for key, entry in store.items():
            if entry.sessionId == session_id:
                return key
        return None
    
    def list_sessions_by_channel(self, channel: str) -> dict[str, str]:
        """List all sessions for a specific channel."""
        sessions = {}
        store = self._get_session_store()
        for key, entry in store.items():
            parsed = parse_agent_session_key(key)
            if parsed and channel in parsed.rest:
                sessions[key] = entry.sessionId
        return sessions

    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session

        Args:
            session_id: Session to delete

        Returns:
            True if deleted, False if not found
        """
        # Remove from memory
        if session_id in self._sessions:
            del self._sessions[session_id]

        # Remove from session map
        store = self._get_session_store()
        keys_to_remove = [k for k, entry in store.items() if entry.sessionId == session_id]
        for key in keys_to_remove:
            store.delete(key)
        
        if keys_to_remove:
            self._session_store = store
            self._save_session_store()
            logger.info(f"Removed {len(keys_to_remove)} session key(s) for {session_id}")

        # Remove from disk (new canonical path + legacy workspace path)
        # Check both .jsonl (new) and .json (legacy)
        deleted = False
        for ext in (".jsonl", ".json"):
            session_file = self._sessions_dir / f"{session_id}{ext}"
            if session_file.exists():
                session_file.unlink()
                deleted = True
            legacy_file = self.workspace_dir / ".sessions" / f"{session_id}{ext}"
            if legacy_file.exists():
                legacy_file.unlink()
                deleted = True

        return deleted

    def get_all_sessions(self) -> list[Session]:
        """
        Get all sessions

        Returns:
            List of Session instances
        """
        session_ids = self.list_sessions()
        return [self.get_session(sid) for sid in session_ids]

    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """
        Delete sessions older than max_age_days

        Args:
            max_age_days: Maximum age in days

        Returns:
            Number of sessions deleted
        """
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        deleted = 0

        for session in self.get_all_sessions():
            try:
                updated = datetime.fromisoformat(session.updated_at.replace("Z", "+00:00"))
                if updated < cutoff:
                    if self.delete_session(session.session_id):
                        deleted += 1
            except Exception:
                pass

        return deleted

    # ------------------------------------------------------------------
    # SessionEntry access methods (mirrors TS sessionStore.get/patch)
    # ------------------------------------------------------------------

    def get_session_entry(self, session_key: str) -> "SessionEntry | None":
        """Return the ``SessionEntry`` for a session key (or None if absent).

        Mirrors TS ``sessionStore.get(sessionKey)``.
        """
        from openclaw.agents.session_entry import SessionEntry as _SE  # noqa: F401
        store = self._get_session_store()
        return store.get(session_key)

    def get_entry_by_session_key(self, session_key: str) -> "SessionEntry | None":
        """Alias for ``get_session_entry`` — used by channel_manager routing."""
        return self.get_session_entry(session_key)

    def get_entry_by_id(self, session_id: str) -> "SessionEntry | None":
        """Find a ``SessionEntry`` by UUID ``session_id``.

        Mirrors TS ``findSessionEntryById(store, sessionId)``.
        """
        store = self._get_session_store()
        for entry in store.values():
            if entry.sessionId == session_id:
                return entry
        return None

    async def save_entry(self, entry: "SessionEntry") -> None:  # type: ignore[override]
        """Persist an updated ``SessionEntry`` back to the session store.

        Locates the entry by ``sessionId``, replaces it, and saves to disk.
        Mirrors TS ``updateSessionStore(storePath, sessionKey, patch)``.
        """
        store = self._get_session_store()
        # Find the key(s) that match this entry's sessionId
        matching_keys = [k for k, e in store.items() if e.sessionId == entry.sessionId]
        if not matching_keys:
            logger.warning("save_entry: no session key found for sessionId=%s", entry.sessionId)
            return
        for key in matching_keys:
            store.set(key, entry)
        self._session_store = store
        self._save_session_store(store)

    def update_session_meta_preserve_activity(
        self, session_key: str, patch: "dict[str, Any]"
    ) -> None:
        """Merge metadata fields into a ``SessionEntry`` WITHOUT touching ``updatedAt``.

        Mirrors TS ``mergeSessionEntryPreserveActivity`` — inbound metadata
        (lastChannel, lastTo, etc.) must not refresh ``updatedAt`` because
        idle-reset logic depends on that timestamp tracking actual agent turns.

        If no entry exists yet for the key, one is created (upsert).
        """
        import time as _time
        from openclaw.agents.session_entry import merge_session_entry

        store = self._get_session_store()
        existing = store.get(session_key)
        if existing is None:
            # Auto-create a bare entry for the key so metadata has somewhere to live
            from openclaw.agents.session_entry import SessionEntry as _SE
            import uuid as _uuid
            existing = _SE(sessionId=str(_uuid.uuid4()))

        # Merge the patch, preserving the original updatedAt
        original_updated_at = existing.updatedAt
        updated = merge_session_entry(existing, patch)
        updated.updatedAt = original_updated_at  # restore — mirrors preserveActivity

        store.set(session_key, updated)
        self._session_store = store
        self._save_session_store(store)
