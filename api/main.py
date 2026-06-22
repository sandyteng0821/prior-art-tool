"""
api/main.py — FastAPI application entry point.

Responsibilities:
  - FastAPI instance with lifespan context
  - Health check endpoint (GET /)
  - Router registration stub (routers added in J-1..J-4)

Design decisions:
  - D4: DB path from DATABASE_PATH env var, default "cache/patents.db"
  - Health check reads patent count via patent_store.stats() if DB exists
  - If DB file doesn't exist, patents_count returns null (not 0)
  - patent_store.stats() calls init_db() internally which would CREATE
    the DB — so we gate on os.path.exists() first.
"""

import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI


def _get_db_path() -> str:
    return os.environ.get("DATABASE_PATH", os.path.join("cache", "patents.db"))


def _read_patent_count(db_path: str) -> int | None:
    """
    Read total patent count directly from the DB file.

    Returns None if:
      - DB file doesn't exist
      - DB exists but patents table doesn't exist
      - Any other DB read error

    We read directly instead of importing patent_store.stats() because:
      1. patent_store.init_db() creates the DB if absent — we don't want that
      2. patent_store hardcodes DB_PATH; API uses DATABASE_PATH env var (D4)
      3. Keeps api/ decoupled from modules/ import chain
    """
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]
        conn.close()
        return count
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        # Table doesn't exist, or file isn't a valid SQLite DB
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup / shutdown hooks."""
    db_path = _get_db_path()
    app.state.db_path = db_path
    yield
    # Shutdown: nothing to clean up for now


app = FastAPI(
    title="Prior Art Tool API",
    description="REST API layer for the drug repurposing prior art radar tool.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health check ─────────────────────────────────────────────────────────────


@app.get("/")
async def health_check():
    """
    Health check endpoint.

    Returns:
        {"status": "running", "db_path": "...", "patents_count": N | null}
    """
    db_path = app.state.db_path
    patents_count = _read_patent_count(db_path)
    return {
        "status": "running",
        "db_path": db_path,
        "patents_count": patents_count,
    }


# ── Router registration ─────────────────────────────────────────────────────
# J-1: database endpoints
from api.routers import database  # noqa: E402

app.include_router(database.router, prefix="/api/v1/db")

# J-2: inspect endpoint
from api.routers import inspect  # noqa: E402
 
app.include_router(inspect.router, prefix="/api/v1/patents")
 
# Future routers (J-3..J-4):
#   from api.routers import analysis
#   app.include_router(analysis.router, prefix="/api/v1/analysis")
