# Task A — Formulation Snippet Extraction (Data Layer)

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。  
> 完成後請更新 `docs/architecture.md`。

---

## Context

Prior Art Tool 現有架構：`Query → Fetch → Store → Analyze → Output`

現有 DB schema 已有 `examples_extracted`（description 的 Examples 段落）。
這個 task 新增 `formulation_snippets`，是不同的欄位，**不取代** `examples_extracted`。

兩者差異：
- `examples_extracted`：切 Examples 段落（整段），用於 FTO 一般分析
- `formulation_snippets`：從 claims + description 全文切句子，用於 formulation evidence

---

## Goal

在 fetch 階段新增 snippet extraction，讓每筆專利存入 DB 時同時帶有 formulation 相關句子。
**不改 query strategy，不改 analyzer，不存 description 全文。**

---

## Files to Modify

- `modules/patent_fetcher.py`
- `modules/patent_store.py`

---

## Required Changes

### 1. `modules/patent_fetcher.py`

新增 function：

```python
import re

def _extract_formulation_snippets(text: str, drug_aliases: list[str]) -> list[str]:
    """
    從 text 中切出 formulation 相關句子。
    條件：句子同時包含 drug alias 和劑型關鍵字。
    """
    KEYWORDS = [
        "composition", "formulation", "comprises",
        "excipient", "tablet", "capsule", "carrier"
    ]

    sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text)
    snippets = []

    for s in sentences:
        s_lower = s.lower()
        has_drug = any(alias.lower() in s_lower for alias in drug_aliases)
        has_keyword = any(k in s_lower for k in KEYWORDS)
        if has_drug and has_keyword:
            snippets.append(s.strip())

    return snippets[:20]
```

在 fetch pipeline 中呼叫（claims 優先，description 補充）：

```python
# drug_aliases 從 config 取得
from config import DRUG_ALIASES

snippets = []
snippets += _extract_formulation_snippets(claims, DRUG_ALIASES)
if description:
    snippets += _extract_formulation_snippets(description, DRUG_ALIASES)
snippets = snippets[:30]  # hard cap
```

### 2. `modules/patent_store.py`

新增 DB 欄位：

```sql
ALTER TABLE patents ADD COLUMN formulation_snippets TEXT;
```

更新 `upsert_patent()` 加入此欄位（存為 JSON string）：

```python
import json
# 在 upsert 的 dict 中加入：
"formulation_snippets": json.dumps(snippets)
```

新增 helper function：

```python
def get_formulation_snippets(patent_id: str) -> list[str]:
    # query patents table by patent_id
    row = ...  # existing query pattern
    return json.loads(row["formulation_snippets"] or "[]")
```

---

## Expected Outcome

- 每筆新抓的專利在 DB 中有 `formulation_snippets` 欄位
- 欄位內容是 JSON list，每個元素是一個句子
- 舊有專利此欄位為 NULL（可之後用 backfill script 補）
- `examples_extracted` 欄位完全不受影響

---

## Verification

Task A 完成後用以下方式驗證：

```python
from modules.patent_store import get_formulation_snippets

snippets = get_formulation_snippets("CN103830190A")
print(snippets)
# 預期：包含 ampicillin + lactose/MCC 相關句子
```

也可直接查 DB：

```sql
SELECT patent_id, formulation_snippets
FROM patents
WHERE formulation_snippets IS NOT NULL
LIMIT 5;
```

---

## Non-Goals

- 不改 query strategy
- 不改 llm_analyzer.py（Task B 的工作）
- 不存 description 全文
- 不取代 examples_extracted
