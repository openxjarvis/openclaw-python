"""
Skills type definitions

Matches TypeScript src/agents/skills/types.ts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    """
    Skill definition (matches @mariozechner/pi-coding-agent).

    Attributes:
        name: Skill name
        description: Skill description
        location: Path to SKILL.md file (TS: filePath)
        source: Source identifier (bundled, managed, workspace)
        metadata: OpenClaw-specific metadata extracted from frontmatter
    """
    name: str
    description: str
    location: str
    source: str = "unknown"
    metadata: "OpenClawSkillMetadata | None" = None

    @property
    def file_path(self) -> str:
        """Alias for ``location`` — matches TS ``skill.filePath``."""
        return self.location


@dataclass
class LoadSkillsResult:
    """Result of loading skills from a directory."""
    skills: list["Skill"] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SkillRequires:
    """
    Skill requirements (matches TS SkillRequires).

    Attributes:
        bins: All of these binaries must be on PATH
        any_bins: At least one of these binaries must be on PATH
        env: All of these environment variables must be set
        config: All of these config keys must be present
    """
    bins: list[str] = field(default_factory=list)
    any_bins: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    config: list[str] = field(default_factory=list)


@dataclass
class SkillInstallSpec:
    """
    Installation specification (matches TS SkillInstallSpec).
    
    Attributes:
        kind: Installation kind (brew|node|go|uv|download)
        id: Optional install ID
        label: Display label
        bins: Required binaries
        os: Supported operating systems
        formula: Homebrew formula name
        package: Package name (npm/pip/etc)
        module: Module name
        url: Download URL
        archive: Archive name
        extract: Extract archive
        strip_components: Strip path components
        target_dir: Target directory
    """
    kind: str  # brew | node | go | uv | download
    id: str | None = None
    label: str | None = None
    bins: list[str] = field(default_factory=list)
    os: list[str] = field(default_factory=list)
    formula: str | None = None
    package: str | None = None
    module: str | None = None
    url: str | None = None
    archive: str | None = None
    extract: bool = False
    strip_components: int = 0
    target_dir: str | None = None


@dataclass
class OpenClawSkillMetadata:
    """
    OpenClaw-specific skill metadata (matches TS OpenClawSkillMetadata).
    
    Attributes:
        always: Always include this skill
        skill_key: Unique skill key
        primary_env: Primary environment variable
        emoji: Skill emoji icon
        homepage: Skill homepage URL
        os: Supported operating systems
        requires: Requirements (bins, env, config)
        install: Installation specifications
    """
    always: bool = False
    skill_key: str | None = None
    primary_env: str | None = None
    emoji: str | None = None
    homepage: str | None = None
    os: list[str] = field(default_factory=list)
    requires: "SkillRequires | None" = None  # TS: requires (bins/anyBins/env/config)
    install: list[SkillInstallSpec] = field(default_factory=list)


@dataclass
class SkillInvocationPolicy:
    """
    Skill invocation policy (matches TS SkillInvocationPolicy).
    
    Attributes:
        user_invocable: Can be invoked by user
        disable_model_invocation: Disable model invocation
    """
    user_invocable: bool = True
    disable_model_invocation: bool = False


@dataclass
class SkillCommandDispatch:
    """
    Skill command dispatch specification (matches TS SkillCommandDispatchSpec).
    
    Attributes:
        kind: Dispatch kind (currently only "tool")
        tool_name: Name of tool to invoke
        arg_mode: Argument mode ("raw" | "parsed")
    """
    kind: str = "tool"  # Currently only "tool"
    tool_name: str = ""
    arg_mode: str = "parsed"  # "raw" | "parsed"


@dataclass
class SkillCommandSpec:
    """
    Skill command specification (matches TS SkillCommandSpec).
    
    Attributes:
        name: Command name (e.g., /summarize)
        skill_name: Corresponding skill name
        description: Command description
        dispatch: Optional dispatch configuration
        prompt_template: Optional prompt template for the command
        source_file_path: Optional path to source file defining the command
    """
    name: str
    skill_name: str
    description: str
    dispatch: SkillCommandDispatch | None = None
    prompt_template: str | None = None
    source_file_path: str | None = None


@dataclass
class SkillExposure:
    """
    Skill exposure configuration (matches TS SkillExposure).

    Attributes:
        include_in_runtime_registry: Include in runtime tool registry.
        include_in_available_skills_prompt: Include in available-skills prompt.
        user_invocable: Whether users can invoke this skill directly.
    """
    include_in_runtime_registry: bool | None = None
    include_in_available_skills_prompt: bool | None = None
    user_invocable: bool | None = None


@dataclass
class SkillEntry:
    """
    Skill with metadata (matches TS SkillEntry).
    
    Attributes:
        skill: The skill definition
        frontmatter: Parsed YAML frontmatter
        metadata: OpenClaw-specific metadata
        invocation: Invocation policy
        source: Source identifier (bundled, managed, workspace, plugin)
        source_dir: Directory this skill was loaded from
        exposure: Exposure configuration (where this skill surfaces)
        skill_key: Unique key for this skill (used for config lookups)
    """
    skill: Skill
    frontmatter: dict[str, Any] = None  # type: ignore[assignment]
    metadata: OpenClawSkillMetadata | None = None
    invocation: SkillInvocationPolicy | None = None
    source: str = "unknown"
    source_dir: str = ""
    exposure: SkillExposure | None = None

    def __post_init__(self) -> None:
        if self.frontmatter is None:
            self.frontmatter = {}

    @property
    def skill_key(self) -> str:
        """Unique key for config lookups (name-based)."""
        return self.skill.name


@dataclass
class SkillEligibilityContext:
    """
    Context for skill eligibility checking.

    Extends the minimal TS SkillEligibilityContext (which only has ``remote``)
    with local runtime information needed by the Python eligibility engine.

    Attributes:
        remote: Remote environment info (platforms, binary checks) — TS field
        platform: Local OS platform string (darwin | linux | win32)
        available_bins: Set of binaries found on PATH
        env_vars: Dictionary of environment variables
    """
    remote: dict[str, Any] | None = None
    platform: str = ""
    available_bins: "set[str]" = None  # type: ignore[assignment]
    env_vars: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.available_bins is None:
            self.available_bins = set()
        if self.env_vars is None:
            self.env_vars = {}


@dataclass
class SkillSnapshot:
    """
    Skills snapshot for prompt (matches TS SkillSnapshot).

    Attributes:
        prompt: Formatted skills prompt
        skills: List of skill info dicts (name, primaryEnv?, requiredEnv?)
        skill_filter: Normalized agent-level filter used to build this snapshot; None = unrestricted
        resolved_skills: Resolved skill objects (for fast re-use without disk I/O)
        version: Snapshot version (bumped by the skills file watcher)
    """
    prompt: str
    skills: list[dict[str, Any]]
    skill_filter: list[str] | None = None
    resolved_skills: list[Skill] | None = None
    version: int | None = None


@dataclass
class SkillsInstallPreferences:
    """
    Installation preferences (matches TS SkillsInstallPreferences).
    
    Attributes:
        prefer_brew: Prefer Homebrew installation
        node_manager: Node package manager (npm|pnpm|yarn|bun)
    """
    prefer_brew: bool = True
    node_manager: str = "npm"  # npm | pnpm | yarn | bun
