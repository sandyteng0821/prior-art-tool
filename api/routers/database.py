"""
api/routers/database.py — Read-only database endpoints.

Endpoints:
  GET /api/v1/db/patents/{patent_id} — single patent lookup
  GET /api/v1/db/stats               — DB-wide statistics

Logic ported from:
  - check_db._lookup() / _lookup_family()  → patent lookup
  - patent_store.stats()                    → stats

Both use raw sqlite3 queries, not patent_store imports, because:
  1. patent_store.stats() calls init_db() on every invocation
     → creates DB if absent, violating D4
  2. patent_store hardcodes DB_PATH → ignores DATABASE_PATH env var (D4)
  3. Keeps api/ decoupled from modules/ import chain

Sync DB calls wrapped in run_in_executor per D3 (sub-millisecond,
but keeps the event loop clean).
"""

import asyncio
import sqlite3
from functools import partial

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from api.deps import get_db_conn
from api.schemas.database import (
    PatentLookupResponse,
    PatentNotFoundResponse,
    PatentStatsResponse,
)

router = APIRouter(tags=["database"])


# ── Helpers (ported from check_db.py) ────────────────────────────────────────

def _lookup(conn: sqlite3.Connection, patent_id: str) -> dict | None:
    """Fetch a single patent row. Returns dict or None."""
    row = conn.execute(
        "SELECT * FROM patents WHERE patent_id = ?", (patent_id,)
    ).fetchone()
    return dict(row) if row else None


def _lookup_family(conn: sqlite3.Connection, patent_id: str) -> list[str]:
    """Find DB rows whose family_of points to patent_id."""
    rows = conn.execute(
        "SELECT patent_id FROM patents WHERE family_of = ?", (patent_id,)
    ).fetchall()
    return [r["patent_id"] for r in rows]


def _char_count(val) -> int:
    """Safe character count: None/empty → 0."""
    if val is None:
        return 0
    s = str(val).strip()
    if s == "" or s == "[]":
        return 0
    return len(s)


def _has_content(val) -> bool:
    """Check if a text field has meaningful content."""
    if val is None:
        return False
    s = str(val).strip()
    return s != "" and s != "[]"


def _compute_stats(conn: sqlite3.Connection) -> dict:
    """
    Compute DB-wide statistics.
    Ported from patent_store.stats() — same SQL, no init_db() side effect.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM patents"
    ).fetchone()[0]
    has_ex = conn.execute(
        "SELECT COUNT(*) FROM patents WHERE examples_extracted != ''"
    ).fetchone()[0]
    family_fetched = conn.execute(
        "SELECT COUNT(*) FROM patents WHERE family_fetched = 1"
    ).fetchone()[0]
    has_family_of = conn.execute(
        "SELECT COUNT(*) FROM patents WHERE family_of IS NOT NULL"
    ).fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) as n FROM patents GROUP BY source"
    ).fetchall()
    return {
        "total_patents": total,
        "with_examples": has_ex,
        "without_examples": total - has_ex,
        "family_fetched": family_fetched,
        "family_members_in_db": has_family_of,
        "by_source": {r["source"]: r["n"] for r in by_source},
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get(
    "/patents/{patent_id}",
    responses={
        200: {"model": PatentLookupResponse},
        404: {"model": PatentNotFoundResponse},
    },
)
async def get_patent(
    patent_id: str,
    detail: bool = Query(False, description="Include all metadata fields"),
    family: bool = Query(False, description="Include family members"),
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """
    Look up a single patent by ID.

    Returns core fields by default. Use ?detail=true for extended metadata,
    ?family=true for family member list.
    """
    loop = asyncio.get_event_loop()
    row = await loop.run_in_executor(None, partial(_lookup, conn, patent_id))

    if row is None:
        return JSONResponse(
            status_code=404,
            content={"patent_id": patent_id, "found": False},
        )

    result = {
        "patent_id": patent_id,
        "found": True,
        "title": row.get("title"),
        "abstract_chars": _char_count(row.get("abstract")),
        "claims_chars": _char_count(row.get("claims")),
        "examples_chars": _char_count(row.get("examples_extracted")),
        "has_snippets": _has_content(row.get("formulation_snippets")),
        "source": row.get("source"),
    }

    if detail:
        result["detail"] = {
            "status": row.get("status"),
            "year": row.get("year"),
            "fetched_at": row.get("fetched_at"),
            "family_fetched": row.get("family_fetched", 0),
            "family_of": row.get("family_of"),
        }

    if family:
        members = await loop.run_in_executor(
            None, partial(_lookup_family, conn, patent_id)
        )
        result["family_members"] = sorted(members)

    return result


@router.get(
    "/stats",
    response_model=PatentStatsResponse,
)
async def get_stats(
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """
    DB-wide statistics: total patents, coverage counts, source breakdown.
    """
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, partial(_compute_stats, conn))
    return PatentStatsResponse(**stats)
