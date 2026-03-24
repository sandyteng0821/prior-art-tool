# main.py
# Pipeline 入口：串接四個 module，執行完整 prior art 搜尋流程

from concurrent.futures import ThreadPoolExecutor, as_completed
from modules.query_builder import build_queries
from modules.patent_fetcher import fetch_patents
from modules.llm_analyzer   import analyze_patent
from modules.output_writer  import save_results, print_summary


def run_pipeline():
    # ── Step 1：產生搜尋字串 ──────────────────────────────────────────────────
    queries = build_queries()
    print(f"[1/4] 產生 {len(queries)} 組搜尋字串")

    # ── Step 2：抓取專利（去重） ──────────────────────────────────────────────
    print("[2/4] 從 EPO OPS 抓取專利...")
    all_patents, seen_ids = [], set()

    for i, query in enumerate(queries, 1):
        print(f"  Strategy {i}: {query[:70]}...")
        patents = fetch_patents(query)
        new = [p for p in patents if p["patent_id"] not in seen_ids]
        seen_ids.update(p["patent_id"] for p in new)
        all_patents.extend(new)
        print(f"  → 新增 {len(new)} 筆（累計 {len(all_patents)} 筆）")

    if not all_patents:
        print("未找到任何專利，請確認 EPO API key 是否正確。")
        return

    # ── Step 3：LLM 分析（parallel） ─────────────────────────────────────────
    print(f"\n[3/4] LLM 分析 {len(all_patents)} 筆專利（兩段式）...")
    results = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_patent, p): p for p in all_patents}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as e:
                patent = futures[future]
                print(f"  [分析失敗] {patent.get('patent_id', '?')}: {e}")
            print(f"  進度：{i}/{len(all_patents)}", end="\r")

    print()  # 換行

    # ── Step 4：輸出結果 ──────────────────────────────────────────────────────
    print("[4/4] 輸出結果...")
    save_results(results)
    print_summary(results)


if __name__ == "__main__":
    run_pipeline()
