"""
Frontmatter parsing for SKILL.md files

Matches TypeScript src/agents/skills/frontmatter.ts
"""
from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from .types import (
    OpenClawSkillMetadata,
    SkillInstallSpec,
    SkillInvocationPolicy,
    SkillRequires,
)

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter from markdown (matches TS parseFrontmatterBlock).
    
    Format:
    ---
    name: skill-name
    description: Description
    openclaw:
      always: true
    ---
    
    # Skill Content
    
    Args:
        content: Full markdown content
    
    Returns:
        (frontmatter_dict, body_content)
    """
    # Match ---\n...\n---
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, content, re.DOTALL)
    
    if not match:
        return {}, content
    
    yaml_content = match.group(1)
    body = match.group(2)
    
    try:
        frontmatter = yaml.safe_load(yaml_content) or {}
        if not isinstance(frontmatter, dict):
            logger.warning(f"Frontmatter is not a dict: {type(frontmatter)}")
            return {}, content
    except yaml.YAMLError as e:
        logger.warning(f"Failed to parse YAML frontmatter: {e}")
        return {}, content
    
    return frontmatter, body


def parse_openclaw_metadata(frontmatter: dict[str, Any]) -> OpenClawSkillMetadata | None:
    """
    Extract OpenClaw metadata from frontmatter (matches TS resolveOpenClawMetadata).

    Supports two frontmatter layouts:

    1. Top-level ``openclaw:`` block (canonical):
       ```yaml
       openclaw:
         always: true
       ```

    2. Nested ``metadata.openclaw`` block (used by some skills, e.g. pptx):
       ```yaml
       metadata:
         openclaw:
           always: true
       ```

    TS equivalent: resolveOpenClawManifestBlock({ frontmatter }) which reads
    the ``metadata`` field as a JSON5 string and extracts the ``openclaw`` key.
    In Python, YAML already parses ``metadata`` as a native dict, so we just
    access ``frontmatter["metadata"]["openclaw"]`` directly.

    Args:
        frontmatter: Parsed frontmatter dictionary

    Returns:
        OpenClawSkillMetadata or None
    """
    # Layout 1: top-level ``openclaw:`` (most common)
    openclaw = frontmatter.get("openclaw")

    # Layout 2: ``metadata.openclaw`` (e.g. pptx skill)
    if not openclaw:
        metadata_block = frontmatter.get("metadata")
        if isinstance(metadata_block, dict):
            openclaw = metadata_block.get("openclaw")
        elif isinstance(metadata_block, str):
            # Rare: metadata stored as JSON/JSON5 string — try to parse it
            try:
                import json as _json
                parsed = _json.loads(metadata_block)
                if isinstance(parsed, dict):
                    openclaw = parsed.get("openclaw")
            except Exception:
                pass

    if not openclaw or not isinstance(openclaw, dict):
        return None
    
    # Parse requires section
    requires_raw = openclaw.get("requires", {})
    requires: SkillRequires | None = None
    if isinstance(requires_raw, dict):
        requires = SkillRequires(
            bins=normalize_string_list(requires_raw.get("bins", [])),
            any_bins=normalize_string_list(requires_raw.get("anyBins", []) or requires_raw.get("any_bins", [])),
            env=normalize_string_list(requires_raw.get("env", [])),
            config=normalize_string_list(requires_raw.get("config", [])),
        )
    
    # Parse install specs
    install_raw = openclaw.get("install", [])
    install = []
    if isinstance(install_raw, list):
        for spec in install_raw:
            parsed = parse_install_spec(spec)
            if parsed:
                install.append(parsed)
    
    return OpenClawSkillMetadata(
        always=openclaw.get("always", False),
        skill_key=openclaw.get("skillKey"),
        primary_env=openclaw.get("primaryEnv"),
        emoji=openclaw.get("emoji"),
        homepage=openclaw.get("homepage"),
        os=normalize_string_list(openclaw.get("os", [])),
        requires=requires,
        install=install
    )


def parse_install_spec(spec: Any) -> SkillInstallSpec | None:
    """
    Parse installation spec (matches TS parseInstallSpec).
    
    Args:
        spec: Raw install spec from YAML
    
    Returns:
        SkillInstallSpec or None
    """
    if not spec or not isinstance(spec, dict):
        return None
    
    # Get kind
    kind_raw = spec.get("kind", spec.get("type", ""))
    if not isinstance(kind_raw, str):
        return None
    
    kind = kind_raw.strip().lower()
    if kind not in ("brew", "node", "go", "uv", "download"):
        return None
    
    return SkillInstallSpec(
        kind=kind,
        id=spec.get("id"),
        label=spec.get("label"),
        bins=normalize_string_list(spec.get("bins", [])),
        os=normalize_string_list(spec.get("os", [])),
        formula=spec.get("formula"),
        package=spec.get("package"),
        module=spec.get("module"),
        url=spec.get("url"),
        archive=spec.get("archive"),
        extract=spec.get("extract", False),
        strip_components=spec.get("stripComponents", 0),
        target_dir=spec.get("targetDir"),
    )


def _parse_bool(val: Any, default: bool) -> bool:
    """Coerce *val* to bool exactly as TS parseFrontmatterBool() does.

    Accepts ``True/False``, ``"true"/"yes"/"1"`` (case-insensitive).
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "yes", "1")
    if isinstance(val, (int, float)):
        return bool(val)
    return default


