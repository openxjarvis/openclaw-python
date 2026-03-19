"""Session transcript update events — mirrors src/sessions/transcript-events.ts"""
from __future__ import annotations

from typing import Callable, TypedDict


class SessionTranscriptUpdate(TypedDict):
    """Session transcript update payload"""
    session_file: str


SessionTranscriptListener = Callable[[SessionTranscriptUpdate], None]

_listeners: set[SessionTranscriptListener] = set()


def on_session_transcript_update(listener: SessionTranscriptListener) -> Callable[[], None]:
    """
    Register a listener for session transcript updates.
    
    Returns:
        Unsubscribe function
    """
    _listeners.add(listener)
    
    def unsubscribe():
        _listeners.discard(listener)
    
    return unsubscribe


def emit_session_transcript_update(session_file: str) -> None:
    """
    Emit a session transcript update event.
    
    Args:
        session_file: Path to the updated session file
    """
    trimmed = session_file.strip()
    if not trimmed:
        return
    
    update: SessionTranscriptUpdate = {"session_file": trimmed}
    
    for listener in list(_listeners):
        try:
            listener(update)
        except Exception:
            # Ignore listener errors
            pass
