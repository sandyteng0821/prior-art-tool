"""
parse_orange_book — FDA Orange Book Patent Data Lookup

Downloads and parses the FDA Orange Book data files (ZIP → tilde-delimited
TXT), builds a patent→drug lookup with PTE-inclusive expiry dates, and
provides CLI queries by US patent number.

Why this exists:
  EPO's filing+20yr expiry estimate systematically understates US drug
  patents that carry Patent Term Extension (PTE). Orange Book provides
  the FDA-submitted expiry dates *including* PTE, which can add 2-5 years.
  See probe_expiry_date_20260625.md §7 for the US7326708B2 case study.

Data source:
  https://www.fda.gov/media/76860/download
  → ZIP of three tilde-delimited ASCII text files
  → Patent.txt + Products.txt joined on Appl_No (NDA number)

Usage:
    # Download + parse (first-time setup — run manually, FDA domain
    # may require direct browser download if network restricts it)
    python3 -m tools.parse_orange_book --download

    # If automatic download fails (e.g. network egress block),
    # manually download the ZIP and place it at:
    #   cache/orange_book/orange_book.zip
    # Then parse without --download:
    python3 -m tools.parse_orange_book --parse-only

    # Query single patent (accepts bare number or EPO-style ID)
    python3 -m tools.parse_orange_book 7326708
    python3 -m tools.parse_orange_book US7326708B2

    # Query multiple
    python3 -m tools.parse_orange_book 7326708 9415051

    # Compare with EPO base-term expiry (requires tools.fetch_dates)
    python3 -m tools.parse_orange_book 7326708 --compare-epo

    # JSON output (for downstream consumption)
    python3 -m tools.parse_orange_book 7326708 --json

    # Stats (patent count, drug count, date range)
    python3 -m tools.parse_orange_book --stats

Constraints:
    - Read-only tool (does not modify patents.db)
    - Downloaded ZIP → cache/orange_book/ (gitignored)
    - Parsed lookup → cache/orange_book/patents_lookup.json
    - Patent number normalization: strips 'US' prefix and kind code suffix
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ═══════════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════════

_script_dir = Path(__file__).resolve().parent

# If inside the repo (tools/parse_orange_book.py), use project root.
# If standalone (someone copied just this file), use script's own directory.
if _script_dir.name == "tools" and (_script_dir.parent / "cache").exists():
    _project_root = str(_script_dir.parent)
else:
    _project_root = str(_script_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

CACHE_DIR = Path(_project_root) / "cache" / "orange_book"
ZIP_PATH = CACHE_DIR / "orange_book.zip"
LOOKUP_PATH = CACHE_DIR / "patents_lookup.json"
DOWNLOAD_URL = "https://www.fda.gov/media/76860/download"


# ═══════════════════════════════════════════════════════════════════════════════
# Patent number normalization
# ═══════════════════════════════════════════════════════════════════════════════

_PATENT_NUM_RE = re.compile(r"^(?:US)?(\d+)(?:[A-Z]\d*)?$", re.IGNORECASE)


def normalize_patent_number(raw: str) -> str:
    """
    Extract bare patent number from various input formats.

    Orange Book stores patent numbers as bare digits (e.g. '7326708').
    Pipeline uses EPO format (e.g. 'US7326708B2'). This function handles
    both, plus intermediate forms like 'US7326708' or '7326708B2'.

    Returns the bare digit string, or the input unchanged if no match.
    """
    raw = raw.strip()
    m = _PATENT_NUM_RE.match(raw)
    if m:
        return m.group(1)
    return raw


# ═══════════════════════════════════════════════════════════════════════════════
# Download
# ═══════════════════════════════════════════════════════════════════════════════

def download_zip() -> bool:
    """
    Download the Orange Book ZIP from FDA.

    Returns True on success, False on failure. On failure, prints
    instructions for manual download (FDA may block automated requests
    or network egress may restrict the domain).
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading Orange Book ZIP from FDA...")
    print(f"  URL: {DOWNLOAD_URL}")

    try:
        req = Request(DOWNLOAD_URL, headers={
            "User-Agent": "Mozilla/5.0 (patent-tool; research use)"
        })
        with urlopen(req, timeout=60) as resp:
            data = resp.read()

        if len(data) < 1000:
            print(f"  [WARNING] Response too small ({len(data)} bytes) — "
                  f"likely a redirect page or block.")
            print(f"  Manual download required (see below).")
            _print_manual_instructions()
            return False

        ZIP_PATH.write_bytes(data)
        size_kb = len(data) / 1024
        print(f"  ✓ Saved {size_kb:.0f} KB → {ZIP_PATH}")
        return True

    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  [ERROR] Download failed: {e}")
        _print_manual_instructions()
        return False


