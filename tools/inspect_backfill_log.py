"""
Inspect backfill_log and DB-side backfill scope.

Read-only. Replaces sqlite3 CLI for daily verification of backfill
operations.

Subcommands:
  --show              Print recent backfill_log rows (default).
  --show-id N         Print one log row with full args JSON expanded.
  --list-projects     List distinct project names in search_log with
                      patent counts.
  --null-count        Count patents.formulation_snippets IS NULL, both
                      DB-wide and per-project.
  --case2-count       Count Case-2 family backfill candidates (parens
                      SQL, pre-May-2026 A1/A2 with family_fetched=1),
                      both DB-wide and per-project.
  --dangling          Show backfill_log rows with completed_at IS NULL
                      (crashed or in-progress runs).
  --null-provenance   Distribution of fetched_at for remaining NULL
                      formulation_snippets rows. Used to determine if
                      Bug Z is a historical artifact or current bug.

Usage:
  python -m tools.inspect_backfill_log --show
  python -m tools.inspect_backfill_log --show -n 20
  python -m tools.inspect_backfill_log --show-id 3
  python -m tools.inspect_backfill_log --list-projects
  python -m tools.inspect_backfill_log --null-count
  python -m tools.inspect_backfill_log --case2-count
  python -m tools.inspect_backfill_log --dangling

Refs: docs/spec/task_D.md §Verification, scripts/_backfill_common.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Optional

DB_PATH = "cache/patents.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ── --show ────────────────────────────────────────────────────────────────────

def cmd_show(conn: sqlite3.Connection, n: int) -> int:
    if not _table_exists(conn, "backfill_log"):
        print("[inspect_backfill_log] backfill_log table not yet created. "
              "Run a real (non-dry) backfill first.")
        return 0

    rows = conn.execute(
        """
        SELECT id, started_at, completed_at, script, case_type,
               rows_affected, git_commit, notes, args
        FROM backfill_log
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (n,),
    ).fetchall()

    if not rows:
        print("[inspect_backfill_log] backfill_log is empty.")
        return 0

    print(f"{'id':>3}  {'started_at':<19}  {'script':<18}  "
          f"{'case_type':<15}  {'rows':>5}  {'commit':<10}  status")
    print("-" * 100)
    for r in rows:
        status = "ok" if r["completed_at"] else "DANGLING"
        if r["notes"] and "crashed" in (r["notes"] or ""):
            status = "CRASHED"
        # Compact project/aliases summary from args JSON
        summary = ""
        try:
            a = json.loads(r["args"] or "{}")
            proj = a.get("project") or "<all>"
            src = a.get("alias_source", "")
            aliases = a.get("aliases_used") or a.get("aliases") or []
            alias_preview = (
                ", ".join(aliases[:2]) + (f" +{len(aliases) - 2}" if len(aliases) > 2 else "")
                if aliases else ""
            )
            summary = f"proj={proj[:30]}  aliases=[{alias_preview}] ({src})"
        except Exception:
            summary = "<unparseable args>"

        print(
            f"{r['id']:>3}  "
            f"{(r['started_at'] or '')[:19]:<19}  "
            f"{(r['script'] or ''):<18}  "
            f"{(r['case_type'] or ''):<15}  "
            f"{(r['rows_affected'] if r['rows_affected'] is not None else '-'):>5}  "
            f"{(r['git_commit'] or '')[:10]:<10}  "
            f"{status}"
        )
        print(f"     {summary}")
        if r["notes"]:
            print(f"     notes: {r['notes']}")
    return 0


# ── --show-id ────────────────────────────────────────────────────────────────

