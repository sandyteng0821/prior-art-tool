# Prior Art Search Tool
**Drug Repurposing Patent Analyzer** — 自動化 prior art 搜尋與 FTO 風險評估工具

---

## 目的

確認藥物再定位專案（如 Roflumilast 鼻噴劑 × SCA）的專利障礙，自動化：
1. 產生 EPO CQL 搜尋字串
2. 從 EPO OPS 批次抓取專利（免費、無商用限制）
3. 規則評分或 LLM 萃取結構化特徵 + FTO 風險評估
4. 輸出 Gap 分析矩陣（CSV / Excel）

---

## Repo 架構

```
prior_art_tool/
│
├── .env                        # API keys（不進 git）
├── .env.example                # 範本
├── .gitignore
├── requirements.txt
├── README.md
│
├── config.py                   # 所有參數集中管理，換專案只改這裡
│
├── modules/
│   ├── __init__.py
│   ├── query_builder.py        # Module 1：CQL 搜尋字串產生器
│   ├── patent_fetcher.py       # Module 2：EPO OPS API 抓資料 + examples 解析
│   ├── patent_store.py         # Module 3：本地 SQLite 專利庫
│   ├── llm_analyzer.py         # Module 4：規則評分 / LangChain LLM 分析
│   └── output_writer.py        # Module 5：DataFrame 整理、CSV/Excel 輸出
│
├── cache/
│   ├── epo/                    # diskcache：短期 API 回應快取
│   └── patents.db              # SQLite：永久本地專利庫
│
├── output/                     # 分析結果輸出目錄
│   └── gap_analysis_YYYYMMDD_HHMM.csv
│
├── backfill_examples.py        # 補抓已存專利的 examples
├── test_without_epo.py         # 手動貼文字測試分析邏輯
├── test_from_pdf.py            # PDF 解析測試（備用）
└── main.py                     # 入口：串接所有 module
```

---

## 各檔案職責

| 檔案 | 職責 | 會動到的情境 |
|---|---|---|
| `config.py` | 換藥物/適應症/分析模式時**只改這裡** | 每次換新專案 |
| `query_builder.py` | 根據 config 產生 EPO CQL 字串 | 需要新搜尋策略時 |
| `patent_fetcher.py` | EPO OPS API、分頁抓取、B1 auto-fetch、examples 解析 | 換 API 來源或修解析路徑時 |
| `patent_store.py` | SQLite 本地專利庫，跨專案查詢 | 新增查詢功能時 |
| `llm_analyzer.py` | 規則評分 / 兩段式 LLM 分析，由 USE_LLM 切換 | 調整評分邏輯時 |
| `output_writer.py` | 排序、篩選、存 CSV + Excel（含顏色） | 調整輸出格式時 |
| `main.py` | 只負責串流程，邏輯不在這裡 | 盡量不動 |

---

## 快速開始

```bash
# 1. 建立虛擬環境
python -m venv venv && source venv/bin/activate

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 設定 API keys
cp .env.example .env
# 編輯 .env，填入 EPO_CONSUMER_KEY 和 EPO_CONSUMER_SECRET
# 若使用 LLM 模式，另外填入 OPENAI_API_KEY

# 4. 執行
python3 main.py
```

輸出在 `output/gap_analysis_YYYYMMDD_HHMM.csv` 和 `.xlsx`。

---

## .env.example

```
# EPO OPS（申請：https://developers.epo.org）
EPO_CONSUMER_KEY=your_consumer_key_here
EPO_CONSUMER_SECRET=your_consumer_secret_here

# 僅 USE_LLM=True 時需要
OPENAI_API_KEY=sk-...
```

---

## 分析模式切換

在 `config.py` 設定 `USE_LLM`：

```python
USE_LLM = False  # 規則評分（免費，無需 API key）
USE_LLM = True   # 兩段式 LLM 分析（需要 OPENAI_API_KEY）
```

| 模式 | 費用 | 準確度 | 適合情境 |
|---|---|---|---|
| `USE_LLM = False` | 免費 | 中，透明可解釋 | 初期探索、預算有限 |
| `USE_LLM = True` | 每次幾十台幣 | 高，理解語意 | 正式分析、精讀階段 |

---

## 搜尋策略說明

`query_builder.py` 目前有四種策略，全部使用 `ta=`（標題 + 摘要）搜尋：

| Strategy | CQL 格式 | 預估筆數 | 目的 |
|---|---|---|---|
| A | `ta=Roflumilast AND (pn=EP OR pn=US)` | 127 | 所有提到 Roflumilast 的 EP/US 專利 |
| D | `ta=Roflumilast AND pn=EPB` | 23 | EP granted，claims 和 examples 最完整 |
| F | `ta="cognitive impairment" AND ta="PDE4"` | 12 | 認知障礙 × PDE4，確保不漏同機制專利 |
| G | `ta=spinocerebellar` | 200 | SCA 相關所有專利，不限藥物 |