def _print_manual_instructions():
    print()
    print("  ── Manual download instructions ──────────────────────────")
    print(f"  1. Open in browser: {DOWNLOAD_URL}")
    print(f"  2. Save the ZIP file to: {ZIP_PATH}")
    print(f"  3. Re-run: python3 -m tools.parse_orange_book --parse-only")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Parse
# ═══════════════════════════════════════════════════════════════════════════════

def _read_tilde_csv(zip_file: zipfile.ZipFile, filename: str) -> list[dict]:
    """
    Read a tilde-delimited TXT file from the Orange Book ZIP.

    The files use '~' as delimiter and have a header row. Returns a list
    of dicts keyed by header names (stripped of whitespace).
    """
    # Find the file in the ZIP (case-insensitive, might be in a subfolder)
    matching = [n for n in zip_file.namelist()
                if n.lower().endswith(filename.lower())]
    if not matching:
        print(f"  [ERROR] '{filename}' not found in ZIP. "
              f"Contents: {zip_file.namelist()}")
        return []

    raw_bytes = zip_file.read(matching[0])

    # Try common encodings (FDA files are typically latin-1 or utf-8)
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        print(f"  [WARNING] Could not decode {filename}, falling back to "
              f"latin-1 with replace")
        text = raw_bytes.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text), delimiter="~")
    # Strip whitespace from field names (FDA files sometimes have trailing ~)
    rows = []
    for row in reader:
        cleaned = {k.strip(): v.strip() if v else ""
                   for k, v in row.items() if k is not None}
        rows.append(cleaned)
    return rows


def _parse_date(date_str: str) -> str | None:
    """
    Parse Orange Book date format 'Mmm DD, YYYY' → 'YYYY-MM-DD'.

    Returns None if unparseable. Handles variations:
    - 'Nov 24, 2026'
    - 'Jan  5, 2030' (extra space)
    - 'Approved prior to Jan 1, 1982' (returns None — not a real date)
    """
    date_str = date_str.strip()
    if not date_str or "prior to" in date_str.lower():
        return None

    # Normalize whitespace
    date_str = " ".join(date_str.split())

    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    print(f"  [WARNING] Unparseable date: '{date_str}'")
    return None


