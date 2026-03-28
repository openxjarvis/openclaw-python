"""
Tool policy for subagents and sandbox — fully aligned with TypeScript.

Matches:
- src/agents/pi-tools.policy.ts (resolveSubagentToolPolicy, makeToolPolicyMatcher, filterToolsByPolicy)
- src/agents/tool-policy-shared.ts (expandToolGroups, normalizeToolName, TOOL_GROUPS)
- src/agents/glob-pattern.ts (compileGlobPattern, matchesAnyGlobPattern)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional, Union

DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH = 1

SUBAGENT_TOOL_DENY_ALWAYS = [
    "gateway",
    "agents_list",
    "whatsapp_login",
    "session_status",
    "cron",
    "memory_search",
    "memory_get",
    "sessions_send",
]

SUBAGENT_TOOL_DENY_LEAF = [
    "sessions_list",
    "sessions_history",
    "sessions_spawn",
]

TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "exec",
    "apply-patch": "apply_patch",
}

TOOL_GROUPS: dict[str, list[str]] = {
    "group:fs": ["read", "write", "edit", "ls", "find", "grep"],
    "group:runtime": ["exec", "process", "apply_patch"],
    "group:web": ["web_search", "web_fetch"],
    "group:memory": ["memory_search", "memory_get"],
    "group:openclaw": [
        "browser", "message", "subagents", "session_status",
        "sessions_list", "sessions_history", "sessions_send", "sessions_spawn",
        "tts", "voice_call", "cron", "canvas", "gateway", "agents_list",
        "whatsapp_login", "image", "nodes",
    ],
}


# ---------------------------------------------------------------------------
# Normalize helpers (matches tool-policy-shared.ts)
# ---------------------------------------------------------------------------

def normalize_tool_name(name: str) -> str:
    normalized = name.strip().lower()
    return TOOL_NAME_ALIASES.get(normalized, normalized)


def normalize_tool_list(lst: list[str] | None) -> list[str]:
    if not lst:
        return []
    return [n for n in (normalize_tool_name(t) for t in lst) if n]


def expand_tool_groups(lst: list[str] | None) -> list[str]:
    """Expand group:xxx references into individual tool names (TS expandToolGroups)."""
    normalized = normalize_tool_list(lst)
    expanded: list[str] = []
    for value in normalized:
        group = TOOL_GROUPS.get(value)
        if group:
            expanded.extend(group)
        else:
            expanded.append(value)
    return list(dict.fromkeys(expanded))


# ---------------------------------------------------------------------------
# Glob pattern matching (matches glob-pattern.ts)
# ---------------------------------------------------------------------------

@dataclass
class _GlobAll:
    kind: Literal["all"] = "all"

@dataclass
class _GlobExact:
    value: str
    kind: Literal["exact"] = "exact"

@dataclass
class _GlobRegex:
    value: re.Pattern[str]
    kind: Literal["regex"] = "regex"


CompiledGlobPattern = Union[_GlobAll, _GlobExact, _GlobRegex]


def _escape_regex(value: str) -> str:
    return re.escape(value)


def compile_glob_pattern(raw: str) -> CompiledGlobPattern:
    normalized = normalize_tool_name(raw)
    if not normalized:
        return _GlobExact(value="")
    if normalized == "*":
        return _GlobAll()
    if "*" not in normalized:
        return _GlobExact(value=normalized)
    regex_str = "^" + _escape_regex(normalized).replace(r"\*", ".*") + "$"
    return _GlobRegex(value=re.compile(regex_str))


def compile_glob_patterns(raw: list[str] | None) -> list[CompiledGlobPattern]:
    if not raw:
        return []
    patterns = [compile_glob_pattern(r) for r in raw]
    return [p for p in patterns if not (isinstance(p, _GlobExact) and not p.value)]


def matches_any_glob_pattern(value: str, patterns: list[CompiledGlobPattern]) -> bool:
    for p in patterns:
        if isinstance(p, _GlobAll):
            return True
        if isinstance(p, _GlobExact) and value == p.value:
            return True
        if isinstance(p, _GlobRegex) and p.value.search(value):
            return True
    return False


# ---------------------------------------------------------------------------
# Deny list resolution
# ---------------------------------------------------------------------------

def resolve_subagent_deny_list(depth: int, max_spawn_depth: int) -> list[str]:
    is_leaf = depth >= max(1, int(max_spawn_depth))
    if is_leaf:
        return [*SUBAGENT_TOOL_DENY_ALWAYS, *SUBAGENT_TOOL_DENY_LEAF]
    return [*SUBAGENT_TOOL_DENY_ALWAYS]


# ---------------------------------------------------------------------------
# ToolPolicy (uses glob matching like TS makeToolPolicyMatcher)
# ---------------------------------------------------------------------------

class ToolPolicy:
    """
    Tool policy with allow/deny lists.
    Uses glob pattern compilation for matching (aligned with TS makeToolPolicyMatcher).
    """

    def __init__(
        self,
        allow: Optional[list[str]] = None,
        deny: Optional[list[str]] = None,
    ):
        self.allow = allow
        self.deny = deny or []
        self._compiled_deny = compile_glob_patterns(expand_tool_groups(self.deny))
        self._compiled_allow = compile_glob_patterns(
            expand_tool_groups(self.allow) if self.allow is not None else None
        )

    def is_allowed(self, tool_name: str) -> bool:
        normalized = normalize_tool_name(tool_name)

        if matches_any_glob_pattern(normalized, self._compiled_deny):
            return False

        if not self._compiled_allow:
            return True

        if matches_any_glob_pattern(normalized, self._compiled_allow):
            return True

        if normalized == "apply_patch" and matches_any_glob_pattern("exec", self._compiled_allow):
            return True

        return False


# ---------------------------------------------------------------------------
# Subagent tool policy resolution (matches TS resolveSubagentToolPolicy)
# ---------------------------------------------------------------------------

def resolve_subagent_tool_policy(
    config: Optional[dict] = None,
    depth: Optional[int] = None,
) -> ToolPolicy:
    if config is None:
        config = {}

    configured = config.get("tools", {}).get("subagents", {}).get("tools", {})
    max_spawn_depth = (
        config.get("agents", {})
        .get("defaults", {})
        .get("subagents", {})
        .get("maxSpawnDepth", DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH)
    )

    effective_depth = depth if isinstance(depth, int) and depth >= 0 else 1

    base_deny = resolve_subagent_deny_list(effective_depth, max_spawn_depth)

    allow = configured.get("allow") if isinstance(configured.get("allow"), list) else None
    also_allow = configured.get("alsoAllow") if isinstance(configured.get("alsoAllow"), list) else None

    explicit_allow = set(
        normalize_tool_name(t)
        for t in [*(allow or []), *(also_allow or [])]
    )

    deny = [
        tool_name for tool_name in base_deny
        if normalize_tool_name(tool_name) not in explicit_allow
    ]

    if isinstance(configured.get("deny"), list):
        deny.extend(configured["deny"])

    merged_allow = (
        list(dict.fromkeys([*allow, *also_allow])) if allow and also_allow
        else allow
    )

    return ToolPolicy(allow=merged_allow, deny=deny)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def is_tool_allowed_by_policy(
    tool_name: str,
    policy: Optional[ToolPolicy] = None,
) -> bool:
    if policy is None:
        return True
    return policy.is_allowed(tool_name)


def filter_tools_by_policy(
    tools: list,
    policy: Optional[ToolPolicy] = None,
) -> list:
    if policy is None:
        return tools
    return [
        tool for tool in tools
        if hasattr(tool, "name") and policy.is_allowed(tool.name)
    ]


__all__ = [
    "DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH",
    "SUBAGENT_TOOL_DENY_ALWAYS",
    "SUBAGENT_TOOL_DENY_LEAF",
    "TOOL_NAME_ALIASES",
    "TOOL_GROUPS",
    "ToolPolicy",
    "normalize_tool_name",
    "normalize_tool_list",
    "expand_tool_groups",
    "compile_glob_pattern",
    "compile_glob_patterns",
    "matches_any_glob_pattern",
    "resolve_subagent_deny_list",
    "resolve_subagent_tool_policy",
    "is_tool_allowed_by_policy",
    "filter_tools_by_policy",
]
