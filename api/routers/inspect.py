"""
api/routers/inspect.py — Patent inspection endpoint.

POST /api/v1/patents/inspect

Logic ported from inspect_patent.py:
  - DB lookup (get_patent)
  - Per-source alias counting (keyword_count)
  - Snippet extraction (_extract_formulation_snippets for default keywords,
    inline regex logic for custom keywords)

J-2a: DB hit path only. DB miss returns data_source="db_miss" with
fallback URLs. EPO sandbox fallback deferred to J-2b.

Critical: This endpoint is read-only — no DB writes.
"""

import asyncio
import re
import sqlite3
from functools import partial

from fastapi import APIRouter, Depends

from api.deps import get_db_conn
from api.schemas.inspect import (
    DEFAULT_KEYWORDS,
    DataCompleteness,
    FallbackUrls,
    InspectRequest,
    InspectResponse,
    SourceFilter,
)

router = APIRouter(tags=["inspect"])


# ── Internal helpers (ported from inspect_patent.py) ─────────────────────────

def _get_patent(conn: sqlite3.Connection, patent_id: str) -> dict | None:
    """
    Fetch patent from DB. Port of inspect_patent.get_patent().

    Selects the exact columns needed for inspection — avoids SELECT *
    to be explicit about what we use.
    """
    row = conn.execute(
        """SELECT patent_id, title, year, source,
                  claims, examples_extracted, abstract,
                  formulation_snippets
           FROM patents WHERE patent_id = ?""",
        (patent_id,),
    ).fetchone()
    return dict(row) if row else None


def _patent_urls(patent_id: str) -> tuple[str, str]:
    """Generate Espacenet and Google Patents URLs for manual lookup."""
    espacenet = (
        f"https://worldwide.espacenet.com/patent/search?q=pn%3D{patent_id}"
    )
    google = f"https://patents.google.com/patent/{patent_id}/en"
    return espacenet, google


def _alias_counts(
    sources: dict[str, str],
    aliases: list[str],
) -> dict[str, dict[str, int]]:
    """
    Count alias occurrences per source section.

    Port of inspect_patent.py lines 218-225 (the per-source loop).
    Returns: {"Pemirolast": {"claims": 5, "examples": 12, "abstract": 2}, ...}
    """
    counts: dict[str, dict[str, int]] = {}
    for alias in aliases:
        alias_lower = alias.lower()
        per_source = {}
        for name, text in sources.items():
            per_source[name] = text.lower().count(alias_lower)
        # Only include aliases that appear at least once
        if any(per_source.values()):
            counts[alias] = per_source
    return counts


def _extract_snippets_default(text: str, drug_aliases: list[str]) -> list[str]:
    """
    Extract formulation snippets using DEFAULT_KEYWORDS.

    This replicates the logic of patent_fetcher._extract_formulation_snippets
    inline rather than importing it — the API layer avoids importing
    modules/ directly (same pattern as J-1 avoiding patent_store.stats()).

    The function is a pure string operation: split text into sentences,
    keep sentences that contain both a drug alias and a formulation keyword.
    """
    keywords = [
        "composition", "formulation",
        "compris",  # matches comprising/comprises/comprised/comprise
        "excipient", "tablet", "capsule", "carrier",
    ]

    sentences = re.split(
        r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text
    )
    snippets = []
    for s in sentences:
        s_lower = s.lower()
        has_drug = any(a.lower() in s_lower for a in drug_aliases)
        has_keyword = any(k in s_lower for k in keywords)
        if has_drug and has_keyword:
            snippets.append(s.strip())
    return snippets[:20]


def _extract_snippets_custom(
    text: str, drug_aliases: list[str], keywords: list[str],
) -> list[str]:
    """
    Extract snippets with user-supplied keywords.

    Port of inspect_patent.py lines 233-244 (extract_with_custom_kw).
    Same sentence splitter, but uses the caller's keyword list instead
    of the hardcoded DEFAULT_KEYWORDS.
    """
    sentences = re.split(
        r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text
    )
    out = []
    for s in sentences:
        s_lower = s.lower()
        has_drug = any(a.lower() in s_lower for a in drug_aliases)
        has_kw = any(k in s_lower for k in keywords)
        if has_drug and has_kw:
            out.append(s.strip())
    return out[:20]


def _run_inspect(
    conn: sqlite3.Connection,
    req: InspectRequest,
) -> InspectResponse:
    """
    Synchronous inspect logic. Called via run_in_executor.

    Steps:
      1. Look up patent in DB
      2. On miss → return db_miss response with fallback URLs
      3. On hit → build text sources based on source_filter
      4. Count alias occurrences per source
      5. Extract snippets per source (default or custom keywords)
      6. Return structured response
    """
    patent = _get_patent(conn, req.patent_id)

    # ── DB miss ──────────────────────────────────────────────────────────
    if patent is None:
        espacenet_url, google_url = _patent_urls(req.patent_id)
        return InspectResponse(
            patent_id=req.patent_id,
            data_source="db_miss",
            fallback_urls=FallbackUrls(
                espacenet=espacenet_url,
                google_patents=google_url,
            ),
        )

    # ── DB hit ───────────────────────────────────────────────────────────
    # Build text sources
    all_sources = {
        "claims": patent["claims"] or "",
        "examples": patent["examples_extracted"] or "",
        "abstract": patent["abstract"] or "",
    }

    if req.source_filter == SourceFilter.all:
        targets = all_sources
    else:
        key = req.source_filter.value
        targets = {key: all_sources[key]}

    # Alias counts (always computed against all sources for completeness,
    # regardless of source_filter — matches CLI behavior)
    alias_counts = _alias_counts(all_sources, req.drug_aliases)

    # Snippet extraction
    keywords = req.keywords if req.keywords is not None else DEFAULT_KEYWORDS
    use_custom = req.keywords is not None

    snippets: dict[str, list[str]] = {}
    for name, text in targets.items():
        if not text:
            snippets[name] = []
            continue
        if use_custom:
            snippets[name] = _extract_snippets_custom(
                text, req.drug_aliases, keywords,
            )
        else:
            snippets[name] = _extract_snippets_default(
                text, req.drug_aliases,
            )

    total_count = sum(len(v) for v in snippets.values())

    return InspectResponse(
        patent_id=req.patent_id,
        data_source="db",
        title=patent["title"],
        data_completeness=DataCompleteness(
            abstract_chars=len(patent["abstract"] or ""),
            claims_chars=len(patent["claims"] or ""),
            examples_chars=len(patent["examples_extracted"] or ""),
        ),
        alias_counts=alias_counts,
        snippets=snippets,
        total_snippet_count=total_count,
    )


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.post(
    "/inspect",
    response_model=InspectResponse,
)
async def inspect_patent(
    req: InspectRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """
    Inspect a patent: alias counting + snippet extraction.

    DB hit returns structured analysis. DB miss returns fallback URLs
    for manual lookup (EPO sandbox fallback deferred to J-2b).

    force_refetch is accepted but ignored in J-2a (no EPO path yet).
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, partial(_run_inspect, conn, req),
    )
    return result