def parse_orange_book() -> dict:
    """
    Parse Patent.txt and Products.txt from the Orange Book ZIP.

    Returns a dict: patent_number (bare digits) → list of entries.
    Each entry is a dict with:
        patent_number, expire_date, expire_date_raw, drug_name,
        active_ingredient, nda_number, drug_substance, drug_product,
        use_code, delist_flag, applicant

    A patent can have multiple entries (different NDAs, products, or
    use codes). The list preserves all of them.
    """
    if not ZIP_PATH.exists():
        print(f"  [ERROR] ZIP not found: {ZIP_PATH}")
        print(f"  Run with --download first, or manually place the ZIP.")
        return {}

    print(f"  Parsing {ZIP_PATH} ...")

    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        print(f"  ZIP contents: {zf.namelist()}")

        # ── Parse Patent.txt ─────────────────────────────────────────
        patent_rows = _read_tilde_csv(zf, "patent.txt")
        print(f"  Patent.txt: {len(patent_rows)} rows")

        # ── Parse Products.txt ───────────────────────────────────────
        product_rows = _read_tilde_csv(zf, "products.txt")
        print(f"  Products.txt: {len(product_rows)} rows")

    # ── Build product lookup: (Appl_Type, Appl_No) → product info ────
    # Multiple products per NDA (different strengths/forms), so we collect
    # unique (ingredient, trade_name, applicant) combos per NDA.
    nda_products: dict[str, dict] = {}
    for row in product_rows:
        nda = row.get("Appl_No", "").strip()
        if not nda:
            continue
        if nda not in nda_products:
            nda_products[nda] = {
                "active_ingredient": row.get("Ingredient", ""),
                "drug_name": row.get("Trade_Name", row.get("Trade Name", "")),
                "applicant": row.get("Applicant_Full_Name",
                                     row.get("Applicant Full Name",
                                             row.get("Applicant", ""))),
                "approval_date": row.get("Approval_Date", ""),
            }

    # ── Build patent lookup ──────────────────────────────────────────
    lookup: dict[str, list[dict]] = {}

    # Detect actual column names (FDA may use spaces or underscores)
    if patent_rows:
        sample_keys = list(patent_rows[0].keys())
        # Find the patent number column
        pat_col = _find_col(sample_keys, ["Patent_No", "Patent No",
                                           "Patent Number", "Patent_Number"])
        nda_col = _find_col(sample_keys, ["Appl_No", "Appl No",
                                           "Application_No"])
        expire_col = _find_col(sample_keys, ["Patent_Expire_Date_Text",
                                              "Patent Expire Date Text",
                                              "Patent_Expire_Date",
                                              "Patent Expire Date"])
        subst_col = _find_col(sample_keys, ["Drug_Substance_Flag",
                                             "Drug Substance Flag"])
        prod_col = _find_col(sample_keys, ["Drug_Product_Flag",
                                            "Drug Product Flag"])
        use_col = _find_col(sample_keys, ["Patent_Use_Code",
                                           "Patent Use Code"])
        delist_col = _find_col(sample_keys, ["Delist_Flag",
                                              "Delist Flag",
                                              "Patent_Delist_Request_Flag"])

        if not pat_col or not nda_col:
            print(f"  [ERROR] Cannot identify key columns. "
                  f"Available: {sample_keys}")
            return {}

        print(f"  Column mapping: patent={pat_col}, nda={nda_col}, "
              f"expire={expire_col}")

    # Track seen (patent, nda, expire, use_code) to dedup.
    # Orange Book lists one row per Product_No (strength), but after
    # NDA join, 25MG and 50MG of the same drug produce identical entries.
    seen_entries: set[tuple] = set()

    for row in patent_rows:
        pat_num = row.get(pat_col, "").strip()
        if not pat_num:
            continue

        nda = row.get(nda_col, "").strip()
        expire_raw = row.get(expire_col, "").strip() if expire_col else ""
        expire_iso = _parse_date(expire_raw)
        use_code = row.get(use_col, "").strip() if use_col else ""
        delist = row.get(delist_col, "").strip() if delist_col else ""

        # Dedup key: same patent × same NDA × same expiry × same use code
        dedup_key = (pat_num, nda, expire_iso, use_code)
        if dedup_key in seen_entries:
            continue
        seen_entries.add(dedup_key)

        # Get product info from NDA join
        prod_info = nda_products.get(nda, {})

        entry = {
            "patent_number": pat_num,
            "expire_date": expire_iso,
            "expire_date_raw": expire_raw,
            "drug_name": prod_info.get("drug_name", ""),
            "active_ingredient": prod_info.get("active_ingredient", ""),
            "nda_number": nda,
            "applicant": prod_info.get("applicant", ""),
            "drug_substance": row.get(subst_col, "") if subst_col else "",
            "drug_product": row.get(prod_col, "") if prod_col else "",
            "use_code": use_code,
            "delist_flag": delist,
        }

        if pat_num not in lookup:
            lookup[pat_num] = []
        lookup[pat_num].append(entry)

    # ── Save lookup JSON ─────────────────────────────────────────────
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOOKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(lookup, f, ensure_ascii=False, indent=2)

    patents_with_dates = sum(1 for entries in lookup.values()
                             if any(e["expire_date"] for e in entries))
    unique_drugs = len({e["drug_name"]
                        for entries in lookup.values()
                        for e in entries
                        if e["drug_name"]})

    print(f"  ✓ Parsed {len(lookup)} unique patents, "
          f"{patents_with_dates} with expiry dates, "
          f"{unique_drugs} unique drug names")
    print(f"  ✓ Saved lookup → {LOOKUP_PATH}")

    return lookup


def _find_col(keys: list[str], candidates: list[str]) -> str | None:
    """Find the first matching column name from a list of candidates."""
    keys_lower = {k.lower().replace(" ", "_"): k for k in keys}
    for c in candidates:
        # Exact match
        if c in keys:
            return c
        # Case-insensitive match with underscore normalization
        normalized = c.lower().replace(" ", "_")
        if normalized in keys_lower:
            return keys_lower[normalized]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Load cached lookup
