"""Plugin runtime events module — mirrors src/plugins/runtime/runtime-events.ts"""
from __future__ import annotations


def create_runtime_events():
    """
    Create runtime.events module
    
    Provides access to:
    - on_agent_event: Listen to agent runtime events
    - on_session_transcript_update: Listen to session transcript updates
    """
    from openclaw.infra.agent_events import on_agent_event
    from openclaw.sessions.transcript_events import on_session_transcript_update
    
    return {
        "on_agent_event": on_agent_event,
        "on_session_transcript_update": on_session_transcript_update,
    }
