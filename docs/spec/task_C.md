# Task C — Fix Claims Fetch + Sentence Splitter

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。  
> 前置條件：Task A 完成。  
> 完成後請更新 `docs/architecture.md`。

> **Post-implementation note (added after completion):**
> 本 spec 的兩個 bug diagnosis 在實作時被推翻：
> - Bug 1：`_fetch_claims` 不是 bug，是 EPO data licensing 限制（US/CN
>   granted 沒 fulltext 授權）。EP granted 用既有 code 就能取回。
> - Bug 2：splitter 沒問題，claim 1 的 `a)/b)` 結構被完整保留。
>   真實的 bug 是 keyword 比對太死：`"comprises"` 無法 substring-match
>   `"comprising"` 或 `"comprised"`。
>
> 實際修法：`KEYWORDS` 裡 `"comprises"` → `"compris"`（一字之差，
> 涵蓋三種動詞變形）。Spec 提議的 splitter 重寫、Epodoc fallback 等
> 改動均未採用。完整診斷過程見對應 chat。
>
> 本 spec 保留原樣以記錄 spec/diagnosis/implementation 三者落差，
> 作為 LLM 協作範本資料集的一部分。

---

## Context

Two related bugs discovered during Task A validation (Acetaminophen × formulation evidence):

**Bug 1: `_fetch_claims` returns empty (404 on /epodoc/claims)**
- Claims text is missing for many patents
- Directly affects `claims`, `examples_extracted`, and `formulation_snippets` coverage
- Example: `EP2089013B1` had empty claims before re-fetch

**Bug 2: Regex sentence splitter fails on multi-clause claim structures**
- `_extract_formulation_snippets` uses regex to split text into sentences
- Fails on patent claim format with `a) / b)` enumeration style
- Result: even when claims text exists, snippets come back empty
- Example: `EP2089013B1` claims contain acetaminophen + "preparation" but `formulation_snippets = []`

Both bugs together mean `formulation_snippets` is unreliable for patents with complex claim structures.

---

## Goal

Fix both bugs in `patent_fetcher.py` so that:
1. Claims are fetched successfully for EP granted patents (EPB)
2. `_extract_formulation_snippets` correctly handles multi-clause claim structures

---

## Files to Modify

- `modules/patent_fetcher.py`

---

## Required Changes

### Bug 1: Fix `_fetch_claims` 404

Investigate why `/epodoc/claims` returns 404 for some patents.

Likely causes to check:
- Wrong endpoint format (epodoc vs docdb)
- Kind code included when it shouldn't be
- Need fallback: if claims endpoint fails, extract claims from full description fetch

Suggested fallback pattern:
```python
def _fetch_claims(patent_id: str) -> str:
    try:
        # existing claims fetch
        ...
    except Exception:
        # fallback: fetch full text and extract claims section
        full_text = _fetch_full_text(patent_id)
        return _extract_claims_section(full_text) or ""
```

### Bug 2: Fix sentence splitter for patent claim structures

Current regex fails on:
```
1. An oral dose... wherein the dose is comprised of
a) a first analgesic agent...
b) a second analgesic agent...
```

Replace the sentence splitter in `_extract_formulation_snippets` with one that handles:
- Standard sentences ending in `.`
- Numbered claims (`1.`, `2.`, `3.`)
- Sub-clauses (`a)`, `b)`, `c)`)
- Semicolons as clause separators (common in patent claims)

Suggested approach — split on multiple patterns:
```python
def _split_patent_text(text: str) -> list[str]:
    # Split on: sentence end, numbered claims, semicolons
    chunks = re.split(r'(?:\.\s+(?=[A-Z0-9])|\.\s*\n|;\s*\n?|(?<=\))\s*\n)', text)
    # Clean and filter empty
    return [c.strip() for c in chunks if len(c.strip()) > 20]
```

---

## Expected Outcome

- Claims text available for EP granted patents
- `formulation_snippets` correctly populated for patents with `a) / b)` claim structures
- Re-running `_extract_formulation_snippets` on `EP2089013B1` claims returns non-empty list

---

## Verification

```python
from modules.patent_fetcher import _extract_formulation_snippets

# EP2089013B1 claims (paste actual claims text)
claims = """1. An oral dose for use in mitigating or treating pain, wherein the dose is comprised of
a) a first analgesic agent consisting of an effective amount of ibuprofen...
b) a second analgesic agent consisting of an effective amount of acetaminophen..."""

snippets = _extract_formulation_snippets(claims, ["acetaminophen", "paracetamol"])
print(snippets)
# Expected: non-empty list containing acetaminophen-related sentences
```

Also verify via DB after re-fetch:
```sql
SELECT patent_id, formulation_snippets
FROM patents
WHERE patent_id = 'EP2089013B1';
```

---

## Non-Goals

- 不動 `patent_store.py`
- 不動 `llm_analyzer.py`
- 不改 snippet 的篩選邏輯（drug alias × keyword filter），只修斷句
- 不處理 KR/CN/EA 的 description 缺失問題（不同問題）
