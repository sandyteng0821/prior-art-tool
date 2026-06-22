"""
api/routers/inspect.py — Patent inspection endpoint.

POST /api/v1/patents/inspect

Logic ported from inspect_patent.py:
  - DB lookup (get_patent)
  - Per-source alias counting (keyword_count)
  - Snippet extraction (_extract_formulation_snippets for default keywords,
    inline regex logic for custom keywords)

J-2a: DB hit path only.
J-2b: EPO sandbox fallback on DB miss + force_refetch.

Critical: This endpoint is read-only — no DB writes.
"""

import asyncio
import logging
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

logger = logging.getLogger(__name__)
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


# ── EPO sandbox helpers (J-2b) ───────────────────────────────────────────────
#
# Ported inline from patent_fetcher.py — same D1 rationale as llm_analyzer:
# patent_fetcher has module-level side effects (config import, epo_ops.Client
# init, diskcache open, patent_store import) that crash the Docker container
# which only has api/ installed. Inline port avoids the entire modules/
# dependency chain.
#
# Functions ported:
#   _parse_patent_id   (patent_fetcher lines 124-133)
#   _fetch_title       (patent_fetcher lines 558-596)
#   _fetch_abstract    (patent_fetcher lines 459-500)
#   _fetch_claims      (patent_fetcher lines 503-555)
#   _fetch_description (patent_fetcher lines 411-456)
#   _parse_examples    (patent_fetcher lines 353-406)
#
# EPO client + diskcache are lazily initialized on first EPO call (not at
# import time). If epo_ops or diskcache aren't installed, _fetch_from_epo_sync
# catches the ImportError and returns None (graceful degradation with
# fallback URLs).

# Lazy singletons — initialized on first EPO call
_epo_client = None
_epo_cache = None


def _get_epo_client():
    """Lazy-init EPO OPS client. Returns None if epo_ops not available."""
    global _epo_client
    if _epo_client is not None:
        return _epo_client
    try:
        import os
        import epo_ops
        from dotenv import load_dotenv
        load_dotenv()
        _epo_client = epo_ops.Client(
            key=os.getenv("EPO_CONSUMER_KEY"),
            secret=os.getenv("EPO_CONSUMER_SECRET"),
            accept_type="json",
        )
        return _epo_client
    except Exception as e:
        logger.warning("EPO client init failed: %s", e)
        return None


def _get_epo_cache():
    """Lazy-init diskcache for EPO responses. Returns None if unavailable."""
    global _epo_cache
    if _epo_cache is not None:
        return _epo_cache
    try:
        import diskcache
        _epo_cache = diskcache.Cache("cache/epo")
        return _epo_cache
    except Exception as e:
        logger.warning("diskcache init failed: %s", e)
        return None


def _parse_patent_id(patent_id: str) -> tuple[str, str]:
    """
    Split 'US2024335435A1' into ('US2024335435', 'A1').
    EPO Epodoc needs number and kind code separately.
    Port of patent_fetcher._parse_patent_id.
    """
    m = re.match(r'^([A-Z]{2}\d+)([A-Z]\d*)$', patent_id)
    if m:
        return m.group(1), m.group(2)
    return patent_id, ""


def _clear_epo_cache(patent_id: str) -> list[str]:
    """
    Remove stale diskcache entries so _fetch_* hits EPO fresh.
    Port of inspect_patent._clear_epo_cache (lines 73-86).
    Returns list of cleared cache prefixes (for logging).
    """
    cache = _get_epo_cache()
    if cache is None:
        return []
    try:
        cleared = []
        for prefix in ("title", "abstract", "claims"):
            key = f"{prefix}::{patent_id}"
            if key in cache:
                cache.delete(key)
                cleared.append(prefix)
        return cleared
    except Exception as e:
        logger.warning("_clear_epo_cache(%s) failed: %s", patent_id, e)
        return []


