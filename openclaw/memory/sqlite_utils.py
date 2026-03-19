"""SQLite utilities for memory management

Mirrors openclaw/src/memory/sqlite.ts
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def ensure_parent_dir(db_path: Path) -> None:
    """Ensure parent directory exists for database file.
    
    Args:
        db_path: Path to database file
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)


def connect_sqlite(db_path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Connect to SQLite database.
    
    Args:
        db_path: Path to database file
        read_only: Whether to open in read-only mode
        
    Returns:
        SQLite connection
    """
    ensure_parent_dir(db_path)
    
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    
    conn.row_factory = sqlite3.Row
    return conn


def vacuum_sqlite(db_path: Path) -> None:
    """Vacuum SQLite database to reclaim space.
    
    Args:
        db_path: Path to database file
    """
    conn = connect_sqlite(db_path)
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "ensure_parent_dir",
    "connect_sqlite",
    "vacuum_sqlite",
]
