"""
compare_coverage — 比較 Google Patents 與 EPO probe 對同一組專利的覆蓋差異

讀取兩個 JSONL（Google Patents scraper output + batch_epo_probe output），
按 patent ID 配對，比較每筆在兩個 source 各自拿到了什麼。

Modes:
  summary  — jurisdiction × source coverage matrix（預設）
  detail   — 每筆 ID 的 GP vs EPO 欄位差異
  all      — 兩個都輸出

Usage:
    # Summary（預設）
    python3 tools/compare_coverage.py \\
        --gp data/global_patents_archive_GPP_idlist_20260709.jsonl \\
        --epo scratch/epo_probe_gpp.jsonl

    # Detail
    python3 tools/compare_coverage.py \\
        --gp data/global_patents_archive_GPP_idlist_20260709.jsonl \\
        --epo scratch/epo_probe_gpp.jsonl \\
        --mode detail

    # All
    python3 tools/compare_coverage.py \\
        --gp data/global_patents_archive_IPF_idlist_20260709.jsonl \\
        --epo scratch/epo_probe_ipf.jsonl \\
        --mode all

    # 存報告
    python3 tools/compare_coverage.py \\
        --gp data/global_patents_archive_GPP_idlist_20260709.jsonl \\
        --epo scratch/epo_probe_gpp.jsonl \\
        --mode all --output scratch/coverage_compare_gpp.txt
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


# ── JSONL loaders ────────────────────────────────────────────────────────────

# tools/compare_coverage.py, load_gp_jsonl 函數，替換整個函數

def load_gp_jsonl(path: str) -> dict:
    """Load Google Patents scraper JSONL → {requested_id: record}."""
    records = {}
    with open(path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                pid = r.get("requested_id") or r.get("patent_id", "")
                records[pid] = r
            except json.JSONDecodeError as e:
                print(f"  [WARN] Line {i}: JSON parse error — {e}")
    return records


def load_epo_jsonl(path: str) -> dict:
    """Load EPO probe JSONL → {patent_id: record}."""
    records = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        pid = r.get("patent_id", "")
        records[pid] = r
    return records


# ── Classification helpers ───────────────────────────────────────────────────

def classify_gp(rec: dict) -> str:
    """Classify a Google Patents record into a coverage bucket."""
    if not rec:
        return "missing"
    title = rec.get("title", "") or ""
    if title.startswith(("Not Found", "Error")):
        return "error"
    claims = rec.get("claims", "") or ""
    full_text = rec.get("full_text", "") or ""
    abstract = rec.get("abstract", "") or ""
    if claims not in ("N/A", "", None) and len(claims) > 10:
        return "has_claims"
    if full_text not in ("N/A", "", None) and len(full_text) > 10:
        return "desc_only"
    if abstract not in ("N/A", "", None) and len(abstract) > 10:
        return "abstract_only"
    return "empty"


def classify_epo(rec: dict) -> str:
    """Classify an EPO probe record into a coverage bucket."""
    if not rec:
        return "missing"
    bucket = rec.get("bucket", "")
    if bucket:
        return bucket  # already classified by batch_epo_probe
    # fallback: classify from raw data_completeness
    dc = rec.get("data_completeness", {})
    claims_c = dc.get("claims_chars", 0)
    abs_c = dc.get("abstract_chars", 0)
    if claims_c > 0:
        return "has_claims"
    if abs_c > 0:
        return "abstract_only"
    return "epo_empty"


def gp_chars(rec: dict) -> dict:
    """Extract char counts from a GP record."""
    if not rec:
        return {"abstract": 0, "claims": 0, "desc": 0}
    abstract = rec.get("abstract", "") or ""
    claims = rec.get("claims", "") or ""
    full_text = rec.get("full_text", "") or ""
    return {
        "abstract": len(abstract) if abstract not in ("N/A",) else 0,
        "claims": len(claims) if claims not in ("N/A",) else 0,
        "desc": len(full_text) if full_text not in ("N/A",) else 0,
    }


def epo_chars(rec: dict) -> dict:
    """Extract char counts from an EPO probe record."""
    if not rec:
        return {"abstract": 0, "claims": 0, "examples": 0}
    dc = rec.get("data_completeness", {})
    return {
        "abstract": dc.get("abstract_chars", 0),
        "claims": dc.get("claims_chars", 0),
        "examples": dc.get("examples_chars", 0),
    }


def jurisdiction(patent_id: str) -> str:
    m = re.match(r"^([A-Z]{2})", patent_id)
    return m.group(1) if m else "??"


def winner(gp_bucket: str, epo_bucket: str) -> str:
    """Determine which source wins for a given patent."""
    rank = {"has_claims": 4, "desc_only": 3, "abstract_only": 2,
            "empty": 1, "epo_empty": 1, "error": 0, "missing": 0,
            "api_error": 0}
    gp_rank = rank.get(gp_bucket, 0)
    epo_rank = rank.get(epo_bucket, 0)
    if gp_rank > epo_rank:
        return "GP"
    elif epo_rank > gp_rank:
        return "EPO"
    elif gp_rank == epo_rank and gp_rank > 0:
        return "tie"
    else:
        return "neither"


# ── Output: Detail mode ─────────────────────────────────────────────────────

def print_detail(all_ids, gp_data, epo_data, out):
    out.write(f"\n  {'═' * 72}\n")
    out.write(f"  DETAIL: per-patent GP vs EPO comparison\n")
    out.write(f"  {'─' * 72}\n")
    out.write(f"  {'ID':<24} {'GP':<20} {'EPO':<20} {'Winner':<8}\n")
    out.write(f"  {'─' * 72}\n")

    wins = Counter()
    for pid in sorted(all_ids):
        gp_rec = gp_data.get(pid)
        epo_rec = epo_data.get(pid)
        gp_b = classify_gp(gp_rec)
        epo_b = classify_epo(epo_rec)
        w = winner(gp_b, epo_b)
        wins[w] += 1

        gc = gp_chars(gp_rec)
        ec = epo_chars(epo_rec)
        gp_label = f"{gp_b}({gc['claims']}c)" if gp_b == "has_claims" else gp_b
        epo_label = f"{epo_b}({ec['claims']}c)" if epo_b == "has_claims" else epo_b

        out.write(f"  {pid:<24} {gp_label:<20} {epo_label:<20} {w:<8}\n")

    out.write(f"  {'─' * 72}\n")
    out.write(f"  Winner tally: GP={wins.get('GP',0)}  EPO={wins.get('EPO',0)}  "
              f"tie={wins.get('tie',0)}  neither={wins.get('neither',0)}\n")


# ── Output: Summary mode ────────────────────────────────────────────────────

def print_summary(all_ids, gp_data, epo_data, out):
    out.write(f"\n  {'═' * 80}\n")
    out.write(f"  SUMMARY: coverage by jurisdiction\n")
    out.write(f"  {'─' * 80}\n")
    out.write(f"  {'JUR':<6} {'total':>5}  │ {'GP✓clm':>7} {'GP✓abs':>7} {'GP✗':>5}"
              f"  │ {'EPO✓clm':>7} {'EPO✓abs':>7} {'EPO✗':>5}"
              f"  │ {'both✓':>5} {'neithr':>6}\n")
    out.write(f"  {'─' * 80}\n")

    jur_data = {}
    totals = Counter()
    for pid in all_ids:
        jur = jurisdiction(pid)
        if jur not in jur_data:
            jur_data[jur] = Counter()
        jur_data[jur]["total"] += 1

        gp_b = classify_gp(gp_data.get(pid))
        epo_b = classify_epo(epo_data.get(pid))

        # GP buckets
        if gp_b == "has_claims":
            jur_data[jur]["gp_claims"] += 1
        elif gp_b in ("abstract_only", "desc_only"):
            jur_data[jur]["gp_abs"] += 1
        else:
            jur_data[jur]["gp_miss"] += 1

        # EPO buckets
        if epo_b == "has_claims":
            jur_data[jur]["epo_claims"] += 1
        elif epo_b == "abstract_only":
            jur_data[jur]["epo_abs"] += 1
        else:
            jur_data[jur]["epo_miss"] += 1

        # Combined
        gp_has = gp_b in ("has_claims", "desc_only", "abstract_only")
        epo_has = epo_b in ("has_claims", "abstract_only")
        if gp_has and epo_has:
            jur_data[jur]["both"] += 1
        elif not gp_has and not epo_has:
            jur_data[jur]["neither"] += 1

    for jur in sorted(jur_data, key=lambda x: -jur_data[x]["total"]):
        c = jur_data[jur]
        out.write(f"  {jur:<6} {c['total']:>5}  │ {c.get('gp_claims',0):>7} "
                  f"{c.get('gp_abs',0):>7} {c.get('gp_miss',0):>5}"
                  f"  │ {c.get('epo_claims',0):>7} {c.get('epo_abs',0):>7} "
                  f"{c.get('epo_miss',0):>5}"
                  f"  │ {c.get('both',0):>5} {c.get('neither',0):>6}\n")
        for k in c:
            totals[k] += c[k]

    out.write(f"  {'─' * 80}\n")
    out.write(f"  {'ALL':<6} {totals['total']:>5}  │ {totals.get('gp_claims',0):>7} "
              f"{totals.get('gp_abs',0):>7} {totals.get('gp_miss',0):>5}"
              f"  │ {totals.get('epo_claims',0):>7} {totals.get('epo_abs',0):>7} "
              f"{totals.get('epo_miss',0):>5}"
              f"  │ {totals.get('both',0):>5} {totals.get('neither',0):>6}\n")

    # ── Union coverage ───────────────────────────────────────────────────
    union_claims = 0
    union_any = 0
    for pid in all_ids:
        gp_b = classify_gp(gp_data.get(pid))
        epo_b = classify_epo(epo_data.get(pid))
        if gp_b == "has_claims" or epo_b == "has_claims":
            union_claims += 1
        gp_has = gp_b in ("has_claims", "desc_only", "abstract_only")
        epo_has = epo_b in ("has_claims", "abstract_only")
        if gp_has or epo_has:
            union_any += 1

    total = len(all_ids)
    out.write(f"\n  UNION COVERAGE (GP ∪ EPO):\n")
    out.write(f"    Has claims (either source): {union_claims}/{total} "
              f"({union_claims/total*100:.1f}%)\n")
    out.write(f"    Has any content:            {union_any}/{total} "
              f"({union_any/total*100:.1f}%)\n")
    out.write(f"    No data from either:        {total - union_any}/{total} "
              f"({(total - union_any)/total*100:.1f}%)\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compare Google Patents vs EPO probe coverage.",
        prog="python3 tools/compare_coverage.py",
    )
    parser.add_argument("--gp", required=True,
                        help="Google Patents scraper JSONL")
    parser.add_argument("--epo", required=True,
                        help="EPO probe JSONL (from batch_epo_probe)")
    parser.add_argument("--mode", choices=["summary", "detail", "all"],
                        default="summary",
                        help="Output mode (default: summary)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Write output to file (default: stdout)")
    args = parser.parse_args()

    gp_data = load_gp_jsonl(args.gp)
    epo_data = load_epo_jsonl(args.epo)

    # Union of all IDs from both sources
    all_ids = sorted(set(gp_data.keys()) | set(epo_data.keys()))

    gp_only = set(gp_data.keys()) - set(epo_data.keys())
    epo_only = set(epo_data.keys()) - set(gp_data.keys())
    both = set(gp_data.keys()) & set(epo_data.keys())

    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout

    out.write(f"\n  Compare Coverage: GP vs EPO\n")
    out.write(f"  GP file:  {args.gp} ({len(gp_data)} records)\n")
    out.write(f"  EPO file: {args.epo} ({len(epo_data)} records)\n")
    out.write(f"  ID overlap: {len(both)} matched, "
              f"{len(gp_only)} GP-only, {len(epo_only)} EPO-only\n")

    if args.mode in ("summary", "all"):
        print_summary(all_ids, gp_data, epo_data, out)

    if args.mode in ("detail", "all"):
        print_detail(all_ids, gp_data, epo_data, out)

    if args.output:
        out.close()
        print(f"\n  Report saved to {args.output}")


if __name__ == "__main__":
    main()
