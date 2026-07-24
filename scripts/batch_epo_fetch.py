"""
Batch EPO fetch for EP/WO patents that were skip_jurisdiction during JSONL import.

Context: Task M imported 437 IPF patents from Google Patents JSONL. 122 EP/WO
patents were correctly skipped (EPO is authoritative for these jurisdictions).
But some of those 122 may not be in the local DB at all — the pipeline's ta=
search may never have found them, and they weren't imported from JSONL either.

This script:
1. Reads the JSONL and extracts EP/WO patent IDs (the skip_jurisdiction set)
2. Checks which are already in the local DB
3. For missing ones, calls _get_or_fetch() to pull from EPO API and persist
4. Writes search_log entries so backfill_snippets --project can find them

_get_or_fetch() handles the full pipeline: biblio, claims, description,
parse_examples, collect_snippets, upsert, auto B1 upgrade, family expansion.

Usage:
    python -m scripts.batch_epo_fetch \\
        --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \\
        --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \\
        --dry-run

    python -m scripts.batch_epo_fetch \\
        --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \\
        --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \\
        --apply

    python -m scripts.batch_epo_fetch \\
        --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \\
        --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \\
        --apply --limit 5

Refs:
- docs/spec/task_N.md
- docs/spec/task_M.md §"未解決 / 後續"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from scripts._backfill_common import DB_PATH, start_run, finish_run

SCRIPT_NAME = "batch_epo_fetch_skip_jurisdiction"
CASE_TYPE = "epo_fetch_skip_jurisdiction"
DEFAULT_QUERY = "will_manual_review"

# Same as import_google_patents_jsonl.py — EP/WO are EPO-authoritative
SKIP_COUNTRY_CODES = ("EP", "WO")

# Title prefixes that indicate a bad scraper record (not a real patent)
ERROR_TITLE_PREFIXES = ("Not Found", "Error")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
                print(f"  WARN: line {i} malformed JSON, skipping: {e}",
                      file=sys.stderr)


def _extract_skip_jurisdiction_ids(jsonl_path: Path) -> list[str]:
    """
    Read JSONL and return patent IDs that would have been skip_jurisdiction
    by the importer (EP/WO prefix, non-dirty title).

    Replicates the importer's classification logic for the jurisdiction
    check only — this is the source of truth, not the importer's log output.
    """
    ids: list[str] = []
    for _line_no, rec in _load_jsonl(jsonl_path):
        raw_id = rec.get("requested_id", "")
        title = rec.get("title", "") or ""

        # Skip dirty records (same logic as importer _classify)
        if any(title.startswith(p) for p in ERROR_TITLE_PREFIXES):
            continue

        # Only keep EP/WO
        if raw_id[:2] in SKIP_COUNTRY_CODES:
            ids.append(raw_id)

    return ids


def _check_search_log(
    conn: sqlite3.Connection,
    patent_id: str,
    project: str,
) -> bool:
    """Return True if (patent_id, project) already exists in search_log."""
    row = conn.execute(
        "SELECT 1 FROM search_log WHERE patent_id = ? AND project = ?",
        (patent_id, project),
    ).fetchone()
    return row is not None


def _write_search_log(
    conn: sqlite3.Connection,
    patent_id: str,
    project: str,
    query: str,
) -> None:
    """
    Insert search_log entry. Caller must check for duplicates first
    or accept that this may create duplicates (search_log has no UNIQUE
    constraint on (patent_id, project)).
    """
    conn.execute(
        "INSERT INTO search_log (project, query, patent_id, searched_at) "
        "VALUES (?, ?, ?, ?)",
        (project, query, patent_id, datetime.now().isoformat()),
    )


def run(
    jsonl_path: Path,
    project: str,
    query: str,
    apply: bool,
    limit: int | None = None,
    delay: float = 0.6,
) -> dict:
    """
    Main logic. Returns a stats dict for reporting.

    When apply=False (dry-run), only checks DB status — no EPO calls,
    no search_log writes, no audit log.
    """
    # Late import: patent_fetcher does module-level EPO client init,
    # which needs env vars. Only import when we actually need it (apply mode).
    # For dry-run, we only need DB access.
    get_or_fetch = None
    if apply:
        from modules.patent_fetcher import _get_or_fetch
        get_or_fetch = _get_or_fetch

    # Step 1: Extract EP/WO IDs from JSONL
    ep_wo_ids = _extract_skip_jurisdiction_ids(jsonl_path)
    print(f"  EP/WO IDs extracted from JSONL: {len(ep_wo_ids)}")

    # Deduplicate (JSONL might have dups)
    seen = set()
    unique_ids = []
    for pid in ep_wo_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)
    if len(unique_ids) != len(ep_wo_ids):
        print(f"  (deduplicated: {len(ep_wo_ids)} → {len(unique_ids)})")
    ep_wo_ids = unique_ids

    # Step 2: Check each against DB
    conn = _conn()
    stats = {
        "total": len(ep_wo_ids),
        "db_hit": 0,
        "epo_fetched": 0,
        "epo_failed": 0,
        "search_log_written": 0,
        "search_log_existed": 0,
    }
    failures: list[tuple[str, str]] = []
    fetched_ids: list[str] = []

    try:
        for i, patent_id in enumerate(ep_wo_ids, 1):
            if limit is not None and (stats["epo_fetched"] + stats["epo_failed"]) >= limit:
                print(f"  reached --limit {limit}, stopping after {i - 1} IDs checked")
                break

            # Check DB
            row = conn.execute(
                "SELECT patent_id FROM patents WHERE patent_id = ?",
                (patent_id,),
            ).fetchone()

            in_db = row is not None

            if in_db:
                stats["db_hit"] += 1
                if not apply:
                    continue

                # DB hit — still ensure search_log exists
                if not _check_search_log(conn, patent_id, project):
                    _write_search_log(conn, patent_id, project, query)
                    conn.commit()  # release write lock before next iteration
                    stats["search_log_written"] += 1
                else:
                    stats["search_log_existed"] += 1
                continue

            # Not in DB — need EPO fetch
            if not apply:
                # Dry-run: just count
                stats["epo_fetched"] += 1  # "would fetch"
                continue

            # Apply: call _get_or_fetch
            try:
                result = get_or_fetch(patent_id)
                if result:
                    stats["epo_fetched"] += 1
                    fetched_ids.append(patent_id)
                else:
                    # _get_or_fetch returned None — shouldn't happen normally
                    stats["epo_failed"] += 1
                    failures.append((patent_id, "returned None"))
            except Exception as e:
                stats["epo_failed"] += 1
                err_msg = f"{type(e).__name__}: {str(e)[:120]}"
                failures.append((patent_id, err_msg))
                print(f"  [FAIL] {patent_id} — {err_msg}", file=sys.stderr)

            # Write search_log for newly fetched patent
            if patent_id not in [f[0] for f in failures]:
                if not _check_search_log(conn, patent_id, project):
                    _write_search_log(conn, patent_id, project, query)
                    conn.commit()  # release write lock before next iteration
                    stats["search_log_written"] += 1
                else:
                    stats["search_log_existed"] += 1

            # Rate limit between EPO fetches
            if apply:
                time.sleep(delay)

            # Progress
            if i % 20 == 0:
                print(f"  [progress] {i}/{len(ep_wo_ids)} checked, "
                      f"{stats['epo_fetched']} fetched, "
                      f"{stats['db_hit']} DB hit")

        # Each search_log write is committed immediately above,
        # so no batch commit needed here.
    finally:
        conn.close()

    stats["failures"] = failures
    stats["fetched_ids"] = fetched_ids
    return stats


def _print_report(stats: dict, apply: bool, project: str) -> None:
    """Print human-readable summary."""
    mode = "APPLY (DB written)" if apply else "DRY-RUN (no DB write, no EPO calls)"
    label_fetched = "EPO fetched (new)" if apply else "Would fetch (not in DB)"

    print(f"\n{'=' * 55}")
    print(f"  Task N: Batch EPO Fetch for skip_jurisdiction")
    print(f"  Project: {project}")
    print(f"{'=' * 55}")
    print(f"  Total EP/WO IDs:           {stats['total']}")
    print(f"  Already in DB (skip):      {stats['db_hit']}")
    print(f"  {label_fetched:28s} {stats['epo_fetched']}")
    if apply:
        print(f"  EPO fetch failed:          {stats['epo_failed']}")
        print(f"  search_log written:        {stats['search_log_written']}")
        print(f"  search_log already existed:{stats['search_log_existed']}")

    failures = stats.get("failures", [])
    if failures:
        print(f"\n  Failures ({len(failures)}):")
        for pid, err in failures:
            print(f"    {pid}  — {err}")

    print(f"\n  Mode: {mode}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Batch EPO fetch for EP/WO patents skipped during JSONL import.",
    )
    p.add_argument(
        "--jsonl", required=True, type=Path,
        help="Path to the JSONL file that was used for import "
             "(e.g. data/global_patents_archive_IPF_idlist_20260709.jsonl)",
    )
    p.add_argument(
        "--project", required=True, type=str,
        help="Project name for search_log entries "
             "(e.g. 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)')",
    )
    p.add_argument(
        "--query", type=str, default=DEFAULT_QUERY,
        help=f"Query string for search_log (default: '{DEFAULT_QUERY}')",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Check DB status only; no EPO calls, no writes",
    )
    mode.add_argument(
        "--apply", action="store_true",
        help="Fetch from EPO + write search_log + audit log",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Cap number of EPO fetches (for testing)",
    )
    p.add_argument(
        "--delay", type=float, default=0.6,
        help="Seconds between EPO fetches (default: 0.6)",
    )
    args = p.parse_args()

    if not args.jsonl.exists():
        print(f"ERROR: JSONL file not found: {args.jsonl}", file=sys.stderr)
        return 2

    print(f"\n  JSONL: {args.jsonl}")
    print(f"  Project: {args.project}")
    print(f"  Query: {args.query}")

    if args.dry_run:
        stats = run(
            args.jsonl, args.project, args.query,
            apply=False, limit=args.limit, delay=args.delay,
        )
        _print_report(stats, apply=False, project=args.project)
        return 0

    # Apply path: audit log wrap
    args_dict = {
        "jsonl": str(args.jsonl),
        "project": args.project,
        "query": args.query,
        "limit": args.limit,
        "delay": args.delay,
    }
    run_id = start_run(SCRIPT_NAME, CASE_TYPE, args_dict)
    try:
        stats = run(
            args.jsonl, args.project, args.query,
            apply=True, limit=args.limit, delay=args.delay,
        )
        _print_report(stats, apply=True, project=args.project)

        notes = (
            f"total={stats['total']} db_hit={stats['db_hit']} "
            f"fetched={stats['epo_fetched']} failed={stats['epo_failed']} "
            f"search_log={stats['search_log_written']}"
        )
        finish_run(
            run_id,
            rows_affected=stats["epo_fetched"],
            notes=notes,
        )

        # Write list of newly fetched IDs for verification
        fetched_ids = stats.get("fetched_ids", [])
        if fetched_ids:
            out_path = Path("scratch/task_n_fetched.txt")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                "\n".join(fetched_ids) + "\n",
                encoding="utf-8",
            )
            print(f"\n  Fetched IDs written to: {out_path}")

    except Exception as e:
        finish_run(run_id, rows_affected=0, notes=f"FAILED: {e}")
        raise

    return 0


if __name__ == "__main__":
    sys.exit(main())
