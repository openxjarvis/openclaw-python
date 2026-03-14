"""
Core type definitions for Agent system matching pi-mono

This module provides the complete type system for the agent architecture,
including messages, content types, stop reasons, and tool definitions.
All types are designed to match Pi Agent's TypeScript implementation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, TypeVar, Generic

from pydantic import BaseModel, Field


# ============================================================================
# Thinking Level
# ============================================================================

class ThinkingLevel(str, Enum):
    """
    Thinking/reasoning level for models that support extended reasoning.
    
    Matches Pi Agent's thinkingLevel enum.
    """
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


# ============================================================================
# Content Types
# ============================================================================

class TextContent(BaseModel):
    """Text content block"""
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """Image content block"""
    type: Literal["image"] = "image"
    data: str  # Base64 or URL
    mime_type: str | None = Field(default=None, alias="mimeType")


class ThinkingContent(BaseModel):
    """Thinking/reasoning content block"""
    type: Literal["thinking"] = "thinking"
    thinking: str


class ToolCallContent(BaseModel):
    """Tool call content block"""
    type: Literal["toolCall"] = "toolCall"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    args: dict[str, Any]


# Content can be text, image, thinking, or tool call
Content = TextContent | ImageContent | ThinkingContent | ToolCallContent


# ============================================================================
# Tool Call Types
# ============================================================================

class ToolCall(BaseModel):
    """Tool call from LLM"""
    id: str
    name: str
    arguments: dict[str, Any]


# ============================================================================
# Stop Reasons
# ============================================================================

class StopReason(str, Enum):
    """
    Reason why LLM stopped generating.
    
    Matches Pi Agent's StopReason enum.
    """
    STOP = "stop"  # Natural completion
    LENGTH = "length"  # Max tokens reached
    TOOL_USE = "toolUse"  # Model wants to use tools
    ABORTED = "aborted"  # User/system aborted
    ERROR = "error"  # Error occurred


# ============================================================================
# Message Types
# ============================================================================

class UserMessage(BaseModel):
    """User message"""
    role: Literal["user"] = "user"
    content: list[Content] | str
    timestamp: int = Field(default_factory=lambda: int(asyncio.get_event_loop().time() * 1000))


class AssistantMessage(BaseModel):
    """
    Assistant message from LLM.
    
    Matches Pi Agent's AssistantMessage with support for:
    - Text content
    - Thinking/reasoning blocks
    - Tool calls
    - Stop reason tracking
    
    All content (text, thinking, tool calls) is stored in the content array.
    """
    role: Literal["assistant"] = "assistant"
    content: list[Content]  # TextContent | ImageContent | ThinkingContent | ToolCallContent
    stop_reason: StopReason = Field(default=StopReason.STOP, alias="stopReason")
    error_message: str | None = Field(default=None, alias="errorMessage")
    timestamp: int = Field(default_factory=lambda: int(asyncio.get_event_loop().time() * 1000))
    
    # Usage information (optional)
    usage: dict[str, Any] | None = None
    
    # Legacy fields for backward compatibility (extracted from content)
    tool_calls: list[dict[str, Any]] | None = None  # For easier access to tool calls


class ToolResultMessage(BaseModel):
    """
    Tool execution result message.
    
    Matches Pi Agent's ToolResultMessage.
    """
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = Field(alias="toolCallId")
    tool_name: str = Field(alias="toolName")
    content: list[Content]
    details: Any = None  # For UI/logging, not sent to LLM
    is_error: bool = Field(default=False, alias="isError")
    timestamp: int = Field(default_factory=lambda: int(asyncio.get_event_loop().time() * 1000))


# Custom message types support (extensible via Protocol)
class CustomMessageProtocol(Protocol):
    """Protocol for custom message types"""
    role: str
    timestamp: int


# AgentMessage is a union of all message types
AgentMessage = UserMessage | AssistantMessage | ToolResultMessage


# ============================================================================
# Agent State
# ============================================================================

@dataclass
class AgentState:
    """
    Agent execution state.
    
    Matches Pi Agent's AgentState interface.
    """
    system_prompt: str
    model: str
    thinking_level: ThinkingLevel
    tools: list[Any]  # List of AgentTool (forward reference)
    messages: list[AgentMessage]
    is_streaming: bool = False
    stream_message: AgentMessage | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error: str | None = None


# ============================================================================
# Tool System Types
# ============================================================================

TParams = TypeVar("TParams")
TDetails = TypeVar("TDetails")


class AgentToolResult(BaseModel, Generic[TDetails]):
    """
    Tool execution result.
    
    Matches Pi Agent's AgentToolResult with:
    - content: What gets sent to LLM
    - details: For UI/logging, not sent to LLM
    """
    content: list[Content]
    details: TDetails


class AgentToolUpdateCallback(Protocol):
    """Callback for streaming tool updates"""
    def __call__(self, partial_result: AgentToolResult) -> None:
        """Called with partial results during execution"""
        ...


class AgentTool(Protocol[TParams, TDetails]):
    """
    Agent tool interface.
    
    Matches Pi Agent's AgentTool with:
    - name: Tool identifier
    - label: Human-readable name for UI
    - description: For LLM to understand what tool does
    - parameters: JSON Schema for parameters
    - execute: Async execution with streaming support
    """
    name: str
    label: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    
    async def execute(
        self,
        tool_call_id: str,
        params: TParams,
        signal: asyncio.Event | None = None,
        on_update: AgentToolUpdateCallback | None = None,
    ) -> AgentToolResult[TDetails]:
        """
        Execute tool with streaming support.
        
        Args:
            tool_call_id: Unique ID for this invocation
            params: Validated parameters
            signal: Cancellation signal (when set, should abort)
            on_update: Callback for streaming updates (optional)
            
        Returns:
            Tool execution result
        """
        ...


# ============================================================================
# Event Types (from events.py)
# ============================================================================

# These are defined in events.py, but we provide type aliases here
from .events import (
    AgentEvent,
    AgentEventType,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,
)


__all__ = [
    # Thinking
    "ThinkingLevel",
    # Content
    "TextContent",
    "ImageContent",
    "Content",
    # Tool calls
    "ToolCall",
    # Stop reasons
    "StopReason",
    # Messages
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "AgentMessage",
    # State
    "AgentState",
    # Tools
    "AgentToolResult",
    "AgentToolUpdateCallback",
    "AgentTool",
    # Events (re-exported)
    "AgentEvent",
    "AgentEventType",
    "AgentStartEvent",
    "AgentEndEvent",
    "TurnStartEvent",
    "TurnEndEvent",
    "MessageStartEvent",
    "MessageUpdateEvent",
    "MessageEndEvent",
    "ToolExecutionStartEvent",
    "ToolExecutionUpdateEvent",
    "ToolExecutionEndEvent",
]
