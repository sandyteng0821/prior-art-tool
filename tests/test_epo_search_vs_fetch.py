# tests/test_epo_search_vs_fetch.py
#
# 重現 EPO OPS search API 的 patent family 覆蓋缺口
#
# 問題描述：
#   EPO search（ta= query）只回傳 representative publication（通常是 A1）
#   同一個 patent family 的 granted 版本（B2）不會出現在 search 結果裡
#   但用 _get_or_fetch 直接抓 B2 號碼是可以拿到資料的
#
# 發現日期：2025-04
# 發現情境：Pemirolast × IPF 驗證跑，同事手動找到的 Cromolyn B2 專利
#           工具用相同 query 跑卻沒有撈到
#
# 相關 issue：
#   - US10561635B2 / US10583113B2（Cromolyn × pulmonary fibrosis）
#   - EPO search 只回傳 US2019224161A1 / US2018193259A1（同 family 的 A1）
#   - A1 和 B2 的號碼完全不同（不是 kind code 差異，是 patent family 問題）
#
# 根本原因：
#   EPO OPS search 不展開 patent family
#   需要用 EPO family API 才能找到所有成員
#
# 執行方式：
#   python3 tests/test_epo_search_vs_fetch.py

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.patent_fetcher import client, _get_or_fetch

# ── Test 1：EPO search 對這個 query 回傳什麼 ──────────────────────────────────

print("=" * 60)
print("Test 1: EPO search API 回傳結果")
print("Query: ta=cromolyn AND ta=\"pulmonary fibrosis\" AND pd within \"2000 2024\"")
print("=" * 60)

response = client.published_data_search(
    cql='ta=cromolyn AND ta="pulmonary fibrosis" AND pd within "2000 2024"',
    range_begin=1,
    range_end=10,
)
data = response.json()
refs = (
    data["ops:world-patent-data"]
        ["ops:biblio-search"]
        ["ops:search-result"]
        ["ops:publication-reference"]
)
if isinstance(refs, dict):
    refs = [refs]

print(f"Search 回傳 {len(refs)} 筆：")
search_ids = []
for r in refs:
    doc_id  = r.get("document-id", {})
    country = doc_id.get("country", {}).get("$", "")
    number  = doc_id.get("doc-number", {}).get("$", "")
    kind    = doc_id.get("kind", {}).get("$", "")
    pid     = f"{country}{number}{kind}"
    search_ids.append(pid)
    print(f"  {pid}")

# ── Test 2：直接抓 B2 看有沒有資料 ───────────────────────────────────────────

print()
print("=" * 60)
print("Test 2: 直接抓 B2 號碼（同事手動找到的專利）")
print("=" * 60)

target_ids = ["US10561635B2", "US10583113B2"]

for pid in target_ids:
    p = _get_or_fetch(pid)
    title    = p.get("title", "")
    abstract = p.get("abstract", "")
    print(f"\n{pid}")
    print(f"  Title:    {title}")
    print(f"  Abstract: {abstract[:150]}..." if abstract else "  Abstract: EMPTY")

# ── Test 3：確認 search 結果和目標 B2 的關係 ──────────────────────────────────

print()
print("=" * 60)
print("Test 3: Gap 確認")
print("=" * 60)

missing = [pid for pid in target_ids if pid not in search_ids]
if missing:
    print(f"✅ Issue 重現成功")
    print(f"   Search 沒有回傳：{missing}")
    print(f"   但直接 fetch 可以拿到資料")
    print()
    print("根本原因：EPO search 只回傳 representative publication（A1）")
    print("         B2 是同一發明的 granted 版本但號碼不同（patent family）")
    print("         需要 EPO family API 才能找到所有成員")
    print()
    print("建議修法（P2）：")
    print("   在 _get_or_fetch 裡，抓到 A1 後呼叫 EPO family API")
    print("   把所有 family members 一起存進 DB")
else:
    print("❌ Issue 無法重現，search 已經回傳所有目標專利")
    print(f"   Search 結果：{search_ids}")
