"""
fetch_dates — 查詢專利的日期欄位（EPO biblio endpoint）

給一組 patent ID，回傳 filing_date / publication_date / priority_date。
每筆打一次 EPO biblio endpoint，不寫 DB，純查詢工具。

Usage:
    # 單筆 / 多筆
    python3 -m tools.fetch_dates EP2107907B1
    python3 -m tools.fetch_dates EP2107907B1 US9415051B1 CN103830190A

    # 從 Espacenet 貼分號分隔的 family list
    python3 -m tools.fetch_dates 'EP4138798A1;EP4138798B1;US2023157975A1'

    # 從檔案讀
    python3 -m tools.fetch_dates --file patent_ids.txt

    # JSON 輸出（方便下游 script 消費）
    python3 -m tools.fetch_dates EP2107907B1 --json

    # 跟 DB 裡的 year 欄位對照
    python3 -m tools.fetch_dates EP2107907B1 --compare-db

    # 估算 expiry（filing_date + 20 年，粗估）
    python3 -m tools.fetch_dates EP2107907B1 --expiry

Cost: 1 EPO API call per patent (biblio endpoint, same as _fetch_title).
      Rate limited to 1 req/sec.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import epo_ops
from dotenv import load_dotenv

load_dotenv()

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

DB_PATH = Path(_project_root) / "cache" / "patents.db"

# ── EPO client ───────────────────────────────────────────────────────────────
client = epo_ops.Client(
    key=os.getenv("EPO_CONSUMER_KEY"),
    secret=os.getenv("EPO_CONSUMER_SECRET"),
    accept_type="json",
)


def _parse_patent_id(patent_id: str) -> tuple[str, str]:
    """Same logic as patent_fetcher._parse_patent_id"""
    m = re.match(r'^([A-Z]{2}\d+)([A-Z]\d*)$', patent_id)
    if m:
        return m.group(1), m.group(2)
    return patent_id, ""


def _fmt_date(raw: str) -> str:
    """YYYYMMDD → YYYY-MM-DD. Returns raw if format doesn't match."""
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or ""


def _extract_text(node) -> str:
    """Safely extract text from EPO JSON node (could be dict with '$' or str)."""
    if isinstance(node, dict):
        return node.get("$", "")
    return str(node) if node else ""


# ── Core: fetch dates from EPO biblio ────────────────────────────────────────

