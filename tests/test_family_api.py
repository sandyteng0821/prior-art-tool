# tests/test_family_api.py
#
# 驗證 EPO OPS family API 的正確呼叫方式
#
# 關鍵發現：
#   - Epodoc 不能帶 kind code（不能用 Epodoc("US2019224161", "A1")）
#   - 要用 Epodoc("US2019224161") 不帶 kind code
#   - family() 第三個參數要明確傳 None
#   - constituents=["biblio"] 放第四個參數
#
# 執行方式：
#   python3 tests/test_family_api.py

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import epo_ops
import json
from modules.patent_fetcher import client


def parse_family_members(resp_text: str) -> list[str]:
    """
    從 family API 回傳的 JSON 解析出所有 family member 的 patent_id。
    回傳格式：["US2019224161A1", "US10561635B2", ...]
    """
    data = json.loads(resp_text)
    members = (
        data["ops:world-patent-data"]
            ["ops:patent-family"]
            ["ops:family-member"]
    )
    if isinstance(members, dict):
        members = [members]

    result = []
    for member in members:
        pub_refs = member.get("publication-reference", {})
        doc_ids = pub_refs.get("document-id", [])
        if isinstance(doc_ids, dict):
            doc_ids = [doc_ids]

        for doc_id in doc_ids:
            if doc_id.get("@document-id-type") == "docdb":
                country = doc_id.get("country", {}).get("$", "")
                number  = doc_id.get("doc-number", {}).get("$", "")
                kind    = doc_id.get("kind", {}).get("$", "")
                if country and number and kind:
                    result.append(f"{country}{number}{kind}")

    return result


# ── Test 1: EP 號碼 ───────────────────────────────────────────────────────────

print("=" * 60)
print("Test 1: EP 號碼（EP1000000）")
print("=" * 60)

resp_ep = client.family(
    "publication",
    epo_ops.models.Epodoc("EP1000000"),
    None,
    ["biblio"]
)
print(f"Status: {resp_ep.status_code}")
ep_members = parse_family_members(resp_ep.text)
print(f"Family members ({len(ep_members)} 筆):")
for pid in ep_members:
    print(f"  {pid}")


# ── Test 2: US 號碼（不帶 kind code）─────────────────────────────────────────

print()
print("=" * 60)
print("Test 2: US 號碼（US2019224161，不帶 kind code）")
print("=" * 60)

resp_us = client.family(
    "publication",
    epo_ops.models.Epodoc("US2019224161"),  # ← 不能帶 kind code
    None,
    ["biblio"]
)
print(f"Status: {resp_us.status_code}")
us_members = parse_family_members(resp_us.text)
print(f"Family members ({len(us_members)} 筆):")
for pid in us_members:
    print(f"  {pid}")


# ── Test 3: 確認 B2 有在 family 裡 ───────────────────────────────────────────

print()
print("=" * 60)
print("Test 3: 確認 Cromolyn B2 在 family 裡")
print("=" * 60)

target_b2 = ["US10561635B2", "US10583113B2"]
found = [pid for pid in target_b2 if pid in us_members]
missing = [pid for pid in target_b2 if pid not in us_members]

if found:
    print(f"✅ 在 family 裡找到：{found}")
if missing:
    print(f"⚠️  未找到：{missing}（可能在另一個 A1 的 family 下）")

print()
print("結論：family API 可以用，正確呼叫方式是 Epodoc(number) 不帶 kind code")