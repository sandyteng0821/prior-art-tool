# backfill_examples.py
# 對 DB 裡已存的專利補抓 description 並切出 examples
# 執行：python3 backfill_examples.py

import time
from modules.patent_store import _get_conn, upsert_patent, init_db, stats
from modules.patent_fetcher import _fetch_description, _parse_examples

def backfill():
    init_db()

    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT patent_id, title, abstract, claims,
                   status, year, source
            FROM patents
            WHERE examples_extracted = '' OR examples_extracted IS NULL
        """).fetchall()

    total = len(rows)
    print(f"補抓目標：{total} 筆\n")

    hit, miss = 0, 0

    for i, row in enumerate(rows, 1):
        patent_id = row["patent_id"]
        print(f"[{i}/{total}] {patent_id} ...", end=" ", flush=True)

        description = _fetch_description(patent_id)
        examples    = _parse_examples(description)

        upsert_patent({
            **dict(row),
            "examples_extracted": examples,
        })

        if examples:
            print(f"✓ {len(examples)} 字元")
            hit += 1
        else:
            print("— 切不到（無 Examples 區塊）")
            miss += 1

        time.sleep(0.5)

    print(f"\n完成：{hit} 筆有 examples，{miss} 筆切不到")
    print("\n最新 DB 統計：")
    print(stats())

if __name__ == "__main__":
    backfill()