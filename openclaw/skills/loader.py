"""Skills loader — aligned with TypeScript openclaw/src/agents/skills/workspace.ts.

CANONICAL implementation: ``openclaw.agents.skills``
This module is the *standalone* (lightweight) loader kept for legacy/test consumers.
New callers should import from ``openclaw.agents.skills`` or ``openclaw.skills``
(which re-exports the canonical API).


Skill directory precedence (lowest → highest):
  extra (config + plugins) < bundled < managed < agents-skills-personal
  < agents-skills-project < workspace

Constants match TS defaults:
  maxCandidatesPerRoot=300, maxSkillsLoadedPerSource=200, maxSkillsInPrompt=150
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from .eligibility import SkillEligibilityChecker
from .types import Skill, SkillMetadata
from ..config.paths import resolve_state_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — mirror TS defaults from workspace.ts
# ---------------------------------------------------------------------------

DEFAULT_MAX_CANDIDATES_PER_ROOT = 300
DEFAULT_MAX_SKILLS_LOADED_PER_SOURCE = 200
DEFAULT_MAX_SKILLS_IN_PROMPT = 150
DEFAULT_MAX_SKILLS_PROMPT_CHARS = 30_000
DEFAULT_MAX_SKILL_FILE_BYTES = 256_000


def _resolve_skills_limits(config: dict | None = None) -> dict[str, int]:
    """Return effective skills limits from config — mirrors TS resolveSkillsLimits()."""
    limits = {}
    if isinstance(config, dict):
        limits = config.get("skills", {}).get("limits", {}) or {}
    return {
        "max_candidates_per_root": limits.get("maxCandidatesPerRoot", DEFAULT_MAX_CANDIDATES_PER_ROOT),
        "max_skills_loaded_per_source": limits.get("maxSkillsLoadedPerSource", DEFAULT_MAX_SKILLS_LOADED_PER_SOURCE),
        "max_skills_in_prompt": limits.get("maxSkillsInPrompt", DEFAULT_MAX_SKILLS_IN_PROMPT),
        "max_skills_prompt_chars": limits.get("maxSkillsPromptChars", DEFAULT_MAX_SKILLS_PROMPT_CHARS),
        "max_skill_file_bytes": limits.get("maxSkillFileBytes", DEFAULT_MAX_SKILL_FILE_BYTES),
    }


def compact_skill_paths(skills: list[Skill]) -> list[Skill]:
    """Replace HOME prefix in skill paths with '~' to reduce prompt tokens.

    Mirrors TS compactSkillPaths().
    Example: /Users/alice/.openclaw/skills/foo/SKILL.md
          → ~/.openclaw/skills/foo/SKILL.md
    """
    home = str(Path.home())
    if not home:
        return skills
    prefix = home if home.endswith(os.sep) else home + os.sep
    result: list[Skill] = []
    for skill in skills:
        new_path = "~/" + skill.path[len(prefix):] if skill.path.startswith(prefix) else skill.path
        if new_path != skill.path:
            skill = Skill(
                name=skill.name,
                content=skill.content,
                metadata=skill.metadata,
                source=skill.source,
                path=new_path,
            )
        result.append(skill)
    return result


def resolve_plugin_skill_dirs(config: dict | None, workspace_dir: str | None = None) -> list[str]:
    """Collect enabled plugin skill directories from the plugin manifest registry.

    Mirrors TS resolvePluginSkillDirs().
    """
    if not workspace_dir:
        workspace_dir = os.getcwd()
    try:
        from openclaw.plugins.manifest_registry import load_plugin_manifest_registry  # type: ignore[import]
        registry = load_plugin_manifest_registry(workspace_dir=workspace_dir, config=config)
    except Exception:
        return []

    seen: set[str] = set()
    resolved: list[str] = []

    for record in getattr(registry, "plugins", []):
        if not getattr(record, "skills", None):
            continue
        for raw in record.skills:
            raw_trimmed = raw.strip() if isinstance(raw, str) else ""
            if not raw_trimmed:
                continue
            candidate = str(Path(getattr(record, "root_dir", workspace_dir)) / raw_trimmed)
            if not Path(candidate).exists():
                logger.warning(f"plugin skill path not found ({getattr(record, 'id', '?')}): {candidate}")
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            resolved.append(candidate)
    return resolved


class SkillLoader:
    """Loads skills from multiple sources with TS-aligned precedence and limits."""

    def __init__(self, config: Optional[dict] = None):
        self.skills: dict[str, Skill] = {}
        self.config = config or {}
        self.eligibility_checker = SkillEligibilityChecker(self.config)

    def load_from_directory(
        self,
        directory: Path,
        source: str,
        *,
        max_skill_file_bytes: int = DEFAULT_MAX_SKILL_FILE_BYTES,
        max_skills_loaded: int = DEFAULT_MAX_SKILLS_LOADED_PER_SOURCE,
    ) -> list[Skill]:
        """Load skills from a directory, applying file-size and count limits."""
        skills: list[Skill] = []

        if not directory.exists():
            return skills

        count = 0
        for skill_file in sorted(directory.rglob("SKILL.md")):
            if count >= max_skills_loaded:
                logger.debug(f"Reached skill load limit ({max_skills_loaded}) for {directory}")
                break
            try:
                file_size = skill_file.stat().st_size
                if file_size > max_skill_file_bytes:
                    logger.warning(
                        f"Skipping oversized skill file ({file_size} bytes > {max_skill_file_bytes}): {skill_file}"
                    )
                    continue
                skill = self._load_skill_file(skill_file, source)
                if skill:
                    skills.append(skill)
                    count += 1
            except Exception as e:
                logger.error(f"Failed to load skill from {skill_file}: {e}")

        logger.debug(f"Loaded {len(skills)} skills from {directory} ({source})")
        return skills

    def _load_skill_file(self, file_path: Path, source: str) -> Skill | None:
        """Load a single SKILL.md file."""
        try:
            content = file_path.read_text(encoding="utf-8")
            metadata = self._parse_frontmatter(content)
            if not metadata:
                return None
            skill_content = self._extract_content(content)
            skill_name = metadata.get("name") or file_path.parent.name
            return Skill(
                name=skill_name,
                content=skill_content,
                metadata=SkillMetadata(**metadata),
                source=source,
                path=str(file_path),
            )
        except Exception as e:
            logger.error(f"Error loading skill {file_path}: {e}", exc_info=True)
            return None

    def _parse_frontmatter(self, content: str) -> dict | None:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return None
        try:
            return yaml.safe_load(match.group(1))
        except Exception as e:
            logger.error(f"Failed to parse frontmatter: {e}")
            return None

    def _extract_content(self, content: str) -> str:
        return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL).strip()

    def check_eligibility(self, skill: Skill) -> tuple[bool, str | None]:
        return self.eligibility_checker.check(skill)

    def load_all_skills(
        self,
        workspace_dir: str | None = None,
        skill_filter: list[str] | None = None,
    ) -> dict[str, Skill]:
        """Load skills from all sources with TS-aligned precedence.

        Precedence: extra/plugins < bundled < managed < agents-personal
                  < agents-project < workspace
        """
        limits = _resolve_skills_limits(self.config)
        max_file_bytes = limits["max_skill_file_bytes"]
        max_loaded = limits["max_skills_loaded_per_source"]

        ws_dir = workspace_dir or os.getcwd()

        # Config extra dirs
        extra_dirs_raw: list[str] = []
        if isinstance(self.config, dict):
            extra_dirs_raw = self.config.get("skills", {}).get("load", {}).get("extraDirs", []) or []
        extra_dirs = [d.strip() for d in extra_dirs_raw if isinstance(d, str) and d.strip()]

        # Plugin skill dirs
        plugin_skill_dirs = resolve_plugin_skill_dirs(self.config, ws_dir)
        merged_extra_dirs = [
            *extra_dirs,
            *plugin_skill_dirs,
        ]

        # 1. Extra + plugin skills (lowest precedence)
        merged: dict[str, Skill] = {}
        for raw_dir in merged_extra_dirs:
            expanded = Path(os.path.expanduser(raw_dir))
            for skill in self.load_from_directory(
                expanded, "openclaw-extra",
                max_skill_file_bytes=max_file_bytes,
                max_skills_loaded=max_loaded,
            ):
                merged[skill.name] = skill

        # 2. Bundled skills
        bundled_dir = Path(__file__).parent
        for skill in self.load_from_directory(
            bundled_dir, "openclaw-bundled",
            max_skill_file_bytes=max_file_bytes,
            max_skills_loaded=max_loaded,
        ):
            merged[skill.name] = skill

        # 3. Managed skills (~/.openclaw/skills)
        managed_dir = resolve_state_dir() / "skills"
        for skill in self.load_from_directory(
            managed_dir, "openclaw-managed",
            max_skill_file_bytes=max_file_bytes,
            max_skills_loaded=max_loaded,
        ):
            merged[skill.name] = skill

        # 4. Personal agents skills (~/.agents/skills)
        personal_agents_dir = Path.home() / ".agents" / "skills"
        for skill in self.load_from_directory(
            personal_agents_dir, "agents-skills-personal",
            max_skill_file_bytes=max_file_bytes,
            max_skills_loaded=max_loaded,
        ):
            merged[skill.name] = skill

        # 5. Project agents skills (<workspace>/.agents/skills)
        project_agents_dir = Path(ws_dir) / ".agents" / "skills"
        for skill in self.load_from_directory(
            project_agents_dir, "agents-skills-project",
            max_skill_file_bytes=max_file_bytes,
            max_skills_loaded=max_loaded,
        ):
            merged[skill.name] = skill

        # 6. Workspace skills (<workspace>/skills — highest precedence)
        workspace_skills_dir = Path(ws_dir) / "skills"
        for skill in self.load_from_directory(
            workspace_skills_dir, "openclaw-workspace",
            max_skill_file_bytes=max_file_bytes,
            max_skills_loaded=max_loaded,
        ):
            merged[skill.name] = skill

        self.skills = merged

        # Apply skill filter if provided
        if skill_filter is not None:
            normalized_filter = {s.strip().lower() for s in skill_filter if isinstance(s, str) and s.strip()}
            if normalized_filter:
                self.skills = {k: v for k, v in self.skills.items() if k.lower() in normalized_filter}
            else:
                self.skills = {}

        return self.skills

    def get_eligible_skills(
        self,
        workspace_dir: str | None = None,
        skill_filter: list[str] | None = None,
        compact_paths: bool = True,
    ) -> list[Skill]:
        """Return eligible skills, respecting prompt limits and path compaction.

        Mirrors TS filterSkillEntries() + compactSkillPaths().
        """
        if not self.skills:
            self.load_all_skills(workspace_dir, skill_filter)

        limits = _resolve_skills_limits(self.config)

        eligible: list[Skill] = []
        for skill in self.skills.values():
            is_eligible, reason = self.check_eligibility(skill)
            if is_eligible:
                eligible.append(skill)
            else:
                logger.debug(f"Skill {skill.name} not eligible: {reason}")

        # Enforce maxSkillsInPrompt
        max_in_prompt = limits["max_skills_in_prompt"]
        if len(eligible) > max_in_prompt:
            eligible = eligible[:max_in_prompt]

        if compact_paths:
            eligible = compact_skill_paths(eligible)

        return eligible

    def get_eligible_skills_dict(
        self,
        workspace_dir: str | None = None,
        skill_filter: list[str] | None = None,
        compact_paths: bool = True,
    ) -> dict[str, Skill]:
        """Return eligible skills as a name-keyed dict (legacy interface)."""
        skills_list = self.get_eligible_skills(workspace_dir, skill_filter, compact_paths)
        return {s.name: s for s in skills_list}


# Global skill loader
_global_loader = SkillLoader()


def get_skill_loader() -> SkillLoader:
    """Get global skill loader."""
    return _global_loader


def load_skills_from_dirs(
    dirs: list[str | Path],
    source: str = "external",
    config: dict | None = None,
) -> list[Skill]:
    """Load skills from an explicit list of directories.

    Utility for plugins that want to contribute skills without going through
    the full SkillLoader.load_all_skills() pipeline.
    """
    loader = SkillLoader(config)
    limits = _resolve_skills_limits(config)
    result: list[Skill] = []
    for raw_dir in dirs:
        directory = Path(os.path.expanduser(str(raw_dir)))
        skills = loader.load_from_directory(
            directory,
            source,
            max_skill_file_bytes=limits["max_skill_file_bytes"],
            max_skills_loaded=limits["max_skills_loaded_per_source"],
        )
        for skill in skills:
            loader.skills[skill.name] = skill
        result.extend(skills)
    return result
