"""
Probe v2: Pemirolast coverage analysis, scoped to the risk-analysis CSV.

WHY v2 (vs probe_coverage_pemirolast.py / "v1"):
  v1 scoped by search_log.project = 369 patents. But risk-analysis CSV
  outputs 685 distinct patents — 416 of those are family-expansion rows
  not in search_log, and the 100 in search_log but not CSV turned out to
  be non-target-drug search noise (P2X3 / Rho-kinase / AT2R inhibitors
  that were correctly filtered by the LLM analyzer).
  
  To explain risk-score deltas between CSV runs, coverage report must
  align to the CSV's row set, not the search_log row set. This script
  reads patent_ids from the CSV directly.

Answers:
  Q1. Per-channel raw state distribution (abstract/claims/examples)
      over CSV's distinct patent_ids.
  Q2. Row-level lineage cross-tab (source × channel binary has-content).
  Q3. Still-empty rows by (jurisdiction, source).
  Q4. CSV health check: rows vs distinct, IDs not in DB, dup rows.

Read-only. Does not modify the DB. Place under scratch/ (gitignored).

Usage:
    python -m scratch.probe_coverage_v2 \\
        --csv output/gap_analysis_20260603_2232.csv

    python -m scratch.probe_coverage_v2 \\
        --csv <path> --query 1
    
Refs:
  - probe_coverage_pemirolast.py (v1, search_log-scoped, kept for reference)
  - probe_csv_vs_search_log.py (proved CSV/search_log diff is family + noise)
  - probe_searchlog_minus_csv.py (showed the 100 search-log-only IDs are noise)
  - task_I_google_patents_jsonl_import.md
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "cache/patents.db"
DEFAULT_CSV_ID_COL = "patent_id"

# "empty" definition: NULL ∪ '' ∪ 'N/A'.
# - NULL = column never written
# - ''   = written but no content
# - 'N/A' = Task I scrape sentinel (scraped but parse failed)
# Q1 surfaces all three raw states; Q2/Q3 collapse to has/empty.
EMPTY_SENTINELS = ('', 'N/A')


def connect_readonly(db_path: str) -> sqlite3.Connection:
    p = Path(db_path)
    if not p.exists():
        sys.exit(f"ERROR: DB not found at {db_path}")
    conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_csv(csv_path: str, col: str) -> tuple[list[str], set[str]]:
    """Return (raw rows, distinct set). Two values so we can detect dups."""
    p = Path(csv_path)
    if not p.exists():
        sys.exit(f"ERROR: CSV not found at {csv_path}")
    raw: list[str] = []
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if col not in (reader.fieldnames or []):
            sys.exit(f"ERROR: column '{col}' not in CSV. "
                     f"Found: {reader.fieldnames}")
        for row in reader:
            pid = (row.get(col) or "").strip()
            if pid:
                raw.append(pid)
    return raw, set(raw)


def print_table(headers: list[str], rows: list[tuple]) -> None:
    if not rows:
        print("  (empty)")
        return
    cols = [headers] + [[str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in cols) for i in range(len(headers))]

    def fmt(row):
        return "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers)))

    print(fmt(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt([str(c) for c in r]))


def _chunked_in_clause(conn, sql_template: str, ids: list[str], extra_params=()):
    """SQLite IN-clause has a default ~999-param limit. Chunk to avoid it."""
    out = []
    chunk = 500
    for i in range(0, len(ids), chunk):
        batch = ids[i:i + chunk]
        placeholders = ",".join("?" * len(batch))
        sql = sql_template.format(placeholders=placeholders)
        out.extend(conn.execute(sql, (*batch, *extra_params)).fetchall())
    return out


def q1_channel_state(conn: sqlite3.Connection, csv_ids: set[str]) -> None:
    """Per-channel: NULL / '' / 'N/A' / HAS counts over the CSV id set."""
    print("\n=== Q1: Per-channel raw state distribution ===")
    print("Channels: abstract, claims, examples_extracted")
    print("States: NULL, '' (empty), 'N/A' (sentinel), HAS (real content)\n")

    channels = ["abstract", "claims", "examples_extracted"]
    id_list = list(csv_ids)
    rows = []
    for ch in channels:
        # nosec: column name interpolated from closed enum, not user input
        sql_template = f"""
            SELECT
                SUM(CASE WHEN {ch} IS NULL THEN 1 ELSE 0 END) AS null_,
                SUM(CASE WHEN {ch} = ''    THEN 1 ELSE 0 END) AS empty_,
                SUM(CASE WHEN {ch} = 'N/A' THEN 1 ELSE 0 END) AS na_,
                SUM(CASE WHEN COALESCE({ch},'') NOT IN ('','N/A') THEN 1 ELSE 0 END) AS has_,
                COUNT(*) AS total_
            FROM patents
            WHERE patent_id IN ({{placeholders}})
        """
        # Aggregate across chunks
        agg = {"null_": 0, "empty_": 0, "na_": 0, "has_": 0, "total_": 0}
        for r in _chunked_in_clause(conn, sql_template, id_list):
            for k in agg:
                agg[k] += r[k] or 0
        rows.append((ch, agg["null_"], agg["empty_"], agg["na_"],
                     agg["has_"], agg["total_"]))

    print_table(
        ["channel", "NULL", "''", "'N/A'", "HAS", "total"],
        rows,
    )
    print()
    for ch, null_, empty_, na_, has_, total_ in rows:
        if total_:
            pct = 100.0 * has_ / total_
            print(f"  {ch}: {has_}/{total_} has content ({pct:.1f}%)")


def q2_lineage_xtab(conn: sqlite3.Connection, csv_ids: set[str]) -> None:
    """source × channel binary has-content counts."""
    print("\n=== Q2: Row-level lineage × channel coverage ===")
    print("source = the row's overall lineage tag.\n")
    print("CAVEAT: source is row-level lineage, not channel provenance.")
    print("        source='google_patents' rows still inherit abstract from EPO stage.\n")

    id_list = list(csv_ids)
    sql_template = """
        SELECT
            COALESCE(source, '<NULL>') AS source,
            COUNT(*) AS rows_,
            SUM(CASE WHEN COALESCE(abstract,'')           NOT IN ('','N/A') THEN 1 ELSE 0 END) AS abstract_has,
            SUM(CASE WHEN COALESCE(claims,'')             NOT IN ('','N/A') THEN 1 ELSE 0 END) AS claims_has,
            SUM(CASE WHEN COALESCE(examples_extracted,'') NOT IN ('','N/A') THEN 1 ELSE 0 END) AS examples_has
        FROM patents
        WHERE patent_id IN ({placeholders})
        GROUP BY source
    """
    # Aggregate by source across chunks
    by_source: dict[str, dict[str, int]] = {}
    for r in _chunked_in_clause(conn, sql_template, id_list):
        s = r["source"]
        agg = by_source.setdefault(s, {"rows_": 0, "abstract_has": 0,
                                       "claims_has": 0, "examples_has": 0})
        for k in agg:
            agg[k] += r[k] or 0

    rows = sorted(
        [(s, v["rows_"], v["abstract_has"], v["claims_has"], v["examples_has"])
         for s, v in by_source.items()],
        key=lambda x: -x[1],
    )
    if not rows:
        print("  (no rows)")
        return
    print_table(
        ["source", "rows", "abstract_has", "claims_has", "examples_has"],
        rows,
    )

    mixed = [r for r in rows if r[0] == "mixed_epo_google_patents"]
    if mixed and mixed[0][1] > 0:
        print(f"\n  NOTE: mixed_epo_google_patents = {mixed[0][1]} rows.")
        print("  Task I spec L150 said this case did not trigger in the real run.")
        print("  If non-zero here, the hybrid code path fired post-spec — worth investigating.")


def q3_still_empty(conn: sqlite3.Connection, csv_ids: set[str]) -> None:
    """Jurisdiction × source breakdown of empty / all-three-empty rows."""
    print("\n=== Q3: Still-empty rows by (jurisdiction, source) ===")
    print("'all_three_empty' = abstract AND claims AND examples all missing.")
    print("These rows contribute nothing to risk-analysis re-runs.\n")

    id_list = list(csv_ids)
    sql_template = """
        SELECT
            substr(patent_id, 1, 2) AS jurisdiction,
            COALESCE(source, '<NULL>') AS source,
            COUNT(*) AS rows_,
            SUM(CASE WHEN COALESCE(abstract,'')           IN ('','N/A') THEN 1 ELSE 0 END) AS abstract_empty,
            SUM(CASE WHEN COALESCE(claims,'')             IN ('','N/A') THEN 1 ELSE 0 END) AS claims_empty,
            SUM(CASE WHEN COALESCE(examples_extracted,'') IN ('','N/A') THEN 1 ELSE 0 END) AS examples_empty,
            SUM(CASE
                WHEN COALESCE(abstract,'')           IN ('','N/A')
                 AND COALESCE(claims,'')             IN ('','N/A')
                 AND COALESCE(examples_extracted,'') IN ('','N/A')
                THEN 1 ELSE 0 END) AS all_three_empty
        FROM patents
        WHERE patent_id IN ({placeholders})
        GROUP BY jurisdiction, source
    """
    by_jurs: dict[tuple, dict[str, int]] = {}
    for r in _chunked_in_clause(conn, sql_template, id_list):
        k = (r["jurisdiction"], r["source"])
        agg = by_jurs.setdefault(k, {"rows_": 0, "abstract_empty": 0,
                                     "claims_empty": 0, "examples_empty": 0,
                                     "all_three_empty": 0})
        for f in agg:
            agg[f] += r[f] or 0

    rows = sorted(
        [(j, s, v["rows_"], v["abstract_empty"], v["claims_empty"],
          v["examples_empty"], v["all_three_empty"])
         for (j, s), v in by_jurs.items()],
        key=lambda x: (-x[6], -x[2]),
    )
    print_table(
        ["juris", "source", "rows", "abs_∅", "claims_∅", "ex_∅", "all3_∅"],
        rows,
    )
    total_all3 = sum(r[6] for r in rows)
    print(f"\n  Total all_three_empty: {total_all3} rows across {len(rows)} groups.")
    print("  These rows should yield stable scores on re-run (sanity anchor).")


def q4_csv_health(
    conn: sqlite3.Connection,
    raw_rows: list[str],
    csv_ids: set[str],
) -> None:
    """CSV alignment / dup audit. Not a coverage question per se but cheap.

    Three checks:
      a. row count vs distinct (dups in CSV)
      b. CSV IDs not in patents table (alignment broken?)
      c. (a)'s dup IDs listed if present
    """
    print("\n=== Q4: CSV health check ===")
    print(f"  Raw CSV rows (after dropping blank patent_id): {len(raw_rows)}")
    print(f"  Distinct patent_ids:                            {len(csv_ids)}")

    if len(raw_rows) > len(csv_ids):
        # Find dups
        seen: dict[str, int] = {}
        for pid in raw_rows:
            seen[pid] = seen.get(pid, 0) + 1
        dups = [(pid, n) for pid, n in seen.items() if n > 1]
        dups.sort(key=lambda x: -x[1])
        print(f"\n  Duplicated patent_ids ({len(dups)} IDs accounting for "
              f"{sum(n - 1 for _, n in dups)} excess rows):")
        print_table(["patent_id", "row_count"], dups[:20])
        if len(dups) > 20:
            print(f"  (... {len(dups) - 20} more not shown)")
        print("\n  → Out of scope for this coverage report. Track separately"
              " (likely a Phase 4/5 emission bug).")

    # CSV IDs not in DB
    id_list = list(csv_ids)
    sql_template = "SELECT patent_id FROM patents WHERE patent_id IN ({placeholders})"
    in_db = set()
    for r in _chunked_in_clause(conn, sql_template, id_list):
        in_db.add(r["patent_id"])
    not_in_db = csv_ids - in_db
    print(f"\n  CSV IDs in patents table:     {len(in_db)}")
    print(f"  CSV IDs NOT in patents table: {len(not_in_db)}")
    if not_in_db:
        sample = sorted(not_in_db)[:10]
        print(f"    sample: {sample}")
        print("    → coverage Q1-Q3 silently skip these (no DB row to read).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", required=True, help="Path to risk-analysis CSV")
    parser.add_argument("--csv-id-col", default=DEFAULT_CSV_ID_COL)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--query", choices=["1", "2", "3", "4", "all"],
                        default="all")
    args = parser.parse_args()

    conn = connect_readonly(args.db)
    raw_rows, csv_ids = load_csv(args.csv, args.csv_id_col)
    print(f"DB:  {args.db}")
    print(f"CSV: {args.csv}")
    print(f"CSV raw rows:        {len(raw_rows)}")
    print(f"CSV distinct IDs:    {len(csv_ids)}")

    if not csv_ids:
        print("\nWARNING: 0 distinct IDs in CSV. Check --csv-id-col.")
        sys.exit(1)

    if args.query in ("1", "all"):
        q1_channel_state(conn, csv_ids)
    if args.query in ("2", "all"):
        q2_lineage_xtab(conn, csv_ids)
    if args.query in ("3", "all"):
        q3_still_empty(conn, csv_ids)
    if args.query in ("4", "all"):
        q4_csv_health(conn, raw_rows, csv_ids)


if __name__ == "__main__":
    main()