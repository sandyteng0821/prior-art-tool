"""
Backfill filing_date / expiry_date / expiry_source for patents in DB.

Two-layer strategy (per probe_expiry_date_20260625.md §7):

1. **Orange Book** (priority): US patents only. Expiry date includes PTE,
   so it is the most accurate source. Looked up from the local cache file
   `cache/orange_book/patents_lookup.json` — no network call.

2. **EPO biblio** (fallback): All jurisdictions. Retrieves filing_date via
   EPO OPS API, calculates base term = filing_date + 20 years. Does NOT
   include PTE/SPC, so systematically underestimates 2-5 years for FDA-
   approved drug patents. Caveat is recorded in expiry_source.

Target rows: patents where expiry_date IS NULL. Running twice is safe
(already-populated rows are skipped).

Usage:
    python -m scripts.backfill_expiry_dates --dry-run
    python -m scripts.backfill_expiry_dates --apply
    python -m scripts.backfill_expiry_dates --apply --epo-only   # skip OB
    python -m scripts.backfill_expiry_dates --apply --ob-only    # skip EPO

Rate limit: EPO calls throttled to 1 req/sec (see tools.fetch_dates).
Orange Book lookup is local JSON, effectively instant.

Refs:
    probe_expiry_date_20260625.md — full probe results
    architecture.md Gap #5 / #8
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Optional

from scripts._backfill_common import DB_PATH, start_run, finish_run

# ── Orange Book lookup ───────────────────────────────────────────────────────

OB_CACHE_PATH = Path("cache/orange_book/patents_lookup.json")


def _load_orange_book() -> dict:
    """
    Load Orange Book patent lookup from local cache.
    Returns dict keyed by bare patent number (e.g. "7326708").
    Each value is a list of entries with expire_date, drug_name, etc.
    Returns empty dict if cache file doesn't exist.
    """
    if not OB_CACHE_PATH.exists():
        print(f"[backfill_expiry] Orange Book cache not found at {OB_CACHE_PATH}")
        return {}
    try:
        with open(OB_CACHE_PATH) as f:
            data = json.load(f)
        print(f"[backfill_expiry] Orange Book loaded: {len(data)} patents")
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"[backfill_expiry] WARNING: Failed to load Orange Book: {e}")
        return {}


def _normalize_to_bare_number(patent_id: str) -> str:
    """
    Convert patent_id to bare digits for Orange Book lookup.
    US7326708B2 → 7326708
    US20240335435A1 → 20240335435
    EP4138798B1 → '' (not US, can't look up OB)
    """
    m = re.match(r'^US(\d+)[A-Z]\d*$', patent_id)
    if m:
        return m.group(1)
    return ""


def _ob_lookup(patent_id: str, ob_data: dict) -> Optional[dict]:
    """
    Look up a single patent in Orange Book data.
    Returns {"expiry_date": "YYYY-MM-DD", "drug_name": "..."} or None.

    Orange Book expire_date format is MM/DD/YYYY.
    We convert to YYYY-MM-DD for DB consistency.
    """
    bare = _normalize_to_bare_number(patent_id)
    if not bare:
        return None  # not a US patent
    entries = ob_data.get(bare)
    if not entries:
        return None

    # Take the latest (max) expire_date across all entries for this patent.
    # A patent can appear under multiple drugs / NDAs in Orange Book.
    # The latest date is the most conservative (safe) estimate.
    best_date = None
    best_drug = None
    for entry in entries if isinstance(entries, list) else [entries]:
        raw = entry.get("expire_date") or entry.get("expiration_date") or ""
        # Try MM/DD/YYYY first (Orange Book web format)
        parsed = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed:
            if best_date is None or parsed > best_date:
                best_date = parsed
                best_drug = entry.get("drug_name", entry.get("trade_name", ""))

    if best_date:
        return {
            "expiry_date": best_date.isoformat(),
            "drug_name":   best_drug,
        }
    return None


# ── EPO biblio date fetch ────────────────────────────────────────────────────

def _fetch_filing_date_epo(patent_id: str) -> Optional[str]:
    """
    Fetch filing_date from EPO OPS biblio endpoint.
    Returns YYYY-MM-DD string or None on failure.
    Rate limited: 1 req/sec (caller handles sleep).

    Uses tools.fetch_dates module if available; falls back to direct
    EPO API call otherwise.
    """
    try:
        from tools.fetch_dates import fetch_dates_for_patents
        results = fetch_dates_for_patents([patent_id])
        if results and results[0].get("filing_date"):
            raw = results[0]["filing_date"]
            # EPO returns YYYYMMDD; normalize to YYYY-MM-DD
            if len(raw) == 8 and raw.isdigit():
                return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            return raw
    except ImportError:
        pass
    except Exception as e:
        print(f"  [fetch_dates] {patent_id}: {e}")

    # Fallback: direct EPO call (minimal implementation)
    try:
        import epo_ops
        import os
        from dotenv import load_dotenv
        load_dotenv()

        client = epo_ops.Client(
            key=os.getenv("EPO_CONSUMER_KEY"),
            secret=os.getenv("EPO_CONSUMER_SECRET"),
            accept_type="json",
        )
        # Parse patent_id → number + kind
        m = re.match(r'^([A-Z]{2}\d+)([A-Z]\d*)$', patent_id)
        if not m:
            return None
        number, kind = m.group(1), m.group(2)

        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="biblio",
        )
        data = resp.json()
        doc = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
        app_ref = doc["bibliographic-data"]["application-reference"]

        # application-reference → document-id → date
        doc_ids = app_ref.get("document-id", [])
        if isinstance(doc_ids, dict):
            doc_ids = [doc_ids]

        for did in doc_ids:
            raw_date = did.get("date", {}).get("$", "")
            if raw_date and len(raw_date) == 8:
                return f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

    except Exception as e:
        print(f"  [EPO biblio fallback] {patent_id}: {type(e).__name__}: "
              f"{str(e)[:100]}")

    return None


def _filing_date_plus_20(filing_date_str: str) -> Optional[str]:
    """
    Calculate base term expiry = filing_date + 20 years.
    Input/output: YYYY-MM-DD strings.
    """
    try:
        fd = date.fromisoformat(filing_date_str)
        expiry = fd + relativedelta(years=20)
        return expiry.isoformat()
    except (ValueError, TypeError):
        return None


# ── Main ─────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    from modules.patent_store import init_db
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Backfill expiry dates for all patents in DB.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidates and preview OB hit rate; no DB writes.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to DB. Without --apply, defaults to --dry-run.",
    )
    p.add_argument(
        "--ob-only",
        action="store_true",
        help="Only use Orange Book (skip EPO API calls). Fast, US-only.",
    )
    p.add_argument(
        "--epo-only",
        action="store_true",
        help="Only use EPO filing+20yr (skip Orange Book). Slower but global.",
    )
    args = p.parse_args(argv)

    if args.ob_only and args.epo_only:
        print("[backfill_expiry] Cannot use both --ob-only and --epo-only.",
              file=sys.stderr)
        return 2

    is_dry_run = args.dry_run or not args.apply
    if not args.dry_run and not args.apply:
        print("[backfill_expiry] No --apply given; defaulting to --dry-run.")

    conn = _conn()

    # ── Count candidates ─────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]
    candidates = conn.execute(
        "SELECT patent_id FROM patents "
        "WHERE expiry_date IS NULL OR expiry_date = ''"
    ).fetchall()
    n_candidates = len(candidates)

    # Jurisdiction breakdown for candidates
    us_count = sum(1 for r in candidates if r["patent_id"].startswith("US"))
    non_us_count = n_candidates - us_count

    print(f"[backfill_expiry] DB total: {total} patents")
    print(f"[backfill_expiry] Candidates (expiry_date NULL/empty): {n_candidates}")
    print(f"  US patents:     {us_count}")
    print(f"  Non-US patents: {non_us_count}")

    if n_candidates == 0:
        print("[backfill_expiry] Nothing to do.")
        conn.close()
        return 0

    # ── Load Orange Book ─────────────────────────────────────────────────
    ob_data = {}
    if not args.epo_only:
        ob_data = _load_orange_book()

    # ── Dry-run preview ──────────────────────────────────────────────────
    if is_dry_run:
        # Preview OB hit rate
        if ob_data:
            ob_hits = 0
            for row in candidates:
                if _ob_lookup(row["patent_id"], ob_data):
                    ob_hits += 1
            print(f"[backfill_expiry] dry-run: Orange Book would cover "
                  f"{ob_hits}/{us_count} US patents")
            print(f"[backfill_expiry] dry-run: EPO fallback needed for "
                  f"{us_count - ob_hits} US + {non_us_count} non-US = "
                  f"{n_candidates - ob_hits} patents")
        else:
            print(f"[backfill_expiry] dry-run: No Orange Book; "
                  f"all {n_candidates} patents need EPO API calls "
                  f"(~{n_candidates} seconds @ 1 req/sec)")

        print("[backfill_expiry] dry-run complete; no DB write, no audit log.")
        conn.close()
        return 0

    # ── Real run ─────────────────────────────────────────────────────────
    strategy = "ob_only" if args.ob_only else ("epo_only" if args.epo_only else "ob_then_epo")
    args_dict = {
        "strategy":       strategy,
        "candidate_count": n_candidates,
        "us_count":        us_count,
        "non_us_count":    non_us_count,
        "ob_patents":      len(ob_data),
    }
    run_id = start_run("backfill_expiry_dates", "expiry", args_dict)

    n_ob = 0
    n_epo = 0
    n_skipped = 0
    n_failed = 0
    notes = ""

    try:
        for i, row in enumerate(candidates, 1):
            patent_id = row["patent_id"]
            filing_date = None
            expiry_date = None
            expiry_source = None

            # ── Layer 1: Orange Book ─────────────────────────────────
            if not args.epo_only:
                ob_result = _ob_lookup(patent_id, ob_data)
                if ob_result:
                    expiry_date = ob_result["expiry_date"]
                    expiry_source = "orange_book"
                    n_ob += 1
                    # We don't get filing_date from OB, but we can still
                    # try EPO for filing_date only (informational)
                    # Skip for now — OB expiry_date is what matters

            # ── Layer 2: EPO filing+20yr ─────────────────────────────
            if expiry_date is None and not args.ob_only:
                fd = _fetch_filing_date_epo(patent_id)
                if fd:
                    filing_date = fd
                    expiry_date = _filing_date_plus_20(fd)
                    expiry_source = "filing_plus_20"
                    n_epo += 1
                else:
                    n_failed += 1
                    # Log but continue — don't crash the whole run
                    print(f"  [skip] {patent_id}: no filing_date from EPO")
                time.sleep(1)  # EPO rate limit: 1 req/sec

            if expiry_date is None:
                n_skipped += 1
                continue

            # ── Write to DB ──────────────────────────────────────────
            # Narrow UPDATE (not upsert) to avoid touching other columns.
            conn.execute(
                """
                UPDATE patents
                SET filing_date   = COALESCE(?, filing_date),
                    expiry_date   = ?,
                    expiry_source = ?
                WHERE patent_id = ?
                """,
                (filing_date, expiry_date, expiry_source, patent_id),
            )

            if i % 50 == 0:
                conn.commit()
                print(f"  [progress] {i}/{n_candidates}: "
                      f"OB={n_ob}, EPO={n_epo}, fail={n_failed}")

        conn.commit()

    except KeyboardInterrupt:
        notes = f"interrupted at {n_ob + n_epo + n_failed}/{n_candidates}"
        print(f"\n[backfill_expiry] INTERRUPTED: {notes}")
        conn.commit()  # save progress
        finish_run(run_id, n_ob + n_epo, notes)
        conn.close()
        return 1
    except Exception as e:
        notes = f"crashed at row {n_ob + n_epo + n_failed + 1}/{n_candidates}: {e!r}"
        print(f"[backfill_expiry] FAILED: {notes}", file=sys.stderr)
        conn.commit()  # save progress
        finish_run(run_id, n_ob + n_epo, notes)
        conn.close()
        return 1

    notes = (
        f"ob={n_ob}, epo={n_epo}, skipped={n_skipped}, "
        f"failed={n_failed}, strategy={strategy}"
    )
    finish_run(run_id, n_ob + n_epo, notes)

    print(f"\n[backfill_expiry] Done:")
    print(f"  Orange Book:    {n_ob} patents")
    print(f"  EPO filing+20:  {n_epo} patents")
    print(f"  Skipped:        {n_skipped} (no source available)")
    print(f"  Failed:         {n_failed} (EPO error)")
    print(f"  Total written:  {n_ob + n_epo}/{n_candidates}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
