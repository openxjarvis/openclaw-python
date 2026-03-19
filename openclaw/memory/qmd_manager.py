"""QMD (Query-Message-Document) memory manager

Mirrors openclaw/src/memory/qmd-manager.ts

QMD is an external CLI tool that provides semantic search over documents.
This manager spawns qmd commands to index and search memory collections.
"""
from __future__ import annotations

from openclaw.config.paths import resolve_state_dir

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Constants
SNIPPET_HEADER_RE = re.compile(r"@@\s*-([0-9]+),([0-9]+)")
SEARCH_PENDING_UPDATE_WAIT_MS = 500
MAX_QMD_OUTPUT_CHARS = 200_000
HAN_SCRIPT_RE = re.compile(r"[\u3400-\u9fff]")
QMD_BM25_HAN_KEYWORD_LIMIT = 12


QmdSearchMode = Literal["query", "search", "vsearch"]
QmdManagerMode = Literal["full", "status"]
MemorySource = Literal["memory", "sessions"]


@dataclass
class QmdCollection:
    """QMD collection configuration"""
    
    name: str
    """Collection name (scoped by agent)"""
    
    path: str
    """Filesystem path to index"""
    
    pattern: str = "**/*.md"
    """Glob pattern for files"""
    
    kind: Literal["memory", "custom", "sessions"] = "memory"
    """Collection kind"""


@dataclass
class QmdQueryResult:
    """Raw QMD query result"""
    
    docid: str | None = None
    """Document hash"""
    
    score: float | None = None
    """Relevance score"""
    
    collection: str | None = None
    """Collection name"""
    
    file: str | None = None
    """File path hint"""
    
    snippet: str | None = None
    """Text snippet with optional header"""
    
    body: str | None = None
    """Full body text"""


@dataclass
class MemorySearchResult:
    """Final memory search result"""
    
    path: str
    """Relative path"""
    
    start_line: int
    """Start line number"""
    
    end_line: int
    """End line number"""
    
    score: float
    """Relevance score"""
    
    snippet: str
    """Truncated snippet"""
    
    source: MemorySource
    """Source type (memory or sessions)"""
    
    citation: str | None = None
    """Optional citation"""


@dataclass
class SessionExporter:
    """Session export configuration"""
    
    dir: Path
    """Export directory"""
    
    retention_ms: int | None = None
    """Retention time in milliseconds"""
    
    collection_name: str = "sessions"
    """Collection name"""


