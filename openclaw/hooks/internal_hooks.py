"""Internal hook system for OpenClaw agent events.

Provides an extensible event-driven hook system for agent events
like command processing, session lifecycle, etc.

Aligned with TypeScript openclaw/src/hooks/internal-hooks.ts
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Awaitable, Literal, Protocol, TypeGuard

logger = logging.getLogger(__name__)

InternalHookEventType = Literal["command", "session", "agent", "gateway", "message"]


@dataclass
class InternalHookEvent:
    """The base event for all internal hooks."""

    type: InternalHookEventType = "command"
    action: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    session_key: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent hook contexts
# ---------------------------------------------------------------------------

@dataclass
class AgentBootstrapHookContext:
    """Context for agent:bootstrap events."""

    workspace_dir: str
    bootstrap_files: list[Any]
    cfg: dict[str, Any] | None = None
    session_key: str | None = None
    session_id: str | None = None
    agent_id: str | None = None


@dataclass
class AgentBootstrapHookEvent(InternalHookEvent):
    """Agent bootstrap hook event."""

    type: Literal["agent"] = "agent"
    action: Literal["bootstrap"] = "bootstrap"
    context: AgentBootstrapHookContext = field(default_factory=lambda: AgentBootstrapHookContext(
        workspace_dir="", bootstrap_files=[]
    ))


# ---------------------------------------------------------------------------
# Gateway hook contexts
# ---------------------------------------------------------------------------

@dataclass
class GatewayStartupHookContext:
    """Context for gateway:startup events.

    Matches TS GatewayStartupHookContext from src/hooks/internal-hooks.ts.
    """

    cfg: dict[str, Any] | None = None
    deps: dict[str, Any] | None = None
    workspace_dir: str | None = None


@dataclass
class GatewayStartupHookEvent(InternalHookEvent):
    """Gateway startup hook event."""

    type: Literal["gateway"] = "gateway"
    action: Literal["startup"] = "startup"
    context: GatewayStartupHookContext = field(default_factory=GatewayStartupHookContext)


# ---------------------------------------------------------------------------
# Message hook contexts
# ---------------------------------------------------------------------------

@dataclass
class MessageReceivedHookContext:
    """Context for message:received events."""

    sender: str
    content: str
    timestamp: int | None = None
    channel_id: str = ""
    account_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def from_(self) -> str:
        """Alias for sender (matches TS 'from' field)."""
        return self.sender


@dataclass
class MessageReceivedHookEvent(InternalHookEvent):
    """Message received hook event."""

    type: Literal["message"] = "message"
    action: Literal["received"] = "received"
    context: MessageReceivedHookContext = field(default_factory=lambda: MessageReceivedHookContext(
        sender="", content=""
    ))


@dataclass
class MessageSentHookContext:
    """Context for message:sent events."""

    to: str
    content: str
    success: bool
    error: str | None = None
    channel_id: str = ""
    account_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    is_group: bool | None = None
    group_id: str | None = None


@dataclass
class MessageSentHookEvent(InternalHookEvent):
    """Message sent hook event."""

    type: Literal["message"] = "message"
    action: Literal["sent"] = "sent"
    context: MessageSentHookContext = field(default_factory=lambda: MessageSentHookContext(
        to="", content="", success=False
    ))


@dataclass
class MessageTranscribedHookContext:
    """Context for message:transcribed events.

    Matches TS MessageTranscribedHookContext from src/hooks/internal-hooks.ts.
    """

    transcript: str
    channel_id: str = ""
    from_: str | None = None
    to: str | None = None
    body: str | None = None
    body_for_agent: str | None = None
    timestamp: int | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    provider: str | None = None
    surface: str | None = None
    media_path: str | None = None
    media_type: str | None = None


@dataclass
class MessageTranscribedHookEvent(InternalHookEvent):
    """Message transcribed hook event (audio → text)."""

    type: Literal["message"] = "message"
    action: Literal["transcribed"] = "transcribed"
    context: MessageTranscribedHookContext = field(
        default_factory=lambda: MessageTranscribedHookContext(transcript="")
    )


@dataclass
class MessagePreprocessedHookContext:
    """Context for message:preprocessed events.

    Matches TS MessagePreprocessedHookContext from src/hooks/internal-hooks.ts.
    """

    channel_id: str = ""
    from_: str | None = None
    to: str | None = None
    body: str | None = None
    body_for_agent: str | None = None
    timestamp: int | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    sender_username: str | None = None
    provider: str | None = None
    surface: str | None = None
    media_path: str | None = None
    media_type: str | None = None
    transcript: str | None = None
    is_group: bool | None = None
    group_id: str | None = None


@dataclass
class MessagePreprocessedHookEvent(InternalHookEvent):
    """Message preprocessed hook event."""

    type: Literal["message"] = "message"
    action: Literal["preprocessed"] = "preprocessed"
    context: MessagePreprocessedHookContext = field(default_factory=MessagePreprocessedHookContext)


# ---------------------------------------------------------------------------
# Session hook contexts
# ---------------------------------------------------------------------------

@dataclass
class SessionPatchHookContext:
    """Context for session:patch events.

    Matches TS SessionPatchHookContext from src/hooks/internal-hooks.ts.
    """

    session_entry: dict[str, Any]
    patch: dict[str, Any]
    cfg: dict[str, Any]


@dataclass
class SessionPatchHookEvent(InternalHookEvent):
    """Session patch hook event."""

    type: Literal["session"] = "session"
    action: Literal["patch"] = "patch"
    context: SessionPatchHookContext = field(
        default_factory=lambda: SessionPatchHookContext(session_entry={}, patch={}, cfg={})
    )


class InternalHookHandler(Protocol):
    """Protocol for internal hook handlers."""

    async def __call__(self, event: InternalHookEvent) -> None:
        """Handle a hook event.
        
        Args:
            event: The hook event to handle
        """
        ...


# Module-level registry of hook handlers by event key
_handlers: dict[str, list[InternalHookHandler]] = defaultdict(list)


def register_internal_hook(event_key: str, handler: InternalHookHandler) -> None:
    """Register a hook handler for a specific event type or event:action combination.
    
    Args:
        event_key: Event type (e.g., 'command') or specific action (e.g., 'command:new')
        handler: Function to call when the event is triggered
    
    Example:
        >>> # Listen to all command events
        >>> async def my_handler(event):
        ...     print(f"Command: {event.action}")
        >>> register_internal_hook('command', my_handler)
        
        >>> # Listen only to /new commands
        >>> async def save_session(event):
        ...     await save_session_to_memory(event)
        >>> register_internal_hook('command:new', save_session)
    """
    _handlers[event_key].append(handler)


def unregister_internal_hook(event_key: str, handler: InternalHookHandler) -> None:
    """Unregister a specific hook handler.
    
    Args:
        event_key: Event key the handler was registered for
        handler: The handler function to remove
    """
    if event_key not in _handlers:
        return
    
    event_handlers = _handlers[event_key]
    if handler in event_handlers:
        event_handlers.remove(handler)
    
    # Clean up empty handler lists
    if not event_handlers:
        del _handlers[event_key]


def clear_internal_hooks() -> None:
    """Clear all registered hooks (useful for testing)."""
    _handlers.clear()


def get_registered_event_keys() -> list[str]:
    """Get all registered event keys (useful for debugging).
    
    Returns:
        List of registered event keys
    """
    return list(_handlers.keys())


async def trigger_internal_hook(event: InternalHookEvent) -> None:
    """Trigger a hook event.
    
    Calls all handlers registered for:
    1. The general event type (e.g., 'command')
    2. The specific event:action combination (e.g., 'command:new')
    
    Handlers are called in registration order. Errors are caught and logged
    but don't prevent other handlers from running.  No-op when hooks are
    globally disabled via :func:`set_internal_hooks_enabled`.
    
    Args:
        event: The event to trigger
    """
    if not _hooks_enabled:
        return

    type_handlers = _handlers.get(event.type, [])
    specific_handlers = _handlers.get(f"{event.type}:{event.action}", [])
    
    all_handlers = [*type_handlers, *specific_handlers]
    
    if not all_handlers:
        return
    
    for handler in all_handlers:
        try:
            await handler(event)
        except Exception as err:
            logger.error(
                f"Hook error [{event.type}:{event.action}]: {err}",
                exc_info=True
            )


def create_internal_hook_event(
    event_type: InternalHookEventType,
    action: str,
    session_key: str,
    context: dict[str, Any] | None = None,
) -> InternalHookEvent:
    """Create a hook event with common fields filled in.
    
    Args:
        event_type: The event type
        action: The action within that type
        session_key: The session key
        context: Additional context
    
    Returns:
        A new InternalHookEvent
    """
    return InternalHookEvent(
        type=event_type,
        action=action,
        session_key=session_key,
        context=context or {},
        timestamp=datetime.now(),
        messages=[],
    )


def is_agent_bootstrap_event(event: InternalHookEvent) -> TypeGuard[AgentBootstrapHookEvent]:
    """Check if event is an agent:bootstrap event.
    
    Args:
        event: The event to check
    
    Returns:
        True if event is an agent:bootstrap event
    """
    return event.type == "agent" and event.action == "bootstrap"


def is_message_received_event(event: InternalHookEvent) -> TypeGuard[MessageReceivedHookEvent]:
    """Check if event is a message:received event.
    
    Args:
        event: The event to check
    
    Returns:
        True if event is a message:received event
    """
    return event.type == "message" and event.action == "received"


def is_message_sent_event(event: InternalHookEvent) -> TypeGuard[MessageSentHookEvent]:
    """Check if event is a message:sent event.
    
    Args:
        event: The event to check
    
    Returns:
        True if event is a message:sent event
    """
    return event.type == "message" and event.action == "sent"


def is_gateway_startup_event(event: InternalHookEvent) -> TypeGuard[GatewayStartupHookEvent]:
    """Check if event is a gateway:startup event."""
    return event.type == "gateway" and event.action == "startup"


def is_message_transcribed_event(event: InternalHookEvent) -> TypeGuard[MessageTranscribedHookEvent]:
    """Check if event is a message:transcribed event."""
    return event.type == "message" and event.action == "transcribed"


def is_message_preprocessed_event(event: InternalHookEvent) -> TypeGuard[MessagePreprocessedHookEvent]:
    """Check if event is a message:preprocessed event."""
    return event.type == "message" and event.action == "preprocessed"


def is_session_patch_event(event: InternalHookEvent) -> TypeGuard[SessionPatchHookEvent]:
    """Check if event is a session:patch event."""
    return event.type == "session" and event.action == "patch"


# ---------------------------------------------------------------------------
# Enable / disable control — matches TS setInternalHooksEnabled / hasInternalHookListeners
# ---------------------------------------------------------------------------

_hooks_enabled: bool = True


def set_internal_hooks_enabled(enabled: bool) -> None:
    """Enable or disable internal hook dispatch globally.

    When disabled, :func:`trigger_internal_hook` is a no-op.
    Matches TS ``setInternalHooksEnabled()``.
    """
    global _hooks_enabled
    _hooks_enabled = enabled


def has_internal_hook_listeners(event_key: str) -> bool:
    """Return True if there is at least one handler registered for *event_key*.

    *event_key* may be a bare event type (``"command"``) or a
    ``"type:action"`` pair (``"command:new"``).
    Matches TS ``hasInternalHookListeners()``.
    """
    return bool(_handlers.get(event_key))
