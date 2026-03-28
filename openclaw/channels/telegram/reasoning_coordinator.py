"""Reasoning lane coordination - manages reasoning block separation.

Mirrors TypeScript src/telegram/reasoning-lane-coordinator.ts
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReasoningCoordinator:
    """Manages reasoning block separation and buffering.
    
    Mirrors TS createTelegramReasoningStepState() in reasoning-lane-coordinator.ts.
    
    Key responsibilities:
    - Track reasoning delivery state
    - Buffer final answer when waiting for reasoning
    - Coordinate reasoning block separation (forceNewMessage timing)
    """
    
    def __init__(self):
        """Initialize coordinator state."""
        self._split_on_next_stream = False
        self._buffered_final_answer: dict[str, Any] | None = None
        self._reasoning_delivered = False
        self._reasoning_hint = False
    
    def note_reasoning_hint(self) -> None:
        """Mark that reasoning content was detected.
        
        Mirrors TS noteReasoningHint().
        """
        self._reasoning_hint = True
    
    def note_reasoning_delivered(self) -> None:
        """Mark that reasoning was successfully delivered.
        
        Mirrors TS noteReasoningDelivered().
        """
        self._reasoning_delivered = True
    
    def should_buffer_final_answer(self) -> bool:
        """Check if final answer should be buffered (waiting for reasoning delivery).
        
        Mirrors TS shouldBufferFinalAnswer().
        
        Returns:
            True if answer should be buffered (reasoning hint present but not delivered yet)
        """
        return self._reasoning_hint and not self._reasoning_delivered
    
    def buffer_final_answer(self, payload: Any, text: str) -> None:
        """Buffer final answer until reasoning is delivered.
        
        Mirrors TS bufferFinalAnswer().
        
        Args:
            payload: Reply payload to buffer
            text: Answer text to buffer
        """
        self._buffered_final_answer = {
            "payload": payload,
            "text": text,
        }
        logger.debug(f"Buffered final answer (len={len(text)}), waiting for reasoning delivery")
    
    def take_buffered_final_answer(self) -> dict[str, Any] | None:
        """Retrieve and clear buffered final answer.
        
        Mirrors TS takeBufferedFinalAnswer().
        
        Returns:
            Buffered answer dict with 'payload' and 'text', or None if no buffer
        """
        buffered = self._buffered_final_answer
        self._buffered_final_answer = None
        if buffered:
            logger.debug(f"Taking buffered final answer (len={len(buffered['text'])})")
        return buffered
    
    def reset_for_next_step(self) -> None:
        """Reset state for next reasoning step.
        
        Mirrors TS resetForNextStep().
        
        Called after:
        - Final answer is delivered
        - Reasoning block is finalized
        - New turn starts
        """
        self._reasoning_hint = False
        self._reasoning_delivered = False
        self._buffered_final_answer = None
        logger.debug("Reasoning coordinator state reset for next step")
    
    def should_split_reasoning_on_next_stream(self) -> bool:
        """Check if reasoning lane should split on next stream.
        
        Mirrors TS splitReasoningOnNextStream flag check.
        
        Returns:
            True if reasoning lane should call forceNewMessage() on next stream
        """
        return self._split_on_next_stream
    
    def mark_split_reasoning_on_next_stream(self) -> None:
        """Mark that reasoning lane should split on next stream.
        
        Mirrors TS splitReasoningOnNextStream = true.
        
        Called after reasoning final is delivered, to ensure the next
        reasoning block starts a fresh preview message.
        """
        self._split_on_next_stream = True
        logger.debug("Marked split_reasoning_on_next_stream=True")
    
    def clear_split_flag(self) -> None:
        """Clear the split flag after splitting.
        
        Mirrors TS splitReasoningOnNextStream = false.
        
        Called after forceNewMessage() is executed.
        """
        self._split_on_next_stream = False
        logger.debug("Cleared split_reasoning_on_next_stream flag")


__all__ = ["ReasoningCoordinator"]
