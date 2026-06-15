# scratch/inspect_patent.py
"""
On-demand patent inspection tool.

Pulls a patent from the local DB (cache/patents.db) and re-runs snippet
extraction with whatever aliases/keywords you supply at the command line.
If the patent isn't in the DB, fetches raw content from EPO without
persisting (sandbox mode) — useful for ad-hoc exploration without
polluting the production DB.

Read-only with respect to cache/patents.db. Calls EPO API only on DB miss
(or when --force-refetch is used).

Usage:
    # patent already in DB
    python -m tools.inspect_patent EP2089013B1 --aliases acetaminophen

    # patent not in DB → sandbox fetch (not persisted)
    python -m tools.inspect_patent EP1234567B1 --aliases acetaminophen

    # force re-fetch from EPO, bypassing DB and diskcache (read-only)
    python -m tools.inspect_patent EP2443120A2 --force-refetch --raw

    # raw dump
    python -m tools.inspect_patent EA004311B1 --raw --source abstract

    # custom keywords
    python -m tools.inspect_patent EP2089013B1 \\
        --aliases acetaminophen \\
        --keywords compris tablet capsule excipient diluent binder

Designed for:
    - exploration ("does patent X mention drug Y?")
    - debugging Task A snippet quality on individual patents
    - testing new keyword/alias variants before changing config
    - cross-project serendipity (find unexpected drug overlaps)
    - verifying whether EPO has content for a specific patent (--force-refetch)
"""
import argparse
import sqlite3
import sys
from modules.patent_fetcher import _extract_formulation_snippets

DB_PATH = "cache/patents.db"

# Default keywords from patent_fetcher.py (kept in sync manually for now)
DEFAULT_KEYWORDS = [
    "composition", "formulation", "compris",
    "excipient", "tablet", "capsule", "carrier",
]


