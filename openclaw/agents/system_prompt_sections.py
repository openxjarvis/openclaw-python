"""
System prompt section builders

Each function returns a list of strings (lines) that can be joined to form a section.
This modular approach matches the TypeScript implementation in src/agents/system-prompt.ts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Silent reply token (matches TypeScript SILENT_REPLY_TOKEN from auto-reply/tokens.ts)
SILENT_REPLY_TOKEN = "◻️"

# Core tool summaries (aligned with TypeScript coreToolSummaries lines 225-253)
# NOTE: Python tool names may differ from TS (read_file vs read, bash vs exec, etc.)
# because the Python tool registry uses these names. The summaries match TS intent.
CORE_TOOL_SUMMARIES: dict[str, str] = {
    "read": "Read file contents",
    "read_file": "Read file contents",
    "write": "Create or overwrite files",
    "write_file": "Create or overwrite files",
    "edit": "Make precise edits to files",
    "edit_file": "Make precise edits to files",
    "apply_patch": "Apply multi-file patches",
    "grep": "Search file contents for patterns",
    "find": "Find files by glob pattern",
    "ls": "List directory contents",
    "exec": "Run shell commands (pty available for TTY-required CLIs)",
    "bash": "Run shell commands (pty available for TTY-required CLIs)",
    "process": "Manage background exec sessions",
    "web_search": "Search the web (Brave API)",
    "web_fetch": "Fetch and extract readable content from a URL",
    "browser": "Control web browser",
    "canvas": "Present/eval/snapshot the Canvas",
    "nodes": "List/describe/notify/camera/screen on paired nodes",
    "cron": (
        "Manage cron jobs and wake events (use for reminders; when scheduling a reminder, "
        "write the systemEvent text as something that will read like a reminder when it fires, "
        "and mention that it is a reminder depending on the time gap between setting and firing; "
        "include recent context in reminder text if appropriate)"
    ),
    "message": "Send messages and channel actions",
    "gateway": "Restart, apply config, or run updates on the running OpenClaw process",
    "agents_list": "List agent ids allowed for sessions_spawn",
    "sessions_list": "List other sessions (incl. sub-agents) with filters/last",
    "sessions_history": "Fetch history for another session/sub-agent",
    "sessions_send": "Send a message to another session/sub-agent",
    "sessions_spawn": "Spawn a sub-agent session",
    "subagents": "List, steer, or kill sub-agent runs for this requester session",
    "session_status": (
        'Show a /status-equivalent status card (usage + time + Reasoning/Verbose/Elevated); '
        'use for model-use questions (📊 session_status); optional per-session model override'
    ),
    "image": "Analyze an image with the configured image model",
    # Legacy/additional tools (not in TS but present in Python)
    "telegram_actions": "Perform Telegram-specific actions (edit, delete, react, forward)",
    "discord_actions": "Perform Discord-specific actions (manage roles, channels, etc.)",
    "slack_actions": "Perform Slack-specific actions",
    "whatsapp_actions": "Perform WhatsApp-specific actions",
    "tts": "Convert text to speech and save as audio file",
    "voice_call": "Make and receive voice calls",
    "memory_search": "Search memory files for relevant information",
    "memory_get": "Get specific memory content by path and line range",
}

# Preferred tool display order (fully aligned with TypeScript toolOrder lines 255-280)
TOOL_ORDER: list[str] = [
    "read",
    "write",
    "edit",
    "apply_patch",
    "grep",
    "find",
    "ls",
    "exec",
    "process",
    "web_search",
    "web_fetch",
    "browser",
    "canvas",
    "nodes",
    "cron",
    "message",
    "gateway",
    "agents_list",
    "sessions_list",
    "sessions_history",
    "sessions_send",
    "subagents",  # Added to match TS (line 277)
    "session_status",
    "image",
    # Python-specific tools (not in TS order but keep for compatibility)
    "read_file",
    "write_file",
    "edit_file",
    "bash",
    "telegram_actions",
    "discord_actions",
    "slack_actions",
    "whatsapp_actions",
    "sessions_spawn",
    "tts",
    "voice_call",
    "memory_search",
    "memory_get",
]


def build_tooling_section(
    tool_names: list[str] | None,
    tool_summaries: dict[str, str] | None = None
) -> list[str]:
    """Build the Tooling section (matches TS lines 382-406)."""
    if not tool_names:
        return []

    # Merge core summaries with custom ones
    summaries = {**CORE_TOOL_SUMMARIES}
    if tool_summaries:
        summaries.update(tool_summaries)

    lines = [
        "## Tooling",
        "Tool availability (filtered by policy):",
        "Tool names are case-sensitive. Call tools exactly as listed.",
    ]

    # Build tool list with preferred order
    available_set = set(tool_names)

    # 1. Ordered tools
    for tool_name in TOOL_ORDER:
        if tool_name in available_set:
            summary = summaries.get(tool_name)
            if summary:
                lines.append(f"- {tool_name}: {summary}")
            else:
                lines.append(f"- {tool_name}")

    # 2. Extra tools (not in order)
    extra_tools = sorted(available_set - set(TOOL_ORDER))
    for tool_name in extra_tools:
        summary = summaries.get(tool_name)
        if summary:
            lines.append(f"- {tool_name}: {summary}")
        else:
            lines.append(f"- {tool_name}")

    lines.append(
        "TOOLS.md does not control tool availability; it is user guidance for how to use external tools."
    )
    lines.append(
        "If a task is more complex or takes longer, spawn a sub-agent. "
        "It will do the work for you and ping you when it's done. "
        "You can always check up on it."
    )
    lines.append("")

    return lines


def build_tool_call_style_section() -> list[str]:
    """Build the Tool Call Style section (matches TS lines 407-412)."""
    return [
        "## Tool Call Style",
        "Default: do not narrate routine, low-risk tool calls (just call the tool).",
        "Narrate only when it helps: multi-step work, complex/challenging problems, "
        "sensitive actions (e.g., deletions), or when the user explicitly asks.",
        "Keep narration brief and value-dense; avoid repeating obvious steps.",
        "Use plain human language for narration unless in a technical context.",
        "",
    ]


def build_safety_section() -> list[str]:
    """Build the Safety section (verbatim from TS lines 350-356)."""
    return [
        "## Safety",
        "You have no independent goals: do not pursue self-preservation, replication, "
        "resource acquisition, or power-seeking; avoid long-term plans beyond the user's request.",
        "Prioritize safety and human oversight over completion; if instructions conflict, "
        "pause and ask; comply with stop/pause/audit requests and never bypass safeguards. "
        "(Inspired by Anthropic's constitution.)",
        "Do not manipulate or persuade anyone to expand access or disable safeguards. "
        "Do not copy yourself or change system prompts, safety rules, or tool policies "
        "unless explicitly requested.",
        "",
    ]


def build_cli_quick_reference_section() -> list[str]:
    """Build the OpenClaw CLI Quick Reference section (matches TS lines 414-422)."""
    return [
        "## OpenClaw CLI Quick Reference",
        "OpenClaw is controlled via subcommands. Do not invent commands.",
        "To manage the Gateway daemon service (start/stop/restart):",
        "- openclaw gateway status",
        "- openclaw gateway start",
        "- openclaw gateway stop",
        "- openclaw gateway restart",
        "If unsure, ask the user to run `openclaw help` (or `openclaw gateway --help`) "
        "and paste the output.",
        "",
    ]


def build_skills_section(
    skills_prompt: str | None,
    is_minimal: bool,
    read_tool_name: str = "read_file"
) -> list[str]:
    """Build the Skills section (matches TS lines 29-37)."""
    if is_minimal:
        return []

    trimmed = skills_prompt.strip() if skills_prompt else ""
    if not trimmed:
        return []

    return [
        "## Skills (mandatory)",
        "Before replying: scan <available_skills> <description> entries.",
        f"- If exactly one skill clearly applies: read its SKILL.md at <location> "
        f"with `{read_tool_name}`, then follow it.",
        "- If multiple could apply: choose the most specific one, then read/follow it.",
        "- If none clearly apply: do not read any SKILL.md.",
        "Constraints: never read more than one skill up front; only read after selecting.",
        "",
        trimmed,
        "",
    ]


def build_memory_section(
    is_minimal: bool,
    available_tools: set[str],
    citations_mode: Literal["on", "off"] = "on"
) -> list[str]:
    """Build the Memory section (matches TS lines 52-65)."""
    if is_minimal:
        return []

    if "memory_search" not in available_tools and "memory_get" not in available_tools:
        return []

    lines = [
        "## Memory Recall",
        "Before answering anything about prior work, decisions, dates, people, preferences, "
        "or todos: run memory_search on MEMORY.md + memory/*.md; then use memory_get to pull "
        "only the needed lines. If low confidence after search, say you checked.",
    ]

    if citations_mode == "off":
        lines.append(
            "Citations are disabled: do not mention file paths or line numbers in replies "
            "unless the user explicitly asks."
        )
    else:
        lines.append(
            "Citations: include Source: <path#line> when it helps the user verify memory snippets."
        )

    lines.append("")

    return lines


def build_self_update_section(
    has_gateway: bool,
    is_minimal: bool
) -> list[str]:
    """Build the OpenClaw Self-Update section (matches TS lines 426-434)."""
    if not has_gateway or is_minimal:
        return []

    return [
        "## OpenClaw Self-Update",
        "Get Updates (self-update) is ONLY allowed when the user explicitly asks for it.",
        "Do not run config.apply or update.run unless the user explicitly requests an update "
        "or config change; if it's not explicit, ask first.",
        "Actions: config.get, config.schema, config.apply (validate + write full config, "
        "then restart), update.run (update deps or git, then restart).",
        "After restart, OpenClaw pings the last active session automatically.",
        "",
    ]


def build_model_aliases_section(
    model_alias_lines: list[str] | None,
    is_minimal: bool
) -> list[str]:
    """Build the Model Aliases section (matches TS lines 438-447)."""
    if not model_alias_lines or is_minimal:
        return []

    lines = [
        "## Model Aliases",
        "Prefer aliases when specifying model overrides; full provider/model is also accepted.",
    ]
    lines.extend(model_alias_lines)
    lines.append("")

    return lines


def build_docs_section(
    docs_path: str | None,
    is_minimal: bool,
    read_tool_name: str = "read_file"
) -> list[str]:
    """Build the Documentation section (matches TS lines 146-161)."""
    docs_path_trimmed = docs_path.strip() if docs_path else ""

    if not docs_path_trimmed or is_minimal:
        return []

    return [
        "## Documentation",
        f"OpenClaw docs: {docs_path_trimmed}",
        "Mirror: https://docs.openclaw.ai",
        "Source: https://github.com/openclaw/openclaw",
        "Community: https://discord.com/invite/clawd",
        "Find new skills: https://clawhub.com",
        "For OpenClaw behavior, commands, config, or architecture: consult local docs first.",
        "When diagnosing issues, run `openclaw status` yourself when possible; "
        "only ask the user if you lack access (e.g., sandboxed).",
        "",
    ]


def build_time_section(
    user_timezone: str | None
) -> list[str]:
    """Build the Time section (matches TS lines 75-80)."""
    if not user_timezone:
        return []
    return [
        "## Current Date & Time",
        f"Time zone: {user_timezone}",
        ""
    ]


def build_workspace_files_note_section() -> list[str]:
    """Build the Workspace Files (injected) note (matches TS lines 503-504)."""
    return [
        "## Workspace Files (injected)",
        "These user-editable files are loaded by OpenClaw and included below in Project Context.",
        "",
    ]


def build_reply_tags_section(is_minimal: bool) -> list[str]:
    """Build the Reply Tags section (matches TS lines 103-117)."""
    if is_minimal:
        return []

    return [
        "## Reply Tags",
        "To request a native reply/quote on supported surfaces, include one tag in your reply:",
        "- Reply tags must be the very first token in the message (no leading text/newlines): [[reply_to_current]] your reply.",
        "- [[reply_to_current]] replies to the triggering message.",
        "- Prefer [[reply_to_current]]. Use [[reply_to:<id>]] only when an id was explicitly provided (e.g. by the user or a tool).",
        "Whitespace inside the tag is allowed (e.g. [[ reply_to_current ]] / [[ reply_to: 123 ]]).",
        "Tags are stripped before sending; support depends on the current channel config.",
        "",
    ]


def build_messaging_section(
    is_minimal: bool,
    available_tools: set[str],
    message_channel_options: str = "telegram|discord|slack|signal",
    inline_buttons_enabled: bool = False,
    runtime_channel: str | None = None,
    message_tool_hints: list[str] | None = None,
) -> list[str]:
    """Build the Messaging section (matches TS lines 119-157)."""
    if is_minimal:
        return []

    lines = [
        "## Messaging",
        "- Reply in current session → automatically routes to the source channel (Signal, Telegram, etc.)",
        "- Cross-session messaging → use sessions_send(sessionKey, message)",
        "- Sub-agent orchestration → use subagents(action=list|steer|kill)",
        f"- Runtime-generated completion events may ask for a user update. Rewrite those in your normal assistant voice and send the update (do not forward raw internal metadata or default to {SILENT_REPLY_TOKEN}).",
        "- Never use exec/curl for provider messaging; OpenClaw handles all routing internally.",
    ]

    if "message" in available_tools:
        lines.append("")
        lines.append("### message tool")
        lines.append("- Use `message` for proactive sends + channel actions (polls, reactions, etc.).")
        lines.append("- For `action=send`, include `to` and `message`.")
        lines.append(
            f"- If multiple channels are configured, pass `channel` ({message_channel_options})."
        )
        lines.append(
            f"- If you use `message` (`action=send`) to deliver your user-visible reply, "
            f"respond with ONLY: {SILENT_REPLY_TOKEN} (avoid duplicate replies)."
        )

        if inline_buttons_enabled:
            lines.append(
                "- Inline buttons supported. Use `action=send` with "
                '`buttons=[[{text,callback_data,style?}]]`; `style` can be `primary`, `success`, or `danger`.'
            )
        elif runtime_channel:
            lines.append(
                f"- Inline buttons not enabled for {runtime_channel}. "
                f"If you need them, ask to set {runtime_channel}.capabilities.inlineButtons "
                '("dm"|"group"|"all"|"allowlist").'
            )

        if message_tool_hints:
            lines.extend(message_tool_hints)

    lines.append("")

    return lines


def build_voice_section(
    is_minimal: bool,
    tts_hint: str | None = None
) -> list[str]:
    """Build the Voice (TTS) section (matches TS lines 135-143)."""
    if is_minimal:
        return []

    hint = tts_hint.strip() if tts_hint else ""
    if not hint:
        return []

    return [
        "## Voice (TTS)",
        hint,
        "",
    ]


def build_runtime_section(
    runtime_info: dict | None = None,
    is_minimal: bool = False,
    reasoning_level: str = "off",
) -> list[str]:
    """Build the Runtime section (matches TS lines 601-605)."""
    import platform
    import sys

    # No info (None) → omit the section. Empty dict → auto-populate OS/arch/Python.
    if runtime_info is None:
        return []

    if not runtime_info.get("os"):
        runtime_info = {**runtime_info, "os": platform.system().lower()}
    if not runtime_info.get("arch"):
        runtime_info = {**runtime_info, "arch": platform.machine()}
    if not runtime_info.get("python_version"):
        runtime_info = {**runtime_info, "python_version": sys.version.split()[0]}

    parts = []

    if runtime_info.get("agent_id"):
        parts.append(f"agent={runtime_info['agent_id']}")
    if runtime_info.get("host"):
        parts.append(f"host={runtime_info['host']}")
    if runtime_info.get("os"):
        parts.append(f"os={runtime_info['os']}")
    if runtime_info.get("arch"):
        parts.append(f"arch={runtime_info['arch']}")
    if runtime_info.get("python_version"):
        parts.append(f"python={runtime_info['python_version']}")
    if runtime_info.get("model"):
        parts.append(f"model={runtime_info['model']}")
    if runtime_info.get("channel"):
        parts.append(f"channel={runtime_info['channel']}")

    # Add capabilities if present
    capabilities = runtime_info.get("capabilities", [])
    if capabilities:
        parts.append(f"capabilities={','.join(capabilities)}")

    if not parts:
        return []

    runtime_line = " | ".join(parts)

    return [
        "## Runtime",
        f"Runtime: {runtime_line}",
        f"Reasoning: {reasoning_level} (hidden unless on/stream). "
        "Toggle /reasoning; /status shows Reasoning when enabled.",
    ]


def build_heartbeats_section(
    heartbeat_prompt: str | None,
    is_minimal: bool = False
) -> list[str]:
    """Build the Heartbeats section (matches TS lines 588-599)."""
    if is_minimal or not heartbeat_prompt:
        return []

    heartbeat_prompt_line = heartbeat_prompt.strip() if heartbeat_prompt else "(configured)"

    return [
        "## Heartbeats",
        heartbeat_prompt_line,
        "If you receive a heartbeat poll (a user message matching the heartbeat prompt above), "
        "and there is nothing that needs attention, reply exactly:",
        "HEARTBEAT_OK",
        'OpenClaw treats a leading/trailing "HEARTBEAT_OK" as a heartbeat ack '
        "(and may discard it).",
        'If something needs attention, do NOT include "HEARTBEAT_OK"; '
        "reply with the alert text instead.",
        "",
    ]


def build_exec_capabilities_section(
    exec_config: dict | None = None
) -> list[str]:
    """Build the Exec Capabilities section to inform agent about bash capabilities."""
    if not exec_config:
        return []
    
    security = exec_config.get("security", "deny")
    safe_bins = exec_config.get("safe_bins", [])
    timeout_sec = exec_config.get("timeout_sec", 120)
    
    parts = [
        "## Bash Execution Capabilities",
    ]
    
    if security == "full":
        parts.extend([
            "✅ **You CAN execute bash commands freely** (security mode: full)",
            "- You can run `pip install <package>` to install Python packages",
            "- You can run Python scripts with `python scripts/script.py`",
            "- You can use system commands (git, npm, node, etc.)",
            f"- Command timeout: {timeout_sec} seconds",
            "",
            "**━━━ CRITICAL: NO SANDBOX RESTRICTIONS ━━━**",
            "**YOU ARE NOT IN A SANDBOX. YOU HAVE FULL SYSTEM ACCESS.**",
            "- All system commands and tools are available",
            "- python-pptx IS INSTALLED (version 1.0.2)",
            "- You CAN run Python scripts",
            "- You CAN install packages (though not needed)",
            "- There are NO restrictions on bash commands",
            "",
            "**YOU CAN AND MUST GENERATE POWERPOINT FILES**",
            "- The generate_ppt.py script EXISTS at: skills/powerpoint/scripts/generate_ppt.py",
            "- python-pptx library IS AVAILABLE",
            "- You MUST use the 'bash' tool to call generate_ppt.py script",
            "- DO NOT use write_file to create .pptx files - it won't work",
            "- DO NOT say you cannot generate files - YOU CAN",
            "- ALWAYS use bash tool for PPT generation",
            "- ALWAYS use relative paths with --output (e.g., presentations/MyFile.pptx)",
            "- ALWAYS include --workspace parameter with session workspace path",
            "",
            "**PowerPoint Generation Workflow (FOLLOW THIS):**",
            "",
            "1. User asks for PPT → Use BashTool to generate it",
            "",
            "2. Create JSON config (save to /tmp/ppt_config.json):",
            "```json",
            "{",
            "  \"title\": \"Presentation Title\",",
            "  \"slides\": [",
            "    {\"layout\": \"title\", \"title\": \"Main Title\", \"subtitle\": \"Subtitle\"},",
            "    {\"layout\": \"content\", \"title\": \"Slide 1\", \"content\": {\"bullets\": [\"Point 1\", \"Point 2\"]}},",
            "    {\"layout\": \"content\", \"title\": \"Slide 2\", \"content\": {\"bullets\": [\"Point A\", \"Point B\"]}}",
            "  ]",
            "}",
            "```",
            "",
            "3. Generate output path using YOUR SESSION WORKSPACE (from `## Session Workspace` in your prompt):",
            "   - Format: {SESSION_WORKSPACE}/presentations/{Title}_{YYYYMMDD_HHMMSS}.pptx",
            "   - The session workspace path is injected into your system prompt each turn — use it!",
            "",
            "4. CRITICAL: Use bash tool to run generation command (NOT write_file):",
            "   Tool: bash",
            "   Command:",
            "```bash",
            "SESSION_WS=\"<your session workspace path>\"",
            "mkdir -p \"$SESSION_WS/presentations\" && \\",
            "cat > /tmp/ppt_config.json << 'EOF'",
            "{...json config...}",
            "EOF",
            "python skills/powerpoint/scripts/generate_ppt.py \\",
            "  --config /tmp/ppt_config.json \\",
            "  --output \"$SESSION_WS/presentations/Title_$(date +%Y%m%d_%H%M%S).pptx\"",
            "```",
            "",
            "5. After generation succeeds, output a MEDIA: token on its own line so the system",
            "   delivers the file to the user via Telegram:",
            "```",
            "MEDIA:/absolute/path/to/file.pptx",
            "```",
            "   **CRITICAL**: You MUST include a `MEDIA:<path>` line in your reply for the file",
            "   to be sent. Without it, only text is delivered — the file is never attached.",
            "   The path must be absolute (starting with /) or use ~ for home directory.",
            "",
            "**Example Full Command (replace SESSION_WS with your actual session workspace):**",
            "```bash",
            "SESSION_WS=\"~/.openclaw/workspace/agent-main-main-XXXXX\"",
            "cat > /tmp/ppt_config.json << 'EOF'",
            "{\"title\": \"AI Introduction\", \"slides\": [{\"layout\": \"title\", \"title\": \"AI\", \"subtitle\": \"Overview\"}]}",
            "EOF",
            "mkdir -p \"$SESSION_WS/presentations\"",
            "python skills/powerpoint/scripts/generate_ppt.py \\",
            "  --config /tmp/ppt_config.json \\",
            "  --output \"$SESSION_WS/presentations/AI_$(date +%Y%m%d_%H%M%S).pptx\"",
            "```",
            "",
            "**Remember:** Always use the Session Workspace path from your system prompt — never scatter files into workspace root!",
        ])
    elif security == "allowlist":
        safe_bins_str = ", ".join(safe_bins[:10])
        if len(safe_bins) > 10:
            safe_bins_str += f", ... (+{len(safe_bins) - 10} more)"
        parts.extend([
            "⚠️  **Bash execution is restricted** (security mode: allowlist)",
            f"- Only these binaries are allowed: {safe_bins_str}",
            f"- Command timeout: {timeout_sec} seconds",
            "- For other commands, ask the user for approval or suggest manual execution",
        ])
    else:  # deny
        parts.extend([
            "❌ **Bash execution is disabled** (security mode: deny)",
            "- You cannot execute bash commands directly",
            "- Suggest manual execution or use other available tools",
        ])
    
    parts.append("")
    return parts


def build_elevated_section(
    elevated_config: dict | None = None,
    current_level: str | None = None,
) -> list[str]:
    """Build the Elevated Exec section for non-sandbox (gateway) contexts.

    Mirrors the elevated block injected by TS reply-elevated.ts / get-reply-run.ts
    into every turn's system prompt when elevated mode is available for the session.

    Args:
        elevated_config: The ``tools.elevated`` config dict (``enabled``, ``allowFrom``).
        current_level: The session's current ``elevatedLevel`` value (from SessionEntry).
    """
    if not elevated_config or not elevated_config.get("enabled"):
        return []

    parts = [
        "## Elevated Exec",
        "Elevated exec mode is available for authorised users in this session.",
        "Use /elevated on|off|ask|full to toggle (or embed inline in a message).",
        "  on/ask — each host command requires per-command approval.",
        "  full   — host commands are auto-approved (most permissive).",
        "  off    — elevated mode disabled.",
    ]

    if current_level and current_level != "off":
        level_desc = (
            "ask (per-command approval required)"
            if current_level in ("ask", "on")
            else "full (auto-approve all host commands)"
        )
        parts.append(f"Current elevated level for this session: {level_desc}.")
    else:
        parts.append("Current elevated level: off.")

    parts.append("")
    return parts


def build_sandbox_section(
    sandbox_info: dict | None = None
) -> list[str]:
    """Build the Sandbox section (matches TS lines 457-498)."""
    if not sandbox_info or not sandbox_info.get("enabled"):
        return []

    parts = [
        "## Sandbox",
        "You are running in a sandboxed runtime (tools execute in Docker).",
        "Some tools may be unavailable due to sandbox policy.",
        "Sub-agents stay sandboxed (no elevated/host access). "
        "Need outside-sandbox read/write? Don't spawn; ask first.",
    ]

    if sandbox_info.get("workspace_dir"):
        parts.append(f"Sandbox workspace: {sandbox_info['workspace_dir']}")

    if sandbox_info.get("workspace_access"):
        access_line = f"Agent workspace access: {sandbox_info['workspace_access']}"
        if sandbox_info.get("agent_workspace_mount"):
            access_line += f" (mounted at {sandbox_info['agent_workspace_mount']})"
        parts.append(access_line)

    if sandbox_info.get("browser_bridge_url"):
        parts.append("Sandbox browser: enabled.")

    if sandbox_info.get("browser_novnc_url"):
        parts.append(f"Sandbox browser observer (noVNC): {sandbox_info['browser_novnc_url']}")

    host_browser = sandbox_info.get("host_browser_allowed")
    if host_browser is True:
        parts.append("Host browser control: allowed.")
    elif host_browser is False:
        parts.append("Host browser control: blocked.")

    elevated = sandbox_info.get("elevated", {})
    if elevated.get("allowed"):
        parts.append("Elevated exec is available for this session.")
        parts.append("User can toggle with /elevated on|off|ask|full.")
        parts.append("You may also send /elevated on|off|ask|full when needed.")

        if elevated.get("default_level"):
            parts.append(
                f"Current elevated level: {elevated['default_level']} "
                "(ask runs exec on host with approvals; full auto-approves)."
            )

    parts.append("")

    return parts


def build_user_identity_section(
    owner_line: str | None,
    is_minimal: bool = False
) -> list[str]:
    """Build the User Identity section (matches TS lines 68-73)."""
    if not owner_line or is_minimal:
        return []

    return [
        "## User Identity",
        owner_line,
        "",
    ]


def build_silent_replies_section(
    is_minimal: bool,
) -> list[str]:
    """Build the Silent Replies section (matches TS lines 571-586)."""
    if is_minimal:
        return []

    return [
        "## Silent Replies",
        f"When you have nothing to say, respond with ONLY: {SILENT_REPLY_TOKEN}",
        "",
        "⚠️ Rules:",
        "- It must be your ENTIRE message — nothing else",
        f'- Never append it to an actual response (never include "{SILENT_REPLY_TOKEN}" in real replies)',
        "- Never wrap it in markdown or code blocks",
        "",
        f'❌ Wrong: "Here\'s help... {SILENT_REPLY_TOKEN}"',
        f'❌ Wrong: "{SILENT_REPLY_TOKEN}"',
        f"✅ Right: {SILENT_REPLY_TOKEN}",
        "",
    ]


def build_reaction_guidance_section(
    reaction_guidance: dict | None = None,
) -> list[str]:
    """Build the Reactions section (matches TS lines 524-546)."""
    if not reaction_guidance:
        return []

    level = reaction_guidance.get("level", "minimal")
    channel = reaction_guidance.get("channel", "unknown")

    if level == "minimal":
        guidance_text = "\n".join([
            f"Reactions are enabled for {channel} in MINIMAL mode.",
            "React ONLY when truly relevant:",
            "- Acknowledge important user requests or confirmations",
            "- Express genuine sentiment (humor, appreciation) sparingly",
            "- Avoid reacting to routine messages or your own replies",
            "Guideline: at most 1 reaction per 5-10 exchanges.",
        ])
    else:
        guidance_text = "\n".join([
            f"Reactions are enabled for {channel} in EXTENSIVE mode.",
            "Feel free to react liberally:",
            "- Acknowledge messages with appropriate emojis",
            "- Express sentiment and personality through reactions",
            "- React to interesting content, humor, or notable events",
            "- Use reactions to confirm understanding or agreement",
            "Guideline: react whenever it feels natural.",
        ])

    return [
        "## Reactions",
        guidance_text,
        "",
    ]


def build_reasoning_format_section(
    reasoning_hint: str | None = None,
) -> list[str]:
    """Build the Reasoning Format section (matches TS lines 547-549)."""
    if not reasoning_hint:
        return []

    return [
        "## Reasoning Format",
        reasoning_hint,
        "",
    ]


def build_skills_section_workspace(
    workspace_dir: Path | None = None,
    config: Any | None = None,
    read_tool_name: str = "read_file"
) -> list[str]:
    """
    Build the Skills section dynamically from the workspace (runtime variant).

    Use build_skills_section(skills_prompt, is_minimal, read_tool_name) for the
    TS section-builder API.

    Args:
        workspace_dir: Workspace directory
        config: OpenClaw configuration
        read_tool_name: Name of the read tool to reference
    
    Returns:
        List of prompt lines for skills section
    """
    if not workspace_dir:
        return []
    
    try:
        from .skills import build_workspace_skills_prompt
        
        skills_prompt = build_workspace_skills_prompt(
            workspace_dir=workspace_dir,
            config=config,
            read_tool_name=read_tool_name
        )
        
        if skills_prompt:
            # Already formatted with ## Available Skills header
            return [skills_prompt, ""]
        
        return []
    
    except ImportError:
        logger.warning("Skills module not available")
        return []
    except Exception as e:
        logger.error(f"Failed to build skills section: {e}")
        return []
