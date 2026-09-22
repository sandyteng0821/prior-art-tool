# Task Q — Prompt 抽換 + EPO baseline 分析路徑（經 JSONL 導出）

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 前置條件：無（#1 是地基，其餘依賴 #1）。截斷 bug 根因待 grep（見 Non-Goals）。
> 完成後請更新 `docs/architecture.md`。

---

## Context

工具目前有兩條分析路徑，最終都呼叫 `llm_analyzer.analyze_patent`（Phase 4）
與 `output_writer`（Phase 5）：

```
EPO 主流程 (main.py)      config → fetch(含 family expansion) → DB
                          → Phase 4 rule → Phase 5 → Phase 4 LLM → Phase 5
JSONL 補充 (analyze_jsonl) GP/GPSS scraper → JSONL → Phase 4 → Phase 5
```

三來源分工：EPO 負責「發現」（family expansion 最全、meta data 最完整），
GP/GPSS 負責「content」（EPO 的 US/CN/JP fulltext 回 404，多數只有 abstract）。
既定 flow：EPO 發現完整集 → 拿 id 去 GP/GPSS 補 content → 重跑分析。

前置 probe 結果（`scratch/probe_search_log.py`、`probe_keloid_db_source.py`，
唯讀，蟹足腫 project）：

```
search_log 覆蓋率     : 45.2%  ← family expansion 不進 log（語意如此，非缺陷）
project 值           : DB 存成 30 字截斷 '..._(Keloi'，與 config TARGET_PRODUCT 不符
種子 + family        : 277 + 203 = 480 筆（42% 來自 family）
baseline content     : 480 筆中 claims 只有 115 (24%)，365 筆無 claims
```

兩個痛點：
1. **prompt 不可抽換**：`main.py` 無換 prompt 接口；`analyze_jsonl` 有
   `_patch_analysis_chain` 能換 → 兩路能力不對等，「碰巧一致」靠沒人 override
   維持。且 human template 定義兩處，改欄位要手動同步。
2. **缺 EPO baseline 路徑**：想在去 GP/GPSS 補 content 前，用 DB 現有 EPO content
   先跑一次當 baseline，目前只能走 main.py 完整流程（會重打 EPO API）。

---

## Goal

1. 讓 prompt 成為可抽換、單一來源的一級能力，`main.py` 與 `analyze_jsonl` 共用同一
   機制（兩路一致由架構保證，非自律）。
2. 新增「DB → 導出 id+content 成 JSONL」工具，讓 EPO content 不經 GP/GPSS 即可走
   analyze_jsonl 現有入口跑 baseline（讀法 X：source 差異收斂到 JSONL 上游，
   analyze_jsonl 不改）。

---

## Files to Modify

- `modules/llm_analyzer.py`（新增 `ANALYSIS_HUMAN_TEMPLATE` + `rebuild_analysis_chain`）
- `main.py`（新增 `--rubric-override`）
- 新增 `tools/export_db_to_jsonl.py`（DB → JSONL，唯讀）
- （可選）抽 `_load_rubric` 至 `modules/`，`analyze_jsonl` 改用共用 rebuild
- 新增 `tests/test_prompt_consistency.py`

---

## Required Changes

### 1. `modules/llm_analyzer.py` — prompt 單一來源 + 抽換接口

不動現有 FTO 分析邏輯，只加 template 常數與 rebuild function：

```python
# 新增：human template 單一來源（取代 analysis_prompt 的 inline 字串，
#       也取代 analyze_jsonl._patch_analysis_chain 裡重抄的那份）
ANALYSIS_HUMAN_TEMPLATE = (
    "標題：{title}\n\n摘要：{abstract}\n\n請求項：{claims}\n\n法律狀態：{status}"
)

# 改：analysis_prompt 引用常數
analysis_prompt = ChatPromptTemplate.from_messages([
    ("system", ANALYSIS_SYSTEM),
    ("human", ANALYSIS_HUMAN_TEMPLATE),
])

# 新增：prompt 抽換的唯一接口
def rebuild_analysis_chain(system_prompt: str) -> None:
    """用新 system prompt 重建 analysis_chain。human template 仍用單一來源。"""
    global analysis_chain
    if not USE_LLM:
        raise RuntimeError("rebuild_analysis_chain 需要 USE_LLM=True（rule mode 無 prompt 可換）")
    patched = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", ANALYSIS_HUMAN_TEMPLATE),
    ])
    analysis_chain = patched | analysis_llm.with_structured_output(PatentAnalysis)
```

### 2. `main.py` — 支援 `--rubric-override`

透過 #1 接口讓 production flow 也能換 prompt：

```python
# run_pipeline() 加參數；CLI 加 --rubric-override FILE
# --rubric-override 隱含 USE_LLM=True（rule mode 傳此 flag 應報錯）

def run_pipeline(rubric_path: str | None = None):
    ...
    if rubric_path:
        from modules.llm_analyzer import rebuild_analysis_chain
        rebuild_analysis_chain(load_rubric(rubric_path))  # load_rubric 見 #4
    # 其餘不變：fetch → analyze_patent → save_results
```

### 3. `tools/export_db_to_jsonl.py` — DB → JSONL 導出（讀法 X 核心）

