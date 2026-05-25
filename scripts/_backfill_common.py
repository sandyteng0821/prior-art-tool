"""
scripts/_backfill_common.py — shared infrastructure for backfill scripts.

Underscore-prefixed: internal helper, not a CLI entry point.
Used by scripts/backfill_snippets.py and scripts/backfill_family.py.

Provides:
- backfill_log table CREATE on first use
- start_run() → insert a log row, return run_id
- finish_run(run_id, rows_affected, notes='') → fill completed_at + rows_affected
- get_git_commit() → current HEAD short hash

See docs/spec/task_D.md §`backfill_log table`.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

DB_PATH = "cache/patents.db"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def ensure_backfill_log_table() -> None:
    """CREATE TABLE IF NOT EXISTS for backfill_log. Idempotent."""
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backfill_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at    TEXT NOT NULL,
                completed_at  TEXT,
                script        TEXT NOT NULL,
                case_type     TEXT,
                args          TEXT,
                rows_affected INTEGER,
                git_commit    TEXT,
                notes         TEXT
            )
            """
        )


def get_git_commit() -> str:
    """Return current HEAD short hash, or '<unknown>' on failure."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "<unknown>"


def start_run(script: str, case_type: str, args_dict: dict) -> int:
    """
    Insert a row into backfill_log at the start of a real run.
    Returns run_id (INTEGER PRIMARY KEY) to pass to finish_run().

    Do NOT call this for dry-runs — per task_D.md, dry-runs must not
    pollute audit trail.
    """
    ensure_backfill_log_table()
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO backfill_log
                (started_at, script, case_type, args, git_commit)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(),
                script,
                case_type,
                json.dumps(args_dict, sort_keys=True, default=str),
                get_git_commit(),
            ),
        )
        return cur.lastrowid


def finish_run(run_id: int, rows_affected: int, notes: str = "") -> None:
    """Update the log row with completed_at, rows_affected, optional notes."""
    with _conn() as conn:
        conn.execute(
            """
            UPDATE backfill_log
            SET completed_at  = ?,
                rows_affected = ?,
                notes         = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), rows_affected, notes, run_id),
        )