"""
Import Google Patents fulltext from JSONL produced by external scraper.

Context: Google Patents scraping runs on a separate environment (Kaggle
notebook) to isolate IP/ToS risk from the production machine. This script
imports the resulting JSONL back into the local SQLite cache, filling
claims + examples_extracted for non-EP/WO rows where EPO OPS returns 404
(US/CN/KR/JP/EA fulltext licensing gap — see PROJECT_SKILL §"Known EPO
OPS limits").

This script REPLACES the production-fetcher path of task_H_google_patents_l2.
The scraper lives off-machine; we only do read-JSONL + write-DB here, so
the original ToS/IP-block concerns that motivated the modules/scripts split
in task H don't apply — there is no live fetch from this script.

Design:
- Read JSONL line by line, idempotent (re-running same file is safe)
- Skip dirty rows: title starting with "Not Found" or "Error" (404s,
  CSV-header-as-data pollution — verified by probe scratch/probe_jsonl_join.py)
- Skip EP/WO rows: EPO OPS is the authoritative source for these,
  Google Patents should not overwrite
- Only update rows where claims IS NULL OR claims = '' (don't clobber
  existing EPO fulltext)
- Sentinel handling: JSONL "N/A" strings (scraper's parse-failure marker)
  are treated as missing, not written to DB
- Mark source = 'google_patents' so downstream can distinguish origin
- Write claims → claims, full_text → examples_extracted
  (chosen over schema migration: examples_extracted already feeds
  backfill_snippets; new source plugs into existing pipeline unchanged)

After running this, re-run scripts/backfill_snippets.py to refresh
formulation_snippets for the newly populated rows.

Usage:
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --dry-run
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --apply
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --apply --limit 20

Refs:
- docs/spec/task_H_google_patents_l2.md (original spec; this script
  supersedes the in-process fetcher approach)
- scratch/probe_jsonl_join.py (join-key + match-rate verification)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts._backfill_common import DB_PATH, start_run, finish_run

SCRIPT_NAME = "import_google_patents_jsonl"
CASE_TYPE = "google_patents_jsonl_import"
# Source tags written to patents.source column. Existing pre-Task-I value
# is 'epo'. We add two new values to disambiguate downstream queries:
#   'google_patents'           — row entirely (re)written by this importer
#                                (DB row had empty claims AND empty examples)
#   'mixed_epo_google_patents' — claims written by us, examples kept from
#                                EPO. Signals to downstream tools that
#                                examples_extracted came from EPO even though
#                                claims came from Google.
SOURCE_TAG = "google_patents"
MIXED_SOURCE_TAG = "mixed_epo_google_patents"

# Sentinel values the scraper writes when extraction fails or HTTP non-200.
# Treat as missing data; do NOT write to DB (would mask real gaps).
MISSING_SENTINELS = {"N/A", "", None}
ERROR_TITLE_PREFIXES = ("Not Found", "Error")
SKIP_COUNTRY_CODES = ("EP", "WO")  # EPO OPS is authoritative for these


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _is_missing(value) -> bool:
    return value in MISSING_SENTINELS


def _classify(record: dict, existing_claims: str | None) -> str:
    """
    Decide what to do with a JSONL record. Returns a reason string:
      'apply'          — write to DB
      'skip_dirty'     — title indicates fetch error
      'skip_jurisdiction' — EP/WO, leave EPO data alone
      'skip_not_in_db' — patent_id not in local cache
      'skip_has_claims' — DB already has claims, don't overwrite
      'skip_no_useful_content' — both claims and full_text are missing
    """
    raw_id = record.get("requested_id", "")
    title = record.get("title", "") or ""

    if any(title.startswith(p) for p in ERROR_TITLE_PREFIXES):
        return "skip_dirty"

    if raw_id[:2] in SKIP_COUNTRY_CODES:
        return "skip_jurisdiction"

    if existing_claims is None:
        return "skip_not_in_db"

    if existing_claims.strip():
        return "skip_has_claims"

    claims = record.get("claims")
    full_text = record.get("full_text")
    if _is_missing(claims) and _is_missing(full_text):
        return "skip_no_useful_content"

    return "apply"


def _load_jsonl(path: Path):
    """Yield (line_no, record) tuples; log and skip malformed lines."""
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARN: line {i} malformed JSON, skipping: {e}", file=sys.stderr)


def _fetch_existing(
    conn: sqlite3.Connection, patent_id: str
) -> tuple[str | None, str | None]:
    """
    Return (claims, examples_extracted) for the DB row.

    Returns (None, None) sentinel if patent_id not in DB.
    Within a found row, empty/NULL columns are normalized to ''.

    Both columns are checked because EPO sometimes captures examples
    without claims (verified N=7 in cache as of 2026-06-03). The importer
    must protect both independently to avoid overwriting that data.
    """
    row = conn.execute(
        "SELECT claims, examples_extracted FROM patents WHERE patent_id = ?",
        (patent_id,),
    ).fetchone()
    if row is None:
        return None, None
    claims = row["claims"] if row["claims"] is not None else ""
    examples = (
        row["examples_extracted"]
        if row["examples_extracted"] is not None
        else ""
    )
    return claims, examples


def _apply_update(
    conn: sqlite3.Connection,
    patent_id: str,
    claims: str | None,
    full_text: str | None,
    existing_examples: str,
) -> None:
    """
    Write claims + examples_extracted; tag source + fetched_at.

    Protection rules (both columns are protected against accidental overwrite
    of EPO-captured content):
    - claims: only written if non-missing. Caller's _classify() already
      guaranteed existing claims is empty by the time we reach here.
    - examples_extracted: only written if non-missing AND existing is empty.
      _classify() does NOT gate on existing examples (it could, but the
      cleaner separation is: classification decides 'should we touch this
      row at all'; this function decides 'which columns are safe to write').

    Source tagging:
    - Default: SOURCE_TAG ('google_patents') — examples were also (re)written
      from JSONL, so the row's textual content is entirely Google-sourced.
    - When EPO examples are preserved (existing_examples non-empty): use
      MIXED_SOURCE_TAG ('mixed_epo_google_patents'). Tells downstream that
      claims came from Google but examples are still EPO. Without this,
      'source=google_patents' would be misleading for hybrid rows.
    """
    now = datetime.now().isoformat()

    keep_existing_examples = bool(existing_examples.strip())
    source_value = MIXED_SOURCE_TAG if keep_existing_examples else SOURCE_TAG

    fields = ["source = ?", "fetched_at = ?", "formulation_snippets = NULL"]
    values: list = [source_value, now]

    if not _is_missing(claims):
        fields.append("claims = ?")
        values.append(claims)

    if not _is_missing(full_text) and not keep_existing_examples:
        fields.append("examples_extracted = ?")
        values.append(full_text)

    values.append(patent_id)
    conn.execute(
        f"UPDATE patents SET {', '.join(fields)} WHERE patent_id = ?",
        values,
    )


def run(input_path: Path, apply: bool, limit: int | None) -> int:
    counts: Counter = Counter()
    sample_apply: list[str] = []

    conn = _conn()
    try:
        applied = 0
        for line_no, rec in _load_jsonl(input_path):
            patent_id = rec.get("requested_id", "")
            existing_claims, existing_examples = _fetch_existing(conn, patent_id)
            verdict = _classify(rec, existing_claims)
            counts[verdict] += 1

            if verdict != "apply":
                continue

            if len(sample_apply) < 5:
                sample_apply.append(patent_id)

            if apply:
                _apply_update(
                    conn,
                    patent_id,
                    rec.get("claims"),
                    rec.get("full_text"),
                    existing_examples or "",
                )
                applied += 1
                if limit is not None and applied >= limit:
                    print(f"  reached --limit {limit}, stopping")
                    break

        if apply:
            conn.commit()
    finally:
        conn.close()

    # Report
    print("\n=== Result ===")
    for verdict, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {verdict:30s} {n}")
    if sample_apply:
        print(f"\n  apply sample (first 5): {sample_apply}")
    print(f"\nMode: {'APPLY (DB written)' if apply else 'DRY-RUN (no DB write)'}")

    return counts.get("apply", 0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--input", required=True, type=Path, help="Path to JSONL file")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Classify only, no DB write")
    mode.add_argument("--apply", action="store_true", help="Write to DB + audit log")
    p.add_argument("--limit", type=int, default=None, help="Cap number of rows applied (testing)")
    args = p.parse_args()

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    args_dict = {"input": str(args.input), "apply": args.apply, "limit": args.limit}

    if args.dry_run:
        run(args.input, apply=False, limit=args.limit)
        return 0

    # apply path: audit log wrap
    run_id = start_run(SCRIPT_NAME, CASE_TYPE, args_dict)
    try:
        applied_n = run(args.input, apply=True, limit=args.limit)
        finish_run(
            run_id,
            rows_affected=applied_n,
            notes=f"applied={applied_n} from {args.input.name}",
        )
    except Exception as e:
        finish_run(run_id, rows_affected=0, notes=f"FAILED: {e}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())