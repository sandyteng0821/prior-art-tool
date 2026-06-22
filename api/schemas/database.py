"""
api/schemas/database.py — Pydantic response models for database endpoints.

Models:
  - PatentDetail: optional metadata fields (status, year, fetched_at, etc.)
  - PatentLookupResponse: GET /api/v1/db/patents/{patent_id}
  - PatentNotFoundResponse: 404 response
  - PatentStatsResponse: GET /api/v1/db/stats
"""

from pydantic import BaseModel


class PatentDetail(BaseModel):
    """Extended metadata fields, only included when ?detail=true."""
    status: str | None = None
    year: str | None = None
    fetched_at: str | None = None
    family_fetched: int = 0
    family_of: str | None = None


class PatentLookupResponse(BaseModel):
    """Successful patent lookup response."""
    patent_id: str
    found: bool = True
    title: str | None = None
    abstract_chars: int = 0
    claims_chars: int = 0
    examples_chars: int = 0
    has_snippets: bool = False
    source: str | None = None
    detail: PatentDetail | None = None
    family_members: list[str] | None = None


class PatentNotFoundResponse(BaseModel):
    """Patent not found in DB."""
    patent_id: str
    found: bool = False


class SourceCount(BaseModel):
    """Patent count per source."""
    source: str
    count: int


class PatentStatsResponse(BaseModel):
    """DB-wide statistics."""
    total_patents: int = 0
    with_examples: int = 0
    without_examples: int = 0
    family_fetched: int = 0
    family_members_in_db: int = 0
    by_source: dict[str, int] = {}
