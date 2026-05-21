"""
Bash execution tool — matches openclaw/src/agents/bash-tools.exec.ts

Provides the full security pipeline before executing any command:
  security=deny   → reject immediately
  security=full   → run unconditionally
  security=allowlist →
    1. detect_command_obfuscation (16 patterns)
    2. evaluate_shell_allowlist   (chain splitting + safeBins)
    3. requires_exec_approval     (ask=on-miss / always)
    4. request_exec_approval_via_socket  (UNIX socket ↔ UI/CLI)
    5. on allow-always: persist pattern to exec-approvals.json
    6. on deny/timeout → askFallback logic

Also provides:
- Output truncation (50KB/2000 lines)
- Streaming updates via on_update callback
- Cancellation support via signal
- Pluggable operations for remote execution (gateway / node / sandbox)
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from typing import Any, Callable

from ..types import AgentToolResult, TextContent
from .base import AgentToolBase
from .default_operations import DefaultBashOperations
from .operations import BashOperations
from .truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    format_size,
    truncate_tail,
)

logger = logging.getLogger(__name__)

# Mirrors TS processSchema / bash-tools.schemas.ts exec parameters
EXEC_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Shell command to execute"},
        "workdir": {"type": "string", "description": "Working directory for the command"},
        "env": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Environment variables to set for this command",
        },
        "yieldMs": {
            "type": "integer",
            "description": "Milliseconds to wait before returning partial output (background mode)",
        },
        "background": {
            "type": "boolean",
            "description": "Run command in background and return early",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds",
        },
        "pty": {
            "type": "boolean",
            "description": "Allocate a pseudo-TTY for the command",
        },
        "elevated": {
            "type": "boolean",
            "description": "Request elevated execution when supported",
        },
        "host": {
            "type": "string",
            "enum": ["sandbox", "gateway", "node"],
            "description": "Execution host",
        },
        "security": {
            "type": "string",
            "enum": ["deny", "allowlist", "full"],
            "description": "Execution security mode override",
        },
        "ask": {
            "type": "string",
            "enum": ["off", "on-miss", "always"],
            "description": "Approval ask mode override",
        },
        "node": {
            "type": "string",
            "description": "Node id when host is node",
        },
    },
    "required": ["command"],
}


def create_bash_tool(
    cwd: str | None = None,
    operations: BashOperations | None = None,
    command_prefix: str | None = None,
    timeout: int | None = None,
    working_dir: str | None = None,
    exec_host: str | None = None,
    exec_node_id: str | None = None,
    # ── Security parameters (mirrors TS ExecToolConfig) ──────────────────────
    exec_security: str | None = None,          # "deny" | "allowlist" | "full"
    exec_ask: str | None = None,               # "off" | "on-miss" | "always"
    exec_ask_fallback: str | None = None,      # "deny" | "allowlist" | "full"
    exec_safe_bins: list[str] | None = None,   # bins allowed without allowlist entry
    exec_agent_id: str | None = None,          # agent ID for per-agent allowlist lookup
    exec_approval_timeout_ms: int = 120_000,   # approval socket timeout
) -> AgentToolBase:
    """
    Create a bash tool configured for a specific working directory.

    Args:
        cwd: Current working directory for commands
        operations: Bash operations implementation (overrides exec_host routing)
        command_prefix: Optional prefix to prepend to commands
        timeout: Default timeout in seconds
        working_dir: Alias for cwd (for backward compatibility)
        exec_host: Execution host — "gateway" (default), "node", or "sandbox".
                   When "node", routes via NodeBashOperations to exec_node_id.
                   When "sandbox", uses the injected operations (sandbox executor).
        exec_node_id: Node ID to route to when exec_host="node".
        exec_security: Security mode: "deny" | "allowlist" | "full" (default "deny")
        exec_ask: Ask mode: "off" | "on-miss" | "always" (default "on-miss")
        exec_ask_fallback: Fallback on approval timeout: "deny" | "allowlist" | "full"
        exec_safe_bins: Extra safe-bin names (beyond defaults)
        exec_agent_id: Agent ID for per-agent allowlist in exec-approvals.json
        exec_approval_timeout_ms: Timeout for UNIX-socket approval request (ms)

    Returns:
        Configured BashTool instance
    """
    import os as _os
    # working_dir is an alias for cwd
    if working_dir is not None and cwd is None:
        cwd = working_dir
    if cwd is None:
        cwd = _os.getcwd()
    default_timeout = timeout

    # Resolve operations based on exec_host (if not explicitly provided)
    if operations is None:
        if exec_host == "node" and exec_node_id:
            from .node_operations import NodeBashOperations
            operations = NodeBashOperations(exec_node_id)
        else:
            # "gateway" (default) or "sandbox" — caller provides sandbox ops
            operations = DefaultBashOperations()

    ops = operations
    _cwd = cwd
    _default_timeout = default_timeout

    # Resolved security config.
    # None means "not configured" → no security gate (pi-mono / unit-test mode).
    # Explicit "deny"/"allowlist"/"full" activates the security pipeline.
    _security = exec_security  # may be None → skip pipeline
    _ask = exec_ask or "on-miss"
    _ask_fallback = exec_ask_fallback or "deny"
    _agent_id = exec_agent_id

    class BashTool(AgentToolBase[dict, dict]):
        """Bash command execution tool"""

        # Expose configuration on the instance for introspection/testing
        working_dir: str = _cwd
        default_timeout: int | None = _default_timeout

        @property
        def name(self) -> str:
            return "exec"

        @property
        def label(self) -> str:
            return "Exec"

        @property
        def description(self) -> str:
            return (
                f"Execute a shell command. Returns stdout and stderr. Output is "
                f"truncated to last {DEFAULT_MAX_LINES} lines or "
                f"{DEFAULT_MAX_BYTES // 1024}KB. Supports workdir, env, background, "
                f"pty, elevated, host (sandbox/gateway/node), security, and ask."
            )

        @property
        def parameters(self) -> dict[str, Any]:
            return EXEC_TOOL_PARAMETERS
        
        async def execute(
            self,
            tool_call_id: str,
            params: dict,
            signal: asyncio.Event | None = None,
            on_update: Callable[[AgentToolResult], None] | None = None,
        ) -> AgentToolResult[dict]:
            """Execute bash command with full security pipeline + streaming + truncation."""

            command = params["command"]
            timeout = params.get("timeout") or default_timeout
            run_cwd = str(params["workdir"]) if params.get("workdir") else _cwd
            run_security = str(params["security"]) if params.get("security") else _security
            run_ask = str(params["ask"]) if params.get("ask") else _ask

            # Apply command prefix if configured
            resolved_command = f"{command_prefix}\n{command}" if command_prefix else command

            # ── SECURITY PIPELINE (mirrors bash-tools.exec-host-gateway.ts) ──────
            # Only engage when exec_security is explicitly configured.
            # None means pi-mono / unconfigured mode — no security gate.

            if run_security is not None:
                # Step 1: Hard deny — block all execution
                if run_security == "deny":
                    raise Exception(
                        "exec denied: security mode is 'deny'. "
                        "Set tools.exec.security to 'allowlist' or 'full' to enable execution."
                    )

            # Step 2: Obfuscation detection (always, regardless of security mode)
            if run_security in ("allowlist", "full"):
                try:
                    from openclaw.infra.exec_obfuscation_detect import detect_command_obfuscation
                    obfus = detect_command_obfuscation(resolved_command)
                    if obfus.detected:
                        raise Exception(
                            f"exec denied: command obfuscation detected "
                            f"({', '.join(obfus.matched_patterns)}). "
                            f"Use plain commands to enable allowlist approval."
                        )
                except ImportError:
                    pass

            # Step 3: For allowlist mode, evaluate + approval flow
            if run_security is not None and run_security == "allowlist":
                from openclaw.infra.exec_approvals_file import (
                    resolve_exec_approvals,
                    requires_exec_approval,
                    add_allowlist_entry,
                    record_allowlist_use,
                    request_exec_approval_via_socket,
                )
                from openclaw.infra.exec_approvals_allowlist import (
                    evaluate_shell_allowlist,
                    resolve_safe_bins,
                    resolve_allow_always_patterns,
                )

                approvals_ctx = resolve_exec_approvals(_agent_id)
                safe_bins = resolve_safe_bins(exec_safe_bins)

                analysis = evaluate_shell_allowlist(
                    command=resolved_command,
                    allowlist=approvals_ctx["allowlist"],
                    safe_bins=safe_bins,
                    cwd=run_cwd,
                )

                # If analysis failed and we cannot ask, deny immediately
                if not analysis.analysis_ok and run_ask == "off":
                    raise Exception(
                        "exec denied: command analysis failed (shell line-continuation or "
                        "parse error) and ask=off."
                    )

                # If allowlist not satisfied and ask=off, deny without prompting
                if not analysis.allowlist_satisfied and run_ask == "off":
                    raise Exception(
                        "exec denied: command not in allowlist and ask=off. "
                        "Add the command to exec-approvals.json or set ask=on-miss."
                    )

                needs_approval = requires_exec_approval(
                    ask=run_ask,
                    security=run_security,
                    analysis_ok=analysis.analysis_ok,
                    allowlist_satisfied=analysis.allowlist_satisfied,
                )

                if needs_approval:
                    # Build request payload for socket approval
                    request_payload = {
                        "command": resolved_command,
                        "cwd": run_cwd,
                        "host": exec_host or "gateway",
                        "security": run_security,
                        "ask": run_ask,
                        "agentId": _agent_id,
                    }
                    socket_path = approvals_ctx.get("socketPath", "")
                    token = approvals_ctx.get("token", "")

                    decision = await request_exec_approval_via_socket(
                        socket_path=socket_path,
                        token=token,
                        request=request_payload,
                        timeout_ms=exec_approval_timeout_ms,
                    )

                    if decision is None:
                        # Socket timeout → apply askFallback
                        if _ask_fallback == "full":
                            pass  # allow through
                        elif _ask_fallback == "allowlist" and analysis.allowlist_satisfied:
                            pass  # allow through since it was already satisfied
                        else:
                            raise Exception(
                                f"exec denied: approval request timed out (ask_fallback={_ask_fallback}). "
                                f"Start the approval server or change ask_fallback."
                            )
                    elif decision == "deny":
                        raise Exception("exec denied: user rejected the command.")
                    elif decision == "allow-always":
                        # Persist resolved patterns to exec-approvals.json
                        file = approvals_ctx["file"]
                        for pattern in resolve_allow_always_patterns(
                            analysis.segments, cwd=run_cwd
                        ):
                            add_allowlist_entry(file, _agent_id, pattern)
                    # "allow-once" → just run without persisting

                # Record usage for matched allowlist entries
                if analysis.allowlist_matches:
                    try:
                        file = approvals_ctx["file"]
                        for entry in analysis.allowlist_matches:
                            record_allowlist_use(file, _agent_id, entry, resolved_command)
                    except Exception:
                        pass

            # Step 4: security=full → run without any checks (still past obfuscation check above)
            # ── END SECURITY PIPELINE ─────────────────────────────────────────
            
            # Streaming output management
            # Keep a rolling buffer of the last chunk for tail truncation
            chunks: list[bytes] = []
            chunks_bytes = 0
            max_chunks_bytes = DEFAULT_MAX_BYTES * 2  # Keep more than we need
            
            # Temp file for full output
            temp_file_path: str | None = None
            temp_file: Any | None = None
            total_bytes = 0
            
            def handle_data(data: bytes):
                """Handle incoming data from subprocess"""
                nonlocal chunks_bytes, total_bytes, temp_file_path, temp_file
                
                total_bytes += len(data)
                
                # Start writing to temp file once we exceed threshold
                if total_bytes > DEFAULT_MAX_BYTES and not temp_file_path:
                    # Create temp file
                    fd, temp_file_path = tempfile.mkstemp(
                        prefix=f"openclaw-bash-{tool_call_id}-",
                        suffix=".log"
                    )
                    temp_file = open(fd, 'wb')
                    # Write all buffered chunks to the file
                    for chunk in chunks:
                        temp_file.write(chunk)
                
                # Write to temp file if we have one
                if temp_file:
                    temp_file.write(data)
                
                # Keep rolling buffer of recent data
                chunks.append(data)
                chunks_bytes += len(data)
                
                # Trim old chunks if buffer is too large
                while chunks_bytes > max_chunks_bytes and len(chunks) > 1:
                    removed = chunks.pop(0)
                    chunks_bytes -= len(removed)
                
                # Stream partial output to callback (truncated rolling buffer)
                if on_update:
                    full_buffer = b''.join(chunks)
                    full_text = full_buffer.decode('utf-8', errors='replace')
                    truncation = truncate_tail(full_text)
                    on_update(AgentToolResult(
                        content=[TextContent(text=truncation.content or "")],
                        details={
                            "truncation": truncation.__dict__ if truncation.truncated else None,
                            "full_output_path": temp_file_path,
                        }
                    ))
            
            # Check if already cancelled
            if signal and signal.is_set():
                raise asyncio.CancelledError("Operation aborted")
            
            # Execute command
            try:
                result = await ops.exec(
                    command=resolved_command,
                    cwd=run_cwd,
                    on_data=handle_data,
                    signal=signal,
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                # Close temp file on cancellation
                if temp_file:
                    temp_file.close()
                
                # Combine all buffered chunks for output
                full_buffer = b''.join(chunks)
                output = full_buffer.decode('utf-8', errors='replace')
                
                if output:
                    output += "\n\n"
                output += "Command aborted"
                raise Exception(output)
            except asyncio.TimeoutError:
                # Close temp file on timeout
                if temp_file:
                    temp_file.close()
                
                # Combine all buffered chunks for output
                full_buffer = b''.join(chunks)
                output = full_buffer.decode('utf-8', errors='replace')
                
                if output:
                    output += "\n\n"
                output += f"Command timed out after {timeout} seconds"
                raise Exception(output)
            finally:
                # Always close temp file
                if temp_file:
                    temp_file.close()
            
            # Process final output
            full_buffer = b''.join(chunks)
            full_output = full_buffer.decode('utf-8', errors='replace')
            
            # Apply tail truncation
            truncation = truncate_tail(full_output)
            output_text = truncation.content or "(no output)"
            
            # Build details with truncation info
            details: dict[str, Any] | None = None
            
            if truncation.truncated:
                details = {
                    "truncation": truncation.__dict__,
                    "full_output_path": temp_file_path,
                }
                
                # Build actionable notice
                start_line = truncation.total_lines - truncation.output_lines + 1
                end_line = truncation.total_lines
                
                if truncation.last_line_partial:
                    # Edge case: last line alone > 50KB
                    last_line = full_output.split('\n')[-1]
                    last_line_size = format_size(len(last_line.encode('utf-8')))
                    output_text += (
                        f"\n\n[Showing last {format_size(truncation.output_bytes)} "
                        f"of line {end_line} (line is {last_line_size}). "
                        f"Full output: {temp_file_path}]"
                    )
                elif truncation.truncated_by == "lines":
                    output_text += (
                        f"\n\n[Showing lines {start_line}-{end_line} of "
                        f"{truncation.total_lines}. Full output: {temp_file_path}]"
                    )
                else:
                    output_text += (
                        f"\n\n[Showing lines {start_line}-{end_line} of "
                        f"{truncation.total_lines} ({format_size(DEFAULT_MAX_BYTES)} limit). "
                        f"Full output: {temp_file_path}]"
                    )
            
            exit_code = result["exit_code"]
            if exit_code != 0 and exit_code is not None:
                output_text += f"\n\nCommand exited with code {exit_code}"
                raise Exception(output_text)
            
            return AgentToolResult(
                content=[TextContent(text=output_text)],
                details=details
            )
    
    return BashTool()


# Module-level BashTool alias so tests can do:
#   from openclaw.agents.tools.bash import BashTool
#   tool = BashTool()  or  tool = create_bash_tool(cwd)
BashTool = create_bash_tool
create_exec_tool = create_bash_tool

__all__ = ["create_bash_tool", "create_exec_tool", "BashTool", "EXEC_TOOL_PARAMETERS"]
