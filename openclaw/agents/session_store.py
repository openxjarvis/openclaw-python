"""
Session Store for managing session metadata and transcripts

Implements the pi-mono architecture:
- sessions.json: Metadata and session_key → session_id mappings
- {sessionId}.jsonl: JSONL transcript format (one message per line)

With openclaw-ts cache optimization:
- TTL-based caching (45 seconds default)
- mtime checking to detect external changes
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openclaw.agents.session_ids import generate_session_id, looks_like_session_id

logger = logging.getLogger(__name__)


@dataclass
class SessionStoreCacheEntry:
    """
    Session store cache entry - aligned with openclaw-ts
    
    Caches sessions.json content with TTL and mtime validation
    """
    data: dict[str, Any]  # Cached sessions.json content
    mtime: float  # File modification time
    cached_at: float  # Cache timestamp (milliseconds)


class SessionResetConfig(BaseModel):
    """Configuration for session reset triggers"""

    # Daily reset at specific hour (0-23, None to disable)
    daily_reset_hour: int | None = 4  # Default: 4:00 AM

    # Idle timeout in minutes (None to disable)
    idle_minutes: int | None = None

    # Maximum token count before reset (None to disable)
    max_tokens: int | None = None

    # Maximum message count before reset (None to disable)
    max_messages: int | None = None


class SessionEntry(BaseModel):
    """Session metadata entry — mirrors TS SessionEntry (40+ fields).

    All new fields have default values for full backward compatibility.
    """

    # ── Core identity ──────────────────────────────────────────────────────
    session_id: str
    session_key: str | None = None
    model: str | None = None
    thinking_level: str | None = None
    token_count: dict[str, int] = Field(default_factory=lambda: {"input": 0, "output": 0})
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_active_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_reset_at: str | None = None
    message_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ── Agent Harness pin (mirrors TS agentHarnessId) ─────────────────────
    # Once a session starts running, the chosen harness id is recorded here.
    # Subsequent runs use the same harness (avoids mid-session switching).
    agent_harness_id: str | None = None

    # ── Sub-agent relationship ─────────────────────────────────────────────
    spawned_by: str | None = None           # parent session_key that spawned this
    parent_session_key: str | None = None   # alias for spawned_by (TS compat)
    spawn_depth: int = 0                    # nesting depth (0 = top-level)
    subagent_role: str | None = None        # role label (e.g. "coder", "searcher")
    subagent_control_scope: str | None = None  # ACP scope string

    # ── Runtime state ──────────────────────────────────────────────────────
    model_provider: str | None = None       # resolved provider id
    live_model_switch_pending: bool = False # model switch queued for next run
    compaction_count: int = 0              # number of times session was compacted
    total_tokens_fresh: bool = False       # token count is authoritative
    aborted_last_run: bool = False         # last run was aborted by user
    abort_cutoff_at: int | None = None     # epoch-ms when abort was applied

    # ── Channel / delivery ─────────────────────────────────────────────────
    last_channel: str | None = None        # last channel id that sent a message
    queue_mode: str | None = None          # "fifo" | "replace" | None
    queue_cap: int | None = None           # max queued messages
    delivery_context: dict | None = None   # channel-specific delivery metadata

    # ── TTS session state ──────────────────────────────────────────────────
    tts_auto: bool = False                 # auto-read replies via TTS
    last_tts_read_latest_hash: str | None = None  # hash of last TTS-read message

    # ── ACP (Agent Control Plane) ──────────────────────────────────────────
    acp: dict | None = None                # ACP state blob

    # ── Skills / prompt engineering ────────────────────────────────────────
    skills_snapshot: dict | None = None        # resolved skills at session creation
    system_prompt_report: dict | None = None   # system prompt composition report

    # ── Plugin debug ───────────────────────────────────────────────────────
    plugin_debug_entries: list | None = None   # per-run plugin debug log entries


class TranscriptMessage(BaseModel):
    """Single message in transcript (JSONL format)"""

    role: str
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    images: list[str] | None = None


class SessionStore:
    """
    Manages session metadata and transcripts with cache optimization
    
    Architecture:
    - sessions.json: Contains all session metadata and session_key mappings
    - transcripts/{sessionId}.jsonl: JSONL format (one message per line)
    
    Cache features (aligned with openclaw-ts):
    - 45 second TTL for sessions.json
    - mtime checking to detect external changes
    - Automatic cache invalidation on write
    """
    
    DEFAULT_TTL_MS = 45_000  # 45 seconds - aligned with openclaw-ts

    def __init__(
        self, 
        workspace_dir: Path, 
        reset_config: SessionResetConfig | None = None,
        cache_ttl_ms: int = DEFAULT_TTL_MS
    ):
        self.workspace_dir = workspace_dir
        self._sessions_dir = workspace_dir / ".sessions"
        self._transcripts_dir = self._sessions_dir / "transcripts"
        self._sessions_file = self._sessions_dir / "sessions.json"
        self.reset_config = reset_config or SessionResetConfig()
        self.cache_ttl_ms = cache_ttl_ms

        # Create directories
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._transcripts_dir.mkdir(parents=True, exist_ok=True)

        # Cache infrastructure - aligned with openclaw-ts
        self._cache: dict[str, SessionStoreCacheEntry] = {}
        self._lock = asyncio.Lock()

        # In-memory session map — loaded lazily from disk on first access
        self._sessions: dict[str, SessionEntry] = {}

    @property
    def sessions_dir(self) -> Path:
        """Public accessor for sessions directory"""
        return self._sessions_dir

    def _load_sessions(self) -> dict[str, SessionEntry]:
        """
        Load session metadata from sessions.json (without cache)
        
        Used during initialization only. For cached access, use _load_sessions_cached()
        """
        if not self._sessions_file.exists():
            return {}

        try:
            with open(self._sessions_file) as f:
                data = json.load(f)

            sessions = {}
            for session_id, entry_data in data.get("sessions", {}).items():
                sessions[session_id] = SessionEntry(**entry_data)

            logger.info(f"Loaded {len(sessions)} sessions from store")
            return sessions

        except Exception as e:
            logger.error(f"Failed to load sessions.json: {e}")
            return {}
    
    async def _load_sessions_cached(self) -> dict[str, SessionEntry]:
        """
        Load sessions.json with caching - aligned with openclaw-ts loadSessionStore()
        
        Uses TTL and mtime checking to minimize disk I/O
        
        Returns:
            Dictionary of session_id -> SessionEntry
        """
        cache_key = str(self._sessions_file)
        
        async with self._lock:
            # Check cache
            cached = self._cache.get(cache_key)
            if cached:
                # Check file modification time
                try:
                    current_mtime = self._sessions_file.stat().st_mtime if self._sessions_file.exists() else 0
                    
                    if current_mtime == cached.mtime:
                        # File hasn't changed, check TTL
                        now = time.time() * 1000
                        if (now - cached.cached_at) < self.cache_ttl_ms:
                            logger.debug(f"Session store cache hit: {cache_key}")
                            # Return deep copy to prevent external modification of cache
                            return copy.deepcopy(self._sessions)
                except (FileNotFoundError, OSError):
                    pass
            
            # Cache miss or expired - reload from disk
            self._sessions = self._load_sessions()
            
            # Update cache
            self._cache[cache_key] = SessionStoreCacheEntry(
                data=copy.deepcopy({"sessions": {sid: entry.model_dump() for sid, entry in self._sessions.items()}}),
                mtime=self._sessions_file.stat().st_mtime if self._sessions_file.exists() else 0,
                cached_at=time.time() * 1000
            )
            
            return self._sessions

    def _save_sessions(self) -> None:
        """
        Save session metadata to sessions.json and invalidate cache
        
        Aligned with openclaw-ts saveSessionStore()
        """
        try:
            data = {
                "sessions": {
                    session_id: entry.model_dump()
                    for session_id, entry in self._sessions.items()
                }
            }

            with open(self._sessions_file, "w") as f:
                json.dump(data, f, indent=2)
            
            # Invalidate cache - aligned with openclaw-ts
            cache_key = str(self._sessions_file)
            if cache_key in self._cache:
                del self._cache[cache_key]
                logger.debug(f"Invalidated session store cache: {cache_key}")

        except Exception as e:
            logger.error(f"Failed to save sessions.json: {e}")

    async def get_or_create_session_async(
        self,
        session_key: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> tuple[SessionEntry, bool]:
        """
        Get or create session entry (async, uses cache)
        
        Returns: (SessionEntry, is_new)
        """
        # Load from cache
        await self._load_sessions_cached()
        
        # Rest of logic remains the same
        return self._get_or_create_session_internal(session_key, session_id, model, thinking_level)
    
    def get_or_create_session(
        self,
        session_key: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> tuple[SessionEntry, bool]:
        """
        Get or create session entry (sync wrapper for backward compatibility)

        Returns: (SessionEntry, is_new)
        """
        return self._get_or_create_session_internal(session_key, session_id, model, thinking_level)
    
    def _get_or_create_session_internal(
        self,
        session_key: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> tuple[SessionEntry, bool]:
        """Internal implementation of get_or_create_session"""
        # If session_id provided, try to get it directly
        if session_id:
            if session_id in self._sessions:
                return self._sessions[session_id], False
            # Create new session with this ID
            entry = SessionEntry(
                session_id=session_id,
                session_key=session_key,
                model=model,
                thinking_level=thinking_level,
            )
            self._sessions[session_id] = entry
            self._save_sessions()
            return entry, True

        # If session_key provided, lookup by key
        if session_key:
            for entry in self._sessions.values():
                if entry.session_key == session_key:
                    return entry, False

            # Create new session for this key
            new_session_id = generate_session_id()
            entry = SessionEntry(
                session_id=new_session_id,
                session_key=session_key,
                model=model,
                thinking_level=thinking_level,
            )
            self._sessions[new_session_id] = entry
            self._save_sessions()
            return entry, True

        # No session_id or session_key - create new
        new_session_id = generate_session_id()
        entry = SessionEntry(
            session_id=new_session_id,
            model=model,
            thinking_level=thinking_level,
        )
        self._sessions[new_session_id] = entry
        self._save_sessions()
        return entry, True

    def get_session(self, session_id: str) -> SessionEntry | None:
        """Get session by ID"""
        return self._sessions.get(session_id)

    def update_session(
        self,
        session_id: str,
        **kwargs,
    ) -> None:
        """Update session metadata"""
        if session_id not in self._sessions:
            logger.warning(f"Cannot update non-existent session: {session_id}")
            return

        entry = self._sessions[session_id]

        # Update fields
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)

        # Always update last_active_at
        entry.last_active_at = datetime.now(UTC).isoformat()

        self._save_sessions()

    def delete_session(self, session_id: str) -> bool:
        """Delete session and its transcript"""
        if session_id not in self._sessions:
            return False

        # Delete from memory
        del self._sessions[session_id]

        # Delete transcript file
        transcript_file = self._transcripts_dir / f"{session_id}.jsonl"
        if transcript_file.exists():
            transcript_file.unlink()

        # Save sessions.json
        self._save_sessions()

        logger.info(f"Deleted session {session_id}")
        return True

    def list_sessions(
        self,
        session_key_prefix: str | None = None,
    ) -> list[SessionEntry]:
        """List all sessions, optionally filtered by session_key prefix"""
        sessions = list(self._sessions.values())

        if session_key_prefix:
            sessions = [s for s in sessions if s.session_key and s.session_key.startswith(session_key_prefix)]

        # Sort by last_active_at (most recent first)
        sessions.sort(key=lambda s: s.last_active_at, reverse=True)

        return sessions

    # Transcript operations

    def _get_transcript_file(self, session_id: str) -> Path:
        """Get transcript file path"""
        return self._transcripts_dir / f"{session_id}.jsonl"

    def load_transcript(self, session_id: str) -> list[TranscriptMessage]:
        """Load transcript from JSONL file"""
        transcript_file = self._get_transcript_file(session_id)

        if not transcript_file.exists():
            return []

        try:
            messages = []
            with open(transcript_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    messages.append(TranscriptMessage(**data))

            logger.info(f"Loaded {len(messages)} messages from transcript {session_id}")
            return messages

        except Exception as e:
            logger.error(f"Failed to load transcript {session_id}: {e}")
            return []

    def append_message(
        self,
        session_id: str,
        message: TranscriptMessage,
    ) -> None:
        """Append message to transcript (JSONL format)"""
        transcript_file = self._get_transcript_file(session_id)

        try:
            # Append as single line of JSON
            with open(transcript_file, "a") as f:
                f.write(json.dumps(message.model_dump(), default=str) + "\n")

            # Update session metadata
            if session_id in self._sessions:
                entry = self._sessions[session_id]
                entry.message_count += 1
                entry.last_active_at = datetime.now(UTC).isoformat()
                self._save_sessions()

        except Exception as e:
            logger.error(f"Failed to append message to transcript {session_id}: {e}")

    def save_transcript(
        self,
        session_id: str,
        messages: list[TranscriptMessage],
    ) -> None:
        """Save complete transcript (overwrites existing)"""
        transcript_file = self._get_transcript_file(session_id)

        try:
            with open(transcript_file, "w") as f:
                for message in messages:
                    f.write(json.dumps(message.model_dump(), default=str) + "\n")

            # Update session metadata
            if session_id in self._sessions:
                entry = self._sessions[session_id]
                entry.message_count = len(messages)
                entry.last_active_at = datetime.now(UTC).isoformat()
                self._save_sessions()

            logger.info(f"Saved {len(messages)} messages to transcript {session_id}")

        except Exception as e:
            logger.error(f"Failed to save transcript {session_id}: {e}")

    def clear_transcript(self, session_id: str) -> None:
        """Clear transcript (delete all messages)"""
        transcript_file = self._get_transcript_file(session_id)

        if transcript_file.exists():
            transcript_file.unlink()

        # Update session metadata
        if session_id in self._sessions:
            entry = self._sessions[session_id]
            entry.message_count = 0
            entry.last_active_at = datetime.now(UTC).isoformat()
            self._save_sessions()

        logger.info(f"Cleared transcript {session_id}")

    # Session reset triggers

    def should_reset(self, session_id: str) -> tuple[bool, str | None]:
        """
        Check if session should be reset based on configured triggers

        Returns: (should_reset, reason)
        """
        entry = self.get_session(session_id)
        if not entry:
            return False, None

        now = datetime.now(UTC)

        # Check daily reset
        if self.reset_config.daily_reset_hour is not None:
            last_reset = datetime.fromisoformat(entry.last_reset_at) if entry.last_reset_at else datetime.fromisoformat(entry.created_at)
            
            # Check if we've passed the reset hour since last reset
            reset_time_today = now.replace(hour=self.reset_config.daily_reset_hour, minute=0, second=0, microsecond=0)
            
            if last_reset < reset_time_today <= now:
                return True, "daily_reset"

        # Check idle timeout
        if self.reset_config.idle_minutes is not None:
            last_active = datetime.fromisoformat(entry.last_active_at)
            idle_duration = now - last_active
            
            if idle_duration.total_seconds() / 60 > self.reset_config.idle_minutes:
                return True, "idle_timeout"

        # Check token limit
        if self.reset_config.max_tokens is not None:
            total_tokens = entry.token_count.get("input", 0) + entry.token_count.get("output", 0)
            
            if total_tokens >= self.reset_config.max_tokens:
                return True, "token_limit"

        # Check message count limit
        if self.reset_config.max_messages is not None:
            if entry.message_count >= self.reset_config.max_messages:
                return True, "message_limit"

        return False, None

    def reset_session(self, session_id: str, reason: str | None = None) -> bool:
        """
        Reset session (clear transcript and reset counters)

        Returns: True if reset successful
        """
        if session_id not in self._sessions:
            return False

        # Clear transcript
        self.clear_transcript(session_id)

        # Reset counters
        entry = self._sessions[session_id]
        entry.token_count = {"input": 0, "output": 0}
        entry.message_count = 0
        entry.last_reset_at = datetime.now(UTC).isoformat()
        entry.last_active_at = datetime.now(UTC).isoformat()

        self._save_sessions()

        logger.info(f"Reset session {session_id}" + (f" (reason: {reason})" if reason else ""))
        return True

    def check_and_reset_if_needed(self, session_id: str) -> tuple[bool, str | None]:
        """
        Check if session needs reset and perform it if needed

        Returns: (was_reset, reason)
        """
        should_reset, reason = self.should_reset(session_id)
        
        if should_reset:
            self.reset_session(session_id, reason)
            return True, reason
        
        return False, None


# ---------------------------------------------------------------------------
# Session freshness evaluation
# (matches TypeScript openclaw/src/config/sessions/reset.ts)
# ---------------------------------------------------------------------------

DEFAULT_RESET_MODE = "daily"
DEFAULT_RESET_AT_HOUR = 4
DEFAULT_IDLE_MINUTES = 60


@dataclass
class SessionResetPolicy:
    """Reset policy matching TypeScript SessionResetPolicy."""
    mode: str = DEFAULT_RESET_MODE  # "daily" | "idle"
    at_hour: int = DEFAULT_RESET_AT_HOUR
    idle_minutes: int | None = None


@dataclass
class SessionFreshness:
    """Freshness result matching TypeScript SessionFreshness."""
    fresh: bool
    daily_reset_at: int | None = None
    idle_expires_at: int | None = None


def resolve_daily_reset_at_ms(now_ms: int, at_hour: int) -> int:
    """Resolve the daily reset threshold timestamp (ms)."""
    from datetime import datetime, timezone

    now_dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    reset_dt = now_dt.replace(hour=at_hour, minute=0, second=0, microsecond=0)
    reset_ms = int(reset_dt.timestamp() * 1000)

    # If now is before the reset hour today, use yesterday's reset
    if now_ms < reset_ms:
        reset_ms -= 86_400_000  # subtract 24h
    return reset_ms


def evaluate_session_freshness(
    updated_at_ms: int,
    now_ms: int,
    policy: SessionResetPolicy,
) -> SessionFreshness:
    """
    Evaluate whether a session is still fresh.

    Matches TypeScript evaluateSessionFreshness():
    - daily mode: stale if updatedAt < daily reset boundary
    - idle mode:  stale if idle time exceeds idleMinutes

    Args:
        updated_at_ms: Last update time in ms
        now_ms: Current time in ms
        policy: Reset policy

    Returns:
        SessionFreshness with fresh flag and boundary timestamps
    """
    daily_reset_at: int | None = None
    idle_expires_at: int | None = None

    if policy.mode == "daily":
        daily_reset_at = resolve_daily_reset_at_ms(now_ms, policy.at_hour)

    if policy.idle_minutes is not None:
        idle_expires_at = updated_at_ms + policy.idle_minutes * 60_000

    stale_daily = daily_reset_at is not None and updated_at_ms < daily_reset_at
    stale_idle = idle_expires_at is not None and now_ms > idle_expires_at

    return SessionFreshness(
        fresh=not (stale_daily or stale_idle),
        daily_reset_at=daily_reset_at,
        idle_expires_at=idle_expires_at,
    )
