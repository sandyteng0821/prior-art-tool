"""
export_db_to_jsonl — DB → JSONL 導出（讀法 X 核心，Task Q #3）

把某專案在 cache/patents.db 中的 EPO content（種子 + family）導出成 JSONL，
讓 analyze_jsonl.py 不經 GP/GPSS scraper 即可跑 baseline 分析。

用途：
- 在去 GP/GPSS 補 content 前，先用 DB 現有 EPO content 跑一次當 baseline
- 產出的 JSONL 直接餵 scripts/analyze_jsonl.py（欄位已對齊其反向 map）

只讀 DB（mode=ro），不寫、不改。與 inspect / migration 工具分開（不同 risk profile）。

Usage:
    # 導出蟹足腫專案（--project 傳完整 TARGET_PRODUCT 值）
    python3 tools/export_db_to_jsonl.py \
        --project "Empagliflozin 外用製劑治療蟹足腫 (Keloid)" \
        --out data/keloid_baseline.jsonl

    # 再用 analyze_jsonl 讀，確認筆數一致
    python3 scripts/analyze_jsonl.py \
        --input data/keloid_baseline.jsonl \
        --config configs/empagliflozin_keloid.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


# DB search_log.project 的截斷長度（與 patent_fetcher.py 寫入端一致）。
# 寫入端：TARGET_PRODUCT[:30].replace(" ", "_")。前綴匹配必須複製同一 transform，
# 否則帶空格的前綴匹配不到 DB 裡的底線值。截斷根因是另一個 ticket，本工具用前綴繞過。
PROJECT_TRUNCATE_LEN = 30

DEFAULT_DB = "cache/patents.db"


def _project_prefix(project_full: str) -> str:
    """把完整 project 值轉成 DB 裡實際存的前綴 shape（截斷 + 空格換底線）。"""
    return project_full[:PROJECT_TRUNCATE_LEN].replace(" ", "_")


def collect_ids(conn: sqlite3.Connection, project_full: str) -> tuple[set[str], set[str]]:
    """兩段撈法：search_log 種子 + patents.family_of 指向種子的成員。

    回傳 (seeds, family)。呼叫端用 seeds | family 去重得導出集合。

    防呆：前綴若匹配到 >1 個 distinct project，raise（不默默選一個）。
    """
    prefix = _project_prefix(project_full)

    projs = conn.execute(
        "SELECT DISTINCT project FROM search_log WHERE project LIKE ? || '%'",
        (prefix,),
    ).fetchall()
    if len(projs) != 1:
        raise ValueError(
            f"前綴 {prefix!r} 匹配到 {len(projs)} 個 project，需更精確："
            f"{[p[0] for p in projs]}"
        )
    project_db_value = projs[0][0]

    seeds = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT patent_id FROM search_log WHERE project = ?",
            (project_db_value,),
        )
    }
    if not seeds:
        return seeds, set()

    placeholders = ",".join("?" * len(seeds))
    family = {
        r[0] for r in conn.execute(
            f"SELECT patent_id FROM patents WHERE family_of IN ({placeholders})",
            tuple(seeds),
        )
    }
    return seeds, family


def row_to_jsonl_record(row: sqlite3.Row) -> dict:
    """DB row → JSONL record，欄位改名讓 analyze_jsonl.jsonl_to_patent_dicts 原樣吃。

    對應關係（analyze_jsonl 反向 map 已核對）：
        patent_id          → requested_id
        examples_extracted → full_text
        year               → publication_date（analyze_jsonl 再用 _extract_year 抽）
        expiry_date        → expiration_date
    """
    return {
        "requested_id":     row["patent_id"],
        "title":            row["title"] or "",
        "abstract":         row["abstract"] or "",
        "claims":           row["claims"] or "",
        "full_text":        row["examples_extracted"] or "",
        "publication_date": row["year"] or "",
        "expiration_date":  row["expiry_date"] or "",
    }


def export(project_full: str, db_path: str, out_path: str) -> None:
    if not Path(db_path).exists():
        print(f"[ERROR] DB not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        seeds, family = collect_ids(conn, project_full)
        if not seeds:
            print(f"[ERROR] search_log 沒有 project 前綴 "
                  f"{_project_prefix(project_full)!r} 的種子，無可導出。")
            sys.exit(1)

        export_ids = seeds | family
        overlap = seeds & family  # 既是種子又是別人 family member 的筆數

        # 逐筆撈 content 並寫出。以 export_ids 為準，逐 id 查 patents。
        placeholders = ",".join("?" * len(export_ids))
        rows = conn.execute(
            f"SELECT * FROM patents WHERE patent_id IN ({placeholders})",
            tuple(export_ids),
        ).fetchall()

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with_claims = 0
        with open(out_path, "w", encoding="utf-8") as f:
            for row in rows:
                if (row["claims"] or "").strip():
                    with_claims += 1
                rec = row_to_jsonl_record(row)
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 在 patents 表找不到的 id（種子在 search_log 但 patents 無此列）。
        found_ids = {row["patent_id"] for row in rows}
        missing = export_ids - found_ids
    finally:
        conn.close()

    # 導出摘要：印自洽的數字（種子 + family − 重疊 = union）。
    print()
    print("── Export summary ──────────────────────────────────────────")
    print(f"  種子 (search_log distinct)      : {len(seeds)}")
    print(f"  family (family_of IN 種子)      : {len(family)}")
    print(f"    其中本身也是種子 (重疊)       : {len(overlap)}")
    print(f"  導出總數 (union, 去重)          : {len(export_ids)}")
    print(f"  patents 表實際寫出              : {len(rows)}")
    if missing:
        print(f"  [WARN] {len(missing)} 筆 id 在 patents 表找不到（僅存 search_log）："
              f" {sorted(missing)[:5]}{' ...' if len(missing) > 5 else ''}")
    print(f"  content 品質：{with_claims}/{len(rows)} 筆有 claims")
    print(f"  → {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a project's EPO content (seeds + family) from DB to JSONL.",
        prog="python3 tools/export_db_to_jsonl.py",
    )
    parser.add_argument(
        "--project", required=True,
        help="完整 TARGET_PRODUCT 值（工具內部自動截斷 + 換底線比對 DB）",
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB,
        help=f"DB 路徑（default: {DEFAULT_DB}）",
    )
    parser.add_argument(
        "--out", required=True,
        help="輸出 JSONL 路徑",
    )
    args = parser.parse_args()

    export(args.project, args.db, args.out)


if __name__ == "__main__":
    main()
