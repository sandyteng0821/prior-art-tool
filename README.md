# Prior Art Search Tool
**Drug Repurposing Patent Analyzer** — 自動化 prior art 搜尋與 FTO 風險評估工具

---

## 目的

確認藥物再定位專案（如 Roflumilast 鼻噴劑 × SCA）的專利障礙，自動化：
1. 產生 EPO CQL 搜尋字串
2. 從 EPO OPS 批次抓取專利（免費、無商用限制）
3. 用 LLM 萃取結構化特徵 + FTO 風險評分
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
├── config.py                   # 藥物/劑型/適應症參數，集中管理
│
├── modules/
│   ├── __init__.py
│   ├── query_builder.py        # Module 1：CQL 搜尋字串產生器
│   ├── patent_fetcher.py       # Module 2：EPO OPS API 抓資料 + examples 解析
│   ├── patent_store.py         # Module 5：本地 SQLite 專利庫
│   ├── llm_analyzer.py         # Module 3：LangChain + Pydantic schema
│   └── output_writer.py        # Module 4：DataFrame 整理、CSV/Excel 輸出
│
├── cache/
│   ├── epo/                    # diskcache：短期 API 回應快取
│   └── patents.db              # SQLite：永久本地專利庫
│
├── output/                     # 分析結果輸出目錄
│   └── gap_analysis_YYYYMMDD_HHMM.csv
│
├── backfill_examples.py        # 補抓已存專利的 examples
├── test_without_epo.py         # 手動貼文字測試 LLM 分析
├── test_from_pdf.py            # PDF 解析測試（備用）
└── main.py                     # 入口：串接所有 module
```

---

## 各檔案職責

| 檔案 | 職責 | 會動到的情境 |
|---|---|---|
| `config.py` | 換藥物/適應症時**只改這裡** | 每次換新專案 |
| `query_builder.py` | 根據 config 產生 EPO CQL 字串 | 需要新搜尋策略時 |
| `patent_fetcher.py` | 呼叫 EPO OPS API，解析 title/abstract/claims/examples | 換 API 來源或修解析路徑時 |
| `patent_store.py` | SQLite 本地專利庫，跨專案查詢 | 新增查詢功能時 |
| `llm_analyzer.py` | Pydantic schema + 兩段式 LLM 分析 | 調整評分邏輯/欄位時 |
| `output_writer.py` | 排序、篩選、存 CSV + Excel | 調整輸出格式時 |
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
# 編輯 .env，填入三個 key

# 4. 執行
python3 main.py
```

輸出在 `output/gap_analysis_YYYYMMDD_HHMM.csv` 和 `.xlsx`。

---

## .env.example

```
OPENAI_API_KEY=sk-...

# EPO OPS（申請：https://developers.epo.org）
EPO_CONSUMER_KEY=your_consumer_key_here
EPO_CONSUMER_SECRET=your_consumer_secret_here
```

---

## 搜尋策略說明

`query_builder.py` 目前有四種策略：

| Strategy | CQL 格式 | 目的 |
|---|---|---|
| A | `ti=藥物名 AND (pn=EP OR pn=US)` | 最廣，確保不漏 |
| B | `ti=劑型 AND ti=ataxia AND (pn=EP OR pn=US)` | 劑型 × 適應症 |
| C | `ti=機制 AND ti=ataxia AND (pn=EP OR pn=US)` | 機制類比，捕捉同類藥物 |
| D | `ti=藥物名 AND pn=EPB` | EP granted，claims 和 examples 最完整 |

所有策略都加上 `pd within "2000 2024"` 年份過濾。

---

## EPO OPS 資料抓取限制

| 專利類型 | title/abstract | claims | description/examples |
|---|---|---|---|
| EP granted (EPB) | ✅ | ✅ | ✅ |
| EP 申請案 (A1/A2) | ✅ | ❌ | 部分有 |
| US 申請案 (A1) | ✅ | ❌ | ❌ |
| US granted (B1/B2) | ✅ | 部分有 | 部分有 |
| WO/AU/CN/MX | 部分有 | ❌ | ❌ |

**結論：EPB 是 examples 分析的最佳來源，Strategy D 專門針對這個設計。**

---

## 本地專利庫查詢

```python
from modules.patent_store import search_examples, search_claims, stats

# 查有 nasal 相關 examples 的專利
results = search_examples("nasal")

# 查 chitosan 配方出現在哪些 claims
results = search_claims("chitosan")

# DB 統計
print(stats())
# → {'total_patents': 68, 'with_examples': N, ...}
```

---

## 補抓 examples

```bash
# 對 DB 裡已存的專利補抓 description 並切出 examples
python3 backfill_examples.py
```

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
| `reasoning` | LLM 給出風險評分的理由 |

Excel 輸出有顏色標示：🔴 High / 🟡 Medium / 🟢 Low。

---

## 費用控制

兩段式 LLM 設計：

1. `gpt-4o-mini` 初篩全部摘要，Low risk 直接過濾
2. `gpt-4o` 精讀 Medium / High 的完整 claims

省下約 60–70% API 費用。在 `config.py` 調整 `SCREENING_MODEL` 和 `ANALYSIS_MODEL`。

---

## 換專案只需改 config.py

```python
TARGET_PRODUCT = "Roflumilast 鼻噴劑治療小腦萎縮症 (SCA)"
DRUG_ALIASES   = ["Roflumilast", "Daliresp", "Daxas", "B9302-107"]
MECHANISMS     = ["PDE4 inhibitor", "phosphodiesterase 4", "cAMP enhancer"]
FORMULATIONS   = ["nasal", "intranasal", "nose-to-brain", "nasal spray"]
INDICATIONS    = ["spinocerebellar ataxia", "SCA", "cerebellar ataxia"]
SEARCH_ONLY_GRANTED = True
SEARCH_YEAR_RANGE   = "2000 2024"
```

---

## 注意事項

- 此工具定位是**雷達**，不是法律意見。High/Medium risk 的專利仍需人類專利工程師做 Claim Construction。
- EPO OPS 每週流量上限 3.5 GB，`cache/` 目錄自動快取避免重複消耗。
- 重跑 `main.py` 時，已存入 `patents.db` 的專利會走 `[DB hit]` 不重打 API。
- Claims 截斷預設 3000 字元，可在 `config.py` 的 `CLAIMS_MAX_CHARS` 調整。