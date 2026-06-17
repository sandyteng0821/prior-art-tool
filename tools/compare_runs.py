#!/usr/bin/env python3
"""
Morning probe script — Steps 3-6 of the checklist.
不改任何環境變數、不改 config.py。

用法：
    python3 scratch/probe_rerun_diff.py \
        output/gap_analysis_20260603_2232.csv \
        output/gap_analysis_20260616_2108.csv
"""

import csv
import sys


def load_csv(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def run(old_path, new_path):
    old_rows = load_csv(old_path)
    new_rows = load_csv(new_path)

    # ── Step 3：整體 risk 分佈比較 ──────────────────────────────────────────
    print("=" * 70)
    print("STEP 3: Risk 分佈比較")
    print("=" * 70)

    for label, rows in [("OLD", old_rows), ("NEW", new_rows)]:
        counts = {}
        for r in rows:
            risk = r.get("fto_risk", "?")
            counts[risk] = counts.get(risk, 0) + 1
        total = sum(counts.values())
        print(f"  {label} ({total} rows): {counts}")

    # ── Step 4：篇數差異調查 ────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("STEP 4: 篇數差異（patent_id 比對）")
    print("=" * 70)

    old_ids = set(r["patent_id"] for r in old_rows)
    new_ids = set(r["patent_id"] for r in new_rows)

    print(f"  Old: {len(old_ids)} distinct IDs ({len(old_rows)} rows)")
    print(f"  New: {len(new_ids)} distinct IDs ({len(new_rows)} rows)")
    print(f"  Old only: {len(old_ids - new_ids)}")
    print(f"  New only: {len(new_ids - old_ids)}")

    if old_ids - new_ids:
        print()
        print("  Missing from new (top 20):")
        for pid in sorted(old_ids - new_ids)[:20]:
            print(f"    {pid}")

    if new_ids - old_ids:
        print()
        print("  New additions (top 20):")
        for pid in sorted(new_ids - old_ids)[:20]:
            print(f"    {pid}")

    # ── Step 5：risk 變動的專利 ─────────────────────────────────────────────
    print()
    print("=" * 70)
    print("STEP 5: Risk 變動（同 patent_id，risk 不同）")
    print("=" * 70)

    # 取每個 patent_id 的「最高 risk」（同一筆可能有多行，取最嚴重的）
    rank = {"High": 3, "Medium": 2, "Low": 1}

    def best_risk(rows_list, pid):
        risks = [r["fto_risk"] for r in rows_list if r["patent_id"] == pid]
        if not risks:
            return None
        return max(risks, key=lambda x: rank.get(x, 0))

    common_ids = old_ids & new_ids
    upgrades = []
    downgrades = []

    for pid in sorted(common_ids):
        old_r = best_risk(old_rows, pid)
        new_r = best_risk(new_rows, pid)
        if old_r != new_r:
            old_rank = rank.get(old_r, 0)
            new_rank = rank.get(new_r, 0)
            entry = (pid, old_r, new_r)
            if new_rank > old_rank:
                upgrades.append(entry)
            else:
                downgrades.append(entry)

    print(f"  Upgrades:   {len(upgrades)}")
    print(f"  Downgrades: {len(downgrades)}")
    print(f"  Unchanged:  {len(common_ids) - len(upgrades) - len(downgrades)}")

    if upgrades:
        print()
        print("  ↑ Upgrades:")
        for pid, old_r, new_r in upgrades[:30]:
            print(f"    {pid}: {old_r} → {new_r}")

    if downgrades:
        print()
        print("  ↓ Downgrades:")
        for pid, old_r, new_r in downgrades[:30]:
            print(f"    {pid}: {old_r} → {new_r}")

    # ── Step 5b："claims missing" 改善 ──────────────────────────────────────
    print()
    print("=" * 70)
    print("STEP 5b: 'claims missing' 改善")
    print("=" * 70)

    old_missing = set(
        r["patent_id"] for r in old_rows
        if "claims missing" in (r.get("reasoning") or "").lower()
    )
    new_missing = set(
        r["patent_id"] for r in new_rows
        if "claims missing" in (r.get("reasoning") or "").lower()
    )

    print(f"  Old: {len(old_missing)} patents with 'claims missing'")
    print(f"  New: {len(new_missing)} patents with 'claims missing'")
    print(f"  Fixed: {len(old_missing - new_missing)}")
    print(f"  Still missing: {len(old_missing & new_missing)}")

    if old_missing - new_missing:
        print()
        print("  Fixed (sample):")
        for pid in sorted(old_missing - new_missing)[:10]:
            new_reasoning = [
                r["reasoning"] for r in new_rows if r["patent_id"] == pid
            ]
            print(f"    {pid}: {new_reasoning[0][:80] if new_reasoning else '(not in new)'}")

    # ── Step 6：US9415051B1 專屬對比 ────────────────────────────────────────
    print()
    print("=" * 70)
    print("STEP 6: US9415051B1 詳細對比")
    print("=" * 70)

    target = "US9415051B1"
    fields = [
        "fto_risk", "is_target_drug", "delivery_routes",
        "indications", "reasoning", "gap_opportunity",
    ]

    for label, rows in [("OLD", old_rows), ("NEW", new_rows)]:
        hits = [r for r in rows if r["patent_id"] == target]
        print(f"\n  [{label}] {len(hits)} row(s):")
        for i, row in enumerate(hits):
            print(f"    --- row {i+1} ---")
            for f in fields:
                val = row.get(f, "")
                print(f"      {f}: {val[:100]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python3 scratch/probe_rerun_diff.py <old.csv> <new.csv>")
        sys.exit(1)

    run(sys.argv[1], sys.argv[2])