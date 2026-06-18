"""
check_db — 批次查詢 patent ID 在 DB 裡的狀態

給一組 patent ID，快速回報每筆在 DB 裡有沒有、資料完整度如何。
純 DB 讀取，零成本，跑多少次都不會動到任何東西。

Usage:
    # CLI 直貼
    python3 -m tools.check_db US9415051B1 EP4138798B1 AU2020203515A1

    # 從檔案讀（一行一個 ID，# 開頭跳過）
    python3 -m tools.check_db --file expert_ids.txt

    # 顯示更多欄位
    python3 -m tools.check_db US9415051B1 EP4138798B1 --detail

    # 一組 family member 批次查（你已經有 list）
    python3 -m tools.check_db EP4138798A1 EP4138798B1 US2023157975A1 WO2021214451A1

    # 從 DB 撈某筆的 family 成員
    python3 -m tools.check_db EP3522983A1 --family
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

DB_PATH = Path(_project_root) / "cache" / "patents.db"


# ═══════════════════════════════════════════════════════════════════════════════
# DB access (direct sqlite, not through patent_store — keeps it self-contained)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_conn():
    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _lookup(conn, patent_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM patents WHERE patent_id = ?", (patent_id,)
    ).fetchone()
    return dict(row) if row else None


def _lookup_family(conn, patent_id: str) -> list[str]:
    """Find DB rows whose family_of points to patent_id."""
    rows = conn.execute(
        "SELECT patent_id FROM patents WHERE family_of = ?", (patent_id,)
    ).fetchall()
    return [r["patent_id"] for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Display helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _yn(val) -> str:
    """Convert field to concise status indicator."""
    if val is None:
        return "—"
    s = str(val).strip()
    if s == "" or s == "[]":
        return "—"
    return f"{len(s)}c"


def _print_basic(patent_id: str, row: dict | None):
    if row is None:
        print(f"  {patent_id:<24} ✗ NOT IN DB")
        return
    claims = _yn(row.get("claims"))
    abstract = _yn(row.get("abstract"))
    snippets = _yn(row.get("formulation_snippets"))
    examples = _yn(row.get("examples_extracted"))
    source = row.get("source", "")
    print(
        f"  {patent_id:<24} ✓  "
        f"abs={abstract:<6} "
        f"claims={claims:<6} "
        f"ex={examples:<8} "
        f"snip={snippets:<6} "
        f"src={source}"
    )


def _print_detail(patent_id: str, row: dict | None):
    if row is None:
        print(f"  {patent_id}")
        print(f"    NOT IN DB")
        print()
        return
    print(f"  {patent_id}")
    fields = [
        ("title",                row.get("title", "")[:70]),
        ("abstract",             _yn(row.get("abstract"))),
        ("claims",               _yn(row.get("claims"))),
        ("examples_extracted",   _yn(row.get("examples_extracted"))),
        ("formulation_snippets", _yn(row.get("formulation_snippets"))),
        ("status",               row.get("status", "")),
        ("source",               row.get("source", "")),
        ("fetched_at",           row.get("fetched_at", "")),
        ("family_fetched",       row.get("family_fetched", 0)),
        ("family_of",            row.get("family_of") or "—"),
    ]
    for label, val in fields:
        print(f"    {label:<24} {val}")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Batch check patent IDs against local DB.",
        prog="python3 -m tools.check_db",
    )
    parser.add_argument(
        "ids", nargs="*",
        help="Patent IDs to check (space-separated)",
    )
    parser.add_argument(
        "--file", "-f", type=str,
        help="Read patent IDs from file (one per line, # = comment)",
    )
    parser.add_argument(
        "--detail", "-d", action="store_true",
        help="Show full metadata per patent",
    )
    parser.add_argument(
        "--family", action="store_true",
        help="Also look up family members in DB (via family_of field)",
    )
    args = parser.parse_args()

    # ── Collect IDs ───────────────────────────────────────────────────────────
    # Support semicolon/comma-separated paste: "A;B;C" → [A, B, C]
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
                # Support semicolon-separated lists (paste from EPO family)
                for part in line.replace(",", ";").split(";"):
                    part = part.strip()
                    if part:
                        ids.append(part)

    if not ids:
        parser.print_help()
        sys.exit(1)

    # ── Query DB ──────────────────────────────────────────────────────────────
    conn = _get_conn()

    found = 0
    missing = 0
    printer = _print_detail if args.detail else _print_basic

    print()
    print(f"  Checking {len(ids)} patent ID(s) against {DB_PATH}")
    print(f"  {'─' * 64}")

    for pid in ids:
        row = _lookup(conn, pid)
        printer(pid, row)
        if row:
            found += 1
        else:
            missing += 1

    # ── Family lookup ─────────────────────────────────────────────────────────
    if args.family:
        print(f"  {'─' * 64}")
        print(f"  Family members in DB (via family_of):")
        any_found = False
        for pid in ids:
            members = _lookup_family(conn, pid)
            if members:
                any_found = True
                print(f"    {pid} → {len(members)} member(s):")
                for mid in sorted(members):
                    mrow = _lookup(conn, mid)
                    _print_basic(mid, mrow)
        if not any_found:
            print(f"    (none found — family expansion may not have run for these IDs)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"  {'─' * 64}")
    print(f"  Total: {len(ids)}  |  In DB: {found}  |  Missing: {missing}")
    print()

    conn.close()


if __name__ == "__main__":
    main()