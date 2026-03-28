"""Session transcripts management.

Fully aligned with TypeScript openclaw/src/config/sessions/paths.ts
transcript-related functions (lines 234-296).

Transcripts are stored at:
- ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
- With topic support: <sessionId>-topic-<topicId>.jsonl
"""

from pathlib import Path
from typing import Optional
import re
import logging

from openclaw.routing.session_key import DEFAULT_AGENT_ID, normalize_agent_id
from .paths import resolve_agent_sessions_dir

logger = logging.getLogger(__name__)

# Mirrors TS SAFE_SESSION_ID_RE from paths.ts line 60
SAFE_SESSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$", re.IGNORECASE)


def validate_session_id(session_id: str) -> str:
    """
    Validate and return trimmed session ID.
    
    Mirrors TS validateSessionId() from paths.ts lines 62-67
    
    Args:
        session_id: Session ID to validate
        
    Returns:
        Validated session ID
        
    Raises:
        ValueError: If session ID is invalid
    """
    trimmed = session_id.strip()
    if not SAFE_SESSION_ID_RE.match(trimmed):
        raise ValueError(f"Invalid session ID: {session_id}")
    return trimmed


def resolve_session_transcript_path_in_dir(
    session_id: str,
    sessions_dir: str | Path,
    topic_id: str | int | None = None,
) -> Path:
    """
    Resolve transcript path within a sessions directory.
    
    Mirrors TS resolveSessionTranscriptPathInDir() from paths.ts lines 234-245
    
    Args:
        session_id: Session ID
        sessions_dir: Sessions directory path
        topic_id: Optional topic ID for sub-session
        
    Returns:
        Path to transcript file
    """
    safe_session_id = validate_session_id(session_id)
    
    # Handle topic ID
    safe_topic_id: str | None = None
    if topic_id is not None:
        topic_str = str(topic_id).strip()
        # Basic sanitization (alphanumeric, dash, underscore)
        safe_topic_id = re.sub(r"[^a-z0-9_-]", "-", topic_str.lower())
    
    # Construct filename
    if safe_topic_id:
        filename = f"{safe_session_id}-topic-{safe_topic_id}.jsonl"
    else:
        filename = f"{safe_session_id}.jsonl"
    
    sessions_path = Path(sessions_dir).resolve()
    return sessions_path / filename


def resolve_session_transcript_path(
    session_id: str,
    agent_id: str | None = None,
    topic_id: str | int | None = None,
) -> Path:
    """
    Resolve standard transcript path for a session.
    
    Mirrors TS resolveSessionTranscriptPath() from paths.ts lines 247-254
    
    Returns: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
    
    Args:
        session_id: Session ID
        agent_id: Agent ID (defaults to "main")
        topic_id: Optional topic ID for sub-session
        
    Returns:
        Path to transcript file
    """
    sessions_dir = resolve_agent_sessions_dir(agent_id)
    return resolve_session_transcript_path_in_dir(session_id, sessions_dir, topic_id)


def get_session_transcript_path(session_key: str) -> Path:
    """
    Get transcript path for a session (legacy compatibility).
    
    Now redirects to standard path: ~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl
    
    Args:
        session_key: Session key (e.g., "agent:main:main")
        
    Returns:
        Path to transcript file
    """
    # Try to parse session key to extract agent ID and session ID
    # For legacy compatibility, fall back to using the full key as session ID
    from openclaw.routing.session_key import parse_agent_session_key
    
    parsed = parse_agent_session_key(session_key)
    if parsed:
        agent_id = parsed.agent_id
        # Use rest as session ID (may not be a pure UUID, but that's OK)
        session_id = parsed.rest.replace(":", "-")
    else:
        # Fallback: sanitize the full key
        agent_id = None
        session_id = session_key.replace(":", "-").replace("/", "-")
    
    try:
        return resolve_session_transcript_path(session_id, agent_id)
    except ValueError:
        # If validation fails, fall back to safe sanitized name
        safe_key = re.sub(r"[^a-z0-9_-]", "-", session_key.lower())
        sessions_dir = resolve_agent_sessions_dir(agent_id)
        return sessions_dir / f"{safe_key}.jsonl"


def save_session_transcript(session_key: str, content: str) -> None:
    """
    Save session transcript.
    
    Args:
        session_key: Session key
        content: Transcript content (typically JSONL format)
    """
    path = get_session_transcript_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_session_transcript(session_key: str) -> Optional[str]:
    """
    Load session transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        Transcript content or None if not found
    """
    path = get_session_transcript_path(session_key)
    
    if path.exists():
        return path.read_text(encoding="utf-8")
    
    # Fallback: check legacy location
    legacy_path = _get_legacy_transcript_path(session_key)
    if legacy_path and legacy_path.exists():
        logger.info(f"Found transcript at legacy location: {legacy_path}")
        return legacy_path.read_text(encoding="utf-8")
    
    return None