# ═══════════════════════════════════════════════════════════════════════════════

def load_lookup() -> dict:
    """Load the cached patents_lookup.json. Returns empty dict if not found."""
    if not LOOKUP_PATH.exists():
        print(f"  [ERROR] Lookup not found: {LOOKUP_PATH}")
        print(f"  Run with --download or --parse-only first.")
        return {}

    with open(LOOKUP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# Query + display
# ═══════════════════════════════════════════════════════════════════════════════

def query_patent(lookup: dict, raw_id: str) -> list[dict] | None:
    """
    Look up a patent by number. Accepts bare digits or EPO-style IDs.
    Returns list of matching entries, or None if not found.
    """
    bare = normalize_patent_number(raw_id)
    return lookup.get(bare)


def _print_patent(raw_id: str, entries: list[dict] | None):
    """Display patent lookup results in human-friendly format."""
    bare = normalize_patent_number(raw_id)

    if entries is None:
        print(f"  {bare:<12} ✗ NOT IN ORANGE BOOK")
        return

    # Deduplicate display: group by (drug_name, expire_date)
    seen = set()
    for e in entries:
        key = (e["drug_name"], e["expire_date"], e["use_code"])
        if key in seen:
            continue
        seen.add(key)

        drug = e["drug_name"] or "—"
        ingredient = e["active_ingredient"] or "—"
        expire = e["expire_date"] or "—"
        use_code = e["use_code"]

        parts = [f"Patent {bare}"]
        parts.append(f"→ {drug}")
        parts.append(f"({ingredient})")
        parts.append(f"expires {expire}")
        if use_code:
            parts.append(f"[use: {use_code}]")
        if e.get("delist_flag") == "Y":
            parts.append("[DELIST REQUESTED]")

        flags = []
        if e.get("drug_substance") == "Y":
            flags.append("substance")
        if e.get("drug_product") == "Y":
            flags.append("product")
        if flags:
            parts.append(f"[{'+'.join(flags)}]")

        print(f"  {' '.join(parts)}")


def _print_stats(lookup: dict):
    """Print summary statistics about the parsed Orange Book data."""
    total_patents = len(lookup)
    total_entries = sum(len(v) for v in lookup.values())

    # Unique drugs
    drugs = {e["drug_name"] for entries in lookup.values()
             for e in entries if e["drug_name"]}
    ingredients = {e["active_ingredient"] for entries in lookup.values()
                   for e in entries if e["active_ingredient"]}
    ndas = {e["nda_number"] for entries in lookup.values()
            for e in entries if e["nda_number"]}

    # Date range
    dates = [e["expire_date"] for entries in lookup.values()
             for e in entries if e["expire_date"]]
    min_date = min(dates) if dates else "—"
    max_date = max(dates) if dates else "—"

    # Flag stats
    substance_count = sum(1 for entries in lookup.values()
                          for e in entries if e.get("drug_substance") == "Y")
    product_count = sum(1 for entries in lookup.values()
                        for e in entries if e.get("drug_product") == "Y")
    delist_count = sum(1 for entries in lookup.values()
                       for e in entries if e.get("delist_flag") == "Y")

    print()
    print("  ── Orange Book Statistics ────────────────────────────────")
    print(f"  Unique patents:        {total_patents:>8,}")
    print(f"  Total entries:         {total_entries:>8,}  "
          f"(patent × NDA × use_code)")
    print(f"  Unique drug names:     {len(drugs):>8,}")
    print(f"  Unique ingredients:    {len(ingredients):>8,}")
    print(f"  Unique NDAs:           {len(ndas):>8,}")
    print(f"  Expiry date range:     {min_date} → {max_date}")
    print(f"  Drug substance flags:  {substance_count:>8,}")
    print(f"  Drug product flags:    {product_count:>8,}")
    print(f"  Delist requested:      {delist_count:>8,}")
    print(f"  Lookup file:           {LOOKUP_PATH}")
    print(f"  Lookup file size:      "
          f"{LOOKUP_PATH.stat().st_size / 1024:.0f} KB")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Drug reverse lookup
# ═══════════════════════════════════════════════════════════════════════════════

def _build_drug_index(lookup: dict) -> dict[str, list[dict]]:
    """
    Build reverse index: drug_name (uppercase) → list of entries.

    Each entry carries patent_number, expire_date, use_code, etc.
    """
    index: dict[str, list[dict]] = {}
    for entries in lookup.values():
        for e in entries:
            name = e.get("drug_name", "").strip().upper()
            if not name:
                continue
            if name not in index:
                index[name] = []
            index[name].append(e)
    return index


def _search_drug(lookup: dict, query: str, json_mode: bool = False):
    """
    Find all patents for a drug by name (case-insensitive substring match).

    Shows expiry status: EXPIRED / ACTIVE / EXPIRING SOON (within 1 year).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    one_year = (datetime.now().replace(year=datetime.now().year + 1)
                .strftime("%Y-%m-%d"))

    index = _build_drug_index(lookup)
    query_upper = query.strip().upper()

    # Find matching drug names (exact first, then substring)
    exact = [name for name in index if name == query_upper]
    if exact:
        matches = exact
    else:
        matches = sorted(name for name in index if query_upper in name)

    if not matches:
        # Try matching against active_ingredient instead
        ingredient_matches: dict[str, list[dict]] = {}
        for entries in lookup.values():
            for e in entries:
                ingr = e.get("active_ingredient", "").upper()
                if query_upper in ingr:
                    name = e.get("drug_name", "UNKNOWN")
                    if name not in ingredient_matches:
                        ingredient_matches[name] = []
                    ingredient_matches[name].append(e)

        if ingredient_matches:
            matches = sorted(ingredient_matches.keys())
            index = ingredient_matches
        else:
            print(f"\n  No drugs matching '{query}' found in Orange Book.\n")
            return

    if json_mode:
        result = {}
        for name in matches:
            result[name] = index[name]
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    print()
    for name in matches:
        drug_entries = index[name]

        # Collect unique patents for this drug
        seen: dict[str, dict] = {}  # patent_num → best entry
        for e in drug_entries:
            pn = e["patent_number"]
            if pn not in seen or (e["expire_date"] or "") > (seen[pn].get("expire_date") or ""):
                seen[pn] = e

        # Sort by expire_date (earliest first, None last)
        patents = sorted(
            seen.values(),
            key=lambda e: e["expire_date"] or "9999-99-99",
        )

        # Header
        ingredient = patents[0].get("active_ingredient", "") if patents else ""
        nda = patents[0].get("nda_number", "") if patents else ""
        print(f"  {name}")
        if ingredient:
            print(f"  Active ingredient: {ingredient}")
        if nda:
            applicant = patents[0].get("applicant", "")
            print(f"  NDA: {nda}  Applicant: {applicant}")
        print(f"  {'─' * 64}")
        print(f"  {'Patent':<12} {'Expires':<14} {'Status':<16} "
              f"{'Use Code':<10} Flags")
        print(f"  {'─' * 64}")

        expired_count = 0
        active_count = 0

        for e in patents:
            pn = e["patent_number"]
            exp = e["expire_date"] or "—"
            use = e["use_code"] or ""

            # Status
            if not e["expire_date"]:
                status = "UNKNOWN"
            elif e["expire_date"] < today:
                status = "⚪ EXPIRED"
                expired_count += 1
            elif e["expire_date"] <= one_year:
                status = "🟡 EXPIRING SOON"
                active_count += 1
            else:
                status = "🟢 ACTIVE"
                active_count += 1

            # Flags
            flags = []
            if e.get("drug_substance") == "Y":
                flags.append("S")
            if e.get("drug_product") == "Y":
                flags.append("P")
            if e.get("delist_flag") == "Y":
                flags.append("DELIST")
            flag_str = ",".join(flags)

            print(f"  {pn:<12} {exp:<14} {status:<16} "
                  f"{use:<10} {flag_str}")

        print(f"  {'─' * 64}")
        print(f"  Total: {len(patents)} patents  "
              f"| Active: {active_count}  | Expired: {expired_count}")
        print()


# ═══════════════════════════════════════════════════════════════════════════════
# Batch lookup (CMAP TSV / compound list)
# ═══════════════════════════════════════════════════════════════════════════════

def _match_compound(lookup: dict, query: str) -> dict:
    """
    Match a compound name against Orange Book. Returns a summary dict:
      query, matched_drug, ingredient, n_patents, earliest_expiry,
      latest_expiry, status, patent_numbers

    Match order: trade name exact → trade name substring →
                 ingredient substring. Returns first hit group.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    one_year = (datetime.now().replace(year=datetime.now().year + 1)
                .strftime("%Y-%m-%d"))
    query_upper = query.strip().upper()
    if not query_upper:
        return {"query": query, "matched_drug": None}

    # Collect all entries matching this query
    hits: list[dict] = []

    # 1. Trade name match
    for entries in lookup.values():
        for e in entries:
            if query_upper in e.get("drug_name", "").upper():
                hits.append(e)

    # 2. Ingredient fallback
    if not hits:
        for entries in lookup.values():
            for e in entries:
                if query_upper in e.get("active_ingredient", "").upper():
                    hits.append(e)

    if not hits:
        return {"query": query, "matched_drug": None}

    # Deduplicate patents, take latest expiry per patent
    seen: dict[str, dict] = {}
    for e in hits:
        pn = e["patent_number"]
        if pn not in seen or (e["expire_date"] or "") > (seen[pn].get("expire_date") or ""):
            seen[pn] = e

    patents = list(seen.values())
    dates = [e["expire_date"] for e in patents if e["expire_date"]]
    earliest = min(dates) if dates else None
    latest = max(dates) if dates else None

    # Overall status based on latest expiry
    if not latest:
        status = "UNKNOWN"
    elif latest < today:
        status = "⚪ EXPIRED"
    elif latest <= one_year:
        status = "🟡 EXPIRING SOON"
    else:
        status = "🟢 ACTIVE"

    # Pick the best drug name (most common among hits)
    from collections import Counter
    drug_counts = Counter(e["drug_name"] for e in hits if e["drug_name"])
    best_drug = drug_counts.most_common(1)[0][0] if drug_counts else "—"
    best_ingredient = hits[0].get("active_ingredient", "") if hits else ""

    return {
        "query": query,
        "matched_drug": best_drug,
        "active_ingredient": best_ingredient,
        "n_patents": len(patents),
        "earliest_expiry": earliest,
        "latest_expiry": latest,
        "status": status,
        "patent_numbers": sorted(seen.keys()),
    }


def _batch_lookup(lookup: dict, batch_file: str, json_mode: bool = False):
    """
    Batch lookup from a TSV/CSV file (e.g. CMAP compound table).

    Reads 'cmap_name' column, falls back to 'compound_aliases' if
    cmap_name is a BRD code (no OB match). Outputs a summary table.
    """
    batch_path = Path(batch_file)
    if not batch_path.exists():
        print(f"  [ERROR] File not found: {batch_file}")
        sys.exit(1)

    # Detect delimiter
    sample = batch_path.read_text(encoding="utf-8", errors="replace")[:2000]
    if "\t" in sample:
        delimiter = "\t"
    else:
        delimiter = ","

    rows = []
    with open(batch_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            rows.append(row)

    if not rows:
        print("  [ERROR] No data rows found.")
        return

    # Identify name columns
    headers = list(rows[0].keys())
    name_col = None
    alias_col = None
    for h in headers:
        hl = h.strip().lower()
        if hl == "cmap_name":
            name_col = h
        elif hl in ("compound_aliases", "aliases", "compound_name"):
            alias_col = h

    if not name_col:
        # Fallback: first column that looks like a name
        for h in headers:
            hl = h.strip().lower()
            if "name" in hl or "compound" in hl or "drug" in hl:
                name_col = h
                break

    if not name_col:
        print(f"  [ERROR] Cannot find name column. Available: {headers}")
        print(f"  Expected: 'cmap_name', or any column containing 'name'.")
        return

    print(f"  Reading {len(rows)} compounds from {batch_path.name}",
          file=sys.stderr)
    print(f"  Name column: '{name_col}'"
          + (f"  Alias column: '{alias_col}'" if alias_col else ""),
          file=sys.stderr)
    if not json_mode:
        print()

    # Match each compound
    results = []
    for row in rows:
        name = row.get(name_col, "").strip()
        if not name:
            continue

        # Skip empty quoted strings from CMAP format
        if name in ('""', "''", ""):
            continue

        result = _match_compound(lookup, name)

        # If cmap_name didn't match (often BRD codes), try compound_aliases
        if result["matched_drug"] is None and alias_col:
            alias_raw = row.get(alias_col, "").strip().strip('"').strip("'")
            if alias_raw and alias_raw != name:
                result = _match_compound(lookup, alias_raw)
                if result["matched_drug"]:
                    result["query"] = f"{name} → {alias_raw}"

        results.append(result)

    # Output
    if json_mode:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return

    # Summary table
    found = [r for r in results if r["matched_drug"]]
    missed = [r for r in results if not r["matched_drug"]]

    print(f"  {'Compound':<30} {'OB Drug':<20} {'Patents':>7}  "
          f"{'Latest Expiry':<14} Status")
    print(f"  {'─' * 90}")

    for r in sorted(results, key=lambda x: (x["matched_drug"] is None,
                                             x.get("latest_expiry") or "9999")):
        name_display = r["query"]
        if len(name_display) > 28:
            name_display = name_display[:27] + "…"
        drug = r["matched_drug"] or "—"
        if len(drug) > 18:
            drug = drug[:17] + "…"
        n_pat = r.get("n_patents", 0)
        latest = r.get("latest_expiry") or "—"
        status = r.get("status", "") if r["matched_drug"] else ""

        print(f"  {name_display:<30} {drug:<20} {n_pat:>7}  "
              f"{latest:<14} {status}")

    print(f"  {'─' * 90}")
    print(f"  Total: {len(results)}  |  In OB: {len(found)}  "
          f"|  Not found: {len(missed)}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# EPO comparison
# ═══════════════════════════════════════════════════════════════════════════════

def _compare_epo(lookup: dict, raw_ids: list[str]):
    """
    Compare Orange Book expiry dates with EPO filing+20yr estimates.

    Calls tools/fetch_dates.py via subprocess (it's a CLI tool, not a
    library). Shows the PTE gap (Orange Book date − EPO base term).
    """
    import subprocess

    # Verify fetch_dates is available
    fetch_dates_path = Path(_project_root) / "tools" / "fetch_dates.py"
    if not fetch_dates_path.exists():
        print("  [ERROR] tools/fetch_dates.py not found.")
        print(f"  Expected at: {fetch_dates_path}")
        print("  Run from project root where tools/fetch_dates.py exists.")
        return

    print()
    print("  ── Orange Book vs EPO (filing+20yr) ─────────────────────")
    print(f"  {'Patent':<12} {'OB Expiry':<14} {'EPO Base':<14} "
          f"{'Gap':>8}  Drug")
    print(f"  {'─' * 68}")

    for raw_id in raw_ids:
        bare = normalize_patent_number(raw_id)
        entries = lookup.get(bare)

        # Orange Book date — take the latest expiry across all NDAs.
        # Different NDAs for the same patent can have different PTE
        # (e.g. JUVISYNC 2026-04-11 vs JANUVIA 2026-11-24).
        # The latest date represents the longest effective protection.
        ob_date = None
        drug_name = "—"
        if entries:
            best = max(
                (e for e in entries if e["expire_date"]),
                key=lambda e: e["expire_date"],
                default=None,
            )
            if best:
                ob_date = best["expire_date"]
                drug_name = best["drug_name"] or "—"

        # EPO base term via subprocess: try B2, then B1
        epo_date = None
        for kind in ("B2", "B1"):
            epo_id = f"US{bare}{kind}"
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "tools.fetch_dates",
                     epo_id, "--expiry", "--json"],
                    capture_output=True, text=True, timeout=30,
                    cwd=_project_root,
                )
                if result.returncode != 0:
                    continue
                data = json.loads(result.stdout)
                if isinstance(data, list) and data:
                    rec = data[0]
                elif isinstance(data, dict):
                    rec = data.get(epo_id, data)
                else:
                    continue

                # fetch_dates --json --expiry output format:
                # [ { "patent_id": "...", "status": "ok",
                #     "estimated_expiry": "2024-06-23", ... } ]
                exp = (rec.get("estimated_expiry")
                       or rec.get("expiry_estimate")
                       or rec.get("expiry_date"))
                if exp and rec.get("status") == "ok":
                    # Normalize to YYYY-MM-DD if it's YYYY/MM/DD
                    epo_date = exp.replace("/", "-")[:10]
                    break
            except (subprocess.TimeoutExpired, json.JSONDecodeError,
                    Exception) as e:
                print(f"  [WARNING] fetch_dates {epo_id}: {e}")
                continue

        # Calculate gap
        gap_str = ""
        if ob_date and epo_date:
            try:
                ob_dt = datetime.strptime(ob_date, "%Y-%m-%d")
                epo_dt = datetime.strptime(epo_date, "%Y-%m-%d")
                gap_days = (ob_dt - epo_dt).days
                if gap_days > 0:
                    gap_years = gap_days / 365.25
                    gap_str = f"+{gap_years:.1f}yr"
                elif gap_days < 0:
                    gap_str = f"{gap_days}d"
                else:
                    gap_str = "exact"
            except ValueError:
                gap_str = "?"

        ob_str = ob_date or "—"
        epo_str = epo_date or "—"
        print(f"  {bare:<12} {ob_str:<14} {epo_str:<14} "
              f"{gap_str:>8}  {drug_name}")

    print()
    print("  Note: Gap > 0 indicates PTE (Patent Term Extension).")
    print("  Orange Book includes PTE; EPO base term = filing + 20yr.")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="FDA Orange Book patent expiry lookup "
                    "(includes PTE-adjusted dates).",
        prog="python3 -m tools.parse_orange_book",
    )
    parser.add_argument(
        "ids", nargs="*",
        help="Patent numbers to look up (bare digits or EPO-style, "
             "e.g. '7326708' or 'US7326708B2')",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download Orange Book ZIP from FDA, then parse",
    )
    parser.add_argument(
        "--parse-only", action="store_true",
        help="Parse existing ZIP without downloading "
             "(use after manual download)",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show summary statistics",
    )
    parser.add_argument(
        "--compare-epo", action="store_true",
        help="Compare Orange Book expiry with EPO filing+20yr estimate",
    )
    parser.add_argument(
        "--drug", type=str,
        help="Look up all patents for a drug by name or ingredient "
             "(case-insensitive substring match, e.g. 'januvia' or "
             "'sitagliptin')",
    )
    parser.add_argument(
        "--batch", type=str,
        help="Batch lookup from a TSV/CSV file (e.g. CMAP compound table). "
             "Uses 'cmap_name' column, falls back to 'compound_aliases'.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output in JSON format",
    )
    args = parser.parse_args()

    # ── Download / parse ─────────────────────────────────────────────
    if args.download:
        print()
        ok = download_zip()
        if not ok and not ZIP_PATH.exists():
            sys.exit(1)
        print()
        lookup = parse_orange_book()
        if not lookup:
            sys.exit(1)
        print()

        # If no IDs to query and no --stats and no --drug/--batch, we're done
        if not args.ids and not args.stats and not args.drug and not args.batch:
            return

    elif args.parse_only:
        print()
        lookup = parse_orange_book()
        if not lookup:
            sys.exit(1)
        print()
        if not args.ids and not args.stats and not args.drug and not args.batch:
            return

    else:
        # Load cached lookup
        lookup = load_lookup()
        if not lookup:
            sys.exit(1)

    # ── Stats ────────────────────────────────────────────────────────
    if args.stats:
        _print_stats(lookup)
        if not args.ids and not args.drug:
            return

    # ── Drug lookup ──────────────────────────────────────────────────
    if args.drug:
        _search_drug(lookup, args.drug, json_mode=args.json)
        return

    # ── Batch lookup ─────────────────────────────────────────────────
    if args.batch:
        _batch_lookup(lookup, args.batch, json_mode=args.json)
        return

    # ── Query ────────────────────────────────────────────────────────
    if not args.ids:
        parser.print_help()
        sys.exit(1)

    # Expand semicolon/comma-separated inputs (match check_db pattern)
    ids = []
    for raw in args.ids:
        for part in raw.replace(",", ";").split(";"):
            part = part.strip()
            if part:
                ids.append(part)

    if args.compare_epo:
        _compare_epo(lookup, ids)
        return

    if args.json:
        result = {}
        for raw_id in ids:
            bare = normalize_patent_number(raw_id)
            entries = lookup.get(bare)
            result[bare] = entries  # None if not found
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Default: human-friendly display
    print()
    for raw_id in ids:
        entries = query_patent(lookup, raw_id)
        _print_patent(raw_id, entries)
    print()


if __name__ == "__main__":
    main()
    