"""
Skill loader — mirrors TS loadSkillsFromDir / loadSkillEntries

Loads skills from directories with file-size enforcement.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .frontmatter import (
    extract_description_from_body,
    parse_frontmatter,
    parse_invocation_policy,
    parse_openclaw_metadata,
)
from .types import Skill, SkillEntry

logger = logging.getLogger(__name__)

DEFAULT_MAX_SKILL_FILE_BYTES = 256_000  # 256 KB — matches TS


class SkillLoader:
    """Skill loader class for managing skills from multiple directories"""

    def __init__(self, bundled_skills_dir: Path | str | None = None):
        self.bundled_skills_dir = Path(bundled_skills_dir) if bundled_skills_dir else None
        self.skills: list[Skill] = []

    def load_all_skills(self) -> None:
        if self.bundled_skills_dir:
            bundled = load_skills_from_dir(self.bundled_skills_dir, source="bundled")
            self.skills.extend(bundled)
            logger.info(f"Loaded {len(bundled)} bundled skills")

    def get_skills(self) -> list[Skill]:
        return self.skills


def _list_child_directories(directory: Path) -> list[str]:
    """List immediate child directories, skipping hidden/node_modules — mirrors TS listChildDirectories."""
    try:
        dirs: list[str] = []
        for entry in directory.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.name == "node_modules":
                continue
            if entry.is_dir() or (entry.is_symlink() and entry.resolve().is_dir()):
                dirs.append(entry.name)
        return dirs
    except Exception:
        return []


def _resolve_nested_skills_root(
    directory: Path,
    max_entries_to_scan: int = 100,
) -> tuple[Path, str | None]:
    """Detect nested skills root at dir/skills/ — mirrors TS resolveNestedSkillsRoot.

    Returns (base_dir, optional_note).
    """
    nested = directory / "skills"
    try:
        if not nested.exists() or not nested.is_dir():
            return directory, None
    except Exception:
        return directory, None

    nested_dirs = _list_child_directories(nested)
    to_scan = nested_dirs[:min(len(nested_dirs), max_entries_to_scan)]

    for name in to_scan:
        skill_md = nested / name / "SKILL.md"
        if skill_md.exists():
            return nested, f"Detected nested skills root at {nested}"

    return directory, None


def load_skills_from_dir(
    directory: Path | str,
    source: str = "workspace",
    *,
    max_skill_file_bytes: int = DEFAULT_MAX_SKILL_FILE_BYTES,
    max_skills_loaded: int = 200,
    max_candidates_per_root: int = 300,
) -> list[Skill]:
    """Load skills from directory with size + count enforcement — mirrors TS loadSkills inner."""
    if isinstance(directory, str):
        directory = Path(directory)

    if not directory.exists():
        return []
    if not directory.is_dir():
        return []

    base_dir, _note = _resolve_nested_skills_root(directory, max_candidates_per_root)

    root_skill_md = base_dir / "SKILL.md"
    if root_skill_md.exists():
        try:
            size = root_skill_md.stat().st_size
            if size > max_skill_file_bytes:
                logger.warning("Skipping skills root due to oversized SKILL.md: %s (%d bytes)", base_dir, size)
                return []
        except Exception:
            return []

        skill = load_skill_from_file(root_skill_md, source)
        return [skill] if skill else []

    child_dirs = _list_child_directories(base_dir)
    if len(child_dirs) > max_candidates_per_root:
        logger.warning("Skills root looks suspiciously large, truncating: %s (%d dirs)", base_dir, len(child_dirs))

    limited = sorted(child_dirs)[:max_skills_loaded]
    skills: list[Skill] = []

    for name in limited:
        skill_md = base_dir / name / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            size = skill_md.stat().st_size
            if size > max_skill_file_bytes:
                logger.warning("Skipping oversized SKILL.md: %s (%d bytes)", skill_md, size)
                continue
        except Exception:
            continue

        try:
            skill = load_skill_from_file(skill_md, source)
            if skill:
                skills.append(skill)
        except Exception as e:
            logger.warning("Failed to load skill from %s: %s", name, e)

        if len(skills) >= max_skills_loaded:
            break

    return skills


def load_skill_entries_from_dir(
    directory: Path | str,
    source: str = "workspace",
    *,
    max_skill_file_bytes: int = DEFAULT_MAX_SKILL_FILE_BYTES,
    max_skills_loaded: int = 200,
    max_candidates_per_root: int = 300,
) -> list[SkillEntry]:
    """Load skill entries (with metadata) from directory with size enforcement."""
    if isinstance(directory, str):
        directory = Path(directory)

    if not directory.exists():
        return []

    base_dir, _note = _resolve_nested_skills_root(directory, max_candidates_per_root)

    root_skill_md = base_dir / "SKILL.md"
    if root_skill_md.exists():
        try:
            size = root_skill_md.stat().st_size
            if size > max_skill_file_bytes:
                logger.warning("Skipping skills root due to oversized SKILL.md: %s (%d bytes)", base_dir, size)
                return []
        except Exception:
            return []

        entry = load_skill_entry_from_file(root_skill_md, source)
        return [entry] if entry else []

    child_dirs = _list_child_directories(base_dir)
    limited = sorted(child_dirs)[:max_skills_loaded]
    entries: list[SkillEntry] = []

    for name in limited:
        skill_md = base_dir / name / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            size = skill_md.stat().st_size
            if size > max_skill_file_bytes:
                logger.warning("Skipping oversized SKILL.md: %s (%d bytes)", skill_md, size)
                continue
        except Exception:
            continue

        try:
            entry = load_skill_entry_from_file(skill_md, source)
            if entry:
                entries.append(entry)
        except Exception as e:
            logger.warning("Failed to load skill entry from %s: %s", name, e)

        if len(entries) >= max_skills_loaded:
            break

    return entries


def load_skill_from_file(
    file_path: Path,
    source: str = "workspace"
) -> Skill | None:
    """Load skill from SKILL.md file."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return None

    frontmatter, body = parse_frontmatter(content)

    name = frontmatter.get("name")
    if not name or not isinstance(name, str):
        name = file_path.parent.name

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        description = ""

    if not description and body:
        description = extract_description_from_body(body)

    return Skill(
        name=name.strip(),
        description=description.strip(),
        location=str(file_path),
        source=source,
    )


