#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_annotation_cache.py — 對 build_surechembl_annotation_cache.py 建好的 cache
印資料品質報告（覆蓋率三桶 / found 品質 / laundry-list 分佈 / 候選排序）。

為什麼有這支
------------
server 上沒有互動式 SQL client，tool guide 那幾條「建完先看資料品質」的 SQL
不能直接跑。這支把那些 query 包成可執行 script：

    python3 tools/report_annotation_cache.py --cache surechembl_annotations.duckdb

只讀 cache（read_only），不碰 15GB parquet；除非給 --data-dir 才會做 US 補零檢查。
每段對應的 SQL 就寫在該函式的 docstring 裡，有 SQL client 也可直接抄。

Deps: duckdb（與 build 工具相同）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def log(m: str = "") -> None:
    print(m, flush=True)


def section(t: str) -> None:
    log()
    log(f"── {t} " + "─" * max(1, 60 - len(t)))


def connect(cache: Path):
    try:
        import duckdb
    except ImportError as e:
        raise SystemExit("需要 duckdb：pip install duckdb") from e
    if not cache.exists():
        raise SystemExit(f"cache 不存在：{cache}")
    return duckdb.connect(str(cache), read_only=True)


def q(con, sql: str, params=None):
    return con.execute(sql, params or []).fetchall()


def has_table(con, name: str) -> bool:
    r = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(r and r[0])


def cache_header(con) -> None:
    section("cache 資訊")
    if not has_table(con, "cache_info"):
        log("  （無 cache_info 表，可能不是本工具建的 cache）")
        return
    rows = dict(q(con, "SELECT key, value FROM cache_info"))
    for k in ("schema_version", "surechembl_release", "biomedical_annotation_through",
              "drug_dictionary_file", "updated_at"):
        if k in rows:
            log(f"  {k:<32} {rows[k]}")


def coverage(con) -> None:
    """SELECT status, count(*) FROM patent_meta GROUP BY 1;"""
    section("① 覆蓋率三桶（patent_meta.status）")
    if not has_table(con, "patent_meta"):
        log("  ⚠ 無 patent_meta 表")
        return
    rows = q(con, "SELECT status, count(*) n FROM patent_meta GROUP BY 1 ORDER BY n DESC")
    tot = sum(r[1] for r in rows) or 1
    log(f"  {'status':<26}{'n':>10}{'%':>8}")
    for st, n in rows:
        log(f"  {str(st):<26}{n:>10,}{100 * n / tot:>7.1f}%")
    log(f"  {'合計':<26}{tot:>10,}")
    log("  ⚠ not_in_bulk_patents 是上界（本篇沒抽到小分子），非發明層級「沒有」")


def found_quality(con) -> None:
    """
    SELECT biomedical_annotation_status, count(*) n,
           count(*) FILTER (WHERE has_known_drug) known_drug,
           count(*) FILTER (WHERE has_disease)    with_disease
    FROM patent_annotation_summary WHERE status='found' GROUP BY 1;
    """
    section("② found 之中的品質（biomedical coverage / known-drug / disease）")
    if not has_table(con, "patent_annotation_summary"):
        log("  ⚠ 無 patent_annotation_summary 表")
        return
    rows = q(con, """
        SELECT biomedical_annotation_status, count(*) n,
               count(*) FILTER (WHERE has_known_drug) known_drug,
               count(*) FILTER (WHERE has_disease)    with_disease
        FROM patent_annotation_summary
        WHERE status = 'found' GROUP BY 1 ORDER BY n DESC
    """)
    if not rows:
        log("  （沒有 found 的專利）")
        return
    log(f"  {'biomedical_status':<26}{'found':>8}{'known_drug':>12}{'with_disease':>14}")
    for bs, n, kd, wd in rows:
        log(f"  {str(bs):<26}{n:>8,}{kd:>12,}{wd:>14,}")
    log("  ⚠ known_drug = 命中字典的那些藥；with_disease 在 outside_known_coverage 上不可信")


