"""Session transcripts management."""

from pathlib import Path
from typing import Optional


def get_session_transcript_path(session_key: str) -> Path:
    """Get transcript path for a session."""
    # Sanitize session key for file system
    safe_key = session_key.replace(":", "_").replace("/", "_")
    
    # Use .openclaw directory
    home = Path.home()
    transcripts_dir = home / ".openclaw" / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    
    return transcripts_dir / f"{safe_key}.txt"


def save_session_transcript(session_key: str, content: str) -> None:
    """Save session transcript."""
    path = get_session_transcript_path(session_key)
    path.write_text(content, encoding="utf-8")


def load_session_transcript(session_key: str) -> Optional[str]:
    """Load session transcript."""
    path = get_session_transcript_path(session_key)
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def read_session_preview_items(session_key: str, limit: int = 10) -> list:
    """Read preview items from session transcript.
    
    Args:
        session_key: Session key
        limit: Maximum number of items to return
        
    Returns:
        List of preview items (messages, events, etc.)
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return []
    
    # Simple implementation: return last N lines
    lines = transcript.strip().split("\n")
    preview_lines = lines[-limit:] if len(lines) > limit else lines
    
    # Convert to preview items (simplified)
    items = []
    for line in preview_lines:
        if line.strip():
            items.append({
                "type": "text",
                "content": line[:100],  # Truncate for preview
            })
    
    return items


def compact_transcript(transcript: str, max_length: int = 10000) -> str:
    """Compact a transcript by removing older content if too long.
    
    Args:
        transcript: Full transcript text
        max_length: Maximum length to keep
        
    Returns:
        Compacted transcript
    """
    if len(transcript) <= max_length:
        return transcript
    
    # Keep the most recent content
    return "...(earlier content truncated)...\n\n" + transcript[-max_length:]


def delete_transcript(session_key: str) -> bool:
    """Delete session transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        True if deleted, False if not found
    """
    path = get_session_transcript_path(session_key)
    if path.exists():
        path.unlink()
        return True
    return False


def read_first_user_message(session_key: str) -> Optional[str]:
    """Read the first user message from transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        First user message content or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    # Simple implementation: find first "user:" line
    for line in transcript.split("\n"):
        if line.strip().startswith("user:"):
            return line.replace("user:", "").strip()
    
    return None


def get_transcript_stats(session_key: str) -> dict:
    """Get statistics about a transcript.
    
    Args:
        session_key: Session key
        
    Returns:
        Dict with stats (size, line_count, etc.)
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return {"size": 0, "line_count": 0}
    
    return {
        "size": len(transcript),
        "line_count": len(transcript.split("\n")),
    }


def read_last_message_preview(session_key: str, max_length: int = 100) -> Optional[str]:
    """Read a preview of the last message in transcript.
    
    Args:
        session_key: Session key
        max_length: Maximum preview length
        
    Returns:
        Preview of last message or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    # Get last non-empty line
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    if not lines:
        return None
    
    last_line = lines[-1]
    if len(last_line) > max_length:
        return last_line[:max_length] + "..."
    return last_line


def read_transcript_preview(session_key: str, max_lines: int = 5) -> Optional[str]:
    """Read a preview of the transcript.
    
    Args:
        session_key: Session key
        max_lines: Maximum number of lines to include
        
    Returns:
        Transcript preview or None
    """
    transcript = load_session_transcript(session_key)
    if not transcript:
        return None
    
    lines = transcript.split("\n")
    preview_lines = lines[-max_lines:] if len(lines) > max_lines else lines
    return "\n".join(preview_lines)


__all__ = [
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
