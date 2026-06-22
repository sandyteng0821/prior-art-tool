"""
api/schemas/inspect.py — Pydantic models for the inspect endpoint.

Models:
  - InspectRequest:  POST body for /api/v1/patents/inspect
  - DataCompleteness: char counts per text section
  - InspectResponse: full inspect response (DB hit or DB miss)

J-2a: DB hit path only. EPO sandbox fallback deferred to J-2b.
"""

from enum import Enum

from pydantic import BaseModel, Field


# Default keywords from patent_fetcher.py (kept in sync manually —
# same list as inspect_patent.py DEFAULT_KEYWORDS).
DEFAULT_KEYWORDS = [
    "composition", "formulation", "compris",
    "excipient", "tablet", "capsule", "carrier",
]


class SourceFilter(str, Enum):
    all = "all"
    claims = "claims"
    examples = "examples"
    abstract = "abstract"


class InspectRequest(BaseModel):
    """POST body for /api/v1/patents/inspect."""
    patent_id: str
    drug_aliases: list[str]
    keywords: list[str] | None = Field(
        default=None,
        description="Keywords for snippet filter. Defaults to DEFAULT_KEYWORDS.",
    )
    source_filter: SourceFilter = SourceFilter.all
    force_refetch: bool = False


class DataCompleteness(BaseModel):
    """Char counts per text section."""
    abstract_chars: int = 0
    claims_chars: int = 0
    examples_chars: int = 0


class FallbackUrls(BaseModel):
    """Manual lookup URLs when API has no content."""
    espacenet: str
    google_patents: str


class InspectResponse(BaseModel):
    """
    Full inspect response.

    data_source values:
      - "db"          — patent found in local DB
      - "db_miss"     — patent not in DB (J-2a: no EPO fallback yet)
      - "epo_sandbox" — fetched from EPO, not persisted (J-2b)
    """
    patent_id: str
    data_source: str
    title: str | None = None
    data_completeness: DataCompleteness = DataCompleteness()
    alias_counts: dict[str, dict[str, int]] = {}
    snippets: dict[str, list[str]] = {}
    total_snippet_count: int = 0
    fallback_urls: FallbackUrls | None = None
