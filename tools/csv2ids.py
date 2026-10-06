#!/usr/bin/env python3
"""
csv2ids — 從 gap_analysis CSV 抽出專利號清單(一行一個),餵 gpss-probe/fetch_by_pn.py 的 --ids

放置位置:tools/csv2ids.py

為什麼存在
──────────
Pass 1 Report 的 CSV → Pass 2 fetch_by_pn 的 --ids 之間,要把專利號欄抽成純清單。
本工具固定三件每次都要處理的事,取代重貼的 one-liner:
  1. utf-8-sig 讀取 —— gap_analysis CSV 常帶 BOM,否則第一欄名(patent_id)會抓不到。
  2. id 欄 —— 預設自動偵測(挑值最像專利號的欄);不確定時用 --id-col 指定,不要賭偵測。
  3. 去重 + 排序 + 一行一個。

用法
────
    python3 tools/csv2ids.py output/gap_analysis_20261001_1142.csv data/hydroflumethiazide_hs_ids.txt
    python3 tools/csv2ids.py in.csv out.txt --id-col patent_id        # 跳過偵測、指定欄
    python3 tools/csv2ids.py in.csv out.txt --risk High Medium        # 只抽指定 risk(選用)
    python3 tools/csv2ids.py in.csv out.txt --dry-run                 # 只印偵測結果與筆數,不寫檔
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# 像專利號的 token:2 碼國別 +(選)1 個 series 字母 + 4 碼以上數字。
# 刻意寬鬆(不驗 kind code),只為了「從一堆欄裡挑出放專利號的那欄」。
LOOKS_LIKE_ID = re.compile(r"^[A-Z]{2}[A-Z]?\d{4,}")


def detect_id_col(rows: list[dict], cols: list[str]) -> tuple[str, int]:
    """挑『值最像專利號』的欄。回 (欄名, 命中列數)。"""
    best, best_hits = cols[0], -1
    for c in cols:
        hits = sum(bool(LOOKS_LIKE_ID.match((r.get(c) or "").strip().upper())) for r in rows)
        if hits > best_hits:
            best, best_hits = c, hits
    return best, best_hits


def detect_risk_col(cols: list[str]) -> str | None:
    for c in cols:
        if "risk" in c.lower():
            return c
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python3 tools/csv2ids.py",
        description="gap_analysis CSV → 專利號清單(給 fetch_by_pn --ids)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("csv_path", type=Path, help="輸入 CSV(gap_analysis_*.csv)")
    ap.add_argument("out_path", type=Path, help="輸出 .txt(一行一個專利號)")
    ap.add_argument("--id-col", default=None,
                    help="指定專利號欄名(預設自動偵測;不確定偵測對不對時用這個)")
    ap.add_argument("--risk", nargs="+", metavar="LEVEL",
                    help="只抽這些 risk 等級(如 --risk High Medium);預設全抽")
    ap.add_argument("--risk-col", default=None,
                    help="risk 欄名(配合 --risk;預設自動偵測含 'risk' 的欄)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只印偵測結果與筆數,不寫輸出檔")
    args = ap.parse_args()

    if not args.csv_path.exists():
        sys.exit(f"找不到 {args.csv_path}")

    with open(args.csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit(f"{args.csv_path} 沒有資料列")
    cols = list(rows[0].keys())

    # ── id 欄 ──
    if args.id_col:
        if args.id_col not in cols:
            sys.exit(f"--id-col {args.id_col!r} 不在欄位中:{cols}")
        id_col, hits = args.id_col, None
    else:
        id_col, hits = detect_id_col(rows, cols)
        if hits == 0:
            sys.exit(f"自動偵測不到專利號欄(沒有一欄的值像專利號)。\n"
                     f"  欄位:{cols}\n  → 用 --id-col 指定。")

    # ── risk 過濾(選用)──
    kept = rows
    risk_note = "全部"
    if args.risk:
        risk_col = args.risk_col or detect_risk_col(cols)
        if not risk_col:
            sys.exit("找不到 risk 欄,無法用 --risk 過濾。→ 用 --risk-col 指定。")
        want = {x.strip().lower() for x in args.risk}
        kept = [r for r in rows if (r.get(risk_col) or "").strip().lower() in want]
        risk_note = f"{'/'.join(args.risk)}(欄={risk_col!r})"

    ids = sorted({(r.get(id_col) or "").strip() for r in kept if (r.get(id_col) or "").strip()})
    if not ids:
        sys.exit("抽不到任何專利號(risk 過濾後為空?或 id 欄抓錯?)")

    det = "指定" if args.id_col else f"自動偵測,命中 {hits}/{len(rows)} 列"
    print(f"  id 欄     = {id_col!r}({det})")
    print(f"  risk 過濾 = {risk_note}")
    print(f"  專利號    = {len(ids)} 筆(去重)")

    if args.dry_run:
        print(f"  [dry-run] 未寫檔。前 5 筆:{ids[:5]}")
        return

    args.out_path.parent.mkdir(parents=True, exist_ok=True)
    args.out_path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"  → {args.out_path}")


if __name__ == "__main__":
    main()