def fetch_patent_dates(patent_id: str) -> dict:
    """
    Call EPO biblio endpoint, extract all date fields.

    Returns dict with:
        patent_id, status ('ok' / 'biblio_404' / 'error'),
        publication_date, filing_date, priority_dates (list),
        raw (original YYYYMMDD values for audit)
    """
    number, kind = _parse_patent_id(patent_id)
    result = {
        "patent_id": patent_id,
        "status": "error",
        "publication_date": "",
        "filing_date": "",
        "priority_dates": [],   # list of {date, country, id_type}
        "earliest_priority": "",
        "raw": {},
    }

    try:
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="biblio",
        )

        data = resp.json()
        doc = (
            data.get("ops:world-patent-data", {})
                .get("exchange-documents", {})
                .get("exchange-document", {})
        )
        bib = doc.get("bibliographic-data", {})

        # ── publication-reference ────────────────────────────────────────
        pub_ref = bib.get("publication-reference", {})
        pub_doc_ids = pub_ref.get("document-id", [])
        if isinstance(pub_doc_ids, dict):
            pub_doc_ids = [pub_doc_ids]
        for d in pub_doc_ids:
            date_val = _extract_text(d.get("date", {}))
            if date_val and d.get("@document-id-type") == "epodoc":
                result["publication_date"] = _fmt_date(date_val)
                result["raw"]["publication_date"] = date_val
                break
        # fallback to docdb if epodoc not found
        if not result["publication_date"]:
            for d in pub_doc_ids:
                date_val = _extract_text(d.get("date", {}))
                if date_val:
                    result["publication_date"] = _fmt_date(date_val)
                    result["raw"]["publication_date"] = date_val
                    break

        # ── application-reference (filing date) ─────────────────────────
        app_ref = bib.get("application-reference", {})
        app_doc_ids = app_ref.get("document-id", [])
        if isinstance(app_doc_ids, dict):
            app_doc_ids = [app_doc_ids]
        for d in app_doc_ids:
            date_val = _extract_text(d.get("date", {}))
            if date_val:
                result["filing_date"] = _fmt_date(date_val)
                result["raw"]["filing_date"] = date_val
                break

        # ── priority-claims ──────────────────────────────────────────────
        priority_claims = bib.get("priority-claims", {}).get("priority-claim", [])
        if isinstance(priority_claims, dict):
            priority_claims = [priority_claims]

        raw_priorities = []
        for pc in priority_claims:
            doc_ids = pc.get("document-id", [])
            if isinstance(doc_ids, dict):
                doc_ids = [doc_ids]

            # Collect date + country across doc-id variants (docdb vs epodoc).
            # docdb carries country as a separate node; epodoc may not.
            pc_date = ""
            pc_country = ""
            for d in doc_ids:
                date_node = d.get("date", {})
                if isinstance(date_node, dict):
                    date_val = date_node.get("$", "")
                else:
                    date_val = str(date_node) if date_node else ""

                country_node = d.get("country", {})
                if isinstance(country_node, dict):
                    country_val = country_node.get("$", "")
                else:
                    country_val = str(country_node) if country_node else ""

                if date_val and not pc_date:
                    pc_date = date_val
                if country_val and not pc_country:
                    pc_country = country_val

            if pc_date:
                raw_priorities.append(pc_date)
                result["priority_dates"].append({
                    "date": _fmt_date(pc_date),
                    "country": pc_country,
                    "raw": pc_date,
                })

        # earliest priority
        if raw_priorities:
            earliest = min(raw_priorities)
            result["earliest_priority"] = _fmt_date(earliest)
            result["raw"]["earliest_priority"] = earliest

        result["status"] = "ok"

    except Exception as e:
        err_str = str(e)
        if "404" in err_str:
            result["status"] = "biblio_404"
        elif "timed out" in err_str.lower() or "timeout" in err_str.lower():
            result["status"] = "timeout"
        elif "429" in err_str or "throttl" in err_str.lower():
            result["status"] = "rate_limited"
        else:
            result["status"] = f"error: {err_str[:80]}"

    return result


# ── DB comparison ────────────────────────────────────────────────────────────