def laundry(con) -> None:
    """
    SELECT CASE WHEN description_compound_count<=10 THEN '≤10' ... END bucket,
           count(*) FROM patent_annotation_summary WHERE status='found' GROUP BY 1;
    """
    section("③ laundry-list 分佈（description_compound_count）")
    if not has_table(con, "patent_annotation_summary"):
        log("  ⚠ 無 patent_annotation_summary 表")
        return
    rows = q(con, """
        SELECT CASE WHEN description_compound_count <= 10   THEN '1'
                    WHEN description_compound_count <= 200  THEN '2'
                    WHEN description_compound_count <= 1000 THEN '3'
                    ELSE '4' END b, count(*) n
        FROM patent_annotation_summary WHERE status = 'found' GROUP BY 1 ORDER BY 1
    """)
    label = {"1": "≤10", "2": "11-200", "3": "201-1000", "4": ">1000"}
    tot = sum(r[1] for r in rows) or 1
    log(f"  {'desc 化合物數':<14}{'專利數':>10}{'%':>8}")
    for b, n in rows:
        log(f"  {label.get(b, b):<14}{n:>10,}{100 * n / tot:>7.1f}%")
    log("  → 右尾（>200）多半是列舉背景；large_compound_list 門檻依此校準")


def candidates(con, top: int) -> None:
    """
    SELECT canonical_patent_number, drug_disease_same_field, drug_in_claims,
           disease_in_claims, description_compound_count
    FROM patent_annotation_summary
    WHERE status='found' AND has_drug_and_disease
    ORDER BY drug_disease_same_field DESC, drug_in_claims DESC LIMIT :top;
    """
    section(f"④ 候選排序：drug+disease 同 field 優先（前 {top}）")
    if not has_table(con, "patent_annotation_summary"):
        log("  ⚠ 無 patent_annotation_summary 表")
        return
    rows = q(con, f"""
        SELECT canonical_patent_number, drug_disease_same_field,
               drug_in_claims, disease_in_claims, description_compound_count
        FROM patent_annotation_summary
        WHERE status = 'found' AND has_drug_and_disease
        ORDER BY drug_disease_same_field DESC NULLS LAST,
                 drug_in_claims DESC, disease_in_claims DESC
        LIMIT {int(top)}
    """)
    if not rows:
        log("  （沒有同時有 known-drug 與 disease 的 found 專利）")
        return
    log(f"  {'patent':<20}{'same_field':>11}{'drug_clm':>10}{'dis_clm':>9}{'desc_cmpd':>11}")
    for pn, sf, dc, ic, nd in rows:
        log(f"  {str(pn):<20}{str(sf):>11}{str(dc):>10}{str(ic):>9}"
            f"{(nd if nd is not None else 0):>11,}")


def padding_check(con, data_dir: Path) -> None:
    """
    SELECT count(*) FROM read_parquet('.../patents.parquet')
    WHERE country='US' AND regexp_matches(replace(patent_number,'-',''), '^US0');
    """
    section("⑤（選用）US 補零 join 檢查")
    hits = sorted(data_dir.glob("**/patents*.parquet"))
    if not hits:
        log(f"  ⚠ {data_dir} 找不到 patents.parquet，略過")
        return
    src = f"'{hits[0]}'" if len(hits) == 1 else "[" + ",".join(f"'{h}'" for h in hits) + "]"
    n = con.execute(f"""
        SELECT count(*) FROM read_parquet({src})
        WHERE country = 'US'
          AND regexp_matches(replace(patent_number, '-', ''), '^US0')
    """).fetchone()[0]
    log(f"  bulk US patent_number 以 0 開頭者：{n:,}")
    if n:
        log("  ⚠ >0：這些 US 補零列會被 canon_pn 漏配（靜默判 not_in_bulk），"
            "join 要補對稱剝零")
    else:
        log("  → 0：沒有補零漏配問題")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="SureChEMBL annotation cache 資料品質報告（只讀 cache）")
    ap.add_argument("--cache", type=Path, required=True, help="build 出來的 .duckdb")
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="給了才做 US 補零檢查（需讀 patents.parquet）")
    ap.add_argument("--top", type=int, default=20, help="④ 候選排序印幾筆")
    args = ap.parse_args()

    con = connect(args.cache)
    log("=" * 62)
    log(f"  annotation cache 報告 — {args.cache}")
    log("=" * 62)
    cache_header(con)
    coverage(con)
    found_quality(con)
    laundry(con)
    candidates(con, args.top)
    if args.data_dir:
        padding_check(con, args.data_dir)
    log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