def _get_legacy_transcript_path(session_key: str) -> Path | None:
    """
    Get legacy transcript path for backward compatibility.
    
    Legacy location: ~/.openclaw/transcripts/<safe_key>.txt
    
    Args:
        session_key: Session key
        
    Returns:
        Legacy path or None if invalid
    """
    try:
        safe_key = session_key.replace(":", "_").replace("/", "_")
        home = Path.home()
        return home / ".openclaw" / "transcripts" / f"{safe_key}.txt"
    except Exception:
        return None


def read_session_preview_items(session_key: str, limit: int = 10) -> list:
    """
    Read preview items from session transcript (JSONL format).
    
    Args:
        session_key: Session key
        limit: Maximum number of items to return
        
    Returns:
        List of preview items (last N lines)
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return []
    
    # JSONL: each line is a JSON object
    lines = [l.strip() for l in transcript.strip().split("\n") if l.strip()]
    preview_lines = lines[-limit:] if len(lines) > limit else lines
    
    # Parse JSON lines for preview
    items = []
    import json
    for line in preview_lines:
        try:
            obj = json.loads(line)
            items.append({
                "type": obj.get("type", "unknown"),
                "content": str(obj)[:100],  # Truncate for preview
            })
        except json.JSONDecodeError:
            items.append({
                "type": "text",
                "content": line[:100],
            })
    
    return items


def compact_transcript(transcript: str, max_length: int = 10000) -> str:
    """
    Compact a transcript by removing older content if too long.
    
    For JSONL transcripts, removes oldest lines.
    
    Args:
        transcript: Full transcript text
        max_length: Maximum length to keep
        
    Returns:
        Compacted transcript
    """
    if len(transcript) <= max_length:
        return transcript
    
    # For JSONL, keep complete lines from the end
    lines = transcript.strip().split("\n")
    kept_lines = []
    current_length = 0
    
    for line in reversed(lines):
        line_length = len(line) + 1  # +1 for newline
        if current_length + line_length > max_length:
            break
        kept_lines.insert(0, line)
        current_length += line_length
    
    return "\n".join(kept_lines)


def delete_transcript(session_key: str) -> bool:
    """
    Delete session transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        True if deleted, False if not found
    """
    path = get_session_transcript_path(session_key)
    deleted = False
    
    if path.exists():
        path.unlink()
        deleted = True
    
    # Also try to delete legacy location
    legacy_path = _get_legacy_transcript_path(session_key)
    if legacy_path and legacy_path.exists():
        legacy_path.unlink()
        deleted = True
    
    return deleted


def read_first_user_message(session_key: str) -> Optional[str]:
    """
    Read the first user message from transcript.
    
    For JSONL transcripts, parses JSON to find first user message.
    
    Args:
        session_key: Session key
        
    Returns:
        First user message content or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    import json
    for line in transcript.split("\n"):
        line = line.strip()
        if not line:
            continue
        
        try:
            obj = json.loads(line)
            if obj.get("role") == "user" or obj.get("type") == "user":
                return obj.get("content") or obj.get("text") or str(obj)
        except json.JSONDecodeError:
            # Fallback for non-JSON transcripts
            if line.startswith("user:"):
                return line.replace("user:", "").strip()
    
    return None


def get_transcript_stats(session_key: str) -> dict:
    """
    Get statistics about a transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        Dict with stats (size, line_count, etc.)
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return {"size": 0, "line_count": 0, "format": "unknown"}
    
    lines = [l for l in transcript.split("\n") if l.strip()]
    
    # Detect format
    format_type = "text"
    import json
    if lines:
        try:
            json.loads(lines[0])
            format_type = "jsonl"
        except json.JSONDecodeError:
            pass
    
    return {
        "size": len(transcript),
        "line_count": len(lines),
        "format": format_type,
    }


def read_last_message_preview(session_key: str, max_length: int = 100) -> Optional[str]:
    """
    Read a preview of the last message in transcript.
    
    Args:
        session_key: Session key
        max_length: Maximum preview length
        
    Returns:
        Preview of last message or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    if not lines:
        return None
    
    last_line = lines[-1]
    
    # Try to parse JSON and extract content
    import json
    try:
        obj = json.loads(last_line)
        content = obj.get("content") or obj.get("text") or str(obj)
        if len(content) > max_length:
            return content[:max_length] + "..."
        return content
    except json.JSONDecodeError:
        if len(last_line) > max_length:
            return last_line[:max_length] + "..."
        return last_line


def read_transcript_preview(session_key: str, max_lines: int = 5) -> Optional[str]:
    """
    Read a preview of the transcript.
    
    Args:
        session_key: Session key
        max_lines: Maximum number of lines to include
        
    Returns:
        Transcript preview or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    lines = [l for l in transcript.split("\n") if l.strip()]
    preview_lines = lines[-max_lines:] if len(lines) > max_lines else lines
    return "\n".join(preview_lines)


__all__ = [
    "validate_session_id",
    "resolve_session_transcript_path_in_dir",
    "resolve_session_transcript_path",
    "get_session_transcript_path",
    "save_session_transcript",
    "load_session_transcript",
    "read_session_preview_items",
    "compact_transcript",
    "delete_transcript",
    "read_first_user_message",
    "get_transcript_stats",
    "read_last_message_preview",
    "read_transcript_preview",
]