def get_patent(patent_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT patent_id, title, year, source,
                      claims, examples_extracted, abstract,
                      formulation_snippets
               FROM patents WHERE patent_id = ?""",
            (patent_id,),
        ).fetchone()
    return dict(row) if row else None


def _patent_urls(patent_id):
    """Generate Espacenet and Google Patents URLs for manual lookup."""
    espacenet = f"https://worldwide.espacenet.com/patent/search?q=pn%3D{patent_id}"
    google    = f"https://patents.google.com/patent/{patent_id}/en"
    return espacenet, google


def _clear_epo_cache(patent_id):
    """Remove stale diskcache entries for a patent so _fetch_* hits EPO fresh."""
    import diskcache
    cache = diskcache.Cache("cache/epo")
    cleared = []
    for prefix in ("title", "abstract", "claims"):
        key = f"{prefix}::{patent_id}"
        if key in cache:
            cache.delete(key)
            cleared.append(prefix)
    if cleared:
        print(f"[!] Cleared diskcache: {', '.join(cleared)}")
    else:
        print(f"[!] No diskcache entries found for {patent_id}")


def get_patent_with_fallback(patent_id, force=False):
    """
    DB miss 時打 EPO 抓 raw，但不寫 DB。
    Returns (patent_dict, source) where source is 'db' or 'epo_sandbox'.
    Returns (None, 'epo_sandbox') when EPO returns no content at all.

    force=True: skip DB lookup, clear diskcache, fetch fresh from EPO.
    """
    if not force:
        p = get_patent(patent_id)
        if p:
            return p, "db"

    if force:
        print(f"[!] --force-refetch: skipping DB, clearing diskcache for {patent_id}")
        _clear_epo_cache(patent_id)
    
    print(f"[!] {patent_id} {'not in DB — ' if not force else ''}fetching from EPO (not persisted)")
    from modules.patent_fetcher import (
        _fetch_title, _fetch_abstract, _fetch_claims,
        _fetch_description, _parse_examples,
    )
    title    = _fetch_title(patent_id)
    abstract = _fetch_abstract(patent_id)
    claims   = _fetch_claims(patent_id)
    examples = _parse_examples(_fetch_description(patent_id))

    # All content empty → EPO API has nothing for this patent
    if not any([title, abstract, claims, examples]):
        espacenet_url, google_url = _patent_urls(patent_id)
        print(f"[!] EPO API returned no content for {patent_id}")
        print(f"    The EPO OPS API has narrower coverage than the")
        print(f"    Espacenet website — a patent visible on the web")
        print(f"    may not be available through the API.")
        print(f"    Try manually:")
        print(f"      Espacenet: {espacenet_url}")
        print(f"      Google:    {google_url}")
        return None, "epo_sandbox"

    return {
        "patent_id": patent_id,
        "title": title,
        "abstract": abstract,
        "claims": claims,
        "examples_extracted": examples,
        "year": "",
        "source": "epo_sandbox",
        "formulation_snippets": None,
    }, "epo_sandbox"


def keyword_count(text, words):
    text_lower = text.lower()
    return {w: text_lower.count(w.lower()) for w in words}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("patent_id")
    ap.add_argument("--aliases", nargs="+", default=None,
                    help="Drug aliases to search (default: count common alias families)")
    ap.add_argument("--keywords", nargs="+", default=None,
                    help=f"Keywords for filter (default: {DEFAULT_KEYWORDS})")
    ap.add_argument("--raw", action="store_true",
                    help="Print full claims + examples instead of extracting snippets")
    ap.add_argument("--source", choices=["claims", "examples", "abstract", "all"],
                    default="all",
                    help="Which DB field to extract from (default: all)")
    ap.add_argument("--force-refetch", action="store_true",
                    help="Skip DB and diskcache, fetch fresh from EPO (read-only, no DB write)")
    args = ap.parse_args()

    p, source = get_patent_with_fallback(args.patent_id, force=args.force_refetch)
    if not p:
        print(f"ERROR: {args.patent_id} — no content available from EPO or DB.",
              file=sys.stderr)
        sys.exit(1)

    # Header
    print(f"\n{'='*70}")
    print(f"Patent: {p['patent_id']}  ({p['year'] or '?'}, fetched_from={source})")
    print(f"Title:  {p['title'] or '(no title)'}")
    print('='*70)
    print(f"  claims:             {len(p['claims'] or ''):>6} chars")
    print(f"  examples_extracted: {len(p['examples_extracted'] or ''):>6} chars")
    print(f"  abstract:           {len(p['abstract'] or ''):>6} chars")
    print(f"  stored snippets:    {len(p['formulation_snippets'] or '') > 2 and 'yes' or 'empty/NULL'}")

    # Jurisdiction hint: sandbox title/abstract only (no fulltext)
    if source == "epo_sandbox" and not (p["claims"] or p["examples_extracted"]):
        espacenet_url, google_url = _patent_urls(p["patent_id"])
        print(f"\n  ⚠  EPO API returned title/abstract only (no fulltext).")
        print(f"     The OPS API does not license non-EP claims/description.")
        print(f"     (Espacenet website may still show them — different backend.)")
        print(f"     For full text, try:")
        print(f"       Espacenet: {espacenet_url}")
        print(f"       Google:    {google_url}")

    # Pick text source
    sources = {
        "claims":   p["claims"] or "",
        "examples": p["examples_extracted"] or "",
        "abstract": p["abstract"] or "",
    }
    if args.source == "all":
        targets = sources
    else:
        targets = {args.source: sources[args.source]}

    # Raw dump mode
    if args.raw:
        for name, text in targets.items():
            print(f"\n--- {name} ({len(text)} chars) ---")
            print(text or "(empty)")
        return

    # Default mode: keyword counts + snippet extraction
    if args.aliases:
        aliases = args.aliases
    else:
        # Common probe set — change to whatever you usually want
        aliases = ["acetaminophen", "paracetamol", "tylenol",
                   "ampicillin", "pemirolast", "roflumilast",
                   "ibuprofen", "lactose", "MCC", "microcrystalline cellulose"]

    keywords = args.keywords or DEFAULT_KEYWORDS

    # Per-source keyword/alias counts
    print(f"\nAlias counts (per source):")
    print(f"  {'alias':<35s} | {'claims':>7s} | {'examples':>9s} | {'abstract':>9s}")
    print(f"  {'-'*35}-+-{'-'*7}-+-{'-'*9}-+-{'-'*9}")
    for alias in aliases:
        counts = {name: text.lower().count(alias.lower())
                  for name, text in sources.items()}
        if any(counts.values()):
            print(f"  {alias:<35s} | {counts['claims']:>7d} | "
                  f"{counts['examples']:>9d} | {counts['abstract']:>9d}")

    # Snippet extraction (custom)
    # Temporarily monkey-patch the keyword list if user gave one
    if args.keywords:
        # Call the function but with our keyword set
        # _extract_formulation_snippets has KEYWORDS hardcoded inside,
        # so we replicate the logic here with the custom list.
        import re
        def extract_with_custom_kw(text, drug_aliases, kw):
            sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text)
            out = []
            for s in sentences:
                s_lower = s.lower()
                has_drug = any(a.lower() in s_lower for a in drug_aliases)
                has_kw = any(k in s_lower for k in kw)
                if has_drug and has_kw:
                    out.append(s.strip())
            return out[:20]
        extractor = lambda text: extract_with_custom_kw(text, aliases, keywords)
    else:
        extractor = lambda text: _extract_formulation_snippets(text, aliases)

    print(f"\nSnippets (aliases={aliases[:5]}{'...' if len(aliases) > 5 else ''},")
    print(f"          keywords={keywords}):")

    for name, text in targets.items():
        snippets = extractor(text) if text else []
        print(f"\n--- from {name}: {len(snippets)} snippet(s) ---")
        for s in snippets:
            preview = s if len(s) <= 300 else s[:300] + " ..."
            print(f"  - {preview}")


if __name__ == "__main__":
    main()