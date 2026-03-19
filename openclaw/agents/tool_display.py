"""Tool display formatting for UI

Mirrors openclaw/src/agents/tool-display.ts and tool-display-common.ts

Provides JSON-based UI formatting for tool calls and results (complementary to the
markdown formatting in formatting/tool_result.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class ToolDisplay:
    """Tool display metadata for UI"""
    name: str
    emoji: str
    title: str
    label: str
    verb: str | None = None
    detail: str | None = None


@dataclass
class ToolDisplaySpec:
    """Tool display specification"""
    title: str | None = None
    label: str | None = None
    emoji: str | None = None
    detail_keys: list[str] | None = None


# Fallback display
FALLBACK_EMOJI = "🧩"

# Tool display specifications (partial, aligned with TS)
TOOL_DISPLAY_MAP: dict[str, ToolDisplaySpec] = {
    "read": ToolDisplaySpec(emoji="📖", title="Read", label="Read", detail_keys=["path"]),
    "write": ToolDisplaySpec(emoji="✏️", title="Write", label="Write", detail_keys=["path"]),
    "edit": ToolDisplaySpec(emoji="✍️", title="Edit", label="Edit", detail_keys=["path"]),
    "apply_patch": ToolDisplaySpec(emoji="🩹", title="Patch", label="Patch", detail_keys=["path"]),
    "exec": ToolDisplaySpec(emoji="⚡", title="Exec", label="Exec", detail_keys=["command"]),
    "process": ToolDisplaySpec(emoji="⚙️", title="Process", label="Process", detail_keys=["action"]),
    "web_search": ToolDisplaySpec(emoji="🔍", title="Search", label="Search", detail_keys=["query"]),
    "web_fetch": ToolDisplaySpec(emoji="🌐", title="Fetch", label="Fetch", detail_keys=["url"]),
    "memory_search": ToolDisplaySpec(emoji="🧠", title="Memory", label="Memory", detail_keys=["query"]),
    "memory_get": ToolDisplaySpec(emoji="📝", title="Recall", label="Recall", detail_keys=["path"]),
    "sessions_list": ToolDisplaySpec(emoji="📋", title="Sessions", label="Sessions", detail_keys=[]),
    "sessions_history": ToolDisplaySpec(emoji="📜", title="History", label="History", detail_keys=["session_key"]),
    "sessions_send": ToolDisplaySpec(emoji="📤", title="Send", label="Send", detail_keys=["session_key", "message"]),
    "sessions_spawn": ToolDisplaySpec(emoji="🚀", title="Spawn", label="Spawn", detail_keys=["agent_id"]),
    "subagents": ToolDisplaySpec(emoji="👥", title="Subagents", label="Subagents", detail_keys=["action"]),
    "session_status": ToolDisplaySpec(emoji="📊", title="Status", label="Status", detail_keys=[]),
    "browser": ToolDisplaySpec(emoji="🌐", title="Browser", label="Browser", detail_keys=["action"]),
    "canvas": ToolDisplaySpec(emoji="🎨", title="Canvas", label="Canvas", detail_keys=["action"]),
    "message": ToolDisplaySpec(emoji="💬", title="Message", label="Message", detail_keys=["channel", "text"]),
    "cron": ToolDisplaySpec(emoji="⏰", title="Cron", label="Cron", detail_keys=["action"]),
    "gateway": ToolDisplaySpec(emoji="🚪", title="Gateway", label="Gateway", detail_keys=["action"]),
    "nodes": ToolDisplaySpec(emoji="🖥️", title="Nodes", label="Nodes", detail_keys=["action"]),
    "agents_list": ToolDisplaySpec(emoji="🤖", title="Agents", label="Agents", detail_keys=[]),
    "image": ToolDisplaySpec(emoji="🖼️", title="Image", label="Image", detail_keys=["path"]),
    "tts": ToolDisplaySpec(emoji="🔊", title="TTS", label="TTS", detail_keys=["text"]),
}

# Detail label overrides (mirrors TS DETAIL_LABEL_OVERRIDES)
DETAIL_LABEL_OVERRIDES: dict[str, str] = {
    "agentId": "agent",
    "agent_id": "agent",
    "sessionKey": "session",
    "session_key": "session",
    "targetId": "target",
    "target_id": "target",
    "targetUrl": "url",
    "target_url": "url",
    "nodeId": "node",
    "node_id": "node",
    "requestId": "request",
    "request_id": "request",
    "messageId": "message",
    "message_id": "message",
    "threadId": "thread",
    "thread_id": "thread",
    "channelId": "channel",
    "channel_id": "channel",
    "guildId": "guild",
    "guild_id": "guild",
    "userId": "user",
    "user_id": "user",
    "runTimeoutSeconds": "timeout",
    "run_timeout_seconds": "timeout",
    "timeoutSeconds": "timeout",
    "timeout_seconds": "timeout",
    "includeTools": "tools",
    "include_tools": "tools",
    "pollQuestion": "poll",
    "poll_question": "poll",
    "maxChars": "max chars",
    "max_chars": "max chars",
}

MAX_DETAIL_ENTRIES = 8
MAX_DETAIL_STRING_LENGTH = 160


def normalize_tool_name(name: str | None) -> str:
    """Normalize tool name"""
    return (name or "tool").strip()


def default_title(name: str) -> str:
    """Generate default title from tool name"""
    cleaned = name.replace("_", " ").strip()
    if not cleaned:
        return "Tool"
    
    # Capitalize each word
    words = cleaned.split()
    return " ".join(
        part if (len(part) <= 2 and part.isupper()) else part.capitalize()
        for part in words
    )


def coerce_display_value(value: Any, max_length: int = MAX_DETAIL_STRING_LENGTH) -> str | None:
    """Coerce a value to display string"""
    if value is None:
        return None
    
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        # Get first line
        first_line = trimmed.split("\n")[0].strip()
        if not first_line:
            return None
        # Truncate if too long
        if len(first_line) > max_length:
            return f"{first_line[:max_length-1]}…"
        return first_line
    
    if isinstance(value, bool):
        return "true" if value else "false"
    
    if isinstance(value, (int, float)):
        return str(value)
    
    if isinstance(value, list):
        values = [coerce_display_value(item) for item in value if item is not None]
        values = [v for v in values if v]
        if not values:
            return None
        preview = ", ".join(values[:3])
        return f"{preview}…" if len(values) > 3 else preview
    
    return None


def format_detail_key(raw: str) -> str:
    """Format detail key for display"""
    # Split by dots and get last segment
    segments = raw.split(".")
    last = segments[-1] if segments else raw
    
    # Check for override
    if last in DETAIL_LABEL_OVERRIDES:
        return DETAIL_LABEL_OVERRIDES[last]
    
    # Clean up: replace _ and - with spaces
    cleaned = last.replace("_", " ").replace("-", " ")
    
    # Add spaces before capital letters (camelCase)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", cleaned)
    
    return spaced.strip().lower() or last.lower()


def resolve_detail_from_keys(
    args: dict[str, Any],
    keys: list[str],
    mode: Literal["first", "summary"] = "summary",
) -> str | None:
    """Resolve detail from argument keys"""
    if mode == "first":
        # Return first non-empty value
        for key in keys:
            value = args.get(key)
            display = coerce_display_value(value)
            if display:
                return display
        return None
    
    # Summary mode: collect all key-value pairs
    entries: list[tuple[str, str]] = []
    for key in keys:
        value = args.get(key)
        display = coerce_display_value(value)
        if display:
            label = format_detail_key(key)
            entries.append((label, display))
    
    if not entries:
        return None
    
    if len(entries) == 1:
        return entries[0][1]
    
    # Deduplicate and format
    seen = set()
    unique = []
    for label, value in entries:
        token = f"{label}:{value}"
        if token not in seen:
            seen.add(token)
            unique.append((label, value))
    
    if not unique:
        return None
    
    # Format as "key1 value1 · key2 value2"
    return " · ".join(
        f"{label} {value}"
        for label, value in unique[:MAX_DETAIL_ENTRIES]
    )


def resolve_tool_display(
    name: str | None = None,
    args: dict[str, Any] | None = None,
    meta: str | None = None,
) -> ToolDisplay:
    """Resolve tool display metadata.
    
    Mirrors TypeScript resolveToolDisplay()
    
    Args:
        name: Tool name
        args: Tool arguments
        meta: Optional metadata string
        
    Returns:
        ToolDisplay with emoji, title, label, verb, detail
    """
    tool_name = normalize_tool_name(name)
    key = tool_name.lower()
    spec = TOOL_DISPLAY_MAP.get(key)
    
    emoji = (spec.emoji if spec else None) or FALLBACK_EMOJI
    title = (spec.title if spec else None) or default_title(tool_name)
    label = (spec.label if spec else None) or title
    
    # Resolve verb (action)
    verb = None
    if args and "action" in args:
        action = args.get("action")
        if isinstance(action, str):
            verb = action.replace("_", " ").strip()
    
    # Resolve detail
    detail = None
    if args and spec and spec.detail_keys:
        detail = resolve_detail_from_keys(args, spec.detail_keys, mode="summary")
    
    if not detail and meta:
        detail = meta
    
    return ToolDisplay(
        name=tool_name,
        emoji=emoji,
        title=title,
        label=label,
        verb=verb,
        detail=detail,
    )


def format_tool_detail(display: ToolDisplay) -> str | None:
    """Format tool detail for display.
    
    Mirrors TypeScript formatToolDetail()
    """
    if not display.detail:
        return None
    
    # Convert " · " to ", " for readability
    if " · " in display.detail:
        parts = [part.strip() for part in display.detail.split(" · ") if part.strip()]
        return ", ".join(parts) if parts else None
    
    return display.detail


def format_tool_summary(display: ToolDisplay) -> str:
    """Format complete tool summary for UI.
    
    Mirrors TypeScript formatToolSummary()
    
    Returns:
        Formatted string like "🔍 Search: for 'python async'"
    """
    detail = format_tool_detail(display)
    if detail:
        return f"{display.emoji} {display.label}: {detail}"
    return f"{display.emoji} {display.label}"


def format_tool_call_for_ui(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Format tool call for UI display.
    
    Args:
        tool_call: Tool call dict with name, args, id
        
    Returns:
        Dict with display metadata
    """
    name = tool_call.get("name") or tool_call.get("tool_name")
    args = tool_call.get("args") or tool_call.get("arguments", {})
    
    display = resolve_tool_display(name=name, args=args)
    
    return {
        "id": tool_call.get("id") or tool_call.get("tool_call_id"),
        "name": display.name,
        "emoji": display.emoji,
        "title": display.title,
        "label": display.label,
        "verb": display.verb,
        "detail": format_tool_detail(display),
        "summary": format_tool_summary(display),
    }


def format_tool_result_for_ui(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Format tool result for UI display.
    
    Args:
        tool_result: Tool result dict with tool_call_id, output, error
        
    Returns:
        Dict with display metadata
    """
    output = tool_result.get("output") or tool_result.get("result")
    error = tool_result.get("error")
    
    # Truncate long output
    output_preview = None
    if output:
        output_str = str(output)
        if len(output_str) > 500:
            output_preview = f"{output_str[:497]}..."
        else:
            output_preview = output_str
    
    return {
        "id": tool_result.get("tool_call_id") or tool_result.get("id"),
        "success": not error,
        "output_preview": output_preview,
        "error": error,
        "has_output": bool(output),
    }


__all__ = [
    "ToolDisplay",
    "ToolDisplaySpec",
    "resolve_tool_display",
    "format_tool_detail",
    "format_tool_summary",
    "format_tool_call_for_ui",
    "format_tool_result_for_ui",
]
