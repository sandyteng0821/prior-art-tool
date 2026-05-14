# Task B — Formulation Evidence Extraction (Analysis Layer)

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。  
> 前置條件：Task A 必須完成，`formulation_snippets` 欄位已存在且有資料。  
> 完成後請更新 `docs/architecture.md`。

---

## Context

Task A 完成後，每筆專利在 DB 中有 `formulation_snippets`（formulation 相關句子的 JSON list）。

Task B 的目標是在這些 snippets 上做兩層分析：
1. Rule-based：比對已知 excipient list，不需要 LLM
2. LLM（optional）：只吃 snippets，不吃全文，用於語意推論

---

## Goal

新增 excipient extraction 邏輯，讓系統能從 `formulation_snippets` 產出結構化的 formulation evidence。

---

## Files to Modify

- `modules/llm_analyzer.py`（更新 LLM prompt）
- 新增 `modules/formulation_extractor.py`（rule-based logic）

---

## Required Changes

### 1. 新增 `modules/formulation_extractor.py`

```python
import json
from modules.patent_store import get_formulation_snippets

# 已知 excipient list（可從 config 或獨立 JSON 檔案載入）
DEFAULT_EXCIPIENT_LIST = [
    "lactose", "microcrystalline cellulose", "MCC",
    "polymethacrylate", "starch", "magnesium stearate",
    "ammonium alginate", "povidone", "HPMC",
    # ... 補充完整 list
]

def extract_excipients_rule(patent_id: str, excipient_list: list[str] = None) -> dict:
    """
    Rule-based excipient extraction from formulation_snippets.
    Returns structured evidence dict.
    """
    if excipient_list is None:
        excipient_list = DEFAULT_EXCIPIENT_LIST

    snippets = get_formulation_snippets(patent_id)
    found = set()

    for s in snippets:
        s_lower = s.lower()
        for exc in excipient_list:
            if exc.lower() in s_lower:
                found.add(exc)

    return {
        "patent_id": patent_id,
        "excipients_found": sorted(found),
        "snippet_count": len(snippets),
        "method": "rule-based",
        "confidence": "high" if found else "none"
    }


def extract_excipients_llm(patent_id: str, drug: str) -> dict:
    """
    LLM-based extraction. Only called when rule-based is insufficient.
    Feeds snippets only (not full claims).
    """
    snippets = get_formulation_snippets(patent_id)
    if not snippets:
        return {"patent_id": patent_id, "excipients_found": [], "method": "llm", "confidence": "none"}

    # LLM call — see llm_analyzer.py update below
    from modules.llm_analyzer import analyze_formulation_snippets
    return analyze_formulation_snippets(patent_id, snippets, drug)
```

### 2. 更新 `modules/llm_analyzer.py`

新增 function（不動現有 FTO 分析邏輯）：

```python
def analyze_formulation_snippets(patent_id: str, snippets: list[str], drug: str) -> dict:
    """
    LLM analysis using snippets only. Does NOT use full claims text.
    """
    snippet_text = "\n".join(f"- {s}" for s in snippets)

    prompt = f"""Given the following formulation-related sentences from a patent:

{snippet_text}

Extract:
1. Active pharmaceutical ingredient (API) — confirm if {drug} is mentioned
2. Excipients mentioned (list all)
3. Dosage form (tablet, capsule, solution, etc.)

Respond in JSON format:
{{
  "api_confirmed": true/false,
  "excipients": ["excipient1", "excipient2"],
  "dosage_form": "tablet"
}}
"""

    # use existing LLM call pattern in this file
    response = ...  # existing LLM call
    return {
        "patent_id": patent_id,
        "method": "llm",
        **parse_json_response(response)
    }
```

---

## Expected Outcome

- `extract_excipients_rule(patent_id)` 可對任何有 snippets 的專利快速跑出 excipient list
- `extract_excipients_llm(patent_id, drug)` 作為補充，只在 rule-based 沒抓到東西時呼叫
- 現有 FTO 分析邏輯完全不受影響

---

## Verification

Task B 完成後驗證：

```python
from modules.formulation_extractor import extract_excipients_rule

# CN103830190A claim 1 含 lactose + MCC
result = extract_excipients_rule("CN103830190A")
print(result)
# 預期：excipients_found 包含 lactose 和 MCC

# EA004311B1 detailed description 含 lactose + MCC
result = extract_excipients_rule("EA004311B1")
print(result)
```

---

## Non-Goals

- 不重寫現有 FTO 分析邏輯
- 不建 co-occurrence table（這是 Task B 完成後的下一步）
- 不修改 output_writer.py（另外規劃）
