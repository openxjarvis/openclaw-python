"""
Memory search and retrieval tools

Matches TypeScript src/agents/tools/memory-tool.ts
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from openclaw.memory import MemorySearchManager, MemorySearchResult
from openclaw.memory.manager import get_memory_search_manager

from .base import AgentTool, ToolResult

logger = logging.getLogger(__name__)


class MemorySearchTool(AgentTool):
    """
    Memory search tool (matches TS createMemorySearchTool).
    
    Semantically searches MEMORY.md + memory/*.md (and optional session transcripts)
    before answering questions about prior work, decisions, dates, people, preferences, or todos.
    """
    
    def __init__(
        self,
        workspace_dir: Path,
        config: Any | None = None,
        session_key: str | None = None
    ):
        """
        Initialize memory search tool.
        
        Args:
            workspace_dir: Workspace directory
            config: OpenClaw configuration
            session_key: Optional session key for filtering
        """
        super().__init__()
        self.name = "memory_search"
        self.description = (
            "Mandatory recall step: semantically search MEMORY.md + memory/*.md "
            "(and optional session transcripts) before answering questions about "
            "prior work, decisions, dates, people, preferences, or todos; "
            "returns top snippets with path + lines. "
            "If response has disabled=true, memory retrieval is unavailable and should be surfaced to the user."
        )
        self.workspace_dir = workspace_dir
        self.config = config
        self.session_key = session_key
        self._manager: MemorySearchManager | None = None
    
    def get_schema(self) -> dict[str, Any]:
        """Get JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "maxResults": {
                    "type": "number",
                    "description": "Maximum number of results (default: 10)"
                },
                "minScore": {
                    "type": "number",
                    "description": "Minimum score threshold (0-1, default: 0)"
                }
            },
            "required": ["query"]
        }
    
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute memory search (matches TS execute logic)."""
        query = params.get("query", "")
        if not query:
            return ToolResult(
                success=False,
                content="",
                error="query parameter required"
            )
        
        max_results = params.get("maxResults", 10)
        min_score = params.get("minScore", 0.0)
        
        try:
            # Get or create manager
            if self._manager is None:
                self._manager = await get_memory_search_manager(
                    self.workspace_dir,
                    self.config
                )
            
            # Search
            results = await self._manager.search(query, {
                "maxResults": max_results,
                "minScore": min_score,
                "sessionKey": self.session_key
            })
            
            # Format results
            status = self._manager.status()
            
            formatted_results = self._format_results(results)
            
            return ToolResult(
                success=True,
                content=formatted_results,
                metadata={
                    "results": [self._result_to_dict(r) for r in results],
                    "provider": status.provider,
                    "backend": status.backend
                }
            )
        
        except Exception as e:
            logger.error(f"Memory search failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=str(e)
            )
    
    def _format_results(self, results: list[MemorySearchResult]) -> str:
        """Format search results for display."""
        if not results:
            return "No results found."
        
        lines = []
        for i, result in enumerate(results, 1):
            citation = self._format_citation(result)
            lines.append(f"{i}. {citation}")
            lines.append(f"   Score: {result.score:.2f}")
            lines.append(f"   {result.snippet}")
            lines.append("")
        
        return "\n".join(lines)
    
    def _format_citation(self, result: MemorySearchResult) -> str:
        """Format citation string (matches TS formatCitation)."""
        if result.start_line == result.end_line:
            line_range = f"#L{result.start_line}"
        else:
            line_range = f"#L{result.start_line}-L{result.end_line}"
        
        return f"{result.path}{line_range}"
    
    def _result_to_dict(self, result: MemorySearchResult) -> dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "path": result.path,
            "startLine": result.start_line,
            "endLine": result.end_line,
            "score": result.score,
            "snippet": result.snippet,
            "source": result.source.value,
            "citation": self._format_citation(result)
        }


class MemoryGetTool(AgentTool):
    """
    Memory file reader tool (matches TS createMemoryGetTool).
    
    Safe snippet read from MEMORY.md or memory/*.md with optional from/lines.
    Use after memory_search to pull only the needed lines and keep context small.
    """
    
    def __init__(
        self,
        workspace_dir: Path,
        config: Any | None = None,
        session_key: str | None = None
    ):
        """
        Initialize memory get tool.
        
        Args:
            workspace_dir: Workspace directory
            config: OpenClaw configuration
            session_key: Optional session key
        """
        super().__init__()
        self.name = "memory_get"
        self.description = (
            "Safe snippet read from MEMORY.md or memory/*.md with optional from/lines; "
            "use after memory_search to pull only the needed lines and keep context small."
        )
        self.workspace_dir = workspace_dir
        self.config = config
        self.session_key = session_key
        self._manager: MemorySearchManager | None = None
    
    def get_schema(self) -> dict[str, Any]:
        """Get JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path (e.g., MEMORY.md or memory/project.md)"
                },
                "from": {
                    "type": "number",
                    "description": "Starting line number (1-indexed, optional)"
                },
                "lines": {
                    "type": "number",
                    "description": "Number of lines to read (optional)"
                }
            },
            "required": ["path"]
        }
    
    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute memory file read (matches TS execute logic)."""
        rel_path = params.get("path", "")
        if not rel_path:
            return ToolResult(
                success=False,
                content="",
                error="path parameter required"
            )
        
        from_line = params.get("from")
        lines = params.get("lines")
        
        try:
            # Get or create manager
            if self._manager is None:
                self._manager = await get_memory_search_manager(
                    self.workspace_dir,
                    self.config
                )
            
            # Read file
            result = await self._manager.read_file({
                "relPath": rel_path,
                "from": from_line,
                "lines": lines
            })
            
            text = result.get("text", "")
            error = result.get("error")
            
            if error:
                return ToolResult(
                    success=False,
                    content="",
                    error=error
                )
            
            return ToolResult(
                success=True,
                content=text,
                metadata={
                    "path": rel_path,
                    "from": from_line,
                    "lines": lines
                }
            )
        
        except Exception as e:
            logger.error(f"Memory read failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                content="",
                error=str(e)
            )