def _fetch_title(patent_id: str) -> str:
    """
    Fetch title via EPO biblio endpoint.
    Port of patent_fetcher._fetch_title (lines 558-596).
    """
    import time

    cache = _get_epo_cache()
    cache_key = f"title::{patent_id}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    client = _get_epo_client()
    if client is None:
        return ""

    try:
        import epo_ops
        import xmltodict
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="biblio",
        )
        try:
            data = resp.json()
            doc = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
            titles = doc["bibliographic-data"].get("invention-title", {})
        except Exception:
            data = xmltodict.parse(resp.text)
            doc = (data.get("ops:world-patent-data", {})
                       .get("exchange-documents", {})
                       .get("exchange-document", {}))
            titles = doc.get("bibliographic-data", {}).get("invention-title", {})

        if isinstance(titles, list):
            for t in titles:
                if isinstance(t, dict) and t.get("@lang", "") == "en":
                    result = t.get("$", "")
                    break
            else:
                result = titles[0].get("$", "") if titles else ""
        elif isinstance(titles, dict):
            result = titles.get("$", "")
        else:
            result = str(titles)
    except Exception:
        result = ""

    if cache is not None:
        cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result


def _fetch_abstract(patent_id: str) -> str:
    """
    Fetch abstract via EPO abstract endpoint.
    Port of patent_fetcher._fetch_abstract (lines 459-500).
    """
    import time

    cache = _get_epo_cache()
    cache_key = f"abstract::{patent_id}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    client = _get_epo_client()
    if client is None:
        return ""

    try:
        import epo_ops
        import xmltodict
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="abstract",
        )
        try:
            data = resp.json()
            doc = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
            texts = doc.get("abstract", {})
        except Exception:
            data = xmltodict.parse(resp.text)
            doc = (data.get("ops:world-patent-data", {})
                       .get("exchange-documents", {})
                       .get("exchange-document", {}))
            texts = doc.get("abstract", {})

        if isinstance(texts, list):
            for t in texts:
                if isinstance(t, dict) and t.get("@lang", "") == "en":
                    p = t.get("p", {})
                    result = p.get("$", "") if isinstance(p, dict) else str(p)
                    break
            else:
                p = texts[0].get("p", {}) if texts else {}
                result = p.get("$", "") if isinstance(p, dict) else ""
        elif isinstance(texts, dict):
            p = texts.get("p", {})
            result = p.get("$", "") if isinstance(p, dict) else str(p)
        else:
            result = ""
    except Exception:
        result = ""

    if cache is not None:
        cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result


def _fetch_claims(patent_id: str) -> str:
    """
    Fetch claims via EPO fulltext claims endpoint.
    Port of patent_fetcher._fetch_claims (lines 503-555).
    Non-EP jurisdictions return 404 (EPO licensing limit, not a bug).
    """
    import time

    cache = _get_epo_cache()
    cache_key = f"claims::{patent_id}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    client = _get_epo_client()
    if client is None:
        return ""

    try:
        import epo_ops
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="claims",
        )
        data = resp.json()
        doc = (data["ops:world-patent-data"]
                   ["ftxt:fulltext-documents"]
                   ["ftxt:fulltext-document"])

        claims_list = doc.get("claims", [])
        if isinstance(claims_list, dict):
            claims_list = [claims_list]

        # Prefer English; fallback to first available
        target = None
        for c in claims_list:
            if isinstance(c, dict) and c.get("@lang", "").upper() == "EN":
                target = c
                break
        if target is None and claims_list:
            target = claims_list[0]

        if target is None:
            result = ""
        else:
            claim_items = target.get("claim", {})
            claim_texts = claim_items.get("claim-text", [])
            if isinstance(claim_texts, list):
                result = " ".join(
                    t.get("$", "") if isinstance(t, dict) else str(t)
                    for t in claim_texts
                )
            elif isinstance(claim_texts, dict):
                result = claim_texts.get("$", "")
            else:
                result = str(claim_texts)
    except Exception as e:
        logger.info("_fetch_claims %s: %s (expected for non-EP)", patent_id, e)
        result = ""

    if cache is not None:
        cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result


