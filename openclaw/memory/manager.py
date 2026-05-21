"""Memory search manager implementation.

Matches TypeScript src/memory/manager.ts (simplified / intermediate tier).

Enhancements over the original stub:
- SQLite FTS5 index as intermediate backend (replaces substring matching).
- Session source searching (``MemorySource.SESSIONS``).
- Hybrid merge wired in via ``merge_hybrid_results`` from ``hybrid.py``.
- Temporal decay default is ``enabled=False`` (aligns with TS default).
- INDEX_CACHE module-level singleton (mirrors TS INDEX_CACHE Map<string, MemoryIndexManager>).
- Unified factory: returns BuiltinMemoryManager when embedding provider is configured.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from .hybrid import SearchResult, merge_hybrid_results
from .types import (
    MemoryEmbeddingProbeResult,
    MemoryProviderStatus,
    MemorySearchResult,
    MemorySource,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# INDEX_CACHE: module-level singleton — mirrors TS INDEX_CACHE Map<string, MemoryIndexManager>
# A CACHE_PENDING guard prevents duplicate construction under concurrent calls.
# ---------------------------------------------------------------------------
_INDEX_CACHE: dict[str, "SimpleMemorySearchManager"] = {}
_CACHE_LOCK: asyncio.Lock | None = None


def _get_cache_lock() -> asyncio.Lock:
    """Lazy-init the async lock (must be created inside a running event loop)."""
    global _CACHE_LOCK
    if _CACHE_LOCK is None:
        _CACHE_LOCK = asyncio.Lock()
    return _CACHE_LOCK

# SQLite FTS5 table name (mirrors TS FTS_TABLE)
_FTS_TABLE = "chunks_fts"

# Temporal decay default: disabled (mirrors TS DEFAULT_TEMPORAL_DECAY_CONFIG)
_DEFAULT_TEMPORAL_DECAY = {"enabled": False, "halfLifeDays": 30}


def _create_fts_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite FTS5 database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    # Chunks table holds file metadata
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            source  TEXT    NOT NULL,
            path    TEXT    NOT NULL,
            start_line  INTEGER,
            end_line    INTEGER,
            content TEXT    NOT NULL,
            updated_at  INTEGER NOT NULL DEFAULT 0
        )
    """)
    # FTS5 virtual table for full-text search
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
        USING fts5(
            content,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 1'
        )
    """)
    # Triggers to keep FTS in sync
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO {_FTS_TABLE}(rowid, content) VALUES (new.id, new.content);
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, content) VALUES('delete', old.id, old.content);
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, content) VALUES('delete', old.id, old.content);
            INSERT INTO {_FTS_TABLE}(rowid, content) VALUES (new.id, new.content);
        END
    """)
    conn.commit()
    return conn


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    source_filter: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Run an FTS5 keyword search, returning raw rows."""
    try:
        # Escape FTS5 query: wrap in double-quotes to treat as phrase
        escaped = query.replace('"', '""')
        fts_query = f'"{escaped}"'
        if source_filter:
            sql = f"""
                SELECT c.id, c.source, c.path, c.start_line, c.end_line, c.content,
                       bm25({_FTS_TABLE}) AS rank
                FROM {_FTS_TABLE}
                JOIN chunks c ON c.id = {_FTS_TABLE}.rowid
                WHERE {_FTS_TABLE} MATCH ? AND c.source = ?
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, source_filter, limit)).fetchall()
        else:
            sql = f"""
                SELECT c.id, c.source, c.path, c.start_line, c.end_line, c.content,
                       bm25({_FTS_TABLE}) AS rank
                FROM {_FTS_TABLE}
                JOIN chunks c ON c.id = {_FTS_TABLE}.rowid
                WHERE {_FTS_TABLE} MATCH ?
                ORDER BY rank
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, limit)).fetchall()
        results = []
        for row in rows:
            row_id, source, path, start_line, end_line, content, rank = row
            # BM25 rank is negative (lower = better); convert to [0, 1] score
            score = max(0.0, min(1.0, 1.0 / (1.0 + abs(float(rank or 0)))))
            results.append({
                "id": str(row_id),
                "source": source,
                "path": path,
                "start_line": start_line or 1,
                "end_line": end_line or 1,
                "content": content,
                "score": score,
            })
        return results
    except sqlite3.OperationalError:
        return []


def _upsert_chunk(
    conn: sqlite3.Connection,
    source: str,
    path: str,
    start_line: int,
    end_line: int,
    content: str,
) -> None:
    """Insert or replace a chunk in the FTS index."""
    now = int(time.time() * 1000)
    # Check for existing row
    existing = conn.execute(
        "SELECT id FROM chunks WHERE source=? AND path=? AND start_line=? AND end_line=?",
        (source, path, start_line, end_line),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE chunks SET content=?, updated_at=? WHERE id=?",
            (content, now, existing[0]),
        )
    else:
        conn.execute(
            "INSERT INTO chunks (source, path, start_line, end_line, content, updated_at) VALUES (?,?,?,?,?,?)",
            (source, path, start_line, end_line, content, now),
        )


class SimpleMemorySearchManager:
    """Memory search manager backed by SQLite FTS5.

    Replaces the original substring-matching implementation with:
    - SQLite FTS5 for keyword search (intermediate backend).
    - Hybrid merge from ``hybrid.py`` (weighted vector + keyword).
    - Session source searching when ``include_sessions=True`` or the
      config sources include ``MemorySource.SESSIONS``.
    - Temporal decay with default ``enabled=False``.

    Mirrors TS MemorySearchManager interface.
    """

    def __init__(
        self,
        workspace_dir: Path,
        config: Any | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.config = config
        self._memory_files: list[Path] = []
        self._session_files: list[Path] = []
        self._indexed = False

        # Resolve DB path inside workspace
        db_dir = workspace_dir / ".openclaw"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(db_dir / "memory.db")
        try:
            self._db: sqlite3.Connection | None = _create_fts_db(db_path)
            self._fts_enabled = True
        except Exception as exc:
            logger.warning("FTS5 SQLite unavailable: %s — falling back to substring", exc)
            self._db = None
            self._fts_enabled = False

        # Resolve which sources to include
        self._sources: set[str] = {"memory"}
        cfg_sources = self._resolve_config_sources()
        if cfg_sources:
            self._sources = set(cfg_sources)

        # Temporal decay config (default: disabled)
        self._temporal_decay: dict[str, Any] = dict(_DEFAULT_TEMPORAL_DECAY)
        td_cfg = self._resolve_config_temporal_decay()
        if td_cfg:
            self._temporal_decay.update(td_cfg)

    def _resolve_config_sources(self) -> list[str] | None:
        try:
            cfg = self.config
            if isinstance(cfg, dict):
                return cfg.get("memory", {}).get("sources") or None
            if cfg is not None:
                mem = getattr(cfg, "memory", None)
                if mem is not None:
                    return getattr(mem, "sources", None)
        except Exception:
            pass
        return None

    def _resolve_config_temporal_decay(self) -> dict[str, Any] | None:
        try:
            cfg = self.config
            if isinstance(cfg, dict):
                return cfg.get("memory", {}).get("temporalDecay") or None
            if cfg is not None:
                mem = getattr(cfg, "memory", None)
                if mem is not None:
                    td = getattr(mem, "temporalDecay", None) or getattr(mem, "temporal_decay", None)
                    if isinstance(td, dict):
                        return td
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        opts: dict[str, Any] | None = None,
    ) -> list[MemorySearchResult]:
        """Search memory using FTS5 + optional hybrid merge."""
        if not self._indexed:
            await self._index_files()

        opts = opts or {}
        max_results = int(opts.get("maxResults", 10))
        min_score = float(opts.get("minScore", 0.0))
        include_sessions = bool(
            opts.get("includeSessions") or opts.get("include_sessions")
            or "sessions" in self._sources
        )

        all_results: list[MemorySearchResult] = []

        # FTS5 keyword search (intermediate backend)
        if self._fts_enabled and self._db is not None:
            keyword_rows = _fts_search(
                self._db,
                query,
                source_filter=None,
                limit=max_results * 3,
            )
            for row in keyword_rows:
                source = (
                    MemorySource.SESSIONS
                    if row["source"] == "sessions"
                    else MemorySource.MEMORY
                )
                if source == MemorySource.SESSIONS and not include_sessions:
                    continue
                all_results.append(MemorySearchResult(
                    path=row["path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    score=row["score"],
                    snippet=row["content"][:500],
                    source=source,
                ))

            if all_results:
                # Apply hybrid merge using keyword results as both vector + keyword
                # (no vector provider in this tier)
                kw_search_results = [
                    SearchResult(
                        id=f"{r.source}:{r.path}:{r.start_line}:{r.end_line}",
                        text=r.snippet,
                        path=r.path,
                        source=str(r.source),
                        score=r.score,
                        start_line=r.start_line,
                        end_line=r.end_line,
                    )
                    for r in all_results
                ]
                merged = merge_hybrid_results(
                    vector_results=[],
                    keyword_results=kw_search_results,
                    vector_weight=0.0,
                    text_weight=1.0,
                    min_score=min_score,
                )
                all_results = [
                    MemorySearchResult(
                        path=r.path,
                        start_line=r.start_line or 1,
                        end_line=r.end_line or 1,
                        score=r.score,
                        snippet=r.text,
                        source=MemorySource.SESSIONS
                        if r.source == "sessions"
                        else MemorySource.MEMORY,
                    )
                    for r in merged
                ]
        else:
            # Fallback: substring search
            all_results = self._substring_search(query, min_score, include_sessions)

        all_results.sort(key=lambda r: r.score, reverse=True)
        top_results = all_results[:max_results]

        # Emit recall event — mirrors TS appendMemoryHostEvent("memory.recall.recorded")
        try:
            from .events import (
                MemoryRecallRecordedEvent,
                MemoryRecallResultEntry,
                append_memory_host_event,
            )
            evt = MemoryRecallRecordedEvent(
                query=query,
                result_count=len(top_results),
                results=[
                    MemoryRecallResultEntry(
                        path=r.path,
                        start_line=r.start_line,
                        end_line=r.end_line,
                        score=r.score,
                    )
                    for r in top_results
                ],
            )
            append_memory_host_event(str(self.workspace_dir), evt)
        except Exception as _evt_exc:
            logger.debug("Failed to emit recall event: %s", _evt_exc)

        return top_results

    def _substring_search(
        self,
        query: str,
        min_score: float,
        include_sessions: bool,
    ) -> list[MemorySearchResult]:
        """Fallback substring search when FTS5 is unavailable."""
        results: list[MemorySearchResult] = []
        query_lower = query.lower()
        files_to_search = list(self._memory_files)
        if include_sessions:
            files_to_search.extend(self._session_files)

        for file_path in files_to_search:
            source = (
                MemorySource.SESSIONS
                if file_path in self._session_files
                else MemorySource.MEMORY
            )
            try:
                content = file_path.read_text(encoding="utf-8")
                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if query_lower in line.lower():
                        score = 1.0 if query.lower() == line.lower().strip() else 0.5
                        if score >= min_score:
                            start_idx = max(0, i - 2)
                            end_idx = min(len(lines), i + 3)
                            snippet = "\n".join(lines[start_idx:end_idx])
                            try:
                                rel = file_path.relative_to(self.workspace_dir)
                            except ValueError:
                                rel = file_path
                            results.append(MemorySearchResult(
                                path=str(rel),
                                start_line=start_idx + 1,
                                end_line=end_idx,
                                score=score,
                                snippet=snippet,
                                source=source,
                            ))
            except Exception as exc:
                logger.debug("Substring search error %s: %s", file_path, exc)
        return results

    async def read_file(self, params: dict[str, Any]) -> dict[str, str]:
        """Read a file from the workspace."""
        rel_path = params.get("relPath", "")
        from_line = params.get("from")
        lines_count = params.get("lines")

        file_path = self.workspace_dir / rel_path
        if not file_path.exists():
            return {"path": rel_path, "text": "", "error": "File not found"}

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            if from_line is not None:
                start = max(0, from_line - 1)
                end = min(len(lines), start + lines_count) if lines_count else len(lines)
                lines = lines[start:end]
            return {"path": rel_path, "text": "\n".join(lines)}
        except Exception as exc:
            logger.error("read_file %s: %s", file_path, exc)
            return {"path": rel_path, "text": "", "error": str(exc)}

    def status(self) -> MemoryProviderStatus:
        """Return provider status."""
        chunk_count = 0
        if self._db:
            try:
                chunk_count = self._db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            except Exception:
                pass
        return MemoryProviderStatus(
            backend="builtin",
            provider="fts5-sqlite" if self._fts_enabled else "simple-text-search",
            files=len(self._memory_files) + len(self._session_files),
            chunks=chunk_count,
            workspace_dir=str(self.workspace_dir),
            fts={"enabled": self._fts_enabled, "available": self._fts_enabled},
            sources=[MemorySource(s) for s in self._sources if s in ("memory", "sessions")],
        )

    async def sync(self, params: dict[str, Any] | None = None) -> None:
        """Re-index memory and session files into FTS5."""
        await self._index_files()
        if self._fts_enabled and self._db is not None:
            self._db.commit()

    async def probe_embedding_availability(self) -> MemoryEmbeddingProbeResult:
        return MemoryEmbeddingProbeResult(
            ok=False,
            error="Embeddings not configured (FTS5 intermediate backend only)",
        )

    async def probe_vector_availability(self) -> bool:
        return False

    async def close(self) -> None:
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None
        self._memory_files = []
        self._session_files = []
        self._indexed = False

    # ------------------------------------------------------------------
    # Internal indexing
    # ------------------------------------------------------------------

    async def _index_files(self) -> None:
        """Discover and index memory (and optionally session) files."""
        self._memory_files = []
        self._session_files = []

        # Memory files
        memory_file = self.workspace_dir / "MEMORY.md"
        if memory_file.exists():
            self._memory_files.append(memory_file)

        memory_dir = self.workspace_dir / "memory"
        if memory_dir.exists() and memory_dir.is_dir():
            for f in sorted(memory_dir.glob("*.md")):
                if f.is_file():
                    self._memory_files.append(f)

        # Session files (JSONL transcripts)
        if "sessions" in self._sources:
            sessions_dir = self.workspace_dir / ".openclaw" / "sessions"
            if sessions_dir.exists():
                for f in sorted(sessions_dir.glob("*.jsonl")):
                    if f.is_file():
                        self._session_files.append(f)

        # Index into FTS5
        if self._fts_enabled and self._db is not None:
            for fp in self._memory_files:
                self._index_file_into_fts(fp, "memory")
            for fp in self._session_files:
                self._index_file_into_fts(fp, "sessions")
            self._db.commit()

        self._indexed = True
        logger.info(
            "Memory indexed: %d memory files, %d session files (fts=%s)",
            len(self._memory_files),
            len(self._session_files),
            self._fts_enabled,
        )

    def _index_file_into_fts(self, file_path: Path, source: str) -> None:
        """Chunk file into lines and upsert into FTS5."""
        if self._db is None:
            return
        try:
            content = file_path.read_text(encoding="utf-8")
            try:
                rel = str(file_path.relative_to(self.workspace_dir))
            except ValueError:
                rel = str(file_path)

            lines = content.split("\n")
            chunk_size = 20
            for start in range(0, len(lines), chunk_size):
                end = min(len(lines), start + chunk_size)
                chunk_text = "\n".join(lines[start:end]).strip()
                if not chunk_text:
                    continue
                _upsert_chunk(
                    self._db,
                    source=source,
                    path=rel,
                    start_line=start + 1,
                    end_line=end,
                    content=chunk_text,
                )
        except Exception as exc:
            logger.debug("FTS index error %s: %s", file_path, exc)


def _resolve_embedding_provider(config: Any | None) -> str | None:
    """Extract embedding provider name from config, if set."""
    try:
        if config is None:
            return None
        if isinstance(config, dict):
            return config.get("memory", {}).get("embeddingProvider") or None
        mem = getattr(config, "memory", None)
        if mem is None:
            return None
        return (
            getattr(mem, "embeddingProvider", None)
            or getattr(mem, "embedding_provider", None)
        )
    except Exception:
        return None


async def get_memory_search_manager(
    workspace_dir: Path,
    config: Any | None = None,
    agent_id: str = "default",
) -> "SimpleMemorySearchManager":
    """Return a cached memory search manager for the given workspace.

    Mirrors TS ``MemoryIndexManager.get()``:
    - Module-level INDEX_CACHE keyed by workspace path prevents duplicate instances.
    - Async lock (CACHE_PENDING guard) prevents duplicate construction under
      concurrent callers for the same key.
    - When ``config.memory.embeddingProvider`` is set, tries to return a
      ``BuiltinMemoryManager`` instance that supports vector + hybrid search.
      Falls back to ``SimpleMemorySearchManager`` if construction fails.
    """
    cache_key = str(workspace_dir.resolve())
    if cache_key in _INDEX_CACHE:
        return _INDEX_CACHE[cache_key]

    lock = _get_cache_lock()
    async with lock:
        # Double-check after acquiring lock
        if cache_key in _INDEX_CACHE:
            return _INDEX_CACHE[cache_key]

        # Try BuiltinMemoryManager when embedding provider is configured
        embedding_provider = _resolve_embedding_provider(config)
        if embedding_provider:
            try:
                from .builtin_manager import BuiltinMemoryManager  # type: ignore[attr-defined]
                builtin = BuiltinMemoryManager(
                    agent_id=agent_id,
                    workspace_dir=workspace_dir,
                    embedding_provider=embedding_provider,
                )
                await builtin.sync()
                # BuiltinMemoryManager exposes a compatible search(query, opts) adapter
                _INDEX_CACHE[cache_key] = builtin  # type: ignore[assignment]
                logger.info(
                    "Memory: using BuiltinMemoryManager (provider=%s) for %s",
                    embedding_provider,
                    cache_key,
                )
                return _INDEX_CACHE[cache_key]
            except Exception as exc:
                logger.warning(
                    "BuiltinMemoryManager unavailable (%s); falling back to SimpleMemorySearchManager",
                    exc,
                )

        manager = SimpleMemorySearchManager(workspace_dir, config)
        await manager.sync()
        _INDEX_CACHE[cache_key] = manager
        return manager