class QmdMemoryManager:
    """QMD memory backend manager
    
    Manages QMD index, collections, and search operations.
    """
    
    def __init__(
        self,
        agent_id: str,
        workspace_dir: Path,
        config: dict[str, Any] | None = None,
    ):
        self.agent_id = agent_id
        self.workspace_dir = workspace_dir
        self.config = config or {}
        
        # QMD config
        qmd_config = self.config.get("memory", {}).get("qmd", {})
        
        # Command
        self.qmd_command = qmd_config.get("command", "qmd")
        
        # Search mode
        self.search_mode: QmdSearchMode = qmd_config.get("searchMode", "search")
        
        # Collections
        self.collections: list[QmdCollection] = []
        
        # Paths (align with TS: agents/<id>/qmd/, not state/agents/<id>/qmd/)
        state_dir = resolve_state_dir()
        self.qmd_dir = state_dir / "agents" / agent_id / "qmd"
        self.xdg_config_home = self.qmd_dir / "xdg-config"
        self.xdg_cache_home = self.qmd_dir / "xdg-cache"
        self.index_path = self.xdg_cache_home / "qmd" / "index.sqlite"
        
        # Environment
        self.env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(self.xdg_config_home),
            "QMD_CONFIG_DIR": str(self.xdg_config_home),
            "XDG_CACHE_HOME": str(self.xdg_cache_home),
            "NO_COLOR": "1",
        }
        
        # Session exporter
        sessions_config = qmd_config.get("sessions", {})
        if sessions_config.get("enabled", False):
            export_dir = sessions_config.get("exportDir")
            if export_dir:
                export_path = Path(export_dir)
            else:
                export_path = self.qmd_dir / "sessions"
            
            retention_days = sessions_config.get("retentionDays")
            retention_ms = retention_days * 24 * 60 * 60 * 1000 if retention_days else None
            
            self.session_exporter = SessionExporter(
                dir=export_path,
                retention_ms=retention_ms,
                collection_name=f"sessions-{agent_id}",
            )
        else:
            self.session_exporter = None
        
        # Limits
        limits = qmd_config.get("limits", {})
        self.max_results = limits.get("maxResults", 6)
        self.max_snippet_chars = limits.get("maxSnippetChars", 700)
        self.max_injected_chars = limits.get("maxInjectedChars", 4000)
        self.timeout_ms = limits.get("timeoutMs", 4000)
        
        # Update state
        self.update_running = False
    
    async def initialize(self, mode: QmdManagerMode = "full") -> None:
        """Initialize QMD manager.
        
        Args:
            mode: Initialization mode ('full' or 'status')
        """
        self.bootstrap_collections()
        
        if mode == "status":
            return
        
        # Create directories
        self.xdg_config_home.mkdir(parents=True, exist_ok=True)
        self.xdg_cache_home.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self.session_exporter:
            self.session_exporter.dir.mkdir(parents=True, exist_ok=True)
        
        # Symlink shared models
        await self.symlink_shared_models()
        
        # Ensure collections
        await self.ensure_collections()
        
        # Run boot update
        qmd_config = self.config.get("memory", {}).get("qmd", {})
        update_config = qmd_config.get("update", {})
        
        if update_config.get("onBoot", True):
            try:
                await self.run_update("boot")
            except Exception as e:
                logger.warning(f"QMD boot update failed: {e}")
    
    def bootstrap_collections(self) -> None:
        """Bootstrap collections from config."""
        qmd_config = self.config.get("memory", {}).get("qmd", {})
        
        # Default memory collections
        if qmd_config.get("includeDefaultMemory", True):
            self.collections.extend([
                QmdCollection(
                    name=f"memory-root-{self.agent_id}",
                    path=str(self.workspace_dir),
                    pattern="MEMORY.md",
                    kind="memory",
                ),
                QmdCollection(
                    name=f"memory-alt-{self.agent_id}",
                    path=str(self.workspace_dir),
                    pattern="memory.md",
                    kind="memory",
                ),
                QmdCollection(
                    name=f"memory-dir-{self.agent_id}",
                    path=str(self.workspace_dir / "memory"),
                    pattern="**/*.md",
                    kind="memory",
                ),
            ])
        
        # Custom paths
        custom_paths = qmd_config.get("paths", [])
        for idx, path_config in enumerate(custom_paths):
            collection_name = path_config.get("name", f"custom-{idx + 1}")
            collection_name = f"{collection_name}-{self.agent_id}"
            
            self.collections.append(QmdCollection(
                name=collection_name,
                path=path_config["path"],
                pattern=path_config.get("pattern", "**/*.md"),
                kind="custom",
            ))
        
        # Sessions collection
        if self.session_exporter:
            self.collections.append(QmdCollection(
                name=self.session_exporter.collection_name,
                path=str(self.session_exporter.dir),
                pattern="**/*.md",
                kind="sessions",
            ))
    
    async def symlink_shared_models(self) -> None:
        """Symlink shared QMD models directory."""
        default_cache = Path.home() / ".cache" / "qmd" / "models"
        agent_models = self.xdg_cache_home / "qmd" / "models"
        
        if default_cache.exists() and not agent_models.exists():
            try:
                agent_models.parent.mkdir(parents=True, exist_ok=True)
                agent_models.symlink_to(default_cache, target_is_directory=True)
                logger.debug(f"Symlinked QMD models: {agent_models} -> {default_cache}")
            except Exception as e:
                logger.debug(f"Failed to symlink QMD models: {e}")
    
    async def ensure_collections(self) -> None:
        """Ensure all collections are registered with QMD."""
        for collection in self.collections:
            try:
                await self.run_qmd([
                    "collection",
                    "add",
                    collection.path,
                    "--name",
                    collection.name,
                    "--mask",
                    collection.pattern,
                ], timeout_ms=30000)
                logger.debug(f"Ensured QMD collection: {collection.name}")
            except Exception as e:
                logger.warning(f"Failed to ensure collection {collection.name}: {e}")
    
    async def run_update(self, trigger: str, wait: bool = True) -> None:
        """Run QMD update (reindex and embed).
        
        Args:
            trigger: Update trigger ('boot', 'interval', 'manual')
            wait: Whether to wait for completion
        """
        if self.update_running:
            logger.debug("QMD update already running, skipping")
            return
        
        self.update_running = True
        
        try:
            # Run update
            logger.info(f"Running QMD update ({trigger})")
            await self.run_qmd(["update"], timeout_ms=120000)
            
            # Run embed
            await self.run_qmd(["embed"], timeout_ms=120000)
            
            logger.info(f"QMD update completed ({trigger})")
        except Exception as e:
            logger.warning(f"QMD update failed ({trigger}): {e}")
        finally:
            self.update_running = False
    
    async def run_qmd(
        self,
        args: list[str],
        timeout_ms: int | None = None,
    ) -> subprocess.CompletedProcess:
        """Run qmd command.
        
        Args:
            args: Command arguments
            timeout_ms: Timeout in milliseconds
            
        Returns:
            CompletedProcess result
        """
        timeout = (timeout_ms / 1000) if timeout_ms else None
        
        cmd = [self.qmd_command] + args
        
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            
            stdout, stderr = await asyncio.wait_for(
                result.communicate(),
                timeout=timeout,
            )
            
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=result.returncode or 0,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            logger.error(f"QMD command timed out: {' '.join(cmd)}")
            raise
        except Exception as e:
            logger.error(f"QMD command failed: {' '.join(cmd)}: {e}")
            raise
    
    async def search(
        self,
        query: str,
        opts: dict[str, Any] | None = None,
    ) -> list[MemorySearchResult]:
        """Search using QMD.
        
        Args:
            query: Search query
            opts: Search options
            
        Returns:
            List of memory search results
        """
        opts = opts or {}
        limit = opts.get("maxResults", self.max_results)
        
        # Build search command
        search_mode = opts.get("searchMode", self.search_mode)
        
        args = [search_mode, query, "--json", "-n", str(limit)]
        
        # Run search
        try:
            result = await self.run_qmd(args, timeout_ms=self.timeout_ms)
            
            # Parse results
            raw_results = self.parse_qmd_json(result.stdout, result.stderr)
            
            # Convert to memory search results
            return await self.process_search_results(raw_results)
        
        except Exception as e:
            logger.error(f"QMD search failed: {e}")
            return []
    
    def parse_qmd_json(self, stdout: str, stderr: str) -> list[QmdQueryResult]:
        """Parse QMD JSON output.
        
        Args:
            stdout: Standard output
            stderr: Standard error
            
        Returns:
            List of query results
        """
        if not stdout or not stdout.strip():
            return []
        
        try:
            data = json.loads(stdout)
            
            if isinstance(data, list):
                results = []
                for item in data:
                    if isinstance(item, dict):
                        results.append(QmdQueryResult(
                            docid=item.get("docid"),
                            score=item.get("score"),
                            collection=item.get("collection"),
                            file=item.get("file"),
                            snippet=item.get("snippet"),
                            body=item.get("body"),
                        ))
                return results
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse QMD JSON: {e}")
        
        return []
    
    async def process_search_results(
        self,
        raw_results: list[QmdQueryResult],
    ) -> list[MemorySearchResult]:
        """Process raw QMD results into memory search results.
        
        Args:
            raw_results: Raw QMD query results
            
        Returns:
            List of processed memory search results
        """
        results = []
        
        for raw in raw_results:
            if not raw.snippet and not raw.body:
                continue
            
            # Determine source
            source: MemorySource = "sessions" if raw.collection and "sessions" in raw.collection else "memory"
            
            # Extract snippet lines
            snippet = raw.snippet or raw.body or ""
            start_line, end_line = self.extract_snippet_lines(snippet)
            
            # Truncate snippet
            if len(snippet) > self.max_snippet_chars:
                snippet = snippet[: self.max_snippet_chars] + "..."
            
            # Resolve path
            path = raw.file or f"qmd/{raw.collection}/{raw.docid}"
            
            results.append(MemorySearchResult(
                path=path,
                start_line=start_line,
                end_line=end_line,
                score=raw.score or 0.0,
                snippet=snippet,
                source=source,
            ))
        
        return results
    
    def extract_snippet_lines(self, snippet: str) -> tuple[int, int]:
        """Extract line numbers from snippet header.
        
        Args:
            snippet: Snippet text (may have @@ -startLine,count header)
            
        Returns:
            Tuple of (start_line, end_line)
        """
        match = SNIPPET_HEADER_RE.search(snippet)
        if match:
            start = int(match.group(1))
            count = int(match.group(2))
            return (start, start + count - 1)
        
        # Fallback: count lines
        lines = snippet.split("\n")
        return (1, len(lines))


async def create_qmd_memory_manager(
    agent_id: str,
    workspace_dir: Path | str,
    config: dict[str, Any] | None = None,
) -> QmdMemoryManager:
    """Create and initialize QMD memory manager.
    
    Args:
        agent_id: Agent identifier
        workspace_dir: Workspace directory
        config: OpenClaw configuration
        
    Returns:
        Initialized QmdMemoryManager
    """
    if isinstance(workspace_dir, str):
        workspace_dir = Path(workspace_dir)
    
    manager = QmdMemoryManager(agent_id, workspace_dir, config)
    await manager.initialize()
    
    return manager


__all__ = [
    "QmdMemoryManager",
    "QmdCollection",
    "QmdQueryResult",
    "MemorySearchResult",
    "SessionExporter",
    "QmdSearchMode",
    "QmdManagerMode",
    "MemorySource",
    "create_qmd_memory_manager",
]