def _fetch_description(patent_id: str) -> str:
    """
    Fetch full description text via EPO fulltext endpoint.
    Port of patent_fetcher._fetch_description (lines 411-456).
    Not cached in diskcache (too large) — only EPO API hit.
    """
    import time

    client = _get_epo_client()
    if client is None:
        return ""

    try:
        import epo_ops
        import xmltodict
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="description",
        )
        try:
            data = resp.json()
            paras = (data["ops:world-patent-data"]
                         ["ftxt:fulltext-documents"]
                         ["ftxt:fulltext-document"]
                         ["description"]["p"])
        except Exception:
            data = xmltodict.parse(resp.text)
            paras = (data.get("ops:world-patent-data", {})
                         .get("ftxt:fulltext-documents", {})
                         .get("ftxt:fulltext-document", {})
                         .get("description", {})
                         .get("p", []))

        if isinstance(paras, list):
            result = "\n".join(
                p.get("$", "") if isinstance(p, dict) else str(p)
                for p in paras
            )
        elif isinstance(paras, dict):
            result = paras.get("$", "")
        else:
            result = str(paras)
    except Exception:
        result = ""

    time.sleep(0.3)
    return result


def _parse_examples(description: str) -> str:
    """
    Extract Examples section from full description text.
    Port of patent_fetcher._parse_examples (lines 353-406).
    """
    if not description:
        return ""

    start_patterns = [
        r"(?:^|\n)\s*EXAMPLES?\s*\n",
        r"(?:^|\n)\s*EXAMPLE\s+\d+\s*\n",
        r"(?:^|\n)\s*WORKING EXAMPLES?\s*\n",
        r"(?:^|\n)\s*EXPERIMENTAL\s*\n",
        r"(?:^|\n)\s*Example\s+1[\.\:]",
    ]
    end_patterns = [
        r"\n\s*CLAIMS?\s*\n",
        r"\n\s*WHAT IS CLAIMED",
        r"\n\s*INDUSTRIAL APPLICABILITY",
        r"\n\s*REFERENCES?\s*\n",
    ]

    start_idx = None
    for pat in start_patterns:
        m = re.search(pat, description, re.IGNORECASE | re.MULTILINE)
        if m:
            start_idx = m.start()
            break

    if start_idx is None:
        return ""

    text_from_examples = description[start_idx:]

    end_idx = len(text_from_examples)
    for pat in end_patterns:
        m = re.search(pat, text_from_examples, re.IGNORECASE | re.MULTILINE)
        if m:
            end_idx = min(end_idx, m.start())

    examples = text_from_examples[:end_idx].strip()
    examples = re.sub(r"\n{3,}", "\n\n", examples)
    examples = re.sub(r"[ \t]+", " ", examples)
    return examples


def _fetch_from_epo_sync(patent_id: str) -> dict | None:
    """
    Fetch patent content directly from EPO OPS API (sync).
    Port of inspect_patent.get_patent_with_fallback lines 107-137.

    Returns a patent dict (same shape as DB row) or None if EPO
    has nothing at all for this patent, or if EPO libraries are
    not available (graceful degradation).

    Critical: does NOT write to DB (read-only invariant).
    """
    # Bail out early if EPO client can't be initialized
    if _get_epo_client() is None:
        logger.warning("EPO client unavailable for %s", patent_id)
        return None

    logger.info("EPO sandbox fetch: %s", patent_id)
    title = _fetch_title(patent_id)
    abstract = _fetch_abstract(patent_id)
    claims = _fetch_claims(patent_id)
    examples = _parse_examples(_fetch_description(patent_id))

    # All content empty → EPO has nothing for this patent
    if not any([title, abstract, claims, examples]):
        logger.info("EPO returned no content for %s", patent_id)
        return None

    return {
        "patent_id": patent_id,
        "title": title,
        "abstract": abstract,
        "claims": claims,
        "examples_extracted": examples,
        "year": "",
        "source": "epo_sandbox",
        "formulation_snippets": None,
    }


