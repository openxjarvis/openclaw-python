"""Core tool catalog and profiles

Mirrors openclaw/src/agents/tool-catalog.ts

This module defines:
- Tool metadata (id, label, description, section)
- Tool profiles (minimal, coding, messaging, full)
- Tool sections (Files, Runtime, Web, Memory, etc.)
- Tool groups for policy management
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolProfileId = Literal["minimal", "coding", "messaging", "full"]


@dataclass
class ToolProfilePolicy:
    """Policy for tool profile"""
    allow: list[str] | None = None
    deny: list[str] | None = None


@dataclass
class CoreToolSection:
    """Tool section metadata"""
    id: str
    label: str
    tools: list[CoreToolInfo]


@dataclass
class CoreToolInfo:
    """Tool information for display"""
    id: str
    label: str
    description: str


@dataclass
class CoreToolDefinition:
    """Complete tool definition"""
    id: str
    label: str
    description: str
    section_id: str
    profiles: list[ToolProfileId]
    include_in_openclaw_group: bool = False


# Tool section order (mirrors TS CORE_TOOL_SECTION_ORDER)
CORE_TOOL_SECTION_ORDER: list[dict[str, str]] = [
    {"id": "fs", "label": "Files"},
    {"id": "runtime", "label": "Runtime"},
    {"id": "web", "label": "Web"},
    {"id": "memory", "label": "Memory"},
    {"id": "sessions", "label": "Sessions"},
    {"id": "ui", "label": "UI"},
    {"id": "messaging", "label": "Messaging"},
    {"id": "automation", "label": "Automation"},
    {"id": "nodes", "label": "Nodes"},
    {"id": "agents", "label": "Agents"},
    {"id": "media", "label": "Media"},
]

# Core tool definitions (mirrors TS CORE_TOOL_DEFINITIONS)
CORE_TOOL_DEFINITIONS: list[CoreToolDefinition] = [
    CoreToolDefinition("read", "read", "Read file contents", "fs", ["coding"]),
    CoreToolDefinition("write", "write", "Create or overwrite files", "fs", ["coding"]),
    CoreToolDefinition("edit", "edit", "Make precise edits", "fs", ["coding"]),
    CoreToolDefinition("apply_patch", "apply_patch", "Patch files (OpenAI)", "fs", ["coding"]),
    CoreToolDefinition("exec", "exec", "Run shell commands", "runtime", ["coding"]),
    CoreToolDefinition("process", "process", "Manage background processes", "runtime", ["coding"]),
    CoreToolDefinition("web_search", "web_search", "Search the web", "web", [], True),
    CoreToolDefinition("web_fetch", "web_fetch", "Fetch web content", "web", [], True),
    CoreToolDefinition("memory_search", "memory_search", "Semantic search", "memory", ["coding"], True),
    CoreToolDefinition("memory_get", "memory_get", "Read memory files", "memory", ["coding"], True),
    CoreToolDefinition("sessions_list", "sessions_list", "List sessions", "sessions", ["coding", "messaging"], True),
    CoreToolDefinition("sessions_history", "sessions_history", "Session history", "sessions", ["coding", "messaging"], True),
    CoreToolDefinition("sessions_send", "sessions_send", "Send to session", "sessions", ["coding", "messaging"], True),
    CoreToolDefinition("sessions_spawn", "sessions_spawn", "Spawn sub-agent", "sessions", ["coding"], True),
    CoreToolDefinition("subagents", "subagents", "Manage sub-agents", "sessions", ["coding"], True),
    CoreToolDefinition("session_status", "session_status", "Session status", "sessions", ["minimal", "coding", "messaging"], True),
    CoreToolDefinition("browser", "browser", "Control web browser", "ui", [], True),
    CoreToolDefinition("canvas", "canvas", "Control canvases", "ui", [], True),
    CoreToolDefinition("message", "message", "Send messages", "messaging", ["messaging"], True),
    CoreToolDefinition("cron", "cron", "Schedule tasks", "automation", ["coding"], True),
    CoreToolDefinition("gateway", "gateway", "Gateway control", "automation", [], True),
    CoreToolDefinition("nodes", "nodes", "Nodes + devices", "nodes", [], True),
    CoreToolDefinition("agents_list", "agents_list", "List agents", "agents", [], True),
    CoreToolDefinition("image", "image", "Image understanding", "media", ["coding"], True),
    CoreToolDefinition("tts", "tts", "Text-to-speech conversion", "media", [], True),
]

# Build lookup map
CORE_TOOL_BY_ID: dict[str, CoreToolDefinition] = {
    tool.id: tool for tool in CORE_TOOL_DEFINITIONS
}


def list_core_tool_ids_for_profile(profile: ToolProfileId) -> list[str]:
    """List tool IDs for a specific profile"""
    return [tool.id for tool in CORE_TOOL_DEFINITIONS if profile in tool.profiles]


# Core tool profiles (mirrors TS CORE_TOOL_PROFILES)
CORE_TOOL_PROFILES: dict[ToolProfileId, ToolProfilePolicy] = {
    "minimal": ToolProfilePolicy(allow=list_core_tool_ids_for_profile("minimal")),
    "coding": ToolProfilePolicy(allow=list_core_tool_ids_for_profile("coding")),
    "messaging": ToolProfilePolicy(allow=list_core_tool_ids_for_profile("messaging")),
    "full": ToolProfilePolicy(),  # No restrictions
}


def build_core_tool_group_map() -> dict[str, list[str]]:
    """Build tool group mappings for policy management.
    
    Mirrors TypeScript buildCoreToolGroupMap()
    
    Returns:
        Dict mapping group IDs to tool ID lists:
        - "group:openclaw" - All tools marked for OpenClaw group
        - "group:{section}" - Tools by section (fs, runtime, web, etc.)
    """
    section_tool_map: dict[str, list[str]] = {}
    
    for tool in CORE_TOOL_DEFINITIONS:
        group_id = f"group:{tool.section_id}"
        if group_id not in section_tool_map:
            section_tool_map[group_id] = []
        section_tool_map[group_id].append(tool.id)
    
    # Build OpenClaw group
    openclaw_tools = [
        tool.id for tool in CORE_TOOL_DEFINITIONS
        if tool.include_in_openclaw_group
    ]
    
    return {
        "group:openclaw": openclaw_tools,
        **section_tool_map,
    }


# Pre-built tool groups
CORE_TOOL_GROUPS = build_core_tool_group_map()


# Profile display options
PROFILE_OPTIONS = [
    {"id": "minimal", "label": "Minimal"},
    {"id": "coding", "label": "Coding"},
    {"id": "messaging", "label": "Messaging"},
    {"id": "full", "label": "Full"},
]


def resolve_core_tool_profile_policy(profile: str | None) -> ToolProfilePolicy | None:
    """Resolve profile to policy.
    
    Mirrors TypeScript resolveCoreToolProfilePolicy()
    
    Args:
        profile: Profile ID (minimal, coding, messaging, full) or None
        
    Returns:
        ToolProfilePolicy or None if profile not found or has no restrictions
    """
    if not profile:
        return None
    
    resolved = CORE_TOOL_PROFILES.get(profile)  # type: ignore
    if not resolved:
        return None
    
    if not resolved.allow and not resolved.deny:
        return None
    
    return ToolProfilePolicy(
        allow=list(resolved.allow) if resolved.allow else None,
        deny=list(resolved.deny) if resolved.deny else None,
    )


def list_core_tool_sections() -> list[CoreToolSection]:
    """List all tool sections with their tools.
    
    Mirrors TypeScript listCoreToolSections()
    
    Returns:
        List of CoreToolSection objects with tools grouped by section
    """
    sections: list[CoreToolSection] = []
    
    for section_meta in CORE_TOOL_SECTION_ORDER:
        section_id = section_meta["id"]
        section_label = section_meta["label"]
        
        # Find tools for this section
        tools = [
            CoreToolInfo(
                id=tool.id,
                label=tool.label,
                description=tool.description,
            )
            for tool in CORE_TOOL_DEFINITIONS
            if tool.section_id == section_id
        ]
        
        # Only include sections that have tools
        if tools:
            sections.append(CoreToolSection(
                id=section_id,
                label=section_label,
                tools=tools,
            ))
    
    return sections


def resolve_core_tool_profiles(tool_id: str) -> list[ToolProfileId]:
    """Get profiles that include this tool.
    
    Mirrors TypeScript resolveCoreToolProfiles()
    
    Args:
        tool_id: Tool identifier
        
    Returns:
        List of profile IDs that include this tool
    """
    tool = CORE_TOOL_BY_ID.get(tool_id)
    if not tool:
        return []
    return list(tool.profiles)


def is_known_core_tool_id(tool_id: str) -> bool:
    """Check if tool ID is a known core tool.
    
    Mirrors TypeScript isKnownCoreToolId()
    
    Args:
        tool_id: Tool identifier
        
    Returns:
        True if tool is in core catalog
    """
    return tool_id in CORE_TOOL_BY_ID


__all__ = [
    "ToolProfileId",
    "ToolProfilePolicy",
    "CoreToolSection",
    "CoreToolInfo",
    "CoreToolDefinition",
    "CORE_TOOL_SECTION_ORDER",
    "CORE_TOOL_DEFINITIONS",
    "CORE_TOOL_BY_ID",
    "CORE_TOOL_PROFILES",
    "CORE_TOOL_GROUPS",
    "PROFILE_OPTIONS",
    "list_core_tool_ids_for_profile",
    "build_core_tool_group_map",
    "resolve_core_tool_profile_policy",
    "list_core_tool_sections",
    "resolve_core_tool_profiles",
    "is_known_core_tool_id",
]
