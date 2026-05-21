"""Tool factory that wraps pi_coding_agent tools with openclaw policy pipeline.

Replaces the individual openclaw tool files in tools/:
- tools/read.py   → pi_coding_agent.create_read_tool()
- tools/write.py  → pi_coding_agent.create_write_tool()
- tools/edit.py   → pi_coding_agent.create_edit_tool()
- tools/bash.py   → pi_coding_agent.create_bash_tool()
- tools/grep.py   → pi_coding_agent.create_grep_tool()
- tools/find.py   → pi_coding_agent.create_find_tool()
- tools/ls.py     → pi_coding_agent.create_ls_tool()

Additional non-coding tools (web, browser, voice, channels) are kept as-is
from openclaw/tools/ and simply mixed in.

Policy pipeline
---------------
Each tool call passes through:
1. before_tool_call hook (can block / modify)
2. exec approval (for bash / write commands)
3. tool execution
4. tool_result_persist hook
5. after_tool_call hook

Usage::

    tools = create_openclaw_coding_tools(cwd="/workspace")
    # → list of pi_coding_agent tool instances, ready for AgentSession
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _WorkspaceGuardedTool:
    """Wraps a write/edit tool to reject paths outside the workspace root.

    Mirrors TypeScript wrapToolWorkspaceRootGuard() from pi-tools.ts.
    When workspace_only=True is passed to create_openclaw_coding_tools(), all
    write and edit tool calls pass through this guard.
    """

    def __init__(self, inner: Any, workspace_root: Path) -> None:
        self._inner = inner
        self._workspace_root = workspace_root.resolve()
        self.name: str = getattr(inner, "name", str(inner))
        self.description: str = getattr(inner, "description", "")

    def schema(self) -> dict:
        if hasattr(self._inner, "schema"):
            return self._inner.schema()
        if hasattr(self._inner, "parameters"):
            return self._inner.parameters
        return {"type": "object", "properties": {}}

    def _check_path(self, path_arg: Any) -> Optional[str]:
        """Return an error string if path_arg is outside the workspace root."""
        if not path_arg:
            return None
        try:
            resolved = Path(str(path_arg)).expanduser().resolve()
            if not str(resolved).startswith(str(self._workspace_root)):
                return (
                    f"Access denied: {path_arg!r} is outside the workspace "
                    f"({self._workspace_root}). Only files within the workspace may be written."
                )
        except Exception:
            pass
        return None

    async def execute(self, **kwargs: Any) -> Any:
        # Check common path kwargs
        for key in ("path", "file_path", "filename"):
            err = self._check_path(kwargs.get(key))
            if err:
                return err

        fn = getattr(self._inner, "execute", None) or getattr(self._inner, "run", None)
        if fn is None:
            return f"Tool {self.name} has no execute/run method"
        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)


def create_openclaw_coding_tools(
    cwd: str | Path | None = None,
    allowed_dirs: list[str] | None = None,
    denied_commands: list[str] | None = None,
    enable_bash: bool = True,
    enable_write: bool = True,
    enable_read: bool = True,
    enable_edit: bool = True,
    enable_grep: bool = True,
    enable_find: bool = True,
    enable_ls: bool = True,
    workspace_only: bool = False,
) -> list[Any]:
    """Create the standard openclaw coding tool set from pi_coding_agent.

    Args:
        cwd: Working directory to pin tools to.
        allowed_dirs: Directories tools are allowed to access (None = unrestricted).
        denied_commands: Bash commands that are denied.
        enable_*: Toggle individual tools.
        workspace_only: When True, wrap write/edit tools with a path guard that
            rejects writes outside the cwd (workspace root). Mirrors TS
            ``wrapToolWorkspaceRootGuard()`` / ``fsConfig.workspaceOnly``.

    Returns:
        List of pi_coding_agent tool instances.
    """
    try:
        from pi_coding_agent import (
            create_bash_tool,
            create_edit_tool,
            create_find_tool,
            create_grep_tool,
            create_ls_tool,
            create_read_tool,
            create_write_tool,
        )
    except ImportError as exc:
        logger.error(f"pi_coding_agent tools not available: {exc}")
        return []

    cwd_str = str(cwd) if cwd else None
    workspace_root = Path(cwd_str).expanduser().resolve() if cwd_str else None
    tools = []

    # Tools that write/edit files get the workspace guard when workspace_only=True
    write_tool_names = {"write", "edit"}

    tool_factories = [
        ("read", enable_read, create_read_tool, {}),
        ("write", enable_write, create_write_tool, {}),
        ("edit", enable_edit, create_edit_tool, {}),
        ("exec", enable_bash, create_bash_tool, {"denied_commands": denied_commands} if denied_commands else {}),
        ("grep", enable_grep, create_grep_tool, {}),
        ("find", enable_find, create_find_tool, {}),
        ("ls", enable_ls, create_ls_tool, {}),
    ]

    for name, enabled, factory, extra_kwargs in tool_factories:
        if not enabled:
            continue
        try:
            if cwd_str:
                try:
                    tool = factory(cwd=cwd_str, **extra_kwargs)
                except TypeError:
                    tool = factory(**extra_kwargs)
            else:
                tool = factory(**extra_kwargs)

            # Apply workspace-only guard to write/edit tools
            if workspace_only and workspace_root and name in write_tool_names:
                tool = _WorkspaceGuardedTool(tool, workspace_root)
                logger.debug(f"Applied workspace guard to tool: {name}")

            tools.append(tool)
            logger.debug(f"Created pi_coding_agent tool: {name}")
        except Exception as exc:
            logger.warning(f"Failed to create tool {name!r}: {exc}")

    return tools


def build_pi_tools(openclaw_tools: list[Any]) -> list[Any]:
    """Convert openclaw tool objects to the format pi_coding_agent expects.

    pi_coding_agent.AgentSession.run() accepts the same tool instances that
    pi_coding_agent factories produce.  When openclaw tools are already
    pi_coding_agent tools, this is a no-op.  Legacy openclaw SimpleTool
    instances are wrapped in a thin shim.
    """
    result = []
    for tool in openclaw_tools:
        # Already a pi_coding_agent native tool → pass through
        module = type(tool).__module__
        if "pi_coding_agent" in module:
            result.append(tool)
            continue

        # Legacy openclaw SimpleTool → wrap
        try:
            result.append(_wrap_legacy_tool(tool))
        except Exception as exc:
            logger.warning(f"Failed to wrap tool {getattr(tool, 'name', tool)}: {exc}")

    return result


class _WrappedLegacyTool:
    """Minimal shim that makes a legacy openclaw tool look like a pi tool."""

    def __init__(self, legacy: Any) -> None:
        self._legacy = legacy
        self.name: str = getattr(legacy, "name", str(legacy))
        self.description: str = getattr(legacy, "description", "")

    def schema(self) -> dict:
        if hasattr(self._legacy, "schema"):
            return self._legacy.schema()
        if hasattr(self._legacy, "parameters"):
            return self._legacy.parameters
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> Any:
        fn = getattr(self._legacy, "execute", None) or getattr(self._legacy, "run", None)
        if fn is None:
            return f"Tool {self.name} has no execute/run method"
        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)


def _wrap_legacy_tool(tool: Any) -> "_WrappedLegacyTool":
    return _WrappedLegacyTool(tool)


__all__ = [
    "create_openclaw_coding_tools",
    "build_pi_tools",
]
