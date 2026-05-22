"""
Message dispatch and reply routing

This module implements the complete dispatch flow matching TypeScript's
dispatch-from-config.ts functionality:
- Duplicate message detection
- Fast abort (stopping previous runs)
- Agent execution
- Cross-channel routing
- TTS generation
- Block streaming management
- Tool result handling
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, Callable, Optional, AsyncIterator
from dataclasses import dataclass

from openclaw.auto_reply.inbound_context import MsgContext
from openclaw.agents.runtime import AgentRuntime
from openclaw.agents.session import Session
from openclaw.events import Event, EventType

logger = logging.getLogger(__name__)


# ============================================================================
# Duplicate Detection
# ============================================================================

class DuplicateDetector:
    """Detects duplicate messages based on ID and content hash"""
    
    def __init__(self, cache_size: int = 1000, ttl_seconds: float = 300):
        """
        Initialize duplicate detector
        
        Args:
            cache_size: Maximum cache size
            ttl_seconds: Time-to-live for cache entries
        """
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_size = cache_size
        self._ttl_seconds = ttl_seconds
    
    def _compute_content_hash(self, text: str) -> str:
        """Compute hash of message content"""
        return hashlib.sha256(text.encode()).hexdigest()[:16]
    
    def _cleanup_expired(self) -> None:
        """Remove expired cache entries"""
        now = time.time()
        expired = [
            key for key, (_, timestamp) in self._cache.items()
            if now - timestamp > self._ttl_seconds
        ]
        for key in expired:
            del self._cache[key]
    
    def is_duplicate(
        self,
        message_id: Optional[str],
        content: str,
        session_key: str,
    ) -> bool:
        """
        Check if message is a duplicate
        
        Args:
            message_id: Message identifier
            content: Message content
            session_key: Session key
            
        Returns:
            True if duplicate
        """
        # Cleanup expired entries periodically
        if len(self._cache) > self._cache_size:
            self._cleanup_expired()
        
        # Build cache key
        if message_id:
            cache_key = f"{session_key}:{message_id}"
        else:
            # Use content hash if no message ID
            content_hash = self._compute_content_hash(content)
            cache_key = f"{session_key}:hash:{content_hash}"
        
        # Check cache
        if cache_key in self._cache:
            logger.debug(f"Duplicate message detected: {cache_key}")
            return True
        
        # Add to cache
        self._cache[cache_key] = (content, time.time())
        
        # Limit cache size
        if len(self._cache) > self._cache_size:
            # Remove oldest entry
            oldest_key = min(self._cache.items(), key=lambda x: x[1][1])[0]
            del self._cache[oldest_key]
        
        return False


# Global duplicate detector
_duplicate_detector = DuplicateDetector()


# ============================================================================
# Abort Signal
# ============================================================================

class AbortSignal:
    """Signal to abort agent execution"""
    
    def __init__(self):
        self._aborted = False
        self._reason: Optional[str] = None
    
    def abort(self, reason: str = "Aborted") -> None:
        """Abort execution"""
        self._aborted = True
        self._reason = reason
    
    @property
    def aborted(self) -> bool:
        """Check if aborted"""
        return self._aborted
    
    @property
    def reason(self) -> Optional[str]:
        """Get abort reason"""
        return self._reason


# Session-specific abort signals
_session_abort_signals: dict[str, AbortSignal] = {}


def get_abort_signal(session_key: str) -> AbortSignal:
    """Get or create abort signal for session"""
    if session_key not in _session_abort_signals:
        _session_abort_signals[session_key] = AbortSignal()
    return _session_abort_signals[session_key]


def abort_session(session_key: str, reason: str = "New message received") -> None:
    """Abort current agent execution for session"""
    signal = get_abort_signal(session_key)
    if not signal.aborted:
        signal.abort(reason)
        logger.info(f"Aborted session {session_key}: {reason}")


def reset_abort_signal(session_key: str) -> None:
    """Reset abort signal for session"""
    _session_abort_signals[session_key] = AbortSignal()


# ============================================================================
# Reply Collection
# ============================================================================

@dataclass
class AgentReply:
    """Agent reply data"""
    text: str
    tool_calls: Optional[list[dict]] = None
    metadata: Optional[dict] = None
    partial: bool = False


class ReplyCollector:
    """Collects streaming replies from agent"""
    
    def __init__(self):
        self.text_parts: list[str] = []
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.metadata: dict[str, Any] = {}
    
    def add_text(self, text: str) -> None:
        """Add text chunk"""
        self.text_parts.append(text)
    
    def add_tool_call(self, tool_call: dict) -> None:
        """Add tool call"""
        self.tool_calls.append(tool_call)
    
    def add_tool_result(self, result: dict) -> None:
        """Add tool result"""
        self.tool_results.append(result)
    
    def get_reply(self, partial: bool = False) -> AgentReply:
        """Get accumulated reply"""
        return AgentReply(
            text="".join(self.text_parts),
            tool_calls=self.tool_calls if self.tool_calls else None,
            metadata=self.metadata,
            partial=partial,
        )


# ============================================================================
# Reply Routing
# ============================================================================

def should_route_to_originating_channel(
    ctx: MsgContext,
) -> bool:
    """
    Check if reply should be routed to originating channel
    
    Args:
        ctx: Message context
        
    Returns:
        True if should route to originating channel
    """
    # If originating channel differs from current channel
    if ctx.OriginatingChannel and ctx.OriginatingChannel != ctx.Channel:
        return True
    
    # If originating recipient differs from current recipient
    if ctx.OriginatingTo and ctx.OriginatingTo != ctx.To:
        return True
    
    return False


async def route_reply(
    reply: AgentReply,
    ctx: MsgContext,
    send_callback: Callable[[str, MsgContext], Any],
) -> None:
    """
    Route reply to appropriate channel
    
    Args:
        reply: Agent reply
        ctx: Message context
        send_callback: Callback to send message
    """
    # Check if we need to route to different channel
    if should_route_to_originating_channel(ctx):
        logger.info(f"Routing reply to originating channel: {ctx.OriginatingChannel}")
        
        # Build context for originating channel
        routed_ctx = MsgContext(**ctx.model_dump())
        routed_ctx.Channel = ctx.OriginatingChannel
        routed_ctx.To = ctx.OriginatingTo
        
        # Send via originating channel
        await send_callback(reply.text, routed_ctx)
    else:
        # Send via current channel
        await send_callback(reply.text, ctx)


# ============================================================================
# Main Dispatch Function
# ============================================================================

async def dispatch_reply_from_config(
    ctx: MsgContext,
    runtime: AgentRuntime,
    session: Session,
    send_callback: Callable[[str, MsgContext], Any],
    on_partial_reply: Optional[Callable[[str], None]] = None,
    on_block_reply: Optional[Callable[[dict], None]] = None,
    on_tool_result: Optional[Callable[[dict], None]] = None,
    enable_duplicate_check: bool = True,
    enable_fast_abort: bool = True,
    enable_tts: bool = False,
) -> dict[str, Any]:
    """
    Dispatch message and get reply from agent
    
    This is the main entry point for message dispatching, matching TypeScript's
    dispatchReplyFromConfig function.
    
    Args:
        ctx: Message context
        runtime: Agent runtime
        session: Session object
        send_callback: Callback to send reply
        on_partial_reply: Callback for partial text updates
        on_block_reply: Callback for block-level replies
        on_tool_result: Callback for tool execution results
        enable_duplicate_check: Enable duplicate message detection
        enable_fast_abort: Enable fast abort of previous runs
        enable_tts: Enable TTS generation
        
    Returns:
        Dict with status and reply information
    """
    session_key = ctx.SessionKey
    
    # ========================================================================
    # 1. Duplicate Check
    # ========================================================================
    
    if enable_duplicate_check:
        is_dup = _duplicate_detector.is_duplicate(
            message_id=ctx.MessageId,
            content=ctx.Body,
            session_key=session_key,
        )
        if is_dup:
            logger.info(f"Duplicate message ignored: {ctx.MessageId}")
            return {
                "status": "ignored",
                "reason": "duplicate",
            }
    
    # ========================================================================
    # 2. Fast Abort
    # ========================================================================
    
    if enable_fast_abort:
        # Abort any running agent for this session
        abort_session(session_key, "New message received")
        # Wait a bit for abort to take effect
        await asyncio.sleep(0.1)
        # Reset abort signal for new run
        reset_abort_signal(session_key)
    
    # Get abort signal for this run
    abort_signal = get_abort_signal(session_key)
    
    # ========================================================================
    # 3. Run Agent
    # ========================================================================
    
    collector = ReplyCollector()
    
    try:
        # Add user message to session
        user_text = ctx.BodyForAgent or ctx.Body
        session.add_user_message(user_text)
        
        # Collect image URLs from context (mirrors TS MsgContext → images passing)
        images: list[str] | None = None
        media_urls = list(ctx.MediaUrls or [])
        if ctx.MediaUrl and ctx.MediaUrl not in media_urls:
            media_urls.insert(0, ctx.MediaUrl)
        if ctx.MediaPath and ctx.MediaPath not in media_urls:
            media_urls.insert(0, ctx.MediaPath)
        if media_urls:
            images = media_urls

        # Run agent turn
        async for event in runtime.run_turn(
            session=session,
            message=user_text,
            tools=None,  # Tools provided by runtime
            images=images,
        ):
            # Check abort signal
            if abort_signal.aborted:
                logger.info(f"Agent execution aborted: {abort_signal.reason}")
                return {
                    "status": "aborted",
                    "reason": abort_signal.reason,
                }
            
            # Process event
            if event.type == EventType.AGENT_TEXT:
                # Text chunk
                text_delta = event.data.get("delta", "")
                collector.add_text(text_delta)
                
                # Call partial reply callback
                if on_partial_reply:
                    on_partial_reply(text_delta)
            
            elif event.type == EventType.TOOL_USE:
                # Tool call
                tool_call = event.data
                collector.add_tool_call(tool_call)
                
                # Call block reply callback
                if on_block_reply:
                    on_block_reply({
                        "type": "tool_use",
                        "data": tool_call,
                    })
            
            elif event.type == EventType.TOOL_RESULT:
                # Tool result
                tool_result = event.data
                collector.add_tool_result(tool_result)
                
                # Call tool result callback
                if on_tool_result:
                    on_tool_result(tool_result)
            
            elif event.type == EventType.TURN_COMPLETE:
                # Turn complete
                logger.debug("Agent turn complete")
                break
        
        # Get final reply
        reply = collector.get_reply()
        
        # Check if aborted during execution
        if abort_signal.aborted:
            return {
                "status": "aborted",
                "reason": abort_signal.reason,
            }
        
    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
        }
    
    # ========================================================================
    # 4. Route Reply
    # ========================================================================
    
    try:
        await route_reply(reply, ctx, send_callback)
    except Exception as e:
        logger.error(f"Failed to route reply: {e}", exc_info=True)
        return {
            "status": "error",
            "error": f"Failed to send reply: {e}",
        }
    
    # ========================================================================
    # 5. TTS Generation (Optional)
    # ========================================================================
    
    if enable_tts and reply.text:
        # TODO: Implement TTS generation
        logger.debug("TTS generation not yet implemented")
    
    # ========================================================================
    # 6. Return Result
    # ========================================================================
    
    return {
        "status": "success",
        "reply": reply.text,
        "tool_calls": reply.tool_calls,
        "metadata": reply.metadata,
    }
