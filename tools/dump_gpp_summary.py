#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 gpp cache 裡所有 found 專利的 summary dump 成一張可人工掃的 TSV。

直接複用 build_surechembl_annotation_cache.py 的 lookup_payload / canon_pn,
不重寫 SQL。放在同一個 repo 根目錄跑（跟 tools/ 同層）。

用法:
    python3 dump_gpp_summary.py \
        --cache gpp_annotations.duckdb \
        --patents gpp_patent_ids.txt \
        --out gpp_summary_dump.tsv

排序: 預設按 description_compound_count 由小到大（聚焦的先看）。
      這批 same_field 幾乎全 True（GPSS 上游已保證 TI/AB/CL 共現），
      所以 same_field 沒有鑑別力；desc_cmpd 才是分 laundry-list vs 聚焦的軸。
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_surechembl_annotation_cache as B


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--patents", type=Path, required=True,
                    help="原始 PN 清單，決定 dump 哪些")
    ap.add_argument("--out", type=Path, default=Path("gpp_summary_dump.tsv"))
    ap.add_argument("--sort", choices=["desc_cmpd","same_field","drug_clm"],
                    default="desc_cmpd")
    args = ap.parse_args()

    con = B.connect_duckdb(str(args.cache), Path("./duckdb_tmp"), None, None)
    B.init_cache_schema(con)

    reqs = B.load_patent_requests(None, args.patents)
    rows = []
    for r in reqs:
        p = B.lookup_payload(con, r.input_patent_number)
        if not p:
            continue
        m = p["patents"][0]
        s = p.get("summary") or {}
        if m.get("status") != "found":
            continue

        # 每個 field 的 known drug 名稱（人工看 spesolimab 假陽性用）
        def drugs_in(fname):
            return ",".join(d["name"] for d in p["fields"][fname]["known_drugs"])
        def dis_in(fname):
            return ",".join(d["name"] for d in p["fields"][fname]["diseases"][:3])

        rows.append({
            "pn": p["canonical_patent_number"],
            "bio_status": m.get("biomedical_annotation_status"),
            "desc_cmpd": s.get("description_compound_count") or 0,
            "clm_cmpd": s.get("claims_compound_count") or 0,
            "same_field": s.get("drug_disease_same_field"),
            "drug_clm": s.get("drug_in_claims"),
            "dis_clm": s.get("disease_in_claims"),
            "drug_desc_only": s.get("drug_description_only"),
            "has_disease": s.get("has_disease"),
            "large_list": s.get("large_compound_list"),
            "drugs_claims": drugs_in("Claims"),
            "drugs_title": drugs_in("Title"),
            "dis_claims": dis_in("Claims"),
            "title": (m.get("title") or "")[:70],
        })

    # 排序：desc_cmpd 小的先（聚焦），但 large_list=True 的沉底
    keymap = {
        "desc_cmpd": lambda x: (x["desc_cmpd"]),
        "same_field": lambda x: (not x["same_field"], x["desc_cmpd"]),
        "drug_clm": lambda x: (not x["drug_clm"], x["desc_cmpd"]),
    }
    rows.sort(key=keymap[args.sort])

    cols = ["pn","bio_status","desc_cmpd","clm_cmpd","same_field","drug_clm",
            "dis_clm","drug_desc_only","has_disease","large_list",
            "drugs_claims","drugs_title","dis_claims","title"]
    with args.out.open("w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c,"")) for c in cols) + "\n")

    print(f"dumped {len(rows)} found patents -> {args.out}")
    print(f"排序鍵: {args.sort}")
    # 快速統計
    focused = sum(1 for r in rows if r["desc_cmpd"] <= 50)
    laundry = sum(1 for r in rows if r["desc_cmpd"] > 200)
    print(f"  desc_cmpd ≤50 (聚焦候選): {focused}")
    print(f"  desc_cmpd >200 (疑似列舉): {laundry}")
    print(f"  outside_known_coverage (disease 負值不可信): "
          f"{sum(1 for r in rows if r['bio_status']=='outside_known_coverage')}")


if __name__ == "__main__":
    main()