所有策略加上 `pd within "2000 2024"` 年份過濾，EPO 單次上限 100 筆，超過自動分頁抓取。

---

## 抓取邏輯

每筆專利的查詢優先順序：

```
本地 DB (patents.db) → 有就直接回傳（不打 API）
        ↓ 沒有
EPO OPS API → title / abstract / claims / description
        ↓
_parse_examples() → 從 description 切出 Examples 區塊
        ↓
upsert_patent() → 存入本地 DB
        ↓
若為 A1/A2 → 自動嘗試抓對應 B1（granted 版本）
```

---

## EPO OPS 資料抓取限制

| 專利類型 | title/abstract | claims | description/examples |
|---|---|---|---|
| EP granted (EPB) | ✅ | ✅ | ✅ |
| EP 申請案 (A1/A2) | ✅ | ❌ | 部分有 |
| US 申請案 (A1) | ✅ | ❌ | ❌ |
| US granted (B1/B2) | ✅ | 部分有 | 部分有 |
| WO/AU/CN/MX | 部分有 | ❌ | ❌ |

**EPB 是 claims 和 examples 最完整的來源。** 抓到 A1/A2 時會自動嘗試抓對應的 B1。

---

## 本地專利庫查詢

```python
from modules.patent_store import search_examples, search_claims, stats

# DB 統計
print(stats())

# 查有 nasal 相關 examples 的專利
results = search_examples("nasal")

# 查 chitosan 配方出現在哪些 claims
results = search_claims("chitosan")
```

---

## 補抓 examples

```bash
python3 backfill_examples.py
```

對 DB 裡 `examples_extracted` 為空的專利重新嘗試抓取 description 並切出 examples。

---

## 輸出欄位說明

| 欄位 | 說明 |
|---|---|
| `patent_id` | 專利唯一識別碼 |
| `title` | 專利標題 |
| `year` | 公告年份 |
| `status` | Active / Expired / Unknown |
| `is_target_drug` | 是否提及 Roflumilast 或 PDE4i |
| `delivery_routes` | 給藥途徑清單 |
| `indications` | 適應症清單 |
| `fto_risk` | **High / Medium / Low**（排序依據） |
| `gap_opportunity` | 本專利未涵蓋的空白區域 |
| `reasoning` | 評分理由（規則模式：命中關鍵字類別；LLM 模式：語意分析） |

Excel 輸出有顏色標示：🔴 High / 🟡 Medium / 🟢 Low。

---

## 換專案只需改 config.py

```python
# 目標產品描述
TARGET_PRODUCT = "Roflumilast 鼻噴劑治療小腦萎縮症 (SCA)"

# 搜尋關鍵字
DRUG_ALIASES   = ["Roflumilast", "Daliresp", "Daxas", "B9302-107"]
MECHANISMS     = ["PDE4 inhibitor", "phosphodiesterase 4", "cAMP enhancer"]
FORMULATIONS   = ["nasal", "intranasal", "nose-to-brain", "nasal spray"]
INDICATIONS    = ["spinocerebellar ataxia", "SCA", "cerebellar ataxia"]

# 搜尋範圍
SEARCH_YEAR_RANGE = "2000 2024"
FETCH_SIZE        = 200

# 分析模式
USE_LLM = False   # False = 免費規則評分，True = LLM 分析

# 規則評分關鍵字（USE_LLM=False 時使用）
RULE_DRUG_KEYWORDS       = ["roflumilast", "pde4", "phosphodiesterase 4"]
RULE_ROUTE_KEYWORDS      = ["nasal", "intranasal", "nose-to-brain"]
RULE_INDICATION_KEYWORDS = ["spinocerebellar", "ataxia", "cerebellar"]
RULE_CNS_KEYWORDS        = ["neurodegenerat", "cerebellum", "purkinje"]
```

> 注意：`query_builder.py` 的 Strategy F/G 目前仍有寫死的關鍵字，換專案時需要一併修改。

---

## 注意事項

- 此工具定位是**雷達**，不是法律意見。High/Medium risk 的專利仍需人類專利工程師做 Claim Construction。
- EPO OPS 每週流量上限 3.5 GB，`cache/` 目錄自動快取避免重複消耗。
- 重跑 `main.py` 時，已存入 `patents.db` 的專利走 `[DB hit]` 不重打 API。
- Claims 截斷預設 3000 字元，可在 `config.py` 的 `CLAIMS_MAX_CHARS` 調整。
- `RULE_DRUG_KEYWORDS` 等規則評分關鍵字目前在 `llm_analyzer.py` 中定義，尚未移進 `config.py`（待 refactor）。