"""
Workspace skills management — mirrors TS agents/skills/workspace.ts

Loads and merges skills from all 6 sources, applies full filtering
(shouldIncludeSkill + skillFilter), enforces prompt limits (count + chars),
compacts paths, and produces SkillSnapshot objects.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from openclaw.config.paths import resolve_state_dir
from .config import should_include_skill
from .loader import (
    compact_skill_paths,
    format_skills_for_prompt,
    load_skill_entries_from_dir,
    DEFAULT_MAX_SKILL_FILE_BYTES,
)
from .types import Skill, SkillEligibilityContext, SkillEntry, SkillSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits — mirrors TS DEFAULT_MAX_* constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_CANDIDATES_PER_ROOT = 300
DEFAULT_MAX_SKILLS_LOADED_PER_SOURCE = 200
DEFAULT_MAX_SKILLS_IN_PROMPT = 150
DEFAULT_MAX_SKILLS_PROMPT_CHARS = 30_000


def _resolve_skills_limits(config: Any = None) -> dict[str, int]:
    """Resolve skills limits from config — mirrors TS resolveSkillsLimits."""
    defaults = {
        "maxCandidatesPerRoot": DEFAULT_MAX_CANDIDATES_PER_ROOT,
        "maxSkillsLoadedPerSource": DEFAULT_MAX_SKILLS_LOADED_PER_SOURCE,
        "maxSkillsInPrompt": DEFAULT_MAX_SKILLS_IN_PROMPT,
        "maxSkillsPromptChars": DEFAULT_MAX_SKILLS_PROMPT_CHARS,
        "maxSkillFileBytes": DEFAULT_MAX_SKILL_FILE_BYTES,
    }
    if not config:
        return defaults

    try:
        skills = config.get("skills", {}) if isinstance(config, dict) else getattr(config, "skills", None)
        if not skills:
            return defaults
        limits = skills.get("limits", {}) if isinstance(skills, dict) else getattr(skills, "limits", None)
        if not limits or not isinstance(limits, dict):
            return defaults
        return {k: limits.get(k, v) for k, v in defaults.items()}
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# Skill filter normalisation — mirrors TS normalizeSkillFilter
# ---------------------------------------------------------------------------

def normalize_skill_filter(raw: Any) -> list[str] | None:
    """Normalize a skill filter to a string list or None (unrestricted)."""
    if raw is None:
        return None
    if isinstance(raw, list):
        normalized = [str(s).strip() for s in raw if str(s).strip()]
        return normalized if normalized else []
    return None


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def get_openclaw_dir() -> Path:
    return resolve_state_dir()


def get_managed_skills_dir() -> Path:
    return get_openclaw_dir() / "skills"


# ---------------------------------------------------------------------------
# Core skill loading — all 6 sources
# ---------------------------------------------------------------------------

def load_workspace_skill_entries(
    workspace_dir: Path | str,
    config: Any | None = None,
    managed_skills_dir: Path | None = None,
    bundled_skills_dir: Path | None = None,
) -> list[SkillEntry]:
    """Load skills from all 6 sources with TS-aligned precedence.

    Precedence (later overrides earlier by name):
      extra < bundled < managed < agents-skills-personal < agents-skills-project < workspace
    """
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)

    limits = _resolve_skills_limits(config)
    load_kw = dict(
        max_skill_file_bytes=limits["maxSkillFileBytes"],
        max_skills_loaded=limits["maxSkillsLoadedPerSource"],
        max_candidates_per_root=limits["maxCandidatesPerRoot"],
    )

    if managed_skills_dir is None:
        managed_skills_dir = get_managed_skills_dir()

    if bundled_skills_dir is None:
        from openclaw.agents.skills.bundled_dir import resolve_bundled_skills_dir
        bundled_skills_dir = resolve_bundled_skills_dir()

    workspace_skills_dir = workspace_dir / "skills"

    # Source 1: extra dirs (from config) + plugin skill dirs
    extra_entries: list[SkillEntry] = []
    if config:
        for extra_dir in get_extra_skill_dirs(config):
            extra_entries.extend(
                load_skill_entries_from_dir(extra_dir, source="openclaw-extra", **load_kw)
            )

    # Source 2: bundled
    bundled_entries: list[SkillEntry] = []
    if bundled_skills_dir and Path(bundled_skills_dir).exists():
        bundled_entries = load_skill_entries_from_dir(bundled_skills_dir, source="openclaw-bundled", **load_kw)

    # Source 3: managed (~/.openclaw/skills/)
    managed_entries: list[SkillEntry] = []
    if managed_skills_dir.exists():
        managed_entries = load_skill_entries_from_dir(managed_skills_dir, source="openclaw-managed", **load_kw)

    # Source 4: personal (~/.agents/skills/)
    personal_agents_dir = Path.home() / ".agents" / "skills"
    personal_entries: list[SkillEntry] = []
    if personal_agents_dir.exists():
        personal_entries = load_skill_entries_from_dir(personal_agents_dir, source="agents-skills-personal", **load_kw)

    # Source 5: project (<workspace>/.agents/skills/)
    project_agents_dir = workspace_dir / ".agents" / "skills"
    project_entries: list[SkillEntry] = []
    if project_agents_dir.exists():
        project_entries = load_skill_entries_from_dir(project_agents_dir, source="agents-skills-project", **load_kw)

    # Source 6: workspace (<workspace>/skills/) — highest priority
    workspace_entries: list[SkillEntry] = []
    if workspace_skills_dir.exists():
        workspace_entries = load_skill_entries_from_dir(workspace_skills_dir, source="openclaw-workspace", **load_kw)

    # Merge with precedence: extra < bundled < managed < personal < project < workspace
    entries_by_name: dict[str, SkillEntry] = {}
    for entry in extra_entries:
        entries_by_name[entry.skill.name] = entry
    for entry in bundled_entries:
        entries_by_name[entry.skill.name] = entry
    for entry in managed_entries:
        entries_by_name[entry.skill.name] = entry
    for entry in personal_entries:
        entries_by_name[entry.skill.name] = entry
    for entry in project_entries:
        entries_by_name[entry.skill.name] = entry
    for entry in workspace_entries:
        entries_by_name[entry.skill.name] = entry

    return list(entries_by_name.values())


# ---------------------------------------------------------------------------
# Filtering — mirrors TS filterSkillEntries
# ---------------------------------------------------------------------------

def filter_skill_entries(
    entries: list[SkillEntry],
    config: Any | None = None,
    skill_filter: list[str] | None = None,
    eligibility: SkillEligibilityContext | None = None,
) -> list[SkillEntry]:
    """Full TS-aligned filtering: shouldIncludeSkill + skillFilter."""
    filtered = [e for e in entries if should_include_skill(e, config, eligibility)]

    if skill_filter is not None:
        normalized = normalize_skill_filter(skill_filter)
        if normalized is not None:
            if len(normalized) == 0:
                return []
            name_set = set(normalized)
            filtered = [e for e in filtered if e.skill.name in name_set]

    return filtered


# Legacy name kept for backward compat
def filter_workspace_skill_entries(
    entries: list[SkillEntry],
    config: Any | None = None,
    skill_filter: list[str] | None = None,
) -> list[SkillEntry]:
    return filter_skill_entries(entries, config, skill_filter)


# ---------------------------------------------------------------------------
# Prompt limits — mirrors TS applySkillsPromptLimits
# ---------------------------------------------------------------------------

def _apply_skills_prompt_limits(
    skills: list[Skill],
    config: Any | None = None,
) -> tuple[list[Skill], bool, str | None]:
    """Enforce count + char limits on skills for the prompt — mirrors TS applySkillsPromptLimits.

    Returns (skills_for_prompt, truncated, truncated_reason).
    """
    limits = _resolve_skills_limits(config)
    max_count = max(0, limits["maxSkillsInPrompt"])
    max_chars = limits["maxSkillsPromptChars"]

    total = len(skills)
    by_count = skills[:max_count]
    truncated = total > len(by_count)
    reason: str | None = "count" if truncated else None

    def fits(subset: list[Skill]) -> bool:
        return len(format_skills_for_prompt(subset)) <= max_chars

    skills_for_prompt = by_count

    if not fits(skills_for_prompt):
        lo, hi = 0, len(skills_for_prompt)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fits(skills_for_prompt[:mid]):
                lo = mid
            else:
                hi = mid - 1
        skills_for_prompt = skills_for_prompt[:lo]
        truncated = True
        reason = "chars"

    return skills_for_prompt, truncated, reason


# ---------------------------------------------------------------------------
# Prompt state — mirrors TS resolveWorkspaceSkillPromptState
# ---------------------------------------------------------------------------

def _resolve_workspace_skill_prompt_state(
    workspace_dir: str | Path,
    *,
    entries: list[SkillEntry] | None = None,
    config: Any | None = None,
    skill_filter: list[str] | None = None,
    eligibility: SkillEligibilityContext | None = None,
) -> tuple[list[SkillEntry], str, list[Skill]]:
    """Core prompt builder — mirrors TS resolveWorkspaceSkillPromptState.

    Returns (eligible_entries, prompt_string, resolved_skills).
    """
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)

    skill_entries = entries if entries is not None else load_workspace_skill_entries(workspace_dir, config)
    eligible = filter_skill_entries(skill_entries, config, skill_filter, eligibility)

    prompt_entries = [e for e in eligible if not (e.invocation and e.invocation.disable_model_invocation)]

    remote_note = ""
    if eligibility and eligibility.remote and isinstance(eligibility.remote, dict):
        remote_note = (eligibility.remote.get("note") or "").strip()

    resolved_skills = [e.skill for e in prompt_entries]
    skills_for_prompt, truncated, _reason = _apply_skills_prompt_limits(resolved_skills, config)

    truncation_note = ""
    if truncated:
        truncation_note = (
            f"\u26a0\ufe0f Skills truncated: included {len(skills_for_prompt)} "
            f"of {len(resolved_skills)}. Run `openclaw skills check` to audit."
        )

    prompt = "\n".join(filter(None, [
        remote_note,
        truncation_note,
        format_skills_for_prompt(compact_skill_paths(skills_for_prompt)),
    ]))

    return eligible, prompt, resolved_skills


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_workspace_skills_prompt(
    workspace_dir: Path | str,
    config: Any | None = None,
    read_tool_name: str = "read",
    skill_filter: list[str] | None = None,
    entries: list[SkillEntry] | None = None,
    eligibility: SkillEligibilityContext | None = None,
) -> str:
    """Build the raw skills prompt text — mirrors TS buildWorkspaceSkillsPrompt.

    Returns the ``<available_skills>`` XML block only — the caller wraps it
    with the ``## Skills (mandatory)`` header.
    """
    _, prompt, _ = _resolve_workspace_skill_prompt_state(
        workspace_dir,
        entries=entries,
        config=config,
        skill_filter=skill_filter,
        eligibility=eligibility,
    )
    return prompt


def build_workspace_skill_snapshot(
    workspace_dir: Path | str,
    config: Any | None = None,
    skill_filter: list[str] | None = None,
    eligibility: SkillEligibilityContext | None = None,
    snapshot_version: int | None = None,
) -> SkillSnapshot:
    """Build skill snapshot — mirrors TS buildWorkspaceSkillSnapshot.

    Applies full filtering, disableModelInvocation, prompt limits, and compactPaths.
    """
    eligible, prompt, resolved_skills = _resolve_workspace_skill_prompt_state(
        workspace_dir,
        config=config,
        skill_filter=skill_filter,
        eligibility=eligibility,
    )

    normalized_filter = normalize_skill_filter(skill_filter)

    skill_info: list[dict[str, Any]] = []
    for entry in eligible:
        info: dict[str, Any] = {"name": entry.skill.name}
        if entry.metadata and entry.metadata.primary_env:
            info["primaryEnv"] = entry.metadata.primary_env
        req_env: list[str] | None = None
        if entry.metadata and entry.metadata.requires:
            requires = entry.metadata.requires
            if hasattr(requires, "env") and requires.env:
                req_env = list(requires.env)
            elif isinstance(requires, dict) and requires.get("env"):
                req_env = list(requires["env"])
        if req_env:
            info["requiredEnv"] = req_env
        skill_info.append(info)

    return SkillSnapshot(
        prompt=prompt,
        skills=skill_info,
        skill_filter=normalized_filter,
        resolved_skills=resolved_skills,
        version=snapshot_version,
    )


def resolve_skills_prompt_for_run(
    *,
    skills_snapshot: SkillSnapshot | None = None,
    entries: list[SkillEntry] | None = None,
    config: Any | None = None,
    workspace_dir: str | Path = "",
) -> str:
    """Resolve skills prompt for a run — mirrors TS resolveSkillsPromptForRun.

    Fast path: use cached snapshot.prompt. Fallback: rebuild from entries.
    """
    if skills_snapshot:
        prompt = (skills_snapshot.prompt or "").strip()
        if prompt:
            return prompt

    if entries and len(entries) > 0:
        prompt = build_workspace_skills_prompt(
            workspace_dir,
            config=config,
            entries=entries,
        )
        return prompt.strip() if prompt else ""

    return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_extra_skill_dirs(config: Any) -> list[Path]:
    if not config:
        return []
    try:
        if hasattr(config, "skills") and hasattr(config.skills, "load"):
            extra_dirs = getattr(config.skills.load, "extraDirs", [])
            if extra_dirs:
                return [Path(d).expanduser() for d in extra_dirs if d]
    except Exception:
        pass
    return []


_SKILL_COMMAND_MAX_LENGTH = 32
_SKILL_COMMAND_FALLBACK = "skill"
_SKILL_COMMAND_DESCRIPTION_MAX_LENGTH = 100


def _sanitize_skill_command_name(raw: str) -> str:
    """Sanitize skill name to a valid slash command name (mirrors TS sanitizeSkillCommandName)."""
    import re as _re
    normalized = _re.sub(r"[^a-z0-9_]+", "_", raw.lower())
    normalized = _re.sub(r"_+", "_", normalized).strip("_")
    trimmed = normalized[:_SKILL_COMMAND_MAX_LENGTH]
    return trimmed or _SKILL_COMMAND_FALLBACK


def _resolve_unique_skill_command_name(base: str, used: set[str]) -> str:
    base_lower = base.lower()
    if base_lower not in used:
        return base
    for idx in range(2, 1000):
        suffix = f"_{idx}"
        max_base = max(1, _SKILL_COMMAND_MAX_LENGTH - len(suffix))
        candidate = base[:max_base] + suffix
        if candidate.lower() not in used:
            return candidate
    return base[:max(1, _SKILL_COMMAND_MAX_LENGTH - 2)] + "_x"


def build_workspace_skill_command_specs(
    workspace_dir: "str | Path",
    opts: dict[str, Any] | None = None,
    *,
    config: Any = None,
    managed_skills_dir: str | None = None,
    bundled_skills_dir: str | None = None,
    entries: "list[SkillEntry] | None" = None,
    skill_filter: "list[str] | None" = None,
    eligibility: Any = None,
    reserved_names: "set[str] | None" = None,
) -> "list[Any]":
    """Build slash-command specs for eligible skills (mirrors TS buildWorkspaceSkillCommandSpecs).

    Returns list of SkillCommandSpec objects (dataclasses) with .name, .skill_name,
    .description, and optional .dispatch.
    """
    from .types import SkillCommandDispatch, SkillCommandSpec as _SkillCommandSpec

    if opts:
        config = opts.get("config", config)
        managed_skills_dir = opts.get("managedSkillsDir", managed_skills_dir)
        bundled_skills_dir = opts.get("bundledSkillsDir", bundled_skills_dir)
        entries = opts.get("entries", entries)
        skill_filter = opts.get("skillFilter", opts.get("skill_filter", skill_filter))
        eligibility = opts.get("eligibility", eligibility)
        reserved_names = opts.get("reservedNames", reserved_names)

    skill_entries = entries if entries is not None else load_workspace_skill_entries(
        workspace_dir,
        config=config,
        managed_skills_dir=managed_skills_dir,
        bundled_skills_dir=bundled_skills_dir,
    )
    eligible = filter_skill_entries(skill_entries, config, skill_filter, eligibility)
    # Only user-invocable skills get slash commands
    user_invocable = [
        e for e in eligible
        if not (e.invocation and e.invocation.user_invocable is False)
    ]

    used: set[str] = set()
    for reserved in (reserved_names or set()):
        used.add(reserved.lower())

    specs: list[_SkillCommandSpec] = []
    for entry in user_invocable:
        raw_name = entry.skill.name
        base = _sanitize_skill_command_name(raw_name)
        unique = _resolve_unique_skill_command_name(base, used)
        used.add(unique.lower())

        raw_desc = (entry.skill.description or raw_name).strip()
        description = (
            raw_desc[:_SKILL_COMMAND_DESCRIPTION_MAX_LENGTH - 1] + "…"
            if len(raw_desc) > _SKILL_COMMAND_DESCRIPTION_MAX_LENGTH
            else raw_desc
        )

        # Resolve optional tool dispatch from frontmatter
        dispatch: SkillCommandDispatch | None = None
        fm = entry.frontmatter or {}
        kind_raw = (
            fm.get("command-dispatch") or fm.get("command_dispatch") or ""
        ).strip().lower()
        if kind_raw == "tool":
            tool_name = (fm.get("command-tool") or fm.get("command_tool") or "").strip()
            if tool_name:
                arg_mode_raw = (
                    fm.get("command-arg-mode") or fm.get("command_arg_mode") or ""
                ).strip().lower()
                dispatch = SkillCommandDispatch(
                    kind="tool",
                    tool_name=tool_name,
                    arg_mode="raw" if (not arg_mode_raw or arg_mode_raw == "raw") else arg_mode_raw,
                )

        specs.append(
            _SkillCommandSpec(
                name=unique,
                skill_name=raw_name,
                description=description,
                dispatch=dispatch,
            )
        )
    return specs


def resolve_skill_path(
    skill_name: str,
    config: Any = None,
    workspace_dir: "Path | str | None" = None,
) -> str | None:
    """Return the absolute path to SKILL.md for the given skill name.

    Searches all skill sources (bundled, managed, personal, workspace) in order.
    Returns the resolved path string or None if the skill is not found.
    """
    from openclaw.config.paths import resolve_state_dir

    if workspace_dir is None:
        workspace_dir = resolve_state_dir() / "workspace"

    entries = load_workspace_skill_entries(workspace_dir, config)
    skill_name_lower = skill_name.strip().lower()
    for entry in entries:
        if entry.skill.name.lower() == skill_name_lower:
            loc = entry.skill.location
            if loc:
                return str(Path(str(loc)).expanduser().resolve())
    return None


async def sync_skills_to_workspace(
    source_workspace_dir: str | Path,
    target_workspace_dir: str | Path,
    config: Any = None,
) -> None:
    """Copy all skill directories from source workspace into target workspace's skills/ dir.

    Mirrors TS ``syncSkillsToWorkspace`` in agents/skills/workspace.ts.

    Used when creating a sandbox with ``workspaceAccess='none'`` to replicate
    the full skills tree so the agent can read SKILL.md files from its CWD.

    Args:
        source_workspace_dir: Main workspace (e.g. ~/.openclaw/workspace).
        target_workspace_dir: Sandbox workspace (e.g. ~/.openclaw/sandboxes/<slug>).
        config: Optional OpenClawConfig to apply skill filtering.
    """
    import asyncio
    import shutil

    source_dir = Path(str(source_workspace_dir)).expanduser().resolve()
    target_dir = Path(str(target_workspace_dir)).expanduser().resolve()

    if source_dir == target_dir:
        return

    target_skills_dir = target_dir / "skills"

    entries = load_workspace_skill_entries(source_dir, config)

    loop = asyncio.get_event_loop()

    def _do_sync() -> None:
        # Wipe and recreate the target skills dir (mirrors TS: fsp.rm then fsp.mkdir)
        if target_skills_dir.exists():
            shutil.rmtree(target_skills_dir, ignore_errors=True)
        target_skills_dir.mkdir(parents=True, exist_ok=True)

        used_dir_names: set[str] = set()
        for entry in entries:
            skill_path = Path(str(entry.skill.location)).expanduser().resolve()
            base_dir = skill_path.parent
            source_dir_name = base_dir.name.strip()

            if not source_dir_name or source_dir_name in (".", ".."):
                logger.warning("Skipping skill with invalid base dir name: %s", entry.skill.name)
                continue

            # Deduplicate dir name (mirrors TS resolveUniqueSyncedSkillDirName)
            candidate = source_dir_name
            counter = 2
            while candidate.lower() in used_dir_names:
                candidate = f"{source_dir_name}-{counter}"
                counter += 1
            used_dir_names.add(candidate.lower())

            dest = target_skills_dir / candidate
            try:
                shutil.copytree(str(base_dir), str(dest), dirs_exist_ok=True)
            except Exception as exc:
                logger.warning("Failed to copy skill %s to sandbox: %s", entry.skill.name, exc)

    await loop.run_in_executor(None, _do_sync)
    logger.debug(
        "syncSkillsToWorkspace: synced %d skills from %s → %s",
        len(entries),
        source_dir,
        target_dir,
    )