def cmd_show_id(conn: sqlite3.Connection, run_id: int) -> int:
    if not _table_exists(conn, "backfill_log"):
        print("[inspect_backfill_log] backfill_log table not yet created.")
        return 1
    row = conn.execute(
        "SELECT * FROM backfill_log WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        print(f"[inspect_backfill_log] no row with id={run_id}")
        return 1
    print(f"=== backfill_log id={row['id']} ===")
    for k in row.keys():
        v = row[k]
        if k == "args" and v:
            try:
                parsed = json.loads(v)
                print(f"  {k}:")
                for ak, av in parsed.items():
                    print(f"    {ak}: {av}")
                continue
            except Exception:
                pass
        print(f"  {k}: {v}")
    return 0


# ── --list-projects ──────────────────────────────────────────────────────────

def cmd_list_projects(conn: sqlite3.Connection) -> int:
    # Each patent can appear in search_log multiple times (multiple
    # query strategies hit the same patent_id). A naive
    # LEFT JOIN + SUM/COUNT(DISTINCT) mixes scalars (SUM doubles) with
    # set-aggregates (DISTINCT dedups), giving inconsistent per-row
    # totals. We sidestep that by first deduplicating patents within
    # each (project, patent_id) pair as a subquery, then aggregating.
    rows = conn.execute(
        """
        WITH project_patents AS (
            SELECT DISTINCT sl.project, sl.patent_id
            FROM search_log sl
        )
        SELECT pp.project,
               COUNT(*) AS searched,
               SUM(CASE WHEN p.patent_id IS NOT NULL THEN 1 ELSE 0 END) AS in_patents,
               SUM(CASE WHEN p.patent_id IS NOT NULL
                         AND p.formulation_snippets IS NULL THEN 1 ELSE 0 END) AS null_snippets
        FROM project_patents pp
        LEFT JOIN patents p ON p.patent_id = pp.patent_id
        GROUP BY pp.project
        ORDER BY in_patents DESC
        """
    ).fetchall()
    if not rows:
        print("[inspect_backfill_log] search_log is empty.")
        return 0
    print(f"{'project':<55} {'searched':>9} {'in DB':>7} {'NULL snip':>10}")
    print("-" * 90)
    for r in rows:
        print(
            f"{(r['project'] or '<NULL>'):<55} "
            f"{r['searched']:>9} "
            f"{(r['in_patents'] or 0):>7} "
            f"{(r['null_snippets'] or 0):>10}"
        )
    # Patents not associated with any project (no search_log row)
    orphan = conn.execute(
        """
        SELECT COUNT(*) FROM patents p
        WHERE NOT EXISTS (
            SELECT 1 FROM search_log sl WHERE sl.patent_id = p.patent_id
        )
        """
    ).fetchone()[0]
    if orphan:
        print(f"{'<no search_log entry>':<55} {'-':>9} {orphan:>7} "
              f"{'(not joined)':>10}")
        print("  Note: orphans are usually family members upserted by "
              "_fetch_and_store_family (no direct search_log entry).")
    return 0


# ── --null-count ─────────────────────────────────────────────────────────────

def cmd_null_count(conn: sqlite3.Connection) -> int:
    total_null = conn.execute(
        "SELECT COUNT(*) FROM patents WHERE formulation_snippets IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]
    has_text = conn.execute(
        "SELECT COUNT(*) FROM patents WHERE formulation_snippets IS NULL "
        "AND (COALESCE(claims, '') != '' "
        "     OR COALESCE(examples_extracted, '') != '')"
    ).fetchone()[0]
    print(f"DB total patents:                          {total}")
    print(f"formulation_snippets IS NULL:              {total_null}")
    print(f"  of which have claims OR examples text:   {has_text}")
    print(f"  of which will get '[]' (no text):        {total_null - has_text}")
    print()
    print("Per project (NULL count, joined via search_log):")
    rows = conn.execute(
        """
        SELECT sl.project,
               COUNT(DISTINCT p.patent_id) AS null_rows
        FROM patents p
        JOIN search_log sl ON sl.patent_id = p.patent_id
        WHERE p.formulation_snippets IS NULL
        GROUP BY sl.project
        ORDER BY null_rows DESC
        """
    ).fetchall()
    for r in rows:
        print(f"  {(r['project'] or '<NULL>'):<55} {r['null_rows']:>5}")
    return 0


# ── --case2-count ────────────────────────────────────────────────────────────

def cmd_case2_count(conn: sqlite3.Connection) -> int:
    """
    Case 2 family backfill scope.

    Uses the corrected (parenthesized) SQL from probe A2; the spec's
    unparenthesized form returns 71 spurious rows.
    """
    total = conn.execute(
        """
        SELECT COUNT(*) FROM patents
        WHERE family_fetched = 1
          AND fetched_at < '2026-05-18'
          AND (patent_id LIKE '%A1' OR patent_id LIKE '%A2')
        """
    ).fetchone()[0]
    print(f"Case 2 candidates (DB-wide):  {total}")
    print()
    print("Per project:")
    rows = conn.execute(
        """
        SELECT sl.project, COUNT(DISTINCT p.patent_id) AS n
        FROM patents p
        LEFT JOIN search_log sl ON sl.patent_id = p.patent_id
        WHERE p.family_fetched = 1
          AND p.fetched_at < '2026-05-18'
          AND (p.patent_id LIKE '%A1' OR p.patent_id LIKE '%A2')
        GROUP BY sl.project
        ORDER BY n DESC
        """
    ).fetchall()
    for r in rows:
        print(f"  {(r['project'] or '<no search_log>'):<55} {r['n']:>5}")
    print()
    # Case 1 hard-coded IDs (per task_D.md), shown separately as they
    # are NOT in the Case 2 SQL result set (probe A11 confirmed zero
    # overlap — all four have family_fetched=0).
    case1_ids = ["EP2443120B1", "EP2107907B1", "EP1285921B1", "NO20210693B1"]
    print("Case 1 hard-coded IDs (current state):")
    for pid in case1_ids:
        row = conn.execute(
            "SELECT patent_id, family_fetched, family_of, fetched_at "
            "FROM patents WHERE patent_id = ?",
            (pid,),
        ).fetchone()
        if row is None:
            print(f"  {pid:20s} NOT IN DB")
        else:
            children = conn.execute(
                "SELECT COUNT(*) FROM patents WHERE family_of = ?", (pid,)
            ).fetchone()[0]
            print(
                f"  {pid:20s} family_fetched={row['family_fetched']} "
                f"#children={children:>3} "
                f"fetched_at={(row['fetched_at'] or '')[:10]}"
            )
    return 0


# ── --dangling ───────────────────────────────────────────────────────────────

def cmd_dangling(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "backfill_log"):
        print("[inspect_backfill_log] backfill_log table not yet created.")
        return 0
    rows = conn.execute(
        """
        SELECT id, started_at, script, case_type, rows_affected, notes
        FROM backfill_log
        WHERE completed_at IS NULL
        ORDER BY started_at DESC
        """
    ).fetchall()
    if not rows:
        print("[inspect_backfill_log] no dangling runs.")
        return 0
    print(f"DANGLING runs (completed_at IS NULL): {len(rows)}")
    for r in rows:
        print(
            f"  id={r['id']:<3}  started={r['started_at'][:19]}  "
            f"script={r['script']}  case={r['case_type']}  "
            f"rows_affected={r['rows_affected']}"
        )
        if r["notes"]:
            print(f"    notes: {r['notes']}")
    print()
    print("Note: dangling rows are either in-progress runs or hard "
          "crashes (process killed before finish_run). For soft errors "
          "(Python exceptions), finish_run is still called and notes "
          "field records the crash reason — those will show as "
          "completed but with CRASHED status in --show.")
    return 0


# ── argparse + dispatch ──────────────────────────────────────────────────────

def cmd_null_provenance(conn: sqlite3.Connection) -> int:
    """
    Inspect when the remaining NULL formulation_snippets rows were
    fetched. Used to determine whether the underlying bug (Bug Z) is
    a historical artifact (all NULL rows pre-date Task A merge) or
    a current production issue (some NULL rows post-date Task A).

    Reference dates:
      2026-05-21 — first Apremilast production run (post-Task-A).
                    Project's own rows (244) are NOT NULL → strong
                    signal Task A is live by then.
      2026-05-18 — Bug X fix merge (family expansion filter widening).
    """
    summary = conn.execute(
        """
        SELECT MIN(fetched_at)          AS oldest,
               MAX(fetched_at)          AS newest,
               COUNT(*)                 AS n,
               SUM(CASE WHEN fetched_at >= '2026-05-21' THEN 1 ELSE 0 END)
                                        AS post_task_a,
               SUM(CASE WHEN fetched_at <  '2026-05-21' THEN 1 ELSE 0 END)
                                        AS pre_task_a,
               SUM(CASE WHEN fetched_at IS NULL THEN 1 ELSE 0 END)
                                        AS no_timestamp
        FROM patents
        WHERE formulation_snippets IS NULL
        """
    ).fetchone()
    print("Remaining NULL formulation_snippets row provenance:")
    print(f"  total rows:                          {summary['n']}")
    print(f"  oldest fetched_at:                   {summary['oldest']}")
    print(f"  newest fetched_at:                   {summary['newest']}")
    print()
    print("  fetched_at >= 2026-05-21 (post-Task-A): "
          f"{summary['post_task_a']}")
    print("  fetched_at <  2026-05-21 (pre-Task-A):  "
          f"{summary['pre_task_a']}")
    print(f"  fetched_at IS NULL:                     "
          f"{summary['no_timestamp']}")
    print()
    if summary["post_task_a"] == 0:
        print("  → Bug Z appears to be HISTORICAL ARTIFACT.")
        print("    All NULL rows pre-date Apremilast first production run.")
        print("    Family backfill will likely clear them via re-fetch.")
    else:
        print("  → Bug Z may be a CURRENT PRODUCTION ISSUE.")
        print(
            f"    {summary['post_task_a']} NULL rows fetched after Task A "
            "should not exist by spec."
        )
        print("    Investigation needed per bug_Z_silent_null_snippets.md.")

    # Distribution by month for context
    print()
    print("By month:")
    rows = conn.execute(
        """
        SELECT substr(fetched_at, 1, 7) AS month,
               COUNT(*) AS n
        FROM patents
        WHERE formulation_snippets IS NULL
        GROUP BY month
        ORDER BY month
        """
    ).fetchall()
    for r in rows:
        print(f"  {r['month'] or '<NULL>':<10} {r['n']:>5}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true",
                       help="Print recent backfill_log rows (default).")
    group.add_argument("--show-id", type=int, metavar="ID",
                       help="Print one log row with full args expanded.")
    group.add_argument("--list-projects", action="store_true",
                       help="List distinct projects in search_log.")
    group.add_argument("--null-count", action="store_true",
                       help="Count NULL formulation_snippets, "
                            "DB-wide and per-project.")
    group.add_argument("--case2-count", action="store_true",
                       help="Count Case-2 family backfill candidates.")
    group.add_argument("--dangling", action="store_true",
                       help="Show backfill_log rows with "
                            "completed_at IS NULL.")
    group.add_argument("--null-provenance", action="store_true",
                       help="Distribution of fetched_at for rows still "
                            "having NULL formulation_snippets. Used to "
                            "determine if Bug Z is historical or live.")
    ap.add_argument("-n", type=int, default=10,
                    help="(for --show) limit rows. Default 10.")
    args = ap.parse_args(argv)

    conn = _conn()
    try:
        if args.show_id is not None:
            return cmd_show_id(conn, args.show_id)
        if args.list_projects:
            return cmd_list_projects(conn)
        if args.null_count:
            return cmd_null_count(conn)
        if args.case2_count:
            return cmd_case2_count(conn)
        if args.dangling:
            return cmd_dangling(conn)
        if args.null_provenance:
            return cmd_null_provenance(conn)
        # default
        return cmd_show(conn, args.n)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())