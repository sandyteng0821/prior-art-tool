"""
api/deps.py — Dependency injection for the API layer.

Provides:
  - get_db_conn(): SQLite connection from DATABASE_PATH env var.
    Used as a FastAPI Depends() in routers.

Design decisions:
  - D3: Sync sqlite3 wrapped in run_in_executor at the router level.
  - D4: DB path from DATABASE_PATH env var, not patent_store's hardcoded path.
  - check_same_thread=False required for FastAPI's thread pool executor (D3).
"""

import os
import sqlite3
from typing import Generator

from fastapi import HTTPException


def _get_db_path() -> str:
    return os.environ.get("DATABASE_PATH", os.path.join("cache", "patents.db"))


def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    """
    FastAPI dependency that yields a sqlite3 connection.

    Usage in routers:
        @router.get("/...")
        async def endpoint(conn = Depends(get_db_conn)):
            ...

    Raises 503 if DB file doesn't exist (not auto-created — see D4).
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        raise HTTPException(
            status_code=503,
            detail=f"Database not found: {db_path}",
        )
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
