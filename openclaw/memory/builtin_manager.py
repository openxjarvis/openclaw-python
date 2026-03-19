"""Built-in memory manager using SQLite + Vector + FTS.

Exposes a ``search(query, opts)`` adapter so it can be used as a drop-in
replacement for ``SimpleMemorySearchManager`` via ``get_memory_search_manager()``.
"""
import hashlib
import logging
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, Optional, List

from .types import (
    MemoryEmbeddingProbeResult,
    MemoryProviderStatus,
    MemorySearchResult,
    MemorySource,
)
from .embeddings import EmbeddingProvider, OpenAIEmbeddingProvider, create_embedding_provider
from .hybrid import apply_mmr, merge_hybrid_results, normalize_scores, SearchResult
from .chunking import chunk_file_by_tokens, DEFAULT_CHUNK_TOKENS, DEFAULT_CHUNK_OVERLAP
from .advanced_search import extract_keywords, apply_temporal_decay, normalize_han_bm25_query

logger = logging.getLogger(__name__)


class BuiltinMemoryManager:
    """Manages agent memory with vector and full-text search.
    
    Aligned with TS memory/manager.ts:
    - Token-based chunking (400 tokens with 80 overlap)
    - Vector + FTS hybrid search
    - Embedding cache support
    """
    
    def __init__(
        self,
        agent_id: str,
        workspace_dir: Path,
        embedding_provider: Optional[str | EmbeddingProvider] = None,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.agent_id = agent_id
        self.workspace_dir = workspace_dir
        self.chunk_tokens = chunk_tokens
        self.chunk_overlap = chunk_overlap
        # Resolved provider name (string) for status reporting
        self._embedding_provider_name: str = (
            embedding_provider if isinstance(embedding_provider, str) else "custom"
        ) if embedding_provider else "openai"

        # Initialize embedding provider using factory
        if isinstance(embedding_provider, EmbeddingProvider):
            self.embedder = embedding_provider
        else:
            # Use factory to support all configured providers (openai, gemini, voyage, local)
            provider_str = embedding_provider if isinstance(embedding_provider, str) else "openai"
            self.embedder = create_embedding_provider(
                provider_name=provider_str or "openai",
                model=None,  # Use provider defaults
            )
        
        # Set up database path
        memory_dir = workspace_dir / ".openclaw" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = memory_dir / f"{agent_id}_index.db"
        self.db: Optional[sqlite3.Connection] = None
        
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database with schema."""
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        
        # Try to load sqlite-vec extension for native vector operations
        self._sqlite_vec_enabled = False
        try:
            import sqlite_vec
            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.enable_load_extension(False)
            self._sqlite_vec_enabled = True
            logger.info("sqlite-vec extension loaded successfully")
        except Exception as e:
            logger.info(f"sqlite-vec not available ({e}), using fallback vector search")
        
        # Create schema
        self.db.executescript("""
            -- Files table
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                hash TEXT NOT NULL,
                mtime INTEGER NOT NULL,
                size INTEGER NOT NULL,
                indexed_at INTEGER NOT NULL
            );
            
            -- Chunks table
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                source TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                model TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BLOB,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (path) REFERENCES files(path)
            );
            
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
            CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
            
            -- FTS5 virtual table for full-text search
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                id UNINDEXED,
                path,
                text,
                content='chunks',
                content_rowid='rowid'
            );
            
            -- FTS triggers
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, id, path, text)
                VALUES (new.rowid, new.id, new.path, new.text);
            END;
            
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                DELETE FROM chunks_fts WHERE rowid = old.rowid;
            END;
            
            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                DELETE FROM chunks_fts WHERE rowid = old.rowid;
                INSERT INTO chunks_fts(rowid, id, path, text)
                VALUES (new.rowid, new.id, new.path, new.text);
            END;
        """)
        
        self.db.commit()
        logger.info(f"Initialized memory database at {self.db_path}")
    
    async def search(
        self,
        query: str,
        limit: int = 5,
        sources: Optional[list[MemorySource]] = None,
        use_vector: bool = False,
        use_hybrid: bool = True,
        vector_weight: float = 0.7,
    ) -> list[MemorySearchResult]:
        """
        Search memory using full-text search (and optionally vector search).
        
        Includes advanced features from TypeScript:
        - Query expansion (extract keywords)
        - Chinese BM25 optimization
        - Temporal decay
        - MMR re-ranking
        
        Args:
            query: Search query
            limit: Maximum number of results
            sources: Filter by memory sources
            use_vector: Use vector search (requires embeddings)
            use_hybrid: Use hybrid search (combines vector + FTS)
            vector_weight: Weight for vector scores in hybrid search
            
        Returns:
            List of search results
        """
        if not self.db:
            return []
        
        # Query expansion and Chinese optimization
        expanded_query = query
        keywords = extract_keywords(query)
        if keywords:
            expanded_query = " ".join(keywords)
        
        # Chinese BM25 optimization
        normalized_query = normalize_han_bm25_query(expanded_query)
        
        # Search with optimized query
        if use_vector and use_hybrid:
            results = await self._hybrid_search(normalized_query, limit * 2, sources, vector_weight)
        elif use_vector:
            results = await self._vector_search(normalized_query, limit * 2, sources)
        else:
            results = await self._fts_search(normalized_query, limit * 2, sources)
        
        # Apply temporal decay
        results = apply_temporal_decay(results)
        
        # MMR re-ranking for diversity (if we have enough results)
        if len(results) > limit:
            query_embedding = await self.embedder.embed_text(query)
            results = apply_mmr(results, query_embedding, lambda_param=0.7, top_k=limit)
        else:
            results = results[:limit]
        
        return results
    
    async def _fts_search(
        self,
        query: str,
        limit: int,
        sources: Optional[list[MemorySource]]
    ) -> list[MemorySearchResult]:
        """Full-text search using FTS5."""
        try:
            # Build source filter
            source_filter = ""
            source_values = []
            if sources:
                source_names = [s.value for s in sources]
                placeholders = ','.join('?' * len(source_names))
                source_filter = f"AND chunks.source IN ({placeholders})"
                source_values = source_names
            
            # FTS5 query
            sql = f"""
                SELECT 
                    chunks.id,
                    chunks.path,
                    chunks.source,
                    chunks.text,
                    chunks.start_line,
                    chunks.end_line,
                    bm25(chunks_fts) as score
                FROM chunks_fts
                JOIN chunks ON chunks.rowid = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                {source_filter}
                ORDER BY score
                LIMIT ?
            """
            
            cursor = self.db.execute(
                sql,
                [query] + source_values + [limit]
            )
            
            results = []
            for row in cursor.fetchall():
                # Create snippet (first 200 chars)
                snippet = row['text'][:200] + ('...' if len(row['text']) > 200 else '')
                
                results.append(MemorySearchResult(
                    id=row['id'],
                    path=row['path'],
                    source=MemorySource(row['source']),
                    text=row['text'],
                    snippet=snippet,
                    start_line=row['start_line'],
                    end_line=row['end_line'],
                    score=abs(row['score'])  # BM25 scores are negative
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"FTS search error: {e}", exc_info=True)
            return []
    
    async def add_file(
        self,
        file_path: Path,
        source: MemorySource = MemorySource.MEMORY
    ) -> int:
        """
        Add a file to memory index.
        
        Args:
            file_path: Path to file
            source: Memory source type
            
        Returns:
            Number of chunks created
        """
        if not self.db:
            return 0
        
        try:
            # Read file content
            content = file_path.read_text(encoding='utf-8')
            file_hash = self._hash_content(content)
            
            # Check if file already indexed
            existing = self.db.execute(
                'SELECT hash FROM files WHERE path = ?',
                [str(file_path)]
            ).fetchone()
            
            if existing and existing['hash'] == file_hash:
                logger.debug(f"File unchanged: {file_path}")
                return 0
            
            # Chunk the file
            chunks = self._chunk_text(content, str(file_path))
            
            # Delete old chunks
            self.db.execute('DELETE FROM chunks WHERE path = ?', [str(file_path)])
            
            # Insert chunks
            import time
            now = int(time.time())
            
            # Batch-embed all chunks — mirrors TS manager-embedding-ops.ts
            texts = [c['text'] for c in chunks]
            embeddings: list[list[float] | None] = [None] * len(texts)
            try:
                batch_result = await self.embedder.embed_batch(texts)
                raw = batch_result.embeddings if hasattr(batch_result, 'embeddings') else batch_result
                if raw and len(raw) == len(texts):
                    embeddings = list(raw)
                else:
                    logger.warning("embed_batch returned mismatched count (%d vs %d)", len(raw or []), len(texts))
            except Exception as emb_exc:
                logger.debug("Batch embedding failed (%s); chunks stored without embeddings for %s", emb_exc, file_path)

            for i, chunk in enumerate(chunks):
                chunk_id = f"{file_path}:{chunk['start_line']}-{chunk['end_line']}"
                emb = embeddings[i]
                emb_blob = self._serialize_embedding(emb) if emb else None

                self.db.execute("""
                    INSERT INTO chunks
                    (id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    chunk_id,
                    str(file_path),
                    source.value,
                    chunk['start_line'],
                    chunk['end_line'],
                    self._hash_content(chunk['text']),
                    self._embedding_provider_name,
                    chunk['text'],
                    emb_blob,
                    now,
                ])
            
            # Update files table
            stat = file_path.stat()
            self.db.execute("""
                INSERT OR REPLACE INTO files 
                (path, source, hash, mtime, size, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                str(file_path),
                source.value,
                file_hash,
                int(stat.st_mtime),
                stat.st_size,
                now
            ])
            
            self.db.commit()
            logger.info(f"Indexed {len(chunks)} chunks from {file_path}")
            return len(chunks)
            
        except Exception as e:
            logger.error(f"Error adding file {file_path}: {e}", exc_info=True)
            return 0
    
    def _chunk_text(
        self,
        content: str,
        path: str,
        chunk_size: int = 500  # Keep for backward compat, but unused
    ) -> list[dict]:
        """
        Chunk text into smaller pieces using token-based chunking (aligned with TS).
        
        Args:
            content: File content
            path: File path
            chunk_size: Deprecated, kept for compatibility
            
        Returns:
            List of chunk dicts
        """
        # Use new token-based chunking (400 tokens + 80 overlap)
        return chunk_file_by_tokens(
            path,
            content,
            tokens=self.chunk_tokens,
            overlap=self.chunk_overlap
        )
    
    def _hash_content(self, content: str) -> str:
        """Hash content for change detection."""
        return hashlib.sha256(content.encode()).hexdigest()
    
    async def sync(self) -> dict:
        """Sync memory index with filesystem.

        Mirrors TS MemoryIndexManager.runSync():
        1. Scan memory dirs for .md files + sessions dir for .jsonl files
        2. Hash each file; skip unchanged (hash + mtime match DB)
        3. Re-index changed/new files (delete old chunks, insert new with embeddings)
        4. Remove DB entries for files that no longer exist

        Returns:
            Sync statistics dict
        """
        stats = {
            'files_added': 0,
            'files_updated': 0,
            'files_removed': 0,
            'chunks_created': 0,
        }

        if not self.db:
            return stats

        # -- 1. Discover candidate files on disk --
        disk_files: dict[str, MemorySource] = {}

        memory_file = self.workspace_dir / "MEMORY.md"
        if memory_file.exists():
            disk_files[str(memory_file)] = MemorySource.MEMORY

        memory_dir = self.workspace_dir / "memory"
        if memory_dir.is_dir():
            for f in memory_dir.glob("*.md"):
                if f.is_file():
                    disk_files[str(f)] = MemorySource.MEMORY

        sessions_dir = self.workspace_dir / ".openclaw" / "sessions"
        if sessions_dir.is_dir():
            for f in sessions_dir.glob("*.jsonl"):
                if f.is_file():
                    disk_files[str(f)] = MemorySource.SESSIONS

        # -- 2. Compare against DB records --
        db_files = {
            row[0]: row[1]
            for row in self.db.execute("SELECT path, hash FROM files").fetchall()
        }

        # -- 3. Index new/changed files --
        import time as _time
        for path_str, source in disk_files.items():
            try:
                fp = Path(path_str)
                content = fp.read_text(encoding='utf-8')
                new_hash = self._hash_content(content)
                if db_files.get(path_str) == new_hash:
                    continue  # unchanged

                was_new = path_str not in db_files
                added = await self.add_file(fp, source)
                stats['chunks_created'] += added
                if was_new:
                    stats['files_added'] += 1
                else:
                    stats['files_updated'] += 1
            except Exception as exc:
                logger.warning("sync: error indexing %s: %s", path_str, exc)

        # -- 4. Remove orphaned DB entries --
        for path_str in list(db_files.keys()):
            if path_str not in disk_files:
                try:
                    self.db.execute("DELETE FROM chunks WHERE path = ?", [path_str])
                    self.db.execute("DELETE FROM files WHERE path = ?", [path_str])
                    stats['files_removed'] += 1
                except Exception as exc:
                    logger.warning("sync: error removing orphan %s: %s", path_str, exc)

        self.db.commit()
        logger.info(
            "BuiltinMemoryManager.sync complete: +%d added, ~%d updated, -%d removed, %d chunks",
            stats['files_added'], stats['files_updated'],
            stats['files_removed'], stats['chunks_created'],
        )
        return stats
    
    def close(self) -> None:
        """Close database connection."""
        if self.db:
            self.db.close()
            self.db = None

    # ------------------------------------------------------------------
    # Compatibility interface — matches SimpleMemorySearchManager API so
    # BuiltinMemoryManager can be returned by get_memory_search_manager()
    # and used transparently by agents/tools/memory.py.
    # ------------------------------------------------------------------

    async def search(  # type: ignore[override]
        self,
        query: str,
        opts: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
        use_vector: bool | None = None,
        use_hybrid: bool | None = None,
        sources: "list[MemorySource] | None" = None,
        vector_weight: float | None = None,
    ) -> list[MemorySearchResult]:
        """Unified search adapter — accepts both dict-opts and keyword-args calling conventions.

        Supports:
        - ``search(query, opts)``  — SimpleMemorySearchManager drop-in
        - ``search(query, limit=5, use_vector=False)``  — direct kwargs API
        """
        opts = opts or {}
        effective_limit = limit if limit is not None else int(opts.get("maxResults", opts.get("limit", 10)))
        include_sessions = bool(opts.get("includeSessions") or opts.get("include_sessions"))

        effective_sources = sources
        if effective_sources is None and include_sessions:
            effective_sources = [MemorySource.MEMORY, MemorySource.SESSIONS]

        effective_use_vector = use_vector if use_vector is not None else bool(opts.get("use_vector", False))
        effective_use_hybrid = use_hybrid if use_hybrid is not None else bool(opts.get("use_hybrid", True))
        effective_vector_weight = vector_weight if vector_weight is not None else float(opts.get("vector_weight", 0.7))

        if effective_use_vector or effective_use_hybrid:
            return await self._hybrid_search(query, effective_limit, effective_sources)
        return await self._fts_search(query, effective_limit, effective_sources)

    def status(self) -> "MemoryProviderStatus":
        """Return provider status (matches SimpleMemorySearchManager.status())."""
        chunk_count = 0
        if self.db:
            try:
                chunk_count = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            except Exception:
                pass
        return MemoryProviderStatus(
            backend="builtin",
            provider=f"vector+fts5-sqlite ({self._embedding_provider_name})",
            files=0,
            chunks=chunk_count,
            workspace_dir=str(self.workspace_dir),
            fts={"enabled": True, "available": True},
            sources=[MemorySource.MEMORY],
        )

    async def probe_embedding_availability(self) -> "MemoryEmbeddingProbeResult":
        """Probe whether the embedding provider is reachable."""
        try:
            test = await self.embedder.embed_text("test")
            if test and len(test) > 0:
                return MemoryEmbeddingProbeResult(ok=True)
            return MemoryEmbeddingProbeResult(ok=False, error="Empty embedding returned")
        except Exception as exc:
            return MemoryEmbeddingProbeResult(ok=False, error=str(exc))

    async def probe_vector_availability(self) -> bool:
        """Return True — BuiltinMemoryManager always has vector support."""
        return True

    async def _vector_search(
        self,
        query: str,
        limit: int,
        sources: Optional[list[MemorySource]]
    ) -> list[MemorySearchResult]:
        """
        Vector similarity search
        
        Uses sqlite-vec native operations if available, otherwise falls back to Python cosine similarity.
        
        Args:
            query: Search query
            limit: Maximum results
            sources: Optional source filter
            
        Returns:
            Search results
        """
        try:
            # Generate query embedding
            query_embedding = await self.embedder.embed_text(query)
            
            # Build source filter
            source_filter = ""
            source_values = []
            if sources:
                source_names = [s.value for s in sources]
                placeholders = ','.join('?' * len(source_names))
                source_filter = f"AND chunks.source IN ({placeholders})"
                source_values = source_names
            
            # Use sqlite-vec native operations if available
            if self._sqlite_vec_enabled:
                return await self._vector_search_native(query_embedding, limit, source_filter, source_values)
            else:
                return await self._vector_search_fallback(query_embedding, limit, source_filter, source_values)
            
        except Exception as e:
            logger.error(f"Vector search error: {e}", exc_info=True)
            return []
    
    async def _vector_search_native(
        self,
        query_embedding: list[float],
        limit: int,
        source_filter: str,
        source_values: list[str],
    ) -> list[MemorySearchResult]:
        """Vector search using sqlite-vec native operations.
        
        Mirrors TS implementation using vec_distance_cosine.
        """
        # Serialize query embedding for sqlite-vec
        query_blob = self._serialize_embedding(query_embedding)
        
        sql = f"""
            SELECT 
                chunks.id,
                chunks.path,
                chunks.source,
                chunks.text,
                chunks.start_line,
                chunks.end_line,
                vec_distance_cosine(chunks.embedding, ?) as distance
            FROM chunks
            WHERE chunks.embedding IS NOT NULL
            {source_filter}
            ORDER BY distance ASC
            LIMIT ?
        """
        
        params = [query_blob] + source_values + [limit]
        cursor = self.db.execute(sql, params)
        
        results = []
        for row in cursor.fetchall():
            # Convert distance to similarity (1 - distance)
            # sqlite-vec returns cosine distance (0-2), we want similarity (0-1)
            similarity = 1.0 - (row['distance'] / 2.0) if row['distance'] is not None else 0.0
            
            snippet = row['text'][:200] + ('...' if len(row['text']) > 200 else '')
            
            results.append(MemorySearchResult(
                id=row['id'],
                path=row['path'],
                source=MemorySource(row['source']),
                text=row['text'],
                snippet=snippet,
                start_line=row['start_line'],
                end_line=row['end_line'],
                score=similarity
            ))
        
        return results
    
    async def _vector_search_fallback(
        self,
        query_embedding: list[float],
        limit: int,
        source_filter: str,
        source_values: list[str],
    ) -> list[MemorySearchResult]:
        """Fallback vector search using Python cosine similarity.
        
        Used when sqlite-vec is not available.
        """
        sql = f"""
            SELECT 
                chunks.id,
                chunks.path,
                chunks.source,
                chunks.text,
                chunks.start_line,
                chunks.end_line,
                chunks.embedding
            FROM chunks
            WHERE chunks.embedding IS NOT NULL
            {source_filter}
        """
        
        cursor = self.db.execute(sql, source_values)
        
        # Compute cosine similarity in Python
        results = []
        for row in cursor.fetchall():
            embedding_blob = row['embedding']
            if not embedding_blob:
                continue
            
            chunk_embedding = self._deserialize_embedding(embedding_blob)
            similarity = self._cosine_similarity(query_embedding, chunk_embedding)
            
            snippet = row['text'][:200] + ('...' if len(row['text']) > 200 else '')
            
            results.append(MemorySearchResult(
                id=row['id'],
                path=row['path'],
                source=MemorySource(row['source']),
                text=row['text'],
                snippet=snippet,
                start_line=row['start_line'],
                end_line=row['end_line'],
                score=similarity
            ))
        
        # Sort by similarity (descending)
        results.sort(key=lambda r: r.score, reverse=True)
        
        # Return top N
        return results[:limit]
    
    async def _hybrid_search(
        self,
        query: str,
        limit: int,
        sources: Optional[list[MemorySource]],
        vector_weight: float = 0.7,
    ) -> list[MemorySearchResult]:
        """
        Hybrid search combining vector and FTS
        
        Args:
            query: Search query
            limit: Maximum results
            sources: Optional source filter
            vector_weight: Weight for vector scores
            
        Returns:
            Merged search results
        """
        # Get vector results
        vector_results = await self._vector_search(query, limit * 2, sources)
        
        # Get FTS results
        fts_results = await self._fts_search(query, limit * 2, sources)
        
        # Convert to SearchResult format
        vector_sr = [
            SearchResult(
                id=r.id,
                text=r.text,
                path=r.path,
                source=r.source.value,
                score=r.score,
                start_line=r.start_line,
                end_line=r.end_line,
            )
            for r in vector_results
        ]
        
        fts_sr = [
            SearchResult(
                id=r.id,
                text=r.text,
                path=r.path,
                source=r.source.value,
                score=r.score,
                start_line=r.start_line,
                end_line=r.end_line,
            )
            for r in fts_results
        ]
        
        # Normalize scores
        vector_sr = normalize_scores(vector_sr)
        fts_sr = normalize_scores(fts_sr)
        
        # Merge via weighted hybrid scoring
        text_weight = 1.0 - vector_weight
        merged = merge_hybrid_results(vector_sr, fts_sr, vector_weight, text_weight)

        # Apply MMR re-ranking for diversity (mirrors TS MemoryIndexManager)
        merged = apply_mmr(merged, limit=limit * 2)

        # Convert back to MemorySearchResult
        results = []
        for sr in merged[:limit]:
            snippet = sr.text[:200] + ('...' if len(sr.text) > 200 else '')
            
            results.append(MemorySearchResult(
                id=sr.id,
                path=sr.path,
                source=MemorySource(sr.source),
                text=sr.text,
                snippet=snippet,
                start_line=sr.start_line,
                end_line=sr.end_line,
                score=sr.score
            ))
        
        return results
    
    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        """Serialize embedding to bytes"""
        return struct.pack(f'{len(embedding)}f', *embedding)
    
    def _deserialize_embedding(self, blob: bytes) -> List[float]:
        """Deserialize embedding from bytes"""
        num_floats = len(blob) // 4
        return list(struct.unpack(f'{num_floats}f', blob))
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors"""
        if len(a) != len(b):
            return 0.0
        
        dot_product = sum(x * y for x, y in zip(a, b))
        magnitude_a = sum(x * x for x in a) ** 0.5
        magnitude_b = sum(x * x for x in b) ** 0.5
        
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
        
        return dot_product / (magnitude_a * magnitude_b)
