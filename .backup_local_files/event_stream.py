"""
Event stream implementation matching pi-mono's EventStream

This module provides an async iterable event stream that allows:
- Pushing events into a queue
- Async iteration over events
- Waiting for final result
- Clean completion handling

Matches the behavior of pi-mono/packages/ai/src/utils/event-stream.ts
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Generic, TypeVar

T = TypeVar("T")  # Event type
R = TypeVar("R")  # Result type


class EventStream(Generic[T, R]):
    """
    Async event stream with result promise.
    
    This matches Pi Agent's EventStream implementation:
    - Events are pushed with push()
    - Consumers iterate with async for
    - Final result is available via result()
    - Completion is determined by is_complete callback
    
    Example:
        ```python
        def is_complete(event):
            return event.type == "agent_end"
        
        def extract_result(event):
            return event.messages
        
        stream = EventStream(is_complete, extract_result)
        
        # Producer
        await stream.push(event1)
        await stream.push(event2)
        stream.end(final_result)
        
        # Consumer
        async for event in stream:
            print(event)
        
        final = await stream.result()
        ```
    """
    
    def __init__(
        self,
        is_complete: Callable[[T], bool],
        extract_result: Callable[[T], R],
    ):
        """
        Initialize event stream.
        
        Args:
            is_complete: Function to check if event marks completion
            extract_result: Function to extract result from completion event
        """
        self._queue: list[T] = []
        self._waiting: list[asyncio.Future] = []
        self._done: bool = False
        self._is_complete = is_complete
        self._extract_result = extract_result
        
        # Create promise for final result
        self._result_future: asyncio.Future[R] = asyncio.Future()
    
    def push(self, event: T) -> None:
        """
        Push event into stream.
        
        If event marks completion (per is_complete), the stream is ended
        and final result is resolved.
        
        Args:
            event: Event to push
        """
        if self._done:
            return
        
        # Check if this event marks completion
        if self._is_complete(event):
            self._done = True
            result = self._extract_result(event)
            if not self._result_future.done():
                self._result_future.set_result(result)
        
        # Deliver to waiting consumer or queue it
        if self._waiting:
            waiter = self._waiting.pop(0)
            waiter.set_result((event, False))
        else:
            self._queue.append(event)
    
    def end(self, result: R | None = None) -> None:
        """
        End stream and optionally set result.
        
        Args:
            result: Optional final result (if not already set)
        """
        self._done = True
        
        # Set result if provided and not already set
        if result is not None and not self._result_future.done():
            self._result_future.set_result(result)
        
        # Notify all waiting consumers that we're done
        while self._waiting:
            waiter = self._waiting.pop(0)
            waiter.set_result((None, True))
    
    async def result(self) -> R:
        """
        Wait for and return final result.
        
        This can be called before stream completes - it will wait.
        
        Returns:
            Final result extracted from completion event or provided to end()
        """
        return await self._result_future
    
    def __aiter__(self) -> AsyncIterator[T]:
        """Make stream async iterable"""
        return self
    
    async def __anext__(self) -> T:
        """
        Get next event from stream.
        
        Returns:
            Next event
            
        Raises:
            StopAsyncIteration: When stream is complete
        """
        while True:
            # If we have queued events, return next one
            if self._queue:
                return self._queue.pop(0)
            
            # If stream is done and queue is empty, stop iteration
            if self._done:
                raise StopAsyncIteration
            
            # Wait for next event
            future: asyncio.Future = asyncio.Future()
            self._waiting.append(future)
            
            event, done = await future
            
            if done:
                raise StopAsyncIteration
            
            return event


def create_agent_event_stream() -> EventStream:
    """
    Create event stream for agent events.
    
    The stream completes when agent_end event is received,
    and result is extracted from the agent_end event's messages.
    
    Returns:
        EventStream configured for agent events
    """
    from .events import AgentEventType
    
    def is_complete(event) -> bool:
        return hasattr(event, "type") and event.type == AgentEventType.AGENT_END
    
    def extract_result(event):
        # Extract messages from agent_end event payload
        return event.payload.get("messages", [])
    
    return EventStream(is_complete, extract_result)


__all__ = [
    "EventStream",
    "create_agent_event_stream",
]
