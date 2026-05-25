"""
Backfill formulation_snippets for rows where the field is NULL.

Re-runs _collect_snippets() on existing claims + examples_extracted
already in DB. Does NOT call EPO API.

The choice to call _collect_snippets() (rather than concatenating claims
and examples per spec text) is deliberate: it matches the production
write path in modules/patent_fetcher.py exactly (same wrapper, same
hard-cap-30 JSON envelope). Backfill thus produces row shape identical
to a fresh fetch.

Known limitation (carries over from production write path):
  US/CN/EA/KR/JP rows have empty claims + examples_extracted (EPO
  licensing — see PROJECT_SKILL §4.1). They will get '[]'. This still
  distinguishes "processed, no formulation evidence" from NULL ("never
  processed"). Enabling abstract-as-source is a separate enhancement,
  not Task D scope.

Usage:
    python -m scripts.backfill_snippets --dry-run
    python -m scripts.backfill_snippets --dry-run --project Acetaminophen
    python -m scripts.backfill_snippets                    # apply all NULL rows
    python -m scripts.backfill_snippets --project Acetaminophen --apply
    python -m scripts.backfill_snippets --aliases tylenol paracetamol --apply

Refs: docs/spec/task_D.md §`scripts/backfill_snippets.py`
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from typing import Optional

from config import DRUG_ALIASES
import config as _config  # for audit-log introspection of runtime config
from modules.patent_fetcher import _extract_formulation_snippets
from scripts._backfill_common import DB_PATH, start_run, finish_run


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _collect_snippets_for_backfill(
    claims: str,
    examples_extracted: str,
    aliases: list[str],
) -> str:
    """
    Local mirror of modules.patent_fetcher._collect_snippets, with two
    differences required by the backfill context:

    1. Takes `aliases` as a parameter rather than reading DRUG_ALIASES
       at call-time. The production path always uses module-level
       DRUG_ALIASES, but backfill may run with --aliases override
       (e.g. cross-project re-extraction).

    2. Reads `examples_extracted` (the Examples section already cached
       in DB) rather than full `description` (not cached). This is a
       documented semantic narrowing: backfill misses non-Examples
       paragraphs (Background, Detailed Description outside the Examples
       block) that production extraction would have caught at fetch
       time. For rows fetched before Task A this is unrecoverable
       without re-calling EPO description endpoint, which is explicitly
       out of scope for Case 3.

    Cap-30 envelope and JSON serialization match production exactly.
    """
    snippets: list[str] = []
    if claims:
        snippets += _extract_formulation_snippets(claims, aliases)
    if examples_extracted:
        snippets += _extract_formulation_snippets(examples_extracted, aliases)
    snippets = snippets[:30]
    return json.dumps(snippets)


def _select_candidate_rows(
    conn: sqlite3.Connection,
    project: Optional[str],
) -> list[sqlite3.Row]:
    """
    Return all rows with formulation_snippets IS NULL, optionally
    filtered by project (via search_log JOIN, deduped).
    """
    if project:
        sql = """
            SELECT DISTINCT p.patent_id, p.claims, p.examples_extracted
            FROM patents p
            JOIN search_log sl ON sl.patent_id = p.patent_id
            WHERE p.formulation_snippets IS NULL
              AND sl.project = ?
        """
        return conn.execute(sql, (project,)).fetchall()
    sql = """
        SELECT patent_id, claims, examples_extracted
        FROM patents
        WHERE formulation_snippets IS NULL
    """
    return conn.execute(sql).fetchall()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Backfill formulation_snippets for NULL rows.",
    )
    p.add_argument(
        "--project",
        default=None,
        help="Filter by project name from search_log (case-sensitive).",
    )
    p.add_argument(
        "--aliases",
        nargs="+",
        default=None,
        help="Override drug aliases. Default: config.DRUG_ALIASES.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count candidate rows; do not write DB or audit log.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Required to actually write. Without --apply or --dry-run, "
             "the script defaults to --dry-run for safety.",
    )
    p.add_argument(
        "--force-all-projects",
        action="store_true",
        help="Bypass the multi-project safety rail. Use only when you "
             "really want to apply the same aliases to every project in "
             "the DB. See safety rail comment in main().",
    )
    args = p.parse_args(argv)

    alias_source = "cli_override" if args.aliases else "config_default"
    aliases = args.aliases or DRUG_ALIASES
    if not aliases:
        print("[backfill_snippets] No aliases (config.DRUG_ALIASES empty "
              "and no --aliases). Refusing to run.", file=sys.stderr)
        return 2

    # Default to dry-run unless --apply is set
    is_dry_run = args.dry_run or not args.apply
    if not args.dry_run and not args.apply:
        print("[backfill_snippets] No --apply given; defaulting to --dry-run.")

    conn = _conn()

    # ── Safety rail (b) ────────────────────────────────────────────────────
    # config.DRUG_ALIASES holds aliases for ONE drug at a time. If the DB
    # contains multiple projects, using config default risks writing
    # `[]` permanently for rows whose drug isn't in the current config.
    # Force explicit --aliases when scope spans multiple projects, unless
    # --force-all-projects is passed.
    #
    # Single-project DB → spec default applies (config.DRUG_ALIASES OK).
    # Multi-project DB + --project filter + no --aliases → still refuse
    #   (high chance the config doesn't match the project being filtered).
    # Multi-project DB + --aliases → fine (user took responsibility).
    # Multi-project DB + --force-all-projects → fine (user explicit).
    distinct_projects = conn.execute(
        "SELECT COUNT(DISTINCT project) FROM search_log"
    ).fetchone()[0]
    if (
        distinct_projects > 1
        and alias_source == "config_default"
        and not args.force_all_projects
    ):
        print(
            f"[backfill_snippets] DB contains {distinct_projects} distinct "
            "projects in search_log. Using config.DRUG_ALIASES as default "
            "is likely wrong for at least one project (would write '[]' "
            "for rows whose drug isn't in the current config).",
            file=sys.stderr,
        )
        print(
            "[backfill_snippets] Either: (1) pass --aliases explicitly for "
            "this run, or (2) pass --force-all-projects if you really want "
            "config default applied to every project.",
            file=sys.stderr,
        )
        conn.close()
        return 2

    rows = _select_candidate_rows(conn, args.project)
    n_total = len(rows)
    print(f"[backfill_snippets] candidates: {n_total} row(s)"
          f"{f' (project={args.project})' if args.project else ''}")
    print(f"[backfill_snippets] aliases: {aliases}")

    if is_dry_run:
        # Preview: how many rows would yield non-empty snippets
        n_with_text = sum(
            1 for r in rows
            if (r["claims"] or "") or (r["examples_extracted"] or "")
        )
        print(f"[backfill_snippets] dry-run: of {n_total}, "
              f"{n_with_text} have non-empty claims+examples "
              f"(others will write '[]')")
        print("[backfill_snippets] dry-run complete; no DB write, no audit log entry.")
        conn.close()
        return 0

    # Real run: open audit log.
    # Snapshot relevant config state at runtime so future readers can
    # reconstruct exactly what aliases were used, where they came from,
    # and which project config was loaded — even if config.py has
    # changed by then. The `alias_source` field distinguishes user
    # override from config default, which is forensically important
    # when investigating "did this project get the right aliases?".
    args_dict = {
        "project":               args.project,
        "aliases_used":          aliases,
        "alias_source":          alias_source,
        "config_drug_aliases":   list(getattr(_config, "DRUG_ALIASES", [])),
        "config_target_product": getattr(_config, "TARGET_PRODUCT", None),
        "force_all_projects":    args.force_all_projects,
        "candidate_count":       n_total,
    }
    run_id = start_run("backfill_snippets", "snippets", args_dict)

    n_written = 0
    n_with_snippets = 0
    notes = ""
    try:
        for i, row in enumerate(rows, 1):
            patent_id = row["patent_id"]
            claims = row["claims"] or ""
            examples = row["examples_extracted"] or ""
            snippets_json = _collect_snippets_for_backfill(
                claims, examples, aliases
            )
            # Write back. We do a narrow UPDATE rather than calling
            # upsert_patent() — upsert would rewrite every column from
            # a dict we'd have to reconstruct, risking accidental
            # overwrite (e.g. fetched_at). Direct UPDATE preserves
            # everything else.
            conn.execute(
                """
                UPDATE patents
                SET formulation_snippets = ?
                WHERE patent_id = ?
                """,
                (snippets_json, patent_id),
            )
            n_written += 1
            if snippets_json != "[]":
                n_with_snippets += 1
            if i % 100 == 0:
                conn.commit()
                print(f"  [progress] {i}/{n_total} written, "
                      f"{n_with_snippets} non-empty so far")
        conn.commit()
    except Exception as e:
        # Don't silent-except (§3.2). Note the failure but still close
        # the audit log row so it doesn't dangle as completed_at=NULL.
        notes = f"crashed at row {n_written + 1}/{n_total}: {e!r}"
        print(f"[backfill_snippets] FAILED: {notes}", file=sys.stderr)
        finish_run(run_id, n_written, notes)
        conn.close()
        return 1

    finish_run(
        run_id,
        n_written,
        f"non_empty_snippets={n_with_snippets}",
    )
    print(f"[backfill_snippets] done: {n_written}/{n_total} rows updated, "
          f"{n_with_snippets} have non-empty snippets, "
          f"{n_written - n_with_snippets} got '[]'")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())