def parse_invocation_policy(frontmatter: dict[str, Any]) -> SkillInvocationPolicy:
    """
    Extract invocation policy from frontmatter.

    Mirrors TS ``resolveSkillInvocationPolicy()`` which reads **flat top-level**
    hyphenated keys:
      - ``user-invocable``          (default: true)
      - ``disable-model-invocation`` (default: false)

    For backward compatibility the old nested ``openclaw.userInvocable`` /
    ``openclaw.disableModelInvocation`` keys are also checked as a fallback.

    Args:
        frontmatter: Parsed frontmatter dictionary

    Returns:
        SkillInvocationPolicy
    """
    # TS canonical: flat top-level hyphenated keys
    if "user-invocable" in frontmatter or "disable-model-invocation" in frontmatter:
        return SkillInvocationPolicy(
            user_invocable=_parse_bool(frontmatter.get("user-invocable", True), True),
            disable_model_invocation=_parse_bool(
                frontmatter.get("disable-model-invocation", False), False
            ),
        )

    # Legacy / nested openclaw block (backward compat)
    openclaw = frontmatter.get("openclaw", {})
    if not isinstance(openclaw, dict):
        return SkillInvocationPolicy()

    return SkillInvocationPolicy(
        user_invocable=_parse_bool(openclaw.get("userInvocable", True), True),
        disable_model_invocation=_parse_bool(
            openclaw.get("disableModelInvocation", False), False
        ),
    )


def normalize_string_list(value: Any) -> list[str]:
    """
    Normalize value to string list (matches TS normalizeStringList).
    
    Args:
        value: Raw value (string, list, or other)
    
    Returns:
        List of strings
    """
    if not value:
        return []
    
    if isinstance(value, list):
        return [str(item).strip() for item in value if item]
    
    if isinstance(value, str):
        # Split by comma
        return [item.strip() for item in value.split(",") if item.strip()]
    
    return []


def extract_description_from_body(body: str, max_length: int = 200) -> str:
    """
    Extract description from markdown body.
    
    Takes the first paragraph as description.
    
    Args:
        body: Markdown body content
        max_length: Maximum description length
    
    Returns:
        Description string
    """
    if not body:
        return ""
    
    # Get first paragraph
    paragraphs = body.strip().split("\n\n")
    if not paragraphs:
        return ""
    
    # Remove markdown headers
    first_para = paragraphs[0].strip()
    first_para = re.sub(r'^#+\s*', '', first_para)
    
    # Limit length
    if len(first_para) > max_length:
        first_para = first_para[:max_length] + "..."
    
    return first_para


def _parse_openclaw_metadata(raw: dict) -> "Any | None":
    """
    Parse ``metadata.openclaw`` section from frontmatter into OpenClawSkillMetadata.

    Mirrors TS OpenClawSkillMetadata extraction in skills loader.
    """
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
    """
    Parse a SKILL.md file into a Skill object.

    Convenience wrapper that combines parse_frontmatter + Skill construction.
    Also extracts ``metadata.openclaw`` section into OpenClawSkillMetadata.

    Args:
        content: Full SKILL.md file content.
        path: File path (used as the skill's ``location``).

    Returns:
        Skill object, or None if parsing fails.
    """
    from .types import Skill

    frontmatter, body = parse_frontmatter(content)
    if not frontmatter:
        return None

    name = frontmatter.get("name") or ""
    description = frontmatter.get("description") or extract_description_from_body(body)
    if not name:
        return None

    # Extract OpenClaw metadata from frontmatter (mirrors TS skills loader)
    metadata_section = frontmatter.get("metadata") or {}
    openclaw_raw = metadata_section.get("openclaw") if isinstance(metadata_section, dict) else None
    metadata = _parse_openclaw_metadata(openclaw_raw) if openclaw_raw else None

    skill = Skill(name=name, description=description, location=path, metadata=metadata)
    # Attach the full body so tests can inspect it
    skill.content = body  # type: ignore[attr-defined]
    return skill