唯讀 DB（`mode=ro`）。兩段撈法 + project 前綴匹配：

```python
# 兩段撈法（probe 實證：42% 來自 family，只篩 search_log 會漏）
def collect_ids(conn, project_full: str) -> set[str]:
    # project 前綴匹配：DB 存 30 字截斷值，--project 收完整值取前 N 字比對
    prefix = project_full[:PROJECT_TRUNCATE_LEN]  # PROJECT_TRUNCATE_LEN = 30
    # 防呆：比對到 >1 個 distinct project 要 raise，不默默選一個
    projs = conn.execute(
        "SELECT DISTINCT project FROM search_log WHERE project LIKE ? || '%'",
        (prefix,),
    ).fetchall()
    if len(projs) != 1:
        raise ValueError(f"前綴匹配到 {len(projs)} 個 project，需更精確：{[p[0] for p in projs]}")

    seeds = {r[0] for r in conn.execute(
        "SELECT DISTINCT patent_id FROM search_log WHERE project = ?", (projs[0][0],)
    )}
    ph = ",".join("?" * len(seeds))
    family = {r[0] for r in conn.execute(
        f"SELECT patent_id FROM patents WHERE family_of IN ({ph})", tuple(seeds)
    )}
    return seeds | family

# 導出：欄位改名讓 analyze_jsonl.jsonl_to_patent_dicts 原樣吃
#   DB patent_id → requested_id；examples_extracted → full_text
def row_to_jsonl_record(row: dict) -> dict:
    return {
        "requested_id":    row["patent_id"],
        "title":           row["title"] or "",
        "abstract":        row["abstract"] or "",
        "claims":          row["claims"] or "",
        "full_text":       row["examples_extracted"] or "",
        "publication_date": row["year"] or "",
        "expiration_date": row["expiry_date"] or "",
    }

# CLI: --project（完整值）、--db、--out。輸出摘要：種子/family 各幾筆 + content 品質
```

### 4.（可選）抽 `_load_rubric` 共用 + 消 analyze_jsonl monkey-patch

若 #2 需要 `load_rubric` 且不想在 `main.py` 重抄：

```python
# modules/rubric.py（或放 llm_analyzer）
def load_rubric(path: str, cfg) -> str:
    text = Path(path).read_text(encoding="utf-8")
    for k in ("TARGET_DRUG", "TARGET_ROUTE", "TARGET_INDICATION"):
        text = text.replace("{" + k + "}", getattr(cfg, k))
    return text

# analyze_jsonl：移除 _patch_analysis_chain，改呼叫 llm_analyzer.rebuild_analysis_chain
#   → human template 不再兩處定義（消漂移）
```

---

## Expected Outcome

- `main.py --rubric-override FILE` 與 `analyze_jsonl --rubric-override FILE` 走**同一個**
  `rebuild_analysis_chain`，prompt 由架構保證一致。
- `tools/export_db_to_jsonl.py --project "<完整 TARGET_PRODUCT>"` 對蟹足腫導出
  **480 筆**（277 種子 + 203 family）JSONL，可直接餵 `analyze_jsonl` 跑 baseline。
- 現有 FTO 分析邏輯、analyze_jsonl 的 JSONL 入口完全不受影響。

---

## Verification

```python
# #1 prompt 同源（回歸測試，零成本）
import modules.llm_analyzer as a
assert a.analysis_prompt.messages[1].prompt.template == a.ANALYSIS_HUMAN_TEMPLATE

# #3 導出筆數對齊 probe（277 + 203 = 480）
#   python3 tools/export_db_to_jsonl.py --project "Empagliflozin 外用製劑治療蟹足腫 (Keloid)" \
#           --out data/keloid_baseline.jsonl
#   預期輸出摘要：種子 277 / family +203 / 總 480
#   再用 analyze_jsonl --dry-run 讀，確認筆數一致
#   python3 scripts/analyze_jsonl.py --input data/keloid_baseline.jsonl \
#           --config configs/empagliflozin_keloid.py --dry-run

# #3 前綴匹配防呆：故意傳過短前綴應 raise（比對到 >1 project）

# sanity：DB 撈 3–5 筆真實蟹足腫 patent，rule mode 走 main 與 analyze_jsonl
#   兩種呼叫方式，斷言輸出 dict 逐欄相同
```

---

## Non-Goals

- **不修 project 值 30 字截斷根因**（全 DB bug，19 project 全中）。本 task 以前綴匹配
  繞過；修復另開 ticket，需先 grep 確認寫入點（`[:30]` 或 `log_search` 呼叫端）。
- **不讓 analyze_jsonl 讀 DB**（讀法 X：改由導出工具產 JSONL，analyze_jsonl 不改）。
- **不動現有 FTO 分析邏輯**（`analyze_patent` 兩段式、rule_based_analyze 不變）。
- **不做 #4 的 baseline 篩選工具**（baseline 跑過、看 Phase 5 輸出格式後另規劃：
  依「無 claims + High/Medium」挑 id 餵 GP/GPSS）。
- **不斷言 LLM 輸出一致**（回歸測試只證結構上 prompt 同源，LLM 有隨機性）。
