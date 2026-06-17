# tools/debug_scoring — Design Spec (Draft)

> 狀態：Draft，待討論
> 起源：US9415051B1 調查（2026-06-17）中需要重現 LLM scoring 行為

---

## 目的

給定一個 patent_id，重現 pipeline 的 Stage 1 screening + Stage 2 analysis，
完整顯示 LLM 收到的 input 和回傳的 structured output，方便 debug
「這筆為什麼判 Low/Medium/High」。

**不是** inspect_patent 的替代品。定位互補：

| | inspect_patent | debug_scoring |
|---|---|---|
| 看什麼 | 資料層（DB 內容、snippet、alias 命中）| 判斷層（LLM input/output/reasoning）|
| 花不花錢 | 不花（純 DB 讀取）| 花（LLM API call）|
| 典型用途 | 確認資料有沒有進 DB | 確認 LLM 為什麼給這個 risk |
| 搭配使用 | 先跑 inspect 確認資料 OK | 再跑 debug_scoring 看判斷邏輯 |

---

## CLI 介面

```bash
# 基本用法：用指定 config 跑 Stage 1 + Stage 2
python3 -m tools.debug_scoring US9415051B1 \
    --config configs/pemirolast_ipf_v3.py

# 只跑 Stage 2（跳過 screening，直接精讀）
python3 -m tools.debug_scoring US9415051B1 \
    --config configs/pemirolast_ipf_v3.py \
    --stage 2

# 換 model
python3 -m tools.debug_scoring US9415051B1 \
    --config configs/pemirolast_ipf_v3.py \
    --screening-model gpt-4o-mini \
    --analysis-model gpt-4o

# Prompt override（A/B test rubric）
python3 -m tools.debug_scoring US9415051B1 \
    --config configs/pemirolast_ipf_v3.py \
    --rubric-override scratch/rubric_v2.txt

# 不花錢的 dry-run（只印 LLM 會收到的 input，不實際呼叫）
python3 -m tools.debug_scoring US9415051B1 \
    --config configs/pemirolast_ipf_v3.py \
    --dry-run
```

### 必要參數
- `patent_id`：要 debug 的專利 ID
- `--config`：**必填**，指定使用哪個 config 檔。不用當前 config.py，避免汙染。

### 可選參數
- `--stage {1,2,both}`：預設 both。`1` = 只跑 screening，`2` = 跳過 screening 直接精讀。
- `--screening-model`：覆蓋 config 裡的 SCREENING_MODEL
- `--analysis-model`：覆蓋 config 裡的 ANALYSIS_MODEL
- `--rubric-override <file>`：用指定檔案的內容替換 ANALYSIS_SYSTEM prompt
- `--dry-run`：只印 input，不呼叫 LLM（零成本，用來確認 claims 有沒有被正確帶入）
- `--compare <file>`：同時讀一份 rubric override，跑兩次 Stage 2 並排比較（A/B test）

---

## 架構決策：如何處理 module 依賴

### 問題
llm_analyzer.py 在 module level 就建 LLM client，import 即 crash（無 API key）
或使用錯誤的 config（當前 config.py 可能是別的專案）。

### 方案比較

| 方案 | 優點 | 缺點 |
|------|------|------|
| A. 完全不 import llm_analyzer，自己定義 schema + prompt | 零依賴，指定 config 容易 | Schema/prompt 可能跟 production drift |
| B. 從 llm_analyzer import schema class | schema 保證同步 | import 觸發 module-level init → crash |
| C. 把 schema 抽到 llm_schemas.py（改 module）| 最乾淨，schema 單一來源 | 要改現有 module 結構 |
| D. 方案 A + drift detection | 零依賴 + 有同步保障 | 多一點 code，但風險可控 |

### 建議：先 A，加 drift detection（方案 D）

理由：
1. 現階段不動 module，符合「只做調查不改 code」的原則
2. drift detection 機制：用 AST parse llm_analyzer.py，
   抽出 ScreeningResult 和 PatentAnalysis 的 field names + types，
   跟 debug_scoring 自己的 schema 比對。不匹配時印 WARNING。
3. 未來穩定後再做方案 C（把 schema 抽出來），debug_scoring 那時改成
   from llm_schemas import ... 就好，改動很小。

### Config 載入機制

```python
# 不改 sys.modules，不汙染全域 config
import importlib.util

def load_config(config_path):
    spec = importlib.util.spec_from_file_location("_debug_config", config_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# 用法
cfg = load_config("configs/pemirolast_ipf_v3.py")
print(cfg.TARGET_DRUG)      # "Pemirolast（肥大細胞穩定劑 / TGF-beta 抑制劑）"
print(cfg.ANALYSIS_MODEL)   # "gpt-5"
```

這樣每次指定不同 config 都是獨立的 module instance，
不影響 workstation 上的 config.py。

---

## 輸出格式

