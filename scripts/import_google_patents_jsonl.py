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
- --allow-insert: permits inserting patents not yet in DB (e.g. patents
  found by manual expert review that EPO ta= search never returned).
  Without this flag, such rows are skip_not_in_db (original Task I
  behavior preserved by default).
- --project + --query: when provided, automatically write search_log
  entries for each inserted/updated patent, so backfill_snippets
  --project can find them without manual SQL. (Task M improvement;
  resolves Task L "未來改善方向" item.)

After running this, re-run scripts/backfill_snippets.py to refresh
formulation_snippets for the newly populated rows.

Usage:
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --dry-run
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --apply
    python -m scripts.import_google_patents_jsonl --input data/global_patents_archive.jsonl --apply --limit 20
    python -m scripts.import_google_patents_jsonl --input data/will_review.jsonl --allow-insert --dry-run
    python -m scripts.import_google_patents_jsonl --input data/will_review.jsonl --allow-insert --apply
    python -m scripts.import_google_patents_jsonl --input data/expert_batch.jsonl --allow-insert --apply \\
        --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' --query will_expert_batch

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


def _classify(
    record: dict,
    existing_claims: str | None,
    allow_insert: bool = False,
) -> str:
    """
    Decide what to do with a JSONL record. Returns a reason string:
      'apply'          — update existing DB row
      'insert'         — create new DB row (only when allow_insert=True)
      'skip_dirty'     — title indicates fetch error
      'skip_jurisdiction' — EP/WO, leave EPO data alone
      'skip_not_in_db' — patent_id not in local cache (and allow_insert=False)
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
        # Patent not in DB. Original behavior: skip.
        # With --allow-insert: check content before inserting.
        if not allow_insert:
            return "skip_not_in_db"
        claims = record.get("claims")
        full_text = record.get("full_text")
        if _is_missing(claims) and _is_missing(full_text):
            return "skip_no_useful_content"
        return "insert"

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


def _apply_insert(
    conn: sqlite3.Connection,
    record: dict,
) -> None:
    """
    Insert a new patent row from JSONL. Used when --allow-insert is set
    and the patent_id doesn't exist in DB (expert-identified patents that
    EPO ta= search never returned).

    Maps JSONL fields to DB schema:
      requested_id     → patent_id
      title            → title
      abstract         → abstract
      claims           → claims
      full_text        → examples_extracted
      publication_date → year (first 4 chars)

    Source is always 'google_patents' (no hybrid case — row is new).
    family_fetched = 0 (no family expansion yet).
    formulation_snippets = NULL (to be filled by backfill_snippets).
    """
    now = datetime.now().isoformat()
    patent_id = record.get("requested_id", "")
    title = record.get("title")
    abstract = record.get("abstract")
    claims = record.get("claims")
    full_text = record.get("full_text")
    pub_date = record.get("publication_date")

    # Extract year from publication_date (e.g. "2016-06-14" → "2016")
    year = None
    if pub_date and pub_date not in MISSING_SENTINELS:
        year = pub_date[:4] if len(pub_date) >= 4 else None

    conn.execute(
        """INSERT INTO patents (
               patent_id, title, abstract, claims, examples_extracted,
               source, fetched_at, year, family_fetched, formulation_snippets
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)""",
        (
            patent_id,
            None if _is_missing(title) else title,
            None if _is_missing(abstract) else abstract,
            None if _is_missing(claims) else claims,
            None if _is_missing(full_text) else full_text,
            SOURCE_TAG,
            now,
            year,
        ),
    )


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


def _log_search(
    conn: sqlite3.Connection,
    project: str,
    query: str,
    patent_id: str,
) -> None:
    """
    Write a search_log entry so backfill_snippets --project can find
    this patent. Uses INSERT OR IGNORE to be idempotent — re-running
    the importer on the same file won't create duplicate entries.

    Note: search_log has no UNIQUE constraint on (patent_id, project, query),
    so we check for existing entry before inserting to avoid duplicates.
    """
    existing = conn.execute(
        "SELECT 1 FROM search_log WHERE patent_id = ? AND project = ?",
        (patent_id, project),
    ).fetchone()
    if existing:
        return
    conn.execute(
        "INSERT INTO search_log (project, query, patent_id, searched_at) "
        "VALUES (?, ?, ?, ?)",
        (project, query, patent_id, datetime.now().isoformat()),
    )


def run(
    input_path: Path,
    apply: bool,
    limit: int | None,
    allow_insert: bool = False,
    project: str | None = None,
    query: str = "will_manual_review",
) -> int:
    counts: Counter = Counter()
    search_log_count = 0
    sample_apply: list[str] = []
    sample_insert: list[str] = []

    conn = _conn()
    try:
        applied = 0
        for line_no, rec in _load_jsonl(input_path):
            patent_id = rec.get("requested_id", "")
            existing_claims, existing_examples = _fetch_existing(conn, patent_id)
            verdict = _classify(rec, existing_claims, allow_insert=allow_insert)
            counts[verdict] += 1

            if verdict not in ("apply", "insert"):
                continue

            if verdict == "apply" and len(sample_apply) < 5:
                sample_apply.append(patent_id)
            if verdict == "insert" and len(sample_insert) < 5:
                sample_insert.append(patent_id)

            if apply:
                if verdict == "insert":
                    _apply_insert(conn, rec)
                else:
                    _apply_update(
                        conn,
                        patent_id,
                        rec.get("claims"),
                        rec.get("full_text"),
                        existing_examples or "",
                    )

                # Auto-write search_log when --project is provided
                if project:
                    _log_search(conn, project, query, patent_id)
                    search_log_count += 1

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
        print(f"\n  update sample (first 5): {sample_apply}")
    if sample_insert:
        print(f"\n  insert sample (first 5): {sample_insert}")
    if project and apply:
        print(f"\n  search_log entries written: {search_log_count}  (project={project}, query={query})")
    print(f"\nMode: {'APPLY (DB written)' if apply else 'DRY-RUN (no DB write)'}")

    return applied


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--input", required=True, type=Path, help="Path to JSONL file")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Classify only, no DB write")
    mode.add_argument("--apply", action="store_true", help="Write to DB + audit log")
    p.add_argument("--limit", type=int, default=None, help="Cap number of rows applied (testing)")
    p.add_argument(
        "--allow-insert",
        action="store_true",
        help="Allow inserting patents not already in DB (for expert-identified "
             "patents that EPO ta= search never returned). Without this flag, "
             "such rows are classified as skip_not_in_db.",
    )
    p.add_argument(
        "--project",
        type=str,
        default=None,
        help="Project name for search_log entries. When provided, each "
             "inserted/updated patent gets a search_log row so that "
             "backfill_snippets --project can find it. "
             "Example: 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)'",
    )
    p.add_argument(
        "--query",
        type=str,
        default="will_manual_review",
        help="Query string for search_log entries (default: 'will_manual_review'). "
             "Per Task L provenance convention.",
    )
    args = p.parse_args()

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    if args.project and args.dry_run:
        print(f"  NOTE: --project has no effect in dry-run mode (search_log not written)")

    args_dict = {
        "input": str(args.input),
        "apply": args.apply,
        "limit": args.limit,
        "allow_insert": args.allow_insert,
        "project": args.project,
        "query": args.query,
    }

    if args.dry_run:
        run(
            args.input,
            apply=False,
            limit=args.limit,
            allow_insert=args.allow_insert,
            project=args.project,
            query=args.query,
        )
        return 0

    # apply path: audit log wrap
    run_id = start_run(SCRIPT_NAME, CASE_TYPE, args_dict)
    try:
        applied_n = run(
            args.input,
            apply=True,
            limit=args.limit,
            allow_insert=args.allow_insert,
            project=args.project,
            query=args.query,
        )
        notes = f"applied={applied_n} from {args.input.name}"
        if args.project:
            notes += f" | search_log: project={args.project}, query={args.query}"
        finish_run(
            run_id,
            rows_affected=applied_n,
            notes=notes,
        )
    except Exception as e:
        finish_run(run_id, rows_affected=0, notes=f"FAILED: {e}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
