"""Skill command integration.

Loads and manages skill commands from workspace.
Fully aligned with TypeScript openclaw/src/auto-reply/skill-commands.ts
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class SkillCommandSpec(TypedDict):
    """Skill command specification."""
    name: str
    skillName: str
    description: str
    workspaceDir: str


def list_reserved_chat_slash_command_names(extra_names: list[str] | None = None) -> set[str]:
    """List reserved slash command names (mirrors TS listReservedChatSlashCommandNames)."""
    from openclaw.auto_reply.commands_registry import list_chat_commands
    
    reserved = set()
    
    for command in list_chat_commands():
        if command.native_name:
            reserved.add(command.native_name.lower())
        
        for alias in command.text_aliases:
            trimmed = alias.strip()
            if not trimmed.startswith("/"):
                continue
            reserved.add(trimmed[1:].lower())
    
    if extra_names:
        for name in extra_names:
            trimmed = name.strip().lower()
            if trimmed:
                reserved.add(trimmed)
    
    return reserved


def list_skill_commands_for_workspace(
    workspace_dir: str,
    cfg: dict[str, Any],
    skill_filter: list[str] | None = None,
) -> list[SkillCommandSpec]:
    """List skill commands for a workspace (mirrors TS listSkillCommandsForWorkspace)."""
    try:
        from openclaw.agents.skills import build_workspace_skill_command_specs
        
        reserved_names = list_reserved_chat_slash_command_names()
        
        return build_workspace_skill_command_specs(
            workspace_dir,
            config=cfg,
            skill_filter=skill_filter,
            reserved_names=reserved_names,
        )
    except ImportError:
        logger.warning("Skills module not available")
        return []
    except Exception as exc:
        logger.error(f"Failed to list skill commands: {exc}")
        return []


def _scan_workspace_for_skills(workspace_dir: str) -> list[dict[str, Any]]:
    """Scan workspace directory for SKILL.md files and return skill specs.

    Each subdirectory containing a SKILL.md is treated as a skill.
    The skill name is derived from the SKILL.md first heading (h1), falling
    back to the directory name. Spaces/hyphens are converted to underscores.
    """
    results = []
    ws_path = Path(workspace_dir)
    if not ws_path.exists() or not ws_path.is_dir():
        return results

    for item in sorted(ws_path.iterdir()):
        if not item.is_dir():
            continue
        skill_md = item / "SKILL.md"
        if not skill_md.exists():
            continue

        dir_name = item.name  # e.g. "demo-skill"
        cmd_name = re.sub(r"[-\s]+", "_", dir_name.strip().lower())
        description = dir_name

        try:
            content = skill_md.read_text(encoding="utf-8")
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip()
                    if heading:
                        description = heading
                        # Derive command name from heading
                        cmd_name = re.sub(r"[-\s]+", "_", heading.lower())
                        cmd_name = re.sub(r"[^a-z0-9_]", "", cmd_name)
                    break
        except Exception:
            pass

        results.append({
            "name": cmd_name,
            "skillName": dir_name,
            "skill_name": dir_name,
            "description": description,
            "workspaceDir": workspace_dir,
        })
    return results


def list_skill_commands_for_agents(
    cfg: dict[str, Any],
    agent_ids: list[str] | None = None,
) -> list[SkillCommandSpec]:
    """List skill commands for agents (mirrors TS listSkillCommandsForAgents).

    Scans agent workspace directories and builds skill command specs.
    Deduplicates commands when multiple agents share the same workspace.

    Args:
        cfg: OpenClaw configuration
        agent_ids: Optional list of agent IDs (defaults to all agents)

    Returns:
        List of skill command specs
    """
    agents_cfg = cfg.get("agents") or {}
    agents_list = agents_cfg.get("list", []) if isinstance(agents_cfg, dict) else []

    if not agents_list:
        return []

    used: set[str] = set()
    entries: list[dict[str, Any]] = []
    visited_dirs: set[str] = set()

    target_ids = set(agent_ids) if agent_ids is not None else None

    for agent in agents_list:
        if not isinstance(agent, dict):
            continue
        agent_id = agent.get("id", "")
        if target_ids is not None and agent_id not in target_ids:
            continue

        workspace_dir = agent.get("workspace", "")
        if not workspace_dir or not os.path.exists(workspace_dir):
            continue

        canonical_dir = os.path.realpath(workspace_dir)
        if canonical_dir in visited_dirs:
            continue
        visited_dirs.add(canonical_dir)

        # Try to use the agents.skills module first
        commands: list[dict[str, Any]] = []
        try:
            from openclaw.agents.skills import build_workspace_skill_command_specs
            reserved = list_reserved_chat_slash_command_names()
            commands = build_workspace_skill_command_specs(
                workspace_dir, config=cfg, reserved_names=reserved,
            )
        except Exception:
            # Fallback: scan workspace directory directly
            commands = _scan_workspace_for_skills(workspace_dir)

        for command in commands:
            # Support both dict-like (legacy) and dataclass SkillCommandSpec objects
            if isinstance(command, dict):
                name = (command.get("name") or "").lower()
            else:
                name = (getattr(command, "name", None) or "").lower()
            if name and name not in used:
                used.add(name)
                entries.append(command)

    return entries


def _normalize_skill_command_lookup(value: str) -> str:
    """Normalize skill command name for lookup (mirrors TS normalizeSkillCommandLookup)."""
    return re.sub(r"[\s_]+", "-", value.strip().lower())


def _get_skill_name(entry: dict[str, Any]) -> str:
    """Get skill name from entry (supports both skillName and skill_name keys)."""
    return (entry.get("skillName") or entry.get("skill_name") or "").strip()


def find_skill_command(
    skill_commands: list[Any],
    raw_name: str,
) -> dict[str, Any] | None:
    """Find skill command by name (mirrors TS findSkillCommand)."""
    trimmed = raw_name.strip()
    if not trimmed:
        return None

    lowered = trimmed.lower()
    normalized = _normalize_skill_command_lookup(trimmed)

    for entry in skill_commands:
        if isinstance(entry, dict):
            name = (entry.get("name") or "").strip()
            skill_name = _get_skill_name(entry)
        else:
            name = getattr(entry, "name", "")
            skill_name = getattr(entry, "skillName", getattr(entry, "skill_name", ""))

        if name.lower() == lowered:
            return entry
        if skill_name.lower() == lowered:
            return entry
        if (
            _normalize_skill_command_lookup(name) == normalized
            or _normalize_skill_command_lookup(skill_name) == normalized
        ):
            return entry

    return None


def resolve_skill_command_invocation(
    command_body_normalized: str,
    skill_commands: list[SkillCommandSpec],
) -> dict[str, Any] | None:
    """Resolve skill command invocation (mirrors TS resolveSkillCommandInvocation)."""
    trimmed = command_body_normalized.strip()
    if not trimmed.startswith("/"):
        return None
    
    match = re.match(r"^/([^\s]+)(?:\s+([\s\S]+))?$", trimmed)
    if not match:
        return None
    
    command_name = match.group(1).strip().lower() if match.group(1) else ""
    if not command_name:
        return None
    
    # Handle /skill <skillname> <args> syntax
    if command_name == "skill":
        remainder = match.group(2).strip() if match.group(2) else ""
        if not remainder:
            return None
        
        skill_match = re.match(r"^([^\s]+)(?:\s+([\s\S]+))?$", remainder)
        if not skill_match:
            return None
        
        skill_command = find_skill_command(skill_commands, skill_match.group(1) or "")
        if not skill_command:
            return None
        
        args = skill_match.group(2).strip() if skill_match.group(2) else None
        return {"command": skill_command, "args": args}
    
    # Handle direct skill command: /skillname <args>
    # Entries may be dicts or SkillCommandSpec dataclasses — use getattr for safety
    command = next(
        (
            entry for entry in skill_commands
            if (
                entry.get("name", "") if isinstance(entry, dict) else getattr(entry, "name", "")
            ).lower() == command_name
        ),
        None,
    )

    if not command:
        return None

    args = match.group(2).strip() if match.group(2) else None
    return {"command": command, "args": args}


__all__ = [
    "SkillCommandSpec",
    "list_reserved_chat_slash_command_names",
    "list_skill_commands_for_workspace",
    "list_skill_commands_for_agents",
    "find_skill_command",
    "resolve_skill_command_invocation",
]