# ── Core logic ───────────────────────────────────────────────────────────────

def _build_response(
    patent: dict,
    req: InspectRequest,
    data_source: str,
) -> InspectResponse:
    """
    Build InspectResponse from a patent dict (DB row or EPO sandbox result).
    Shared by DB hit and EPO sandbox paths to avoid duplicating the
    alias counting + snippet extraction logic.
    """
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

    # Attach fallback URLs when EPO sandbox has no fulltext
    # (title/abstract only — typical for non-EP jurisdictions)
    fallback_urls = None
    if data_source == "epo_sandbox":
        espacenet_url, google_url = _patent_urls(req.patent_id)
        fallback_urls = FallbackUrls(
            espacenet=espacenet_url,
            google_patents=google_url,
        )

    return InspectResponse(
        patent_id=req.patent_id,
        data_source=data_source,
        title=patent["title"],
        data_completeness=DataCompleteness(
            abstract_chars=len(patent["abstract"] or ""),
            claims_chars=len(patent["claims"] or ""),
            examples_chars=len(patent["examples_extracted"] or ""),
        ),
        alias_counts=alias_counts,
        snippets=snippets,
        total_snippet_count=total_count,
        fallback_urls=fallback_urls,
    )


def _run_inspect(
    conn: sqlite3.Connection | None,
    req: InspectRequest,
) -> InspectResponse:
    """
    Synchronous inspect logic. Called via run_in_executor.

    Steps:
      1. force_refetch → clear diskcache, skip DB, go straight to EPO
      2. Look up patent in DB
      3. On DB hit → build response
      4. On DB miss → EPO sandbox fallback (no DB write)
      5. EPO all-empty → 200 + epo_sandbox + fallback_urls + 0 snippets
    """
    espacenet_url, google_url = _patent_urls(req.patent_id)

    # ── force_refetch: skip DB, clear diskcache, fetch from EPO ──────
    if req.force_refetch:
        cleared = _clear_epo_cache(req.patent_id)
        if cleared:
            logger.info("Cleared diskcache for %s: %s", req.patent_id, cleared)

        epo_patent = _fetch_from_epo_sync(req.patent_id)
        if epo_patent is None:
            return InspectResponse(
                patent_id=req.patent_id,
                data_source="epo_sandbox",
                fallback_urls=FallbackUrls(
                    espacenet=espacenet_url,
                    google_patents=google_url,
                ),
            )
        return _build_response(epo_patent, req, "epo_sandbox")

    # ── DB lookup ────────────────────────────────────────────────────
    patent = _get_patent(conn, req.patent_id) if conn is not None else None

    if patent is not None:
        return _build_response(patent, req, "db")

    # ── DB miss → EPO sandbox fallback ───────────────────────────────
    epo_patent = _fetch_from_epo_sync(req.patent_id)
    if epo_patent is None:
        # EPO also has nothing → return empty response with fallback URLs
        return InspectResponse(
            patent_id=req.patent_id,
            data_source="epo_sandbox",
            fallback_urls=FallbackUrls(
                espacenet=espacenet_url,
                google_patents=google_url,
            ),
        )

    return _build_response(epo_patent, req, "epo_sandbox")


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

    DB hit returns structured analysis. DB miss falls back to EPO
    sandbox (live API fetch, not persisted to DB).

    force_refetch: skip DB + clear diskcache, fetch fresh from EPO.
    EPO all-empty: returns 200 + data_source="epo_sandbox" with
    fallback_urls for manual lookup (Espacenet / Google Patents).
    """
    loop = asyncio.get_event_loop()

    if req.force_refetch:
        # force_refetch doesn't need the DB connection
        result = await loop.run_in_executor(
            None, partial(_run_inspect, None, req),
        )
    else:
        result = await loop.run_in_executor(
            None, partial(_run_inspect, conn, req),
        )
    return result
    