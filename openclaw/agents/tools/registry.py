"""Tool registry — uses pi_coding_agent for base coding tools.

The base coding tools (read, write, edit, bash, grep, find, ls) are imported
directly from pi_coding_agent, mirroring how openclaw TypeScript imports
``codingTools`` from ``@pi-coding-agent``.  All openclaw-specific tools
(web, browser, channels, cron, canvas, voice, …) are kept as-is.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from .base import AgentTool
from . import create_coding_tools  # falls back to pi_coding_agent via __init__

if TYPE_CHECKING:
    from ..session import SessionManager

logger = logging.getLogger(__name__)
from openclaw.browser.tools.browser_tool import UnifiedBrowserTool as BrowserTool
from .canvas import CanvasTool
from .channel_actions import (
    DiscordActionsTool,
    MessageTool,
    SlackActionsTool,
    TelegramActionsTool,
    WhatsAppActionsTool,
)
from .cron import CronTool
# document_gen removed - use pptx skill for PPT, other tools for PDF
from .image import ImageTool
from .memory import MemoryGetTool, MemorySearchTool
from .nodes import NodesTool
from .patch import ApplyPatchTool
from .process import ProcessTool
from .sessions import SessionsHistoryTool, SessionsListTool, SessionsSendTool, SessionsSpawnTool
from .tts import TTSTool
from .voice_call import VoiceCallTool
from .web import WebFetchTool, WebSearchTool


class ToolRegistry:
    """Registry of available tools"""

    def __init__(
        self,
        session_manager: Optional["SessionManager"] = None,
        channel_registry: Any | None = None,
        workspace_dir: Any | None = None,
        config: Any | None = None,
        auto_register: bool = True,
    ):
        self._tools: dict[str, AgentTool] = {}
        self._session_manager = session_manager
        self._channel_registry = channel_registry
        self._workspace_dir = workspace_dir
        self._config = config
        if auto_register:
            self._register_default_tools()

    def _register_default_tools(self) -> None:
        """Register default tools (uses _register_unsafe to avoid duplicate errors on reload)."""
        # Get current working directory
        cwd = str(self._workspace_dir) if self._workspace_dir else os.getcwd()

        # Use factory functions to create coding tools (read, bash, edit, write)
        coding_tools = create_coding_tools(cwd)
        for tool in coding_tools:
            self._tools[tool.name] = tool

        # Also register TS-compatible aliases (read_file, write_file, edit_file)
        from .file_ops import ReadFileTool, WriteFileTool, EditFileTool  # noqa: PLC0415
        for t in [ReadFileTool(), WriteFileTool(), EditFileTool()]:
            self._tools[t.name] = t

        # Web tools
        self._tools[WebFetchTool().name] = WebFetchTool()
        self._tools[WebSearchTool().name] = WebSearchTool()

        # Image analysis — pass workspace_root so the tool saves the image to
        # disk and emits a MEDIA:/abs/path token in its result, which lets
        # channel_manager deliver the file to the user (mirrors TS imageResult).
        from pathlib import Path
        _img_ws = Path(self._workspace_dir) if self._workspace_dir else None
        img = ImageTool(workspace_root=_img_ws)
        self._tools[img.name] = img

        # Memory search (if workspace available)
        if self._workspace_dir:
            from pathlib import Path
            workspace_path = Path(self._workspace_dir) if isinstance(self._workspace_dir, str) else self._workspace_dir
            mst = MemorySearchTool(workspace_path, self._config); self._tools[mst.name] = mst
            mgt = MemoryGetTool(workspace_path, self._config); self._tools[mgt.name] = mgt

        # Session management (only if session manager available)
        if self._session_manager:
            for t in [
                SessionsListTool(self._session_manager),
                SessionsHistoryTool(self._session_manager),
                SessionsSendTool(self._session_manager),
                SessionsSpawnTool(self._session_manager),
            ]:
                self._tools[t.name] = t

        # Advanced tools
        _advanced: list[AgentTool] = []

        # Only include BrowserTool when browser.enabled=true in config.
        # When disabled, keeping the tool in the list causes the agent to retry
        # it repeatedly (every ~2s) even though it always fails with a PERMANENT_ERROR.
        try:
            from openclaw.config.loader import load_config as _lc
            _cfg = _lc(as_dict=True) or {}
            _browser_enabled = _cfg.get("browser", {}).get("enabled", False)
        except Exception:
            _browser_enabled = False
        if _browser_enabled:
            _advanced.append(BrowserTool())

        _advanced += [
            CronTool(channel_registry=self._channel_registry, session_manager=self._session_manager),
            # PPTGeneratorTool(),  # Removed: Use pptx skill for presentations
            # PDFGeneratorTool(),  # Removed: Use write tool + system PDF converters
            TTSTool(),
            ProcessTool(),
        ]
        for t in _advanced:
            self._tools[t.name] = t

        # Channel actions (if channel registry available)
        if self._channel_registry:
            for t in [
                MessageTool(self._channel_registry),
                TelegramActionsTool(self._channel_registry),
                DiscordActionsTool(self._channel_registry),
                SlackActionsTool(self._channel_registry),
                WhatsAppActionsTool(self._channel_registry),
            ]:
                self._tools[t.name] = t

        # Special features & patch tool
        for t in [NodesTool(), CanvasTool(), VoiceCallTool(), ApplyPatchTool()]:
            self._tools[t.name] = t

    def _register_unsafe(self, tool: AgentTool) -> None:
        """Register a tool without duplicate check (used internally)."""
        self._tools[tool.name] = tool

    def register(self, tool: AgentTool) -> None:
        """Register a tool (raises ValueError if name already registered)."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")

        # Wrap old-style tools that override execute() with old signature
        import inspect
        if hasattr(tool, 'execute'):
            sig = inspect.signature(tool.execute)
            params = list(sig.parameters.keys())

            # Old signature: execute(self, params) or execute(self, args)
            # New signature: execute(self, tool_call_id, params, signal, on_update)
            if 'self' in params and len(params) == 2 and 'tool_call_id' not in params:
                original_execute = tool.execute

                async def wrapped_execute(tool_call_id: str, params: dict, signal=None, on_update=None):
                    return await original_execute(params)

                tool.execute = wrapped_execute

        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a registered tool (no-op if not found)."""
        self._tools.pop(name, None)

    def get(self, name: str) -> AgentTool | None:
        """Get tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def list(self) -> list[AgentTool]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_tools(self) -> list[AgentTool]:
        """List all tools (alias for list())."""
        return self.list()

    def list_names(self) -> list[str]:
        """Return a list of all registered tool names."""
        return list(self._tools.keys())

    async def execute(self, name: str, **kwargs) -> Any:
        """Execute a registered tool by name.

        Raises:
            KeyError: If the tool is not registered.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool '{name}' not registered")
        return await tool.execute(**kwargs)

    def get_schema(self, name: str) -> dict | None:
        """Return the JSON schema for a tool (None if not found)."""
        tool = self._tools.get(name)
        if tool is None:
            return None
        if hasattr(tool, 'get_schema'):
            return tool.get_schema()
        if hasattr(tool, 'parameters'):
            return tool.parameters
        return None

    def get_all_schemas(self) -> dict[str, dict]:
        """Return a dict of {tool_name: schema} for all registered tools."""
        return {
            name: (tool.get_schema() if hasattr(tool, 'get_schema') else getattr(tool, 'parameters', {}))
            for name, tool in self._tools.items()
        }

    def get_tools_by_profile(self, profile: str = "full") -> list[AgentTool]:
        """
        Get tools filtered by profile (owner-only, agent-specific, etc.)
        
        Matches TypeScript tool filtering in agents/tools/registry.ts
        
        Args:
            profile: Profile name - "all", "owner", "agent:agentId", "minimal", "coding", "messaging", or "full"
        
        Returns:
            List of tools matching the profile
        """
        # Handle special profiles
        if profile == "all":
            return self.list_tools()
        
        elif profile == "owner":
            # Owner-only tools: sensitive operations like gateway, config, system management
            owner_tool_names = {
                "gateway", "agents_list", "sessions_spawn", "sessions_send",
                "cron", "process", "voice_call", "nodes",
            }
            return [tool for tool in self.list_tools() if tool.name in owner_tool_names]
        
        elif profile.startswith("agent:"):
            # Agent-specific tools: filtered based on agent configuration
            # For now, return all non-owner tools (can be extended with agent-specific logic)
            owner_tool_names = {"gateway", "agents_list", "cron", "process", "voice_call"}
            return [tool for tool in self.list_tools() if tool.name not in owner_tool_names]
        
        elif profile == "minimal":
            # Minimal profile: basic read and web operations
            minimal_names = {"read_file", "web_fetch"}
            return [tool for tool in self.list_tools() if tool.name in minimal_names]
        
        elif profile == "coding":
            # Coding profile: file operations + bash + web
            # Includes both TS-style names (read_file) and Python-style aliases (read)
            coding_names = {
                "read_file", "write_file", "edit_file", "bash",
                "read", "write", "edit",
                "web_fetch", "web_search", "grep", "ls",
            }
            return [tool for tool in self.list_tools() if tool.name in coding_names]
        
        elif profile == "messaging":
            # Messaging profile: web + channel actions
            messaging_names = {"web_fetch", "web_search", "message", "image"}
            return [tool for tool in self.list_tools() if tool.name in messaging_names]
        
        else:  # "full" or default
            return self.list_tools()


# Global tool registry
_global_registry = ToolRegistry()


def get_tool_registry(session_manager: Any | None = None) -> ToolRegistry:
    """Get global tool registry"""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry(session_manager=session_manager)
    return _global_registry