```
══════════════════════════════════════════════════════════════════════
debug_scoring: US9415051B1
Config: configs/pemirolast_ipf_v3.py
Target: Pemirolast 吸入劑治療特發性肺纖維化 (IPF)
══════════════════════════════════════════════════════════════════════

── DB State ──────────────────────────────────────────────────────────
  title:              Use of pemirolast
  abstract:           313 chars
  claims:             1229 chars
  examples_extracted: 38732 chars
  status:             Unknown

── Stage 1: Screening ────────────────────────────────────────────────
  Model:    gpt-5-mini
  Input:    title + abstract (313 chars)
  
  [LLM Response]
    is_relevant: True
    quick_risk:  Medium
  
  → Proceed to Stage 2

── Stage 2: Analysis ─────────────────────────────────────────────────
  Model:    gpt-5
  Input:    title + abstract + claims[:3000] (1229 chars) + status
  Rubric:   default (ANALYSIS_SYSTEM from config)
  
  [Claims sent to LLM - first 500 chars]
    Claims (7) The invention claimed is: 1. A method for the
    treatment of airway hyperresponsiveness, which method comprises:
    administering pemirolast...

  [LLM Response]
    is_target_drug:  True
    delivery_routes: Oral
    indications:     Asthma, COPD, IPF
    claim_scope:     Treatment of AHR using oral pemirolast ≥350mg/day
    fto_risk:        Low
    gap_opportunity: Inhalation route not claimed
    reasoning:       Claim 1 is oral-only; target is inhaled IPF.

══════════════════════════════════════════════════════════════════════
```

### --compare 模式的輸出（A/B test）

```
── Stage 2: A/B Comparison ───────────────────────────────────────────

  [A] Default rubric          │ [B] scratch/rubric_v2.txt
  ────────────────────────────┼────────────────────────────
  fto_risk:    Low            │ fto_risk:    High
  reasoning:   Claim 1 oral   │ reasoning:   Claim 5 IPF
  indications: AHR, COPD, IPF │ indications: AHR, COPD, IPF
```

---

## 不做的事（scope 限制）

- 不改 llm_analyzer.py 或其他 production module
- 不寫入 DB（純讀取）
- 不寫入 output/（不產生 CSV）
- 不做批量分析（一次只處理一筆 patent_id）
  - 如果要批量，用 pipeline（main.py）
- 不做 diskcache 操作（那是 inspect_patent 的事）

---

## 檔案位置

```
tools/
  inspect_patent.py    ← 現有，看資料層
  debug_scoring.py     ← 新增，看判斷層
```

---

## 依賴

- langchain_openai（已在 venv）
- pydantic（已在 venv）
- modules.patent_store（只用 get_by_id，不觸發 LLM init）
- dotenv（讀 .env 拿 API key）

---

## 開發順序

1. 基本功能：--config + Stage 1 + Stage 2 structured output
2. --dry-run（印 input 不呼叫 LLM）
3. --stage 選擇
4. --rubric-override
5. --compare（A/B test）
6. drift detection（比對 llm_analyzer schema）
7. --screening-model / --analysis-model override

Step 1-3 就能處理 90% 的 debug 場景。
Step 4-5 是給專家討論 rubric 時用的。
Step 6-7 是防禦性的。

---

## Validation Cases（從實際調查記錄）

工具寫完後必須通過這些 cases。來源是 2026-06-17 的 US9415051B1 調查。
詳細報告：`findings_US9415051B1_risk_underscoring.md`

### Case 1: US9415051B1 — Pemirolast × IPF

**背景：** pipeline 多次給 Low，專家認為應 High。
根因是三層問題疊加（diskcache 繞過 DB / screening 只看 abstract / rubric 把 dependent claim 降權）。

**前提：** `--config configs/pemirolast_ipf_v3.py`

| 指令 | 預期結果 | 對應調查中的測試 |
|------|---------|---------------|
| `--dry-run` | claims 顯示 1229 chars，內容包含 "idiopathic pulmonary fibrosis" | keyword probe 驗證 |
| `--stage 1` | is_relevant=True, quick_risk=Medium | Test A（gpt-4o-mini 驗證）|
| `--stage 2`（default rubric）| fto_risk=Low 或 Medium, reasoning 提到 oral-only / claim 1 | Test B + 0617 rerun 結果 |
| `--stage 2 --rubric-override rubric_v2.txt` | fto_risk=High, reasoning 提到 claim 5 / IPF | Test D |
| `--compare rubric_v2.txt` | 並排顯示 Low/Medium vs High 的差異 | Test B vs Test D 對比 |

**注意：** LLM 有隨機性（即使 temperature=0），比對的是 risk level 和 reasoning 方向，不是逐字一致。gpt-5 傾向給 Low（嚴格遵守 independent claim 原則），gpt-4o 傾向給 Medium。

### rubric_v2.txt 的內容（Test D 驗證過的版本）

在 ANALYSIS_SYSTEM prompt 中修改兩處：
1. 「重點分析 independent claim，而非 dependent claims」
   → 「分析所有 claims（independent 和 dependent），不要只看 independent claim」
2. 新增第 5 條規則：
   「若任何 claim（含 dependent claim）明確提及目標適應症，
   且活性成分為目標藥物，即使給藥途徑不同，也應判定 High —
   因為同藥同適應症的前案構成 novelty/obviousness 威脅。」

### 未來新增 case 的標準

每次碰到「pipeline 判斷跟專家不一致」的案例，調查完後都加一筆 validation case。
這些 cases 同時是 regression test（確保工具正確）和 prompt engineering 的 benchmark。