def _db_year(patent_id: str) -> str | None:
    """Read current year field from patents.db for comparison."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT year FROM patents WHERE patent_id = ?", (patent_id,)
    ).fetchone()
    conn.close()
    return row["year"] if row else None


# ── Expiry estimation ────────────────────────────────────────────────────────

def _estimate_expiry(filing_date: str) -> str:
    """filing_date (YYYY-MM-DD) + 20 years.

    Patent term is 20 years from the filing date (not priority date).
    Priority date determines right-of-priority, not patent duration.
    Verified against Jenna's manual survey: EP4138798B1 priority=2020,
    filing=2021, expiry=2041 (filing+20, not priority+20).

    Rough estimate — does not account for PTE, SPC, or terminal disclaimer.
    """
    if not filing_date or len(filing_date) != 10:
        return ""
    try:
        year = int(filing_date[:4]) + 20
        return f"{year}{filing_date[4:]}"
    except (ValueError, IndexError):
        return ""


# ── Display ──────────────────────────────────────────────────────────────────

def _print_result(r: dict, compare_db: bool = False, show_expiry: bool = False):
    pid = r["patent_id"]

    if r["status"] != "ok":
        print(f"  {pid:<24} ✗ {r['status']}")
        return

    pub = r["publication_date"] or "—"
    fil = r["filing_date"] or "—"
    pri = r["earliest_priority"] or "—"

    print(f"  {pid}")
    print(f"    publication:       {pub}")
    print(f"    filing:            {fil}")
    print(f"    earliest priority: {pri}")

    if len(r["priority_dates"]) > 1:
        print(f"    all priorities:")
        for p in r["priority_dates"]:
            print(f"      {p['date']}  ({p['country']})")

    if show_expiry:
        exp = _estimate_expiry(r["filing_date"])
        if exp:
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d")
                tag = " ← EXPIRED" if exp_dt < datetime.now() else ""
            except ValueError:
                tag = ""
            print(f"    estimated expiry:  {exp}  (filing+20yr, no PTE/SPC){tag}")
        else:
            print(f"    estimated expiry:  — (no filing date)")

    if compare_db:
        db_year = _db_year(pid)
        if db_year is None:
            print(f"    DB year:           (not in DB)")
        elif db_year == "":
            print(f"    DB year:           (empty string) ← confirms search inline has no date")
        else:
            pub_year = r["publication_date"][:4] if r["publication_date"] else ""
            match = "✓ match" if db_year == pub_year else f"✗ mismatch (pub={pub_year})"
            print(f"    DB year:           {db_year}  {match}")

    google_url = f"https://patents.google.com/patent/{pid}/en"
    print(f"    verify:            {google_url}")
    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch patent date fields from EPO biblio endpoint.",
        prog="python3 -m tools.fetch_dates",
    )
    parser.add_argument(
        "ids", nargs="*",
        help="Patent IDs (space or semicolon separated)",
    )
    parser.add_argument(
        "--file", "-f", type=str,
        help="Read patent IDs from file (one per line, # = comment)",
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output as JSON (for downstream consumption)",
    )
    parser.add_argument(
        "--compare-db", "-c", action="store_true",
        help="Show current DB year field alongside EPO dates",
    )
    parser.add_argument(
        "--expiry", "-e", action="store_true",
        help="Show estimated expiry (filing_date + 20 years, rough)",
    )
    args = parser.parse_args()

    # ── Collect IDs (same pattern as check_db.py) ────────────────────────
    ids = []
    for raw in (args.ids or []):
        for part in raw.replace(",", ";").split(";"):
            part = part.strip()
            if part:
                ids.append(part)

    if args.file:
        if ids:
            print("[ERROR] Use either positional IDs or --file, not both.")
            sys.exit(1)
        p = Path(args.file)
        if not p.exists():
            print(f"[ERROR] File not found: {args.file}")
            sys.exit(1)
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                for part in line.replace(",", ";").split(";"):
                    part = part.strip()
                    if part:
                        ids.append(part)

    if not ids:
        parser.print_help()
        sys.exit(1)

    # ── Fetch dates ──────────────────────────────────────────────────────
    results = []
    ok = 0
    fail = 0

    if not args.json:
        print()
        print(f"  Fetching dates for {len(ids)} patent(s) via EPO biblio")
        print(f"  {'─' * 60}")
        print()

    for i, pid in enumerate(ids):
        r = fetch_patent_dates(pid)
        results.append(r)
        if r["status"] == "ok":
            ok += 1
        else:
            fail += 1

        if not args.json:
            _print_result(r, compare_db=args.compare_db, show_expiry=args.expiry)

        # Rate limit: 1 req/sec (skip sleep after last)
        if i < len(ids) - 1:
            time.sleep(1)

    # ── JSON output ──────────────────────────────────────────────────────
    if args.json:
        # Strip raw field for cleaner output unless debugging
        output = []
        for r in results:
            entry = {
                "patent_id": r["patent_id"],
                "status": r["status"],
                "publication_date": r["publication_date"],
                "filing_date": r["filing_date"],
                "earliest_priority": r["earliest_priority"],
                "priority_dates": r["priority_dates"],
            }
            if args.expiry:
                entry["estimated_expiry"] = _estimate_expiry(r["filing_date"])
            output.append(entry)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"  {'─' * 60}")
    print(f"  Total: {len(ids)}  |  OK: {ok}  |  Failed: {fail}")
    if fail > 0:
        failed_ids = [r["patent_id"] for r in results if r["status"] != "ok"]
        print(f"  Failed IDs: {', '.join(failed_ids)}")
    print()


if __name__ == "__main__":
    main()
    