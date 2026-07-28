"""
inspect_jsonl — Google Patents scraper JSONL 的自檢工具

純讀 JSONL，報告 dirty/clean 比例、各欄位覆蓋率、jurisdiction 分布。
不碰 DB，不碰 API，零成本，隨便跑。

Usage:
    python3 tools/inspect_jsonl.py data/tiagabine_eb_scrape.jsonl

用途：
- Kaggle scraper 跑完後，先確認資料品質再送進 analyze_jsonl
- 找出 no-claims / no-abstract 的專利（可能需要重爬）
"""

import json
import sys
from collections import Counter

# dirty 判斷跟 import_google_patents_jsonl.py 一致
DIRTY_PREFIXES = ("Not Found", "Error")
SENTINELS = {"N/A", "n/a", "N/a", "", None}


def is_missing(val):
    return val is None or (isinstance(val, str) and val.strip() in SENTINELS)


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 tools/inspect_jsonl.py <file.jsonl>")
        sys.exit(1)

    path = sys.argv[1]
    total = 0
    dirty = 0
    clean_rows = []

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                dirty += 1
                continue
            title = rec.get("title", "")
            if any(title.startswith(p) for p in DIRTY_PREFIXES):
                dirty += 1
                continue
            clean_rows.append(rec)

    clean = len(clean_rows)

    print()
    print(f"File:  {path}")
    print(f"Total: {total}")
    print(f"Clean: {clean} ({clean/total*100:.1f}%)" if total else "Clean: 0")
    print(f"Dirty: {dirty} (Not Found / Error / bad JSON)")

    if not clean_rows:
        print("\nNo clean rows to analyze.")
        return

    # 欄位覆蓋率（只算 clean rows）
    print(f"\nField coverage (clean rows):")
    for field in ("title", "abstract", "claims", "full_text"):
        have = sum(1 for r in clean_rows if not is_missing(r.get(field)))
        print(f"  {field:<12} {have}/{clean} ({have/clean*100:.1f}%)")

    # jurisdiction 分布（從 requested_id 前兩碼）
    jur = Counter()
    for r in clean_rows:
        pid = r.get("requested_id", "") or r.get("formatted_id", "")
        jur[pid[:2].upper()] += 1
    print(f"\nJurisdiction:")
    for cc, n in jur.most_common():
        print(f"  {cc}: {n}")

    # 列出沒有 claims 的（可能要重爬）
    no_claims = [
        r.get("requested_id", "?") for r in clean_rows
        if is_missing(r.get("claims"))
    ]
    if no_claims:
        print(f"\nNo-claims rows ({len(no_claims)}):")
        print("  " + ", ".join(no_claims[:30]))
        if len(no_claims) > 30:
            print(f"  ... and {len(no_claims) - 30} more")
    print()


if __name__ == "__main__":
    main()
