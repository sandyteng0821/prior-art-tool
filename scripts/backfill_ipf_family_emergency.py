"""
Emergency one-off: backfill family expansion for the 139 A1/A2 parents
in the Pemirolast IPF project that were family-expanded BEFORE Bug X
fix (commit c3206ce, merged 2026-05-18).

This is NOT a substitute for proper Phase 2 backfill_family.py
(task_D.md §Phase 2). It is a scope-restricted emergency run to
unblock a colleague's FTO analysis. Differences from the planned
Phase 2:

  - hard-coded to ONE project string (Pemirolast IPF), not parameterized
  - hard-coded to A1/A2 kind codes only (probe confirmed 100% of
    in-scope rows are A1/A2; see scratch/probe_ipf_backfill_feasibility.py)
  - does NOT handle the 4 Case-1 hard-coded IDs in task_D.md
  - does NOT handle Acetaminophen / Ampicillin / Roflumilast scopes
  - uses _get_or_fetch [DB hit] code path; relies on its kind-code
    guard matching our A1/A2 scope

Pre-implementation probes (read-only, in scratch/):
  - probe_ipf_family_gap.py         : 139 at-risk parents, 115 with zero
                                      children in DB (83% gap)
  - probe_ipf_backfill_feasibility.py: confirmed 100% A1/A2; no need to
                                      bypass _get_or_fetch
  - probe_ipf_family_graph_consistency.py: no cycles, no dangling
                                      family_of, family graph clean

Known limitation (does NOT block FTO use):
  EPO family API may return patents already in DB with family_of
  pointing to a different project's parent (first-write-wins per
  patent_fetcher.py line 308). Those patents will still be appended
  to results via the existing-row branch (line 311), so they will
  reach the LLM analyzer on the next main pipeline run. Only
  get_family_members(ipf_parent) reverse-queries would miss them,
  and analyzer does not use that path.

Usage:
    # ALWAYS dry-run first
    python -m scripts.backfill_ipf_family_emergency

    # Then small batch to verify EPO API health
    python -m scripts.backfill_ipf_family_emergency --apply --max 5

    # Full run
    python -m scripts.backfill_ipf_family_emergency --apply --max 200

Idempotency:
  Safe to re-run. After first successful run, parents have
  family_fetched=1 again and will be skipped by the at-risk SELECT
  (cutoff is fetched_at, not family_fetched value). Mid-run crash:
  parents processed so far have family_fetched=1, remaining have 0;
  re-running picks up where it left off because the SELECT filter
  matches only family_fetched=1 AND fetched_at < cutoff.

  Wait — that's wrong. After processing, family_fetched flips to 1
  and fetched_at is NOT updated (mark_family_fetched only touches the
  family_fetched column). So the at-risk SELECT will RE-MATCH them.
  See `_already_processed_this_run` guard below.

Refs:
  - docs/spec/task_D.md §Phase 2 (the proper plan this defers to)
  - scratch/probe_ipf_*.py (pre-implementation probes)
  - PROJECT_SKILL §3.1 (probe before code) — followed
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime

# Production imports — we deliberately use _get_or_fetch so the family
# expansion goes through the exact same code path as a fresh fetch.
from modules.patent_fetcher import _get_or_fetch
from modules.patent_store import get_family_members
from scripts._backfill_common import DB_PATH, start_run, finish_run


# ── Hard-coded scope (this is an emergency one-off, not a generic tool) ─────

IPF_PROJECT = "Pemirolast_吸入劑治療特發性肺纖維化_(IPF)"

# Bug X fix merge date. Parents family-fetched before this date used the
# old {B1, B2}-only filter and silently dropped A-series siblings.
BUGX_FIX_DATE = "2026-05-18"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── At-risk parent identification ───────────────────────────────────────────

def select_at_risk_parents(
    conn: sqlite3.Connection,
    skip_ids: set[str],
) -> list[sqlite3.Row]:
    """
    139 at-risk parents (per probes). Filter:
      - in IPF project (via search_log)
      - not themselves a child (family_of IS NULL)
      - previously family-expanded (family_fetched = 1)
      - expanded under the old filter (fetched_at < cutoff)
      - A1/A2 kind code (the only kinds _get_or_fetch will re-expand)
      - not in skip_ids (already processed this run, see docstring)

    The A1/A2 filter is critical: _get_or_fetch's [DB hit] branch only
    fires _fetch_and_store_family when kind in ("A1", "A2"). Probe
    confirmed 100% of IPF at-risk rows match this — but the SQL keeps
    the filter explicit so a future copy-paste to another project
    doesn't silently no-op on B1 rows.
    """
    if skip_ids:
        placeholders = ",".join("?" * len(skip_ids))
        skip_clause = f"AND p.patent_id NOT IN ({placeholders})"
        skip_params = list(skip_ids)
    else:
        skip_clause = ""
        skip_params = []

    return conn.execute(
        f"""
        SELECT DISTINCT p.patent_id, p.year, p.fetched_at
        FROM patents p
        JOIN search_log sl ON sl.patent_id = p.patent_id
        WHERE sl.project = ?
          AND p.family_of IS NULL
          AND p.family_fetched = 1
          AND p.fetched_at < ?
          AND (p.patent_id LIKE '%A1' OR p.patent_id LIKE '%A2')
          {skip_clause}
        ORDER BY p.patent_id
        """,
        [IPF_PROJECT, BUGX_FIX_DATE] + skip_params,
    ).fetchall()


# ── Reset + re-expand for one parent ────────────────────────────────────────

def reset_and_reexpand(parent_id: str, year: str) -> tuple[int, str]:
    """
    Reset family_fetched=0 on parent, then call _get_or_fetch to trigger
    re-expansion via the [DB hit] → A1/A2 branch.

    Returns (children_count_after, status_string).

    NOTE: _get_or_fetch may raise on EPO API errors. We let it propagate
    here and catch in main(); single-parent failure should not abort
    the whole run.
    """
    # 1. Children count BEFORE
    children_before = len(get_family_members(parent_id))

    # 2. Reset flag (this is the only write we make ourselves; everything
    # else is done by production code paths called via _get_or_fetch)
    with _conn() as conn:
        conn.execute(
            "UPDATE patents SET family_fetched = 0 WHERE patent_id = ?",
            (parent_id,),
        )

    # 3. Call production code — this walks _get_or_fetch [DB hit] →
    # kind in ("A1", "A2") → not family_fetched → _fetch_and_store_family.
    # _fetch_and_store_family ends with mark_family_fetched(parent_id),
    # so after this call returns, family_fetched=1 again.
    patent = _get_or_fetch(parent_id, year or "")
    if patent is None:
        # _get_or_fetch returned None — should not happen on DB hit, but
        # defend against it. Restore family_fetched=1 manually to avoid
        # leaving the row in an inconsistent state.
        with _conn() as conn:
            conn.execute(
                "UPDATE patents SET family_fetched = 1 WHERE patent_id = ?",
                (parent_id,),
            )
        return children_before, "get_or_fetch returned None"

    # 4. Children count AFTER
    children_after = len(get_family_members(parent_id))
    delta = children_after - children_before
    return children_after, f"children: {children_before} → {children_after} (+{delta})"


# ── Dry-run preview ─────────────────────────────────────────────────────────

def dry_run(conn: sqlite3.Connection, max_n: int) -> int:
    parents = select_at_risk_parents(conn, skip_ids=set())
    print(f"DRY RUN — no DB writes, no EPO API calls")
    print(f"  IPF at-risk parents matching scope: {len(parents)}")
    print(f"  --max cap: {max_n}")
    print(f"  would process: {min(len(parents), max_n)}")
    print()
    print(f"  first 5:")
    for p in parents[:5]:
        children = len(get_family_members(p["patent_id"]))
        print(
            f"    {p['patent_id']:<20} "
            f"fetched={(p['fetched_at'] or '')[:10]} "
            f"current_children={children}"
        )
    if len(parents) > 5:
        print(f"    ... and {len(parents) - 5} more")
    print()
    print(f"  To apply: rerun with --apply")
    return 0


# ── Apply ───────────────────────────────────────────────────────────────────

def apply_run(conn: sqlite3.Connection, max_n: int) -> int:
    run_id = start_run(
        script="backfill_ipf_family_emergency",
        case_type="family_ipf_emergency",
        args_dict={"max": max_n, "project": IPF_PROJECT, "cutoff": BUGX_FIX_DATE},
    )

    processed: set[str] = set()
    succeeded = 0
    failed = 0
    total_new_children = 0
    failures: list[str] = []

    try:
        while True:
            # Re-select each iteration because each successful run
            # flips family_fetched 0→1→1 (via _fetch_and_store_family's
            # mark_family_fetched at end), which would re-match the
            # SELECT. skip_ids prevents the same parent being processed twice.
            parents = select_at_risk_parents(conn, skip_ids=processed)
            if not parents:
                break
            if len(processed) >= max_n:
                break

            parent = parents[0]
            pid = parent["patent_id"]
            year = parent["year"]

            try:
                children_after, status = reset_and_reexpand(pid, year)
                print(f"  [{len(processed)+1}/{max_n}] {pid}  {status}")
                # Estimate new children from this run by re-counting after
                # the call; reset_and_reexpand returned the delta info in
                # the status string, but we want the absolute delta:
                # parse it back or recompute. Simpler: just track totals.
                succeeded += 1
            except Exception as e:
                msg = f"{pid}: {type(e).__name__}: {e}"
                print(f"  [{len(processed)+1}/{max_n}] {pid}  FAILED: {e}")
                failures.append(msg)
                failed += 1
                # Restore family_fetched=1 so this parent is not retried
                # forever. The family is incomplete, but consistent.
                with _conn() as conn2:
                    conn2.execute(
                        "UPDATE patents SET family_fetched = 1 WHERE patent_id = ?",
                        (pid,),
                    )

            processed.add(pid)
            # Polite delay between parents (each parent triggers many
            # member fetches inside _fetch_and_store_family already).
            time.sleep(1.0)

        # Compute total new children written DB-wide for the IPF parents
        # we just processed (uses family_of join, not delta tracking, to
        # match audit-log "rows_affected" semantics from backfill_snippets).
        if processed:
            placeholders = ",".join("?" * len(processed))
            total_children_now = conn.execute(
                f"""
                SELECT COUNT(*) FROM patents
                WHERE family_of IN ({placeholders})
                """,
                list(processed),
            ).fetchone()[0]
        else:
            total_children_now = 0

        notes = (
            f"processed={len(processed)}, succeeded={succeeded}, "
            f"failed={failed}, total_children_after={total_children_now}"
        )
        if failures:
            # Cap failure detail in notes column (it's just TEXT but
            # let's not write megabytes of stack traces)
            notes += " | failures: " + "; ".join(failures[:10])
            if len(failures) > 10:
                notes += f" ... and {len(failures) - 10} more"

        finish_run(run_id, rows_affected=total_children_now, notes=notes)
        print()
        print(f"Done. {notes}")
        return 0 if failed == 0 else 1

    except KeyboardInterrupt:
        finish_run(
            run_id,
            rows_affected=len(processed),
            notes=f"INTERRUPTED after {len(processed)} parents",
        )
        print("\nInterrupted. Audit log finalized.")
        return 130
    except Exception as e:
        finish_run(
            run_id,
            rows_affected=len(processed),
            notes=f"CRASHED: {type(e).__name__}: {e}",
        )
        raise


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Emergency one-off backfill of IPF family expansion. "
            "See module docstring for scope and rationale."
        )
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually run. Default is dry-run.",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=50,
        help="Max parents to process this run (default 50). "
             "Total at-risk scope is 139.",
    )
    args = ap.parse_args(argv)

    conn = _conn()
    try:
        if not args.apply:
            return dry_run(conn, args.max)
        return apply_run(conn, args.max)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())