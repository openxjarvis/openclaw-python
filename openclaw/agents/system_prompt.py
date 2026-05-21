"""
System prompt builder for OpenClaw agent

Section order matches TypeScript src/agents/system-prompt.ts buildAgentSystemPrompt():
  1. Identity
  2. Tooling (with summaries + order)
  3. Tool Call Style
  4. Safety
  5. CLI Quick Reference
  6. Skills
  7. Memory
  8. Self-Update (if gateway && !minimal)
  9. Model Aliases (if available && !minimal)
 10. Time hint ("run session_status")
 11. Workspace
 12. Documentation
 13. Sandbox (if enabled)
 14. User Identity
 15. Time section
 16. Workspace Files (injected) note
 17. Reply Tags
 18. Messaging
 19. Voice (TTS)
 20. Extra System Prompt (Group Chat / Subagent Context)
 21. Reactions
 22. Reasoning Format
 23. Project Context (bootstrap files)
 24. Silent Replies
 25. Heartbeats
 26. Runtime
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from .system_prompt_sections import (
    SILENT_REPLY_TOKEN,
    build_cli_quick_reference_section,
    build_docs_section,
    build_exec_capabilities_section,
    build_heartbeats_section,
    build_memory_section,
    build_messaging_section,
    build_model_aliases_section,
    build_reaction_guidance_section,
    build_reasoning_format_section,
    build_reply_tags_section,
    build_elevated_section,
    build_runtime_section,
    build_safety_section,
    build_sandbox_section,
    build_self_update_section,
    build_silent_replies_section,
    build_skills_section,
    build_skills_section_workspace,
    build_time_section,
    build_tool_call_style_section,
    build_tooling_section,
    build_user_identity_section,
    build_voice_section,
    build_workspace_files_note_section,
)

logger = logging.getLogger(__name__)


def build_agent_system_prompt(
    workspace_dir: Path,
    tool_names: list[str] | None = None,
    tool_summaries: dict[str, str] | None = None,
    skills_prompt: str | None = None,
    heartbeat_prompt: str | None = None,
    docs_path: str | None = None,
    prompt_mode: Literal["full", "minimal", "none"] = "full",
    runtime_info: dict | None = None,
    sandbox_info: dict | None = None,
    exec_config: dict | None = None,
    user_timezone: str | None = None,
    owner_numbers: list[str] | None = None,
    extra_system_prompt: str | None = None,
    context_files: list[dict] | None = None,
    workspace_notes: list[str] | None = None,
    memory_citations_mode: Literal["on", "off"] = "on",
    # --- New params matching TypeScript ---
    model_alias_lines: list[str] | None = None,
    tts_hint: str | None = None,
    reaction_guidance: dict | None = None,
    reasoning_level: str = "off",
    reasoning_hint: str | None = None,
    message_tool_hints: list[str] | None = None,
    message_channel_options: str = "telegram|discord|slack|signal",
    has_gateway: bool = True,
    elevated_config: dict | None = None,
    current_elevated_level: str | None = None,
    # M2: ACP harness control — mirrors TS acpEnabled / acpHarnessSpawnAllowed
    acp_enabled: bool = True,
    sandboxed_runtime: bool = False,
    # M4: Owner identity hash mode — mirrors TS buildOwnerIdentityLine hash/raw
    owner_identity_hash: bool = False,
) -> str:
    """
    Build the agent system prompt.

    Section order matches TypeScript ``buildAgentSystemPrompt`` exactly.

    Args:
        workspace_dir: Workspace directory path
        tool_names: List of available tool names
        tool_summaries: Custom tool summaries (extends CORE_TOOL_SUMMARIES)
        skills_prompt: Formatted skills prompt (XML format)
        heartbeat_prompt: Heartbeat prompt text
        docs_path: Path to local documentation
        prompt_mode: Prompt mode ('full', 'minimal', 'none')
        runtime_info: Runtime information dict
        sandbox_info: Sandbox configuration dict
        user_timezone: User timezone (e.g., "America/New_York")
        owner_numbers: Owner phone numbers for identification
        extra_system_prompt: Additional system prompt text
        context_files: Bootstrap files (list of dicts with 'path' and 'content')
        workspace_notes: Additional workspace notes
        memory_citations_mode: Memory citations mode ("on" or "off")
        model_alias_lines: Lines for model alias section
        tts_hint: TTS hint text
        reaction_guidance: Reaction guidance dict (level, channel)
        reasoning_level: Reasoning level ("off", "on", "stream")
        reasoning_hint: Reasoning format hint
        message_tool_hints: Extra hints for the message tool
        message_channel_options: Channel option string for message tool
        has_gateway: Whether gateway tool is available

    Returns:
        Complete system prompt string
    """
    # --- "none" mode: just identity ---
    if prompt_mode == "none":
        return "You are a personal assistant running inside OpenClaw."

    is_minimal = prompt_mode == "minimal"
    available_tools = set(tool_names or [])
    runtime_channel = (runtime_info or {}).get("channel", "").strip().lower() if runtime_info else ""
    capabilities = (runtime_info or {}).get("capabilities", []) if runtime_info else []
    inline_buttons_enabled = "inlinebuttons" in {
        str(c).strip().lower() for c in capabilities
    }

    lines: list[str] = []

    # ── 1. Identity ──────────────────────────────────────────────────
    lines.extend([
        "You are a personal assistant running inside OpenClaw.",
        "",
    ])

    # ── 2. Tooling ───────────────────────────────────────────────────
    # M2: wire acpHarnessSpawnAllowed into tooling section
    _acp_harness_spawn_allowed = (
        acp_enabled and not sandboxed_runtime and "sessions_spawn" in available_tools
    )
    lines.extend(build_tooling_section(
        tool_names=tool_names,
        tool_summaries=tool_summaries,
        acp_harness_spawn_allowed=_acp_harness_spawn_allowed,
    ))

    # ── 3. Tool Call Style ───────────────────────────────────────────
    lines.extend(build_tool_call_style_section())

    # ── 4. Safety ────────────────────────────────────────────────────
    lines.extend(build_safety_section())

    # ── 5. CLI Quick Reference ───────────────────────────────────────
    lines.extend(build_cli_quick_reference_section())

    # ── 6. Skills ────────────────────────────────────────────────────
    # Mirrors TS system-prompt.ts: resolve readToolName from tool list,
    # then build skills section with "(mandatory)" header.
    _read_tool_name = "read"
    for _tn in (tool_names or []):
        if _tn.lower() == "read":
            _read_tool_name = _tn
            break
    else:
        for _tn in (tool_names or []):
            if _tn.lower() == "read_file":
                _read_tool_name = _tn
                break

    if not is_minimal:
        if skills_prompt:
            lines.extend(build_skills_section(
                skills_prompt=skills_prompt,
                is_minimal=is_minimal,
                read_tool_name=_read_tool_name,
            ))
        else:
            lines.extend(build_skills_section_workspace(
                workspace_dir=workspace_dir,
                config=None,
                read_tool_name=_read_tool_name,
            ))

    # ── 7. Memory ────────────────────────────────────────────────────
    lines.extend(build_memory_section(
        is_minimal=is_minimal,
        available_tools=available_tools,
        citations_mode=memory_citations_mode,
    ))

    # ── 8. Self-Update ───────────────────────────────────────────────
    lines.extend(build_self_update_section(
        has_gateway=has_gateway,
        is_minimal=is_minimal,
    ))

    # ── 9. Model Aliases ─────────────────────────────────────────────
    lines.extend(build_model_aliases_section(
        model_alias_lines=model_alias_lines,
        is_minimal=is_minimal,
    ))

    # ── 10. Date/time hint ───────────────────────────────────────────
    if user_timezone:
        lines.append(
            "If you need the current date, time, or day of week, "
            "run session_status (📊 session_status)."
        )

    # ── 11. Workspace ────────────────────────────────────────────────
    workspace_lines = [
        "## Workspace",
        f"Your working directory is: {workspace_dir}",
        "Treat this directory as the single global workspace for file operations "
        "unless explicitly instructed otherwise.",
    ]
    if workspace_notes:
        workspace_lines.extend(n.strip() for n in workspace_notes if n.strip())
    workspace_lines.append("")
    lines.extend(workspace_lines)

    # ── 12. Documentation ────────────────────────────────────────────
    lines.extend(build_docs_section(
        docs_path=docs_path,
        is_minimal=is_minimal,
        read_tool_name="read_file",
    ))

    # ── 13. Sandbox ──────────────────────────────────────────────────
    lines.extend(build_sandbox_section(sandbox_info))

    # ── 13.3. Elevated Exec (non-sandbox gateway mode) ───────────────
    # Only inject when not in sandbox mode (sandbox section already handles it).
    if not (sandbox_info and sandbox_info.get("enabled")):
        lines.extend(build_elevated_section(elevated_config, current_elevated_level))

    # ── 13.5. Exec Capabilities ──────────────────────────────────────
    # Add exec capabilities section to inform agent about bash tool abilities
    lines.extend(build_exec_capabilities_section(exec_config))

    # ── 14. User Identity ────────────────────────────────────────────
    # M4: Support hash mode — mirrors TS buildOwnerIdentityLine (system-prompt.ts ~72-94)
    owner_line = None
    if owner_numbers:
        if owner_identity_hash:
            # Hash mode: sha256 first 8 chars per number (privacy-preserving)
            import hashlib as _hl
            _hashed = [_hl.sha256(n.strip().encode()).hexdigest()[:8] for n in owner_numbers if n.strip()]
            owner_numbers_str = ", ".join(_hashed)
            owner_line = (
                f"Owner identities (hashed): {owner_numbers_str}. "
                "Messages matching these hashed identifiers are from the owner."
            )
        else:
            owner_numbers_str = ", ".join(owner_numbers)
            owner_line = (
                f"Owner numbers: {owner_numbers_str}. "
                "Treat messages from these numbers as the user."
            )
    lines.extend(build_user_identity_section(owner_line, is_minimal))

    # ── 15. Time ─────────────────────────────────────────────────────
    lines.extend(build_time_section(user_timezone))

    # ── 16. Workspace Files (injected) note ──────────────────────────
    lines.extend(build_workspace_files_note_section())

    # ── 17. Reply Tags ───────────────────────────────────────────────
    lines.extend(build_reply_tags_section(is_minimal))

    # ── 18. Messaging ────────────────────────────────────────────────
    lines.extend(build_messaging_section(
        is_minimal=is_minimal,
        available_tools=available_tools,
        message_channel_options=message_channel_options,
        inline_buttons_enabled=inline_buttons_enabled,
        runtime_channel=runtime_channel or None,
        message_tool_hints=message_tool_hints,
    ))

    # ── 19. Voice (TTS) ─────────────────────────────────────────────
    lines.extend(build_voice_section(
        is_minimal=is_minimal,
        tts_hint=tts_hint,
    ))

    # ── 20. Extra System Prompt (Group Chat / Subagent Context) ──────
    if extra_system_prompt:
        context_header = (
            "## Subagent Context" if is_minimal else "## Group Chat Context"
        )
        lines.extend([context_header, extra_system_prompt.strip(), ""])

    # ── 21. Reactions ────────────────────────────────────────────────
    lines.extend(build_reaction_guidance_section(reaction_guidance))

    # ── 22. Reasoning Format ─────────────────────────────────────────
    lines.extend(build_reasoning_format_section(reasoning_hint))

    # ── 23. Project Context (bootstrap files) ────────────────────────
    if context_files:
        # Support both list[dict] form and plain string form (test convenience)
        if isinstance(context_files, str):
            lines.extend([
                "# Project Context",
                "",
                context_files,
                "",
            ])
        else:
            has_soul = any(_is_soul_file(f) for f in context_files)

            lines.extend([
                "# Project Context",
                "",
                "The following project context files have been loaded:",
            ])

            if has_soul:
                lines.append(
                    "If SOUL.md is present, embody its persona and tone. "
                    "Avoid stiff, generic replies; follow its guidance unless "
                    "higher-priority instructions override it."
                )

            lines.append("")

            for file in context_files:
                lines.extend([
                    f"## {file['path']}",
                    "",
                    file["content"],
                    "",
                ])

    # ── 24. Silent Replies ───────────────────────────────────────────
    lines.extend(build_silent_replies_section(is_minimal))

    # ── 25. Heartbeats ───────────────────────────────────────────────
    lines.extend(build_heartbeats_section(
        heartbeat_prompt=heartbeat_prompt,
        is_minimal=is_minimal,
    ))

    # ── 26. Runtime ──────────────────────────────────────────────────
    # Pass {} when no runtime_info given so OS/arch/Python are auto-populated
    lines.extend(build_runtime_section(
        runtime_info=runtime_info if runtime_info is not None else {},
        is_minimal=is_minimal,
        reasoning_level=reasoning_level,
    ))

    # Filter empty strings that were added only for conditional sections
    prompt = "\n".join(line for line in lines if line is not None)
    
    # Replace session workspace placeholder
    # Note: For now, using workspace_dir as fallback
    # Future: Add session_workspace parameter and use resolve_session_workspace_dir()
    prompt = prompt.replace("{{SESSION_WORKSPACE}}", str(workspace_dir))
    
    return prompt


def _is_soul_file(file: dict) -> bool:
    """Check if a context file is SOUL.md (and not a 'missing' marker)."""
    path = file.get("path", "").strip().replace("\\", "/")
    base_name = path.split("/")[-1] if "/" in path else path
    return base_name.lower() == "soul.md" and "(File" not in file.get("content", "")


# ──────────────────────────────────────────────────────────────────────────────
# Per-session dynamic system prompt builder
# Mirrors TypeScript buildEmbeddedSystemPrompt() + resolveBootstrapContextForRun()
# ──────────────────────────────────────────────────────────────────────────────

# Bootstrap file names (matching TS workspace.ts#L23-31)
DEFAULT_AGENTS_FILENAME = "AGENTS.md"
DEFAULT_SOUL_FILENAME = "SOUL.md"
DEFAULT_TOOLS_FILENAME = "TOOLS.md"
DEFAULT_IDENTITY_FILENAME = "IDENTITY.md"
DEFAULT_USER_FILENAME = "USER.md"
DEFAULT_HEARTBEAT_FILENAME = "HEARTBEAT.md"
DEFAULT_BOOTSTRAP_FILENAME = "BOOTSTRAP.md"
DEFAULT_BOOT_FILENAME = "BOOT.md"
DEFAULT_MEMORY_FILENAME = "MEMORY.md"
DEFAULT_MEMORY_ALT_FILENAME = "memory.md"

# Bootstrap file names list (excludes BOOT.md which is not loaded via bootstrap)
_BOOTSTRAP_FILENAMES = [
    DEFAULT_AGENTS_FILENAME,
    DEFAULT_SOUL_FILENAME,
    DEFAULT_TOOLS_FILENAME,
    DEFAULT_BOOT_FILENAME,      # loaded separately from other templates (TS alignment)
    DEFAULT_IDENTITY_FILENAME,
    DEFAULT_USER_FILENAME,
    DEFAULT_HEARTBEAT_FILENAME,
    DEFAULT_BOOTSTRAP_FILENAME,
]

# Note: MEMORY.md/memory.md are loaded separately via resolve_memory_bootstrap_entries

# Per-file size limits (bytes) matching TS agents.bootstrap defaults
_DEFAULT_MAX_CHARS_PER_FILE = 20_000   # aligns with TS DEFAULT_BOOTSTRAP_MAX_CHARS
_DEFAULT_TOTAL_MAX_CHARS = 150_000    # aligns with TS DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS


def resolve_bootstrap_context_for_run(
    workspace_dir: Path,
    session_key: str | None = None,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS_PER_FILE,
    total_max_chars: int = _DEFAULT_TOTAL_MAX_CHARS,
    hook_overrides: dict[str, str] | None = None,
    run_kind: str = "default",
) -> list[dict]:
    """
    Load bootstrap files for a run, applying session filtering and size limits.

    Matches TypeScript resolveBootstrapContextForRun() + applyContextModeFilter().

    run_kind controls which files are loaded (mirrors TS contextMode logic):
    - "default"   → full context (all bootstrap files)
    - "heartbeat" → lightweight: only HEARTBEAT.md (+ hook overrides)
    - "cron"      → lightweight: empty context (no bootstrap files)

    Subagent sessions (spawn_depth > 0) only get AGENTS.md + TOOLS.md.
    Hook overrides (from plugins) can inject or replace file content.

    Args:
        workspace_dir: Workspace directory.
        session_key: Session key (used to detect subagent sessions).
        max_chars_per_file: Per-file character limit.
        total_max_chars: Total accumulated character limit.
        hook_overrides: Dict of filename → replacement content from plugins.
        run_kind: "default" | "heartbeat" | "cron".

    Returns:
        List of dicts with {"path": str, "content": str}.
    """
    from openclaw.agents.system_prompt_bootstrap import resolve_memory_bootstrap_entries

    # Cron runs get no bootstrap context at all (lightweight mode)
    if run_kind == "cron":
        return []

    # Heartbeat runs only get HEARTBEAT.md
    if run_kind == "heartbeat":
        heartbeat_file = "HEARTBEAT.md"
        if hook_overrides and heartbeat_file in hook_overrides:
            content = hook_overrides[heartbeat_file]
        else:
            path = workspace_dir / heartbeat_file
            if not path.exists():
                return []
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                return []
        return [{"path": heartbeat_file, "content": content}]

    # Subagent filter: only AGENTS.md + TOOLS.md for sub-sessions
    # Matches TS workspace.ts#L470-478 (filterBootstrapFilesForSession)
    is_subagent = session_key and (":sub:" in session_key or ":spawn:" in session_key)
    allowed = {DEFAULT_AGENTS_FILENAME, DEFAULT_TOOLS_FILENAME} if is_subagent else set(_BOOTSTRAP_FILENAMES)
    
    # Build list of files to load (standard + memory files)
    # Memory files are only loaded for main sessions, not subagents
    filenames_to_load = list(_BOOTSTRAP_FILENAMES)
    if not is_subagent:
        memory_entries = resolve_memory_bootstrap_entries(workspace_dir)
        filenames_to_load.extend([name for name, _ in memory_entries])

    files = []
    total_chars = 0

    for filename in filenames_to_load:
        if filename not in allowed and not any(
            filename.lower() == mem.lower() for mem in [DEFAULT_MEMORY_FILENAME, DEFAULT_MEMORY_ALT_FILENAME]
        ):
            continue

        # Hook override takes precedence
        if hook_overrides and filename in hook_overrides:
            content = hook_overrides[filename]
        else:
            path = workspace_dir / filename
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue

        # Per-file size limit with 70/20 truncation strategy (matching TS)
        if len(content) > max_chars_per_file:
            head_chars = int(max_chars_per_file * 0.7)
            tail_chars = int(max_chars_per_file * 0.2)
            head = content[:head_chars]
            tail = content[-tail_chars:]
            marker = f"\n\n... (truncated {len(content) - max_chars_per_file} chars) ...\n\n"
            content = head + marker + tail

        # Total budget — stop loading entirely once exhausted (TS alignment)
        if total_chars >= total_max_chars:
            break
        if total_chars + len(content) > total_max_chars:
            remaining = total_max_chars - total_chars
            # Stop loading if remaining budget is too small to be useful
            # (less than 10% of the per-file limit → not worth partial-loading)
            if remaining < max(200, max_chars_per_file // 10):
                break
            content = content[:remaining] + "\n…[truncated]"

        files.append({"path": filename, "content": content})
        total_chars += len(content)

    return files


def build_embedded_system_prompt(
    workspace_dir: Path,
    session_key: str | None = None,
    tool_names: list[str] | None = None,
    tool_summaries: dict[str, str] | None = None,
    skills: list[dict] | None = None,
    model_name: str | None = None,
    user_timezone: str | None = None,
    runtime_info: str | None = None,
    hook_overrides: dict[str, str] | None = None,
    prompt_mode: Literal["full", "minimal", "none"] = "full",
    extra_system_prompt: str | None = None,
    max_chars_per_file: int = _DEFAULT_MAX_CHARS_PER_FILE,
    total_max_chars: int = _DEFAULT_TOTAL_MAX_CHARS,
    # --- All params from build_agent_system_prompt (previously dropped) ---
    heartbeat_prompt: str | None = None,
    sandbox_info: dict | None = None,
    exec_config: dict | None = None,
    owner_numbers: list[str] | None = None,
    workspace_notes: list[str] | None = None,
    memory_citations_mode: Literal["on", "off"] = "on",
    model_alias_lines: list[str] | None = None,
    tts_hint: str | None = None,
    reaction_guidance: dict | None = None,
    reasoning_level: str = "off",
    reasoning_hint: str | None = None,
    message_tool_hints: list[str] | None = None,
    message_channel_options: str = "telegram|discord|slack|signal",
    has_gateway: bool = True,
    run_kind: str = "default",
) -> str:
    """
    Per-session dynamic system prompt builder.

    Matches TypeScript buildEmbeddedSystemPrompt():
    1. resolveBootstrapContextForRun() → load AGENTS.md, SOUL.md, etc.
       (respects run_kind so heartbeat/cron runs get lightweight context)
    2. applyBootstrapHookOverrides() (passed as hook_overrides)
    3. buildWorkspaceSkillSnapshot() → already done in skills param
    4. buildAgentSystemPrompt() with ALL params threaded through

    Args:
        workspace_dir: Agent workspace directory.
        session_key: Session key for subagent detection.
        tool_names: Names of available tools.
        tool_summaries: Per-tool description summaries.
        skills: Pre-loaded skill list.
        model_name: Current model (for aliases).
        user_timezone: User's timezone string.
        runtime_info: Runtime/version info string.
        hook_overrides: Plugin-injected bootstrap file overrides.
        prompt_mode: "full" | "minimal" | "none".
        extra_system_prompt: Injected extra context.
        max_chars_per_file: Per-file size limit.
        total_max_chars: Total bootstrap context size limit.
        heartbeat_prompt: Heartbeat section content.
        sandbox_info: Sandbox configuration dict.
        exec_config: Exec security configuration.
        owner_numbers: Owner phone numbers.
        workspace_notes: Additional workspace notes.
        memory_citations_mode: Memory citations mode.
        model_alias_lines: Model alias section lines.
        tts_hint: TTS hint text.
        reaction_guidance: Reaction guidance dict.
        reasoning_level: Reasoning level ("off", "on", "stream").
        reasoning_hint: Reasoning format hint.
        message_tool_hints: Extra hints for message tool.
        message_channel_options: Channel option string for message tool.
        has_gateway: Whether gateway tool is available.
        run_kind: "default" | "heartbeat" | "cron" — controls bootstrap filtering.

    Returns:
        Assembled system prompt string.
    """
    context_files = resolve_bootstrap_context_for_run(
        workspace_dir=workspace_dir,
        session_key=session_key,
        max_chars_per_file=max_chars_per_file,
        total_max_chars=total_max_chars,
        hook_overrides=hook_overrides,
        run_kind=run_kind,
    )

    # Format skills into prompt text if provided
    skills_prompt_text = None
    if skills:
        skills_prompt_text = format_skills_for_prompt(skills)

    return build_agent_system_prompt(
        workspace_dir=workspace_dir,
        tool_names=tool_names,
        tool_summaries=tool_summaries,
        context_files=context_files,
        skills_prompt=skills_prompt_text,
        heartbeat_prompt=heartbeat_prompt,
        sandbox_info=sandbox_info,
        exec_config=exec_config,
        user_timezone=user_timezone,
        owner_numbers=owner_numbers,
        extra_system_prompt=extra_system_prompt,
        workspace_notes=workspace_notes,
        memory_citations_mode=memory_citations_mode,
        model_alias_lines=model_alias_lines,
        tts_hint=tts_hint,
        reaction_guidance=reaction_guidance,
        reasoning_level=reasoning_level,
        reasoning_hint=reasoning_hint,
        message_tool_hints=message_tool_hints,
        message_channel_options=message_channel_options,
        has_gateway=has_gateway,
        prompt_mode=prompt_mode,
        runtime_info=runtime_info,
    )


# ──────────────────────────────────────────────────────────────────────
# Skills formatter (unchanged – already matches TS XML format)
# ──────────────────────────────────────────────────────────────────────

def format_skills_for_prompt(skills: list[dict]) -> str:
    """
    Format skills list as XML prompt.

    Args:
        skills: List of skill dicts with 'name', 'description', 'location'

    Returns:
        XML formatted skills prompt
    """
    if not skills:
        return ""

    lines = ["<available_skills>"]

    for skill in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{skill['name']}</name>")
        if "description" in skill:
            lines.append(f"    <description>{skill['description']}</description>")
        if "location" in skill:
            lines.append(f"    <location>{skill['location']}</location>")
        if "tags" in skill and skill["tags"]:
            tags_str = ", ".join(skill["tags"])
            lines.append(f"    <tags>{tags_str}</tags>")
        lines.append("  </skill>")

    lines.append("</available_skills>")
    return "\n".join(lines)


def load_bootstrap_files_legacy(
    workspace_dir,
    max_chars_per_file: int | None = None,
    cfg=None,
    session_key: str | None = None,
    **kwargs,
) -> list[dict]:
    """Load bootstrap files and return as list of dicts (legacy/TS-compat format).

    Kept for backwards compatibility with tests and external callers.
    When ``session_key`` indicates a subagent session, only AGENTS.md and
    TOOLS.md are returned (matching TS filterBootstrapFilesForSession).
    """
    workspace_path = Path(workspace_dir) if not isinstance(workspace_dir, Path) else workspace_dir
    total_max_chars = kwargs.get("total_max_chars") or _DEFAULT_TOTAL_MAX_CHARS
    return resolve_bootstrap_context_for_run(
        workspace_path,
        session_key=session_key,
        max_chars_per_file=max_chars_per_file or _DEFAULT_MAX_CHARS_PER_FILE,
        total_max_chars=total_max_chars,
    )
