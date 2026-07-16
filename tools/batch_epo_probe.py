"""
batch_epo_probe — 批次透過 inspect API 探測 EPO 對一組專利的覆蓋狀態

給一個 patent ID 的 txt 檔，逐筆打 /api/v1/patents/inspect endpoint，
回報每筆的 data_source（db / epo_sandbox）和 data_completeness，
最後輸出 jurisdiction × coverage 統計。

不寫 DB，不消耗 LLM token。每筆 EPO miss 會打一次 EPO biblio（sandbox）。

Usage:
    # 基本用法
    python3 tools/batch_epo_probe.py data/plainid/GPP_idlist_20260709.txt

    # 指定 API base URL
    python3 tools/batch_epo_probe.py data/plainid/IPF_idlist_20260709.txt \\
        --base-url http://localhost:8007

    # 調整 delay（預設 0.6s，EPO rate limit 安全值）
    python3 tools/batch_epo_probe.py data/plainid/GPP_idlist_20260709.txt --delay 1.0

    # 輸出 JSONL 紀錄（每筆結果存檔，方便後續分析）
    python3 tools/batch_epo_probe.py data/plainid/IPF_idlist_20260709.txt \\
        --output-jsonl scratch/epo_probe_ipf.jsonl
"""

import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path


DEFAULT_BASE_URL = "http://localhost:8007"
DEFAULT_DELAY = 0.6  # seconds between requests (EPO rate limit safe)


def load_ids(file_path: str) -> list[str]:
    """Read patent IDs from txt file (one per line, # = comment, comma/semicolon ok)."""
    ids = []
    for line in Path(file_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for part in line.replace(",", ";").split(";"):
            part = part.strip()
            if part:
                ids.append(part)
    return ids


def jurisdiction(patent_id: str) -> str:
    """Extract 2-letter jurisdiction prefix from patent ID."""
    m = re.match(r"^([A-Z]{2})", patent_id)
    return m.group(1) if m else "??"


def call_inspect(base_url: str, patent_id: str) -> dict:
    """Call inspect API and return parsed response or error dict."""
    url = f"{base_url}/api/v1/patents/inspect"
    payload = json.dumps({
        "patent_id": patent_id,
        "drug_aliases": ["probe"],  # dummy — we only care about data_completeness
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"_error": f"HTTP {e.code}", "_detail": body}
    except Exception as e:
        return {"_error": str(e)}


def classify(result: dict) -> str:
    """Classify a single inspect result into a coverage bucket."""
    if "_error" in result:
        return "api_error"
    dc = result.get("data_completeness", {})
    has_abs = dc.get("abstract_chars", 0) > 0
    has_claims = dc.get("claims_chars", 0) > 0
    has_ex = dc.get("examples_chars", 0) > 0
    source = result.get("data_source", "")
    if has_claims:
        return "has_claims"
    elif has_abs:
        return "abstract_only"
    elif source == "epo_sandbox":
        return "epo_empty"
    else:
        return "empty"


def main():
    parser = argparse.ArgumentParser(
        description="Batch EPO coverage probe via inspect API.",
        prog="python3 tools/batch_epo_probe.py",
    )
    parser.add_argument("file", help="Path to patent ID list (txt, one per line)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Delay between requests in seconds (default: {DEFAULT_DELAY})")
    parser.add_argument("--output-jsonl", "-o", type=str, default=None,
                        help="Write per-patent results to JSONL file")
    args = parser.parse_args()

    ids = load_ids(args.file)
    if not ids:
        print("[ERROR] No patent IDs found in file.")
        sys.exit(1)

    filename = Path(args.file).name
    print(f"\n  Probing {len(ids)} patent(s) from {filename}")
    print(f"  API: {args.base_url}/api/v1/patents/inspect")
    print(f"  Delay: {args.delay}s per request")
    print(f"  {'─' * 64}")

    # ── Main loop ────────────────────────────────────────────────────────────
    results = []
    f_out = None
    if args.output_jsonl:
        f_out = open(args.output_jsonl, "w", encoding="utf-8")

    for i, pid in enumerate(ids, 1):
        resp = call_inspect(args.base_url, pid)
        bucket = classify(resp)
        dc = resp.get("data_completeness", {})
        source = resp.get("data_source", "?")
        title = (resp.get("title") or "")[:50]

        # Per-line output
        if bucket == "has_claims":
            icon = "✓"
        elif bucket == "abstract_only":
            icon = "△"
        elif bucket == "api_error":
            icon = "✗"
            title = resp.get("_error", "")
        else:
            icon = "·"

        abs_c = dc.get("abstract_chars", 0)
        clm_c = dc.get("claims_chars", 0)
        print(f"  [{i:>4}/{len(ids)}] {icon} {pid:<24} src={source:<12} "
              f"abs={abs_c:<6} claims={clm_c:<6} {title}")

        record = {
            "patent_id": pid,
            "jurisdiction": jurisdiction(pid),
            "data_source": source,
            "bucket": bucket,
            "abstract_chars": abs_c,
            "claims_chars": clm_c,
            "examples_chars": dc.get("examples_chars", 0),
        }
        results.append(record)

        if f_out:
            f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
            f_out.flush()

        if i < len(ids):
            time.sleep(args.delay)

    if f_out:
        f_out.close()
        print(f"\n  Results saved to {args.output_jsonl}")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n  {'═' * 64}")
    print(f"  SUMMARY: {filename}")
    print(f"  {'─' * 64}")

    bucket_counts = Counter(r["bucket"] for r in results)
    total = len(results)
    for label, desc in [
        ("has_claims", "EPO has claims"),
        ("abstract_only", "Abstract only (no claims)"),
        ("epo_empty", "EPO empty (sandbox 404)"),
        ("empty", "No data"),
        ("api_error", "API error"),
    ]:
        n = bucket_counts.get(label, 0)
        pct = f"{n/total*100:.1f}%" if total else "—"
        print(f"    {desc:<30} {n:>5}  ({pct})")
    print(f"    {'─' * 44}")
    print(f"    {'Total':<30} {total:>5}")

    # ── By jurisdiction ──────────────────────────────────────────────────────
    print(f"\n  BY JURISDICTION:")
    print(f"  {'─' * 64}")
    print(f"  {'JUR':<6} {'total':>6} {'claims':>7} {'abs':>7} {'empty':>7} {'err':>5}")

    jur_data = {}
    for r in results:
        j = r["jurisdiction"]
        if j not in jur_data:
            jur_data[j] = Counter()
        jur_data[j]["total"] += 1
        jur_data[j][r["bucket"]] += 1

    for j in sorted(jur_data, key=lambda x: -jur_data[x]["total"]):
        c = jur_data[j]
        print(f"  {j:<6} {c['total']:>6} {c.get('has_claims',0):>7} "
              f"{c.get('abstract_only',0):>7} "
              f"{c.get('epo_empty',0)+c.get('empty',0):>7} "
              f"{c.get('api_error',0):>5}")

    print()


if __name__ == "__main__":
    main()
