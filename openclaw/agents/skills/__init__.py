"""
Skills system for OpenClaw — mirrors TS agents/skills/
"""
from .loader import compact_skill_paths, format_skills_for_prompt, load_skills_from_dir
from .types import (
    OpenClawSkillMetadata,
    Skill,
    SkillEligibilityContext,
    SkillEntry,
    SkillRequires,
    SkillSnapshot,
)
from .workspace import (
    build_workspace_skill_command_specs,
    build_workspace_skill_snapshot,
    build_workspace_skills_prompt,
    filter_skill_entries,
    load_workspace_skill_entries,
    normalize_skill_filter,
    resolve_skill_path,
    resolve_skills_prompt_for_run,
)
from .config import should_include_skill
from .env_overrides import (
    apply_skill_env_overrides,
    apply_skill_env_overrides_from_snapshot,
)

load_skill_entries = load_workspace_skill_entries

__all__ = [
    "Skill",
    "SkillEntry",
    "SkillEligibilityContext",
    "SkillRequires",
    "SkillSnapshot",
    "OpenClawSkillMetadata",
    "compact_skill_paths",
    "format_skills_for_prompt",
    "load_skills_from_dir",
    "load_skill_entries",
    "load_workspace_skill_entries",
    "build_workspace_skill_command_specs",
    "build_workspace_skills_prompt",
    "build_workspace_skill_snapshot",
    "filter_skill_entries",
    "normalize_skill_filter",
    "resolve_skill_path",
    "resolve_skills_prompt_for_run",
    "should_include_skill",
    "apply_skill_env_overrides",
    "apply_skill_env_overrides_from_snapshot",
]