def load_skill_entry_from_file(
    file_path: Path,
    source: str = "workspace"
) -> SkillEntry | None:
    """Load skill entry (with metadata) from SKILL.md file."""
    skill = load_skill_from_file(file_path, source)
    if not skill:
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return None

    frontmatter, _ = parse_frontmatter(content)
    metadata = parse_openclaw_metadata(frontmatter)
    invocation = parse_invocation_policy(frontmatter)

    return SkillEntry(
        skill=skill,
        frontmatter=frontmatter,
        metadata=metadata,
        invocation=invocation,
        source=source,
        source_dir=str(file_path.parent.parent),
    )


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def compact_skill_paths(skills: list[Skill]) -> list[Skill]:
    """Replace home directory prefix with ~ in skill paths — mirrors TS compactSkillPaths.

    Saves ~5-6 tokens per skill path.
    """
    home = os.path.expanduser("~")
    if not home:
        return skills

    prefix = home if home.endswith(os.sep) else home + os.sep

    compacted: list[Skill] = []
    for s in skills:
        loc = s.location
        if loc.startswith(prefix):
            loc = "~/" + loc[len(prefix):]
        compacted.append(Skill(
            name=s.name,
            description=s.description,
            location=loc,
            source=s.source,
            metadata=s.metadata,
        ))
    return compacted


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """Format skills for system prompt XML block — mirrors TS formatSkillsForPrompt."""
    if not skills:
        return ""

    lines = [
        "",
        "",
        "The following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill "
        "directory (parent of SKILL.md / dirname of the path) and use that absolute "
        "path in tool commands.",
        "",
        "<available_skills>",
    ]

    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{_escape_xml(skill.name)}</name>")
        if skill.description:
            lines.append(f"    <description>{_escape_xml(skill.description)}</description>")
        if skill.location:
            lines.append(f"    <location>{_escape_xml(str(skill.location))}</location>")
        lines.append("  </skill>")

    lines.append("</available_skills>")

    return "\n".join(lines)


def _parse_openclaw_metadata(raw: dict) -> "Any | None":
    """Parse ``metadata.openclaw`` section from frontmatter into OpenClawSkillMetadata."""
    from .types import OpenClawSkillMetadata, SkillRequires, SkillInstallSpec

    if not isinstance(raw, dict):
        return None

    requires_raw = raw.get("requires")
    requires = None
    if isinstance(requires_raw, dict):
        requires = SkillRequires(
            bins=requires_raw.get("bins") or [],
            any_bins=requires_raw.get("anyBins") or requires_raw.get("any_bins") or [],
            env=requires_raw.get("env") or [],
            config=requires_raw.get("config") or [],
        )

    install_raw = raw.get("install") or []
    install = []
    for spec in install_raw:
        if isinstance(spec, dict) and "kind" in spec:
            install.append(SkillInstallSpec(
                kind=spec["kind"],
                id=spec.get("id"),
                label=spec.get("label"),
                bins=spec.get("bins") or [],
                os=spec.get("os") or [],
                formula=spec.get("formula"),
                package=spec.get("package"),
                module=spec.get("module"),
                url=spec.get("url"),
                archive=spec.get("archive"),
                extract=bool(spec.get("extract", False)),
                strip_components=int(spec.get("stripComponents") or 0),
                target_dir=spec.get("targetDir"),
            ))

    return OpenClawSkillMetadata(
        always=bool(raw.get("always", False)),
        skill_key=raw.get("skillKey") or raw.get("skill_key"),
        primary_env=raw.get("primaryEnv") or raw.get("primary_env"),
        emoji=raw.get("emoji"),
        homepage=raw.get("homepage"),
        os=raw.get("os") or [],
        requires=requires,
        install=install,
    )


def parse_skill_frontmatter(content: str, path: str) -> "Any | None":
    """Parse a SKILL.md file into a Skill object."""
    from .types import Skill

    frontmatter, body = parse_frontmatter(content)
    if not frontmatter:
        return None

    name = frontmatter.get("name") or ""
    description = frontmatter.get("description") or extract_description_from_body(body)
    if not name:
        return None

    metadata_section = frontmatter.get("metadata") or {}
    openclaw_raw = metadata_section.get("openclaw") if isinstance(metadata_section, dict) else None
    metadata = _parse_openclaw_metadata(openclaw_raw) if openclaw_raw else None

    skill = Skill(name=name, description=description, location=path, metadata=metadata)
    skill.content = body  # type: ignore[attr-defined]
    return skill
