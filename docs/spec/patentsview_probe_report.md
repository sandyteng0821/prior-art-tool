# USPTO PatentsView API — Probe Report
**Date**: 2026-06-01  
**Context**: Drug repurposing prior art radar (EPO OPS 為主，評估 PatentsView 作補強)  
**Probe 方式**: 文件全讀（官方 endpoint dictionary、release notes、migration notices）+ 第三方實作確認；`search.patentsview.org` 本身 sandbox DNS 不通，但關鍵發現如下說明。

---

## ⚠️ CRITICAL FINDING（先看這個）

> **PatentsView PatentSearch API（`search.patentsview.org`）已於 2026-03-20 關閉，並遷移至 USPTO Open Data Portal（`data.uspto.gov`）。**

這不是本 probe 的假設——這是現實狀態。以下引用：
- USPTO 官方公告（2026-03-18）：PatentsView will migrate to the Open Data Portal on March 20, 2026
- GitHub `riemannzeta/patent_mcp_server`（2026-03-21 commit）：「The PatentsView API (search.patentsview.org) was **shut down on March 20, 2026**」，所有 14 個 patentsview_* 工具回傳 `API_UNAVAILABLE`
- `patentsview.org` 主頁現在顯示遷移說明，API endpoint 頁面已改成靜態告示

**這個 probe 的完整結論因此分兩層：**
1. PatentsView PatentSearch API（關閉前的能力盤點）— 仍有參考價值，因為 ODP 是其繼承者
2. 現在實際可用的替代方案：USPTO PPUBS + ODP bulk data

---

## Probe 1：API 能力盤點

### 1.1 架構（關閉前）

| 項目 | 內容 |
|------|------|
| Base URL | `https://search.patentsview.org/api/v1` |
| 狀態 | **已關閉（2026-03-20）** |
| 認證 | `X-Api-Key` header，key 需向官方申請（免費） |
| Rate limit | **45 calls/minute per key**，超過回 HTTP 429，header `Retry-After` 給 backoff 秒數 |
| 資料更新頻率 | 季更（每季一次，最後一版含 data through 2025-09-30 或 2025-12-31） |
| 授權 | Creative Commons CC-BY 4.0，**可商用** |
| Legacy API | 2025-05-01 已退役，PatentSearch API 為唯一版本（然後也關了） |

### 1.2 Endpoint 全表（PatentSearch API，關閉前）

**Granted Patent（已授權專利）**

| Endpoint | Response Key | 說明 |
|----------|-------------|------|
| `GET/POST /api/v1/patent/` | `patents` | 核心 metadata：title, abstract, date, CPC, IPC, assignee, inventor, citations... |
| `GET/POST /api/v1/patent/us_patent_citation/` | `us_patent_citations` | US 專利被引用關係 |
| `GET/POST /api/v1/patent/us_application_citation/` | `us_application_citations` | US 申請案被引用 |
| `GET/POST /api/v1/patent/foreign_citation/` | `foreign_citations` | 外國文件被引用 |
| `GET/POST /api/v1/patent/other_reference/` | `other_references` | 非專利文獻引用（NPL） |
| `GET/POST /api/v1/patent/rel_app_text/` | `rel_app_texts` | Related application 描述文字 |
| `GET/POST /api/v1/g_claim/` | `g_claims` | **Claim 全文**（每個 claim 一筆，含 claim_sequence, claim_text, claim_dependent） |
| `GET/POST /api/v1/g_detail_desc_text/` | `g_detail_desc_texts` | **Detailed Description 全文** |
| `GET/POST /api/v1/g_brf_sum_text/` | `g_brf_sum_texts` | Brief Summary 全文 |
| `GET/POST /api/v1/g_draw_desc_text/` | `g_draw_desc_texts` | Drawing Description 全文 |

**Pre-grant Publication（公開申請案）**

| Endpoint | Response Key | 說明 |
|----------|-------------|------|
| `GET/POST /api/v1/publication/` | `publications` | 申請案 metadata |
| `GET/POST /api/v1/pg_claim/` | `pg_claims` | 申請案 claim 全文 |
| `GET/POST /api/v1/pg_detail_desc_text/` | `pg_detail_desc_texts` | 申請案 description 全文 |
| `GET/POST /api/v1/pg_brf_sum_text/` | `pg_brf_sum_texts` | 申請案 brief summary |
| `GET/POST /api/v1/pg_draw_desc_text/` | `pg_draw_desc_texts` | 申請案 drawing desc |
| `GET/POST /api/v1/publication/rel_app_text/` | `rel_app_texts` | 申請案 related app 文字 |

**Disambiguation / Common Entities**

| Endpoint | 說明 |
|----------|------|
| `/api/v1/assignee/` | 受讓人（經消歧義） |
| `/api/v1/inventor/` | 發明人（經消歧義） |
| `/api/v1/location/` | 地點 |
| `/api/v1/cpc_group/`, `/api/v1/cpc_class/`, etc. | CPC 分類層級 |
| `/api/v1/ipc/` | IPC |
| `/api/v1/uspc_mainclass/`, `/uspc_subclass/` | USPC |

### 1.3 Query 語法（POST body）

```python
# 基本結構
payload = {
    "q": {"patent_id": "9457009"},          # filter
    "f": ["patent_id", "patent_title",       # fields to return
          "patent_abstract"],
    "o": {"size": 100}                       # options (max 1000)
}
# POST https://search.patentsview.org/api/v1/patent/
# Header: X-Api-Key: {your_key}

# Claims query
payload_claims = {
    "q": {"patent_id": "9457009"},
    "f": ["patent_id", "claim_sequence", "claim_text",
          "claim_dependent", "exemplary"]
}
# POST https://search.patentsview.org/api/v1/g_claim/

# Text search（drug repurposing use case）
payload_text_search = {
    "q": {"_and": [
        {"_text_any": {"patent_abstract": "pemirolast idiopathic pulmonary fibrosis"}},
        {"_gte": {"patent_date": "1990-01-01"}}
    ]},
    "f": ["patent_id", "patent_title", "patent_abstract",
          "patent_date", "cpc_current.cpc_group"]
}
```

**Query operator 支援**：`_eq`, `_neq`, `_gt`, `_gte`, `_lt`, `_lte`, `_text_any`, `_text_all`, `_text_phrase`, `_and`, `_or`, `_not`

---

## Probe 2：US Fulltext 是否真的拿得到？

### 2.1 Claim 結構

`g_claim` endpoint 的 schema（文件確認）：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `patent_id` | string | Patent number（例如 `"9457009"`） |
| `claim_sequence` | integer | 0-indexed 順序 |
| `claim_number` | string | 0-padded 5 位，e.g. `"00001"` |
| `claim_text` | text | 純文字，**無 XML tag** |
| `claim_dependent` | string | 依附的 claim sequence，independent 時為 NULL |
| `exemplary` | integer | 1 = exemplary claim |

EPO OPS 回傳格式對比：EPO 的 claims 是 XML（`<claim>` tag 含 `<claim-text>` 嵌套），PatentsView 是純文字 array。這是格式落差但不是缺失——純文字反而更容易直接餵 LLM。

### 2.2 Coverage 範圍

- **Granted patents**：1976 至今（`g_claim` / `g_detail_desc_text` endpoints）
- **Pre-grant publications**：2001 至今（`pg_claim` / `pg_detail_desc_text` endpoints）
- **1976 之前**：不在 database 內（USPTO bulk XML 從 1976 開始有 machine-readable 格式）
- **Acetaminophen 相關的老專利**（如 1950s-60s）：不會在這個 database 裡，但那些也在 EPO 公開領域
- **Ampicillin US 專利**（e.g. Beecham，1960s）：**同樣不在 1976 起的 database**

**重要 caveat**：根據 PatentsView release notes，text endpoint 最初（2023 年）只有 2023 年資料，後來 backfill 2005 以後。但 release notes 裡有一條：「Reparse of the 2005-2020 data for all text tables (brf_sum_text, claims, detail_desc_text, draw_desc_text)」——表示 2005+ 的 claims 是有的，但 1976-2004 的 claims 是否完整有待確認（bulk download 的 claims table 標注「for patent applications from 2005 and later」）。

**假設崩塌點**：你書面分析裡假設「PatentsView 補 US fulltext 缺口」——但這只對 2005 年以後的專利成立，且需要 API 可用（現在已關閉）。

### 2.3 跨資料源 patent ID 比對

| 系統 | ID 格式範例 |
|------|------------|
| PatentsView | `"9457009"`（純數字字串，無前綴） |
| EPO OPS | `US-9457009-B2`（country-number-kind） |
| 轉換邏輯 | 從 EPO 格式剝掉 `US-` 前綴和 `-B2` kind code → PatentsView patent_id |

PatentsView 的 `wipo_kind` 欄位可以重建 kind code，但不是主鍵的一部分。**ID 轉換邏輯是明確的，技術上可行**。

---

## Probe 3：Family / Cross-reference

### 3.1 PatentsView 有沒有 Family API？

**沒有獨立的 family API。** 提供的 cross-reference 機制是：

1. **`granted_pregrant_crosswalk`**（nested field in patent 和 publication）
   - patent → 其對應的 pre-grant application number 和 publication number
   - 這是 US-only 的 granted/pregrant 對應，**不是跨國家的 family**

2. **`us_related_documents`**：連結 continuation, division, CIP 等 US 內部繼承關係

3. **`foreign_priority`**：記錄外國優先權申請（country + application_id + filing_date）
   - 這可以間接識別「來自同一個外國優先權」的 family，但需要自行 group

4. **`pct_data`**：PCT 申請資訊（pct_doc_number, pct_371_date）

**結論**：PatentsView **無法**取代 EPO OPS 的 INPADOC family API。EPO INPADOC 是跨 73+ 國家的標準化 family 分組，PatentsView 只有 US-centric 的 related documents。Family 仍必須以 EPO OPS 為主。

---

## Probe 4：Drug Repurposing Query 的可行性

### 4.1 文字搜索能力

PatentsView 支援的文字搜索 operator（適用 `abstract`, `title`, `claim_text`, `description_text` 等 `text` 型別欄位）：

```json
{"_text_any": {"patent_abstract": "pemirolast fibrosis"}}
{"_text_phrase": {"patent_abstract": "idiopathic pulmonary fibrosis"}}
{"_text_all": {"claim_text": "acetaminophen analgesic"}}
```

還可以結合 CPC 分類過濾，對 drug repurposing 特別有用：
```json
{"_and": [
    {"_text_any": {"patent_abstract": "pemirolast"}},
    {"cpc_current.cpc_group": "A61K31/4706"}  // CPC for pemirolast class
]}
```

### 4.2 與 EPO OPS 的角色對比

| 功能 | EPO OPS | PatentsView（關閉前） |
|------|---------|----------------------|
| US fulltext query | ❌ 404 | ✅ 支援（2005+） |
| EP fulltext query | ✅ | ❌ US only |
| Family API | ✅ INPADOC | ❌ US-only crosswalk |
| 化合物名 text search | ✅（EP/WO） | ✅（US abstract/claims） |
| 直接 by patent number | ✅ | ✅ |
| 非 US 司法管轄 | ✅（部分） | ❌ 完全不含 |
| 資料免費可商用 | ✅ | ✅（CC-BY 4.0） |
| API 認證 | 免費 key | 免費 key |
| **現在是否可用** | **✅** | **❌ 已關閉** |

---

## Probe 5：現在實際可用的替代方案

PatentsView PatentSearch API 關閉後，相對應的替代路徑：

### 5.1 USPTO Patent Public Search（PPUBS）`ppubs.uspto.gov`

- **現狀**：仍在運行，是 PatFT/AppFT 的替代者
- **能力**：US 專利全文搜索、claims、description（1976 至今）
- **API 方式**：有隱式 REST API（`ppubs.uspto.gov` 的 JSON endpoint），無官方文件但第三方已實作（如 `patent-client` Python library）
- **認證**：無需 API key（但是 session-based，使用複雜）
- **對 pipeline 的影響**：PPUBS 的 API 是 unofficial，穩定性不保證

### 5.2 USPTO Open Data Portal（ODP）Bulk Data

- **現狀**：PatentsView 資料已遷移至 `data.uspto.gov`
- **能力**：bulk download（TSV），含 claims、description（同 PatentsView 原始資料）
- **API**：ODP 有 REST API（需申請 ODP API key，不同於 PatentsView key），但以 bulk download 為主
- **對 pipeline 的影響**：bulk 資料不適合做 on-demand query；需要本地建索引

### 5.3 PatentsView 關閉的影響評估

| 場景 | 評估 |
|------|------|
| 你的書面分析「EPO OPS + PatentsView 組合」 | **前提已不成立**：PatentSearch API 已關閉 |
| 臨時替代（PPUBS unofficial API） | 可以，但 unofficial，風險高 |
| ODP bulk data 建本地索引 | 技術上可行，但工程量大，不是「補強」而是「建第二個 DB」 |

---

## 能力對照表（更新版）

| 維度 | EPO OPS | PatentsView（2026-03 前） | PPUBS（現在） | ODP Bulk（現在） |
|------|---------|--------------------------|---------------|-----------------|
| US granted fulltext | ❌ 404 | ✅ g_claim / g_detail_desc_text | ✅ | ✅（需本地化） |
| EP/WO fulltext | ✅ | ❌ | ❌ | ❌ |
| Family（跨國） | ✅ INPADOC | ❌ | ❌ | ❌ |
| Drug name text search | ✅ | ✅ | ✅ | 需本地索引 |
| JSON REST API | ✅ | ✅（已關閉） | unofficial | partial |
| 資料新鮮度 | near-realtime | 季更 | near-realtime | 季更 |
| API 穩定性 | 穩定、有 SLA | 已關閉 | unofficial，不穩定 | 新平台，transitioning |
| 商用可行性 | ✅ | ✅（CC-BY 4.0） | ✅（USPTO 公共資料） | ✅ |
| 現在可直接用 | ✅ | ❌ | △（需 session hack） | △（需 bulk + 索引） |

---

## 商用授權分析

**結論：可以商用。**

依據：USPTO Terms of Use（https://www.uspto.gov/terms-use-uspto-websites ，最後更新 2026-01-26）。

### Copyright 條款（核心依據）

USPTO Terms of Use 的 Copyright information 段落明確指出：大部分 USPTO 產出的資料在美國境內屬於 public domain，可自由散布和複製，只「request」（非 require）加上 attribution。Patent information 段落進一步確認：patent 的文字和圖面通常不受著作權限制（37 CFR 1.71(d) & (e) 和 1.84(s) 的有限例外除外）。

**對本工具的意義**：claims text、description text、abstract、metadata 這些從 PPUBS/ODP 取得的資料，本身不受著作權保護，可用於商業產品。

### 使用限制（需注意）

**不可 bulk scrape 網站介面**：Terms of Use 的 Use of USPTO databases 段落禁止透過網站介面進行大量下載（會被封 IP）。但 on-demand 的個別 patent PDF 下載（如本工具的 use case：EPO OPS 找到 US patent → 按需下載單一 PDF）屬於正常使用，不違反此條款。如需 bulk data，USPTO 提供免費的 bulk data products。

**國際著作權保留**：USPTO 保留在國際上主張著作權的權利。但實務上從未行使——Google Patents、Lens.org、各商業 IP 平台都在使用 USPTO 資料，無已知訴訟或 takedown 案例。

### 各來源授權比較

| 來源 | 授權條件 | 商用？ | Attribution |
|------|---------|--------|-------------|
| **USPTO PPUBS / ODP** | US public domain + Terms of Use | ✅ | Requested（非強制） |
| **PatentsView** | CC-BY 4.0 | ✅ | **Required**（CC-BY 條件） |
| **EPO OPS** | EPO Terms & Conditions（fair use policy） | ✅（有條件） | Required |

### 實務建議

在工具的 output（CSV / Excel / 報告）footer 加一行：`Patent data source: United States Patent and Trademark Office (www.uspto.gov)`。技術上非法律義務，但 USPTO 明確 request 了，成本為零，且對下游使用者是透明度的展現。

---

## 已發現的限制 / 風險 / 假設崩塌

1. **假設崩塌（最重要）**：書面分析的「EPO OPS + PatentsView 組合」方案，其 PatentsView 端已失效。這不是技術債，是前提條件不成立。

2. **Fulltext coverage gap**：即使 PatentsView 還在，`g_claim` 的 claims 在 bulk data 明確標注「for patent applications from **2005** and later」。1976-2004 的 claims 透過 API 的完整性未確認——Ampicillin / Acetaminophen 的原始 US 授權專利（1950s-70s）根本不在 database 裡。

3. **Family API 不存在**：PatentsView 無法取代 EPO OPS INPADOC。這一點書面分析裡的描述可能過於樂觀（「可能甚至比 EPO OPS INPADOC 更精確」）。實際上是 US-only related documents，完全不同的概念。

4. **平台遷移噪音**：ODP 遷移剛發生（2026-03-20），目前「temporary interruptions」正在發生，新平台穩定性未知。

5. **ID 格式轉換**：EPO `US-9457009-B2` → PatentsView `9457009` 的轉換是 deterministic 的，但 kind code 對應不是一對一（一個 number 可能有多個 kind code 版本），需要處理。

6. **ODP API key 取得門檻對非美國人極高**（2026-06-01 實測）：ODP API 需透過 USPTO 帳號 + ID.me 身分驗證取得 API key。ID.me 對非美國人的驗證流程要求大量個人文件（護照、地址證明等），程序繁瑣。這實際上讓 ODP API 對台灣團隊而言**近乎不可用**——不是技術限制，是行政門檻。

7. **PPUBS PDF direct link 已失效**（2026-06-02 實測）：原文件記載的 `ppubs.uspto.gov/dirsearch-public/print/downloadPdf/{patent_number}` 路徑**不再有效**。PPUBS 現已改為 `ppubs.uspto.gov/api/pdf/downloadPdf/{number}?requestToken={JWT}`，需要 session token（JWT），無法從 patent number 直接推算。這意味著**無法自動化產生 PPUBS PDF 下載 URL**。Google Patents 頁面 URL（`patents.google.com/patent/{id}`）是目前唯一可 deterministic 生成、且頁面上提供 PDF 下載按鈕的路徑。

---

## 建議的下一步

### 結論傾向：「EPO OPS + Google Patents，不依賴 ODP API 或 PPUBS direct link」

原始書面分析的「EPO OPS + PatentsView 組合」方案中，PatentsView 已關閉、ODP API 非美國人拿不到 key、PPUBS PDF direct link 需要 session token 無法自動化。**三條 US fulltext 補強路全部不通或有重大障礙。**

**短期（現在可做）**：
1. **EPO OPS 繼續作主搜索 + family + EP/WO fulltext**——不動
2. **Google Patents 作為 US fulltext 補強的主要路徑**：`patents.google.com/patent/{CC}{number}{kind}` 是 deterministic URL，頁面提供 HTML 全文 + PDF 下載按鈕，不需認證，覆蓋全球。在 pipeline 的 output（CSV/Excel）中附上 Google Patents URL，使用者可直接點開閱讀全文或下載 PDF
3. 把「PatentsView 補強」「ODP API 整合」「PPUBS PDF direct link」都從短期 roadmap 移除

**中期（如果 pipeline 需要自動化 US fulltext extraction）**：
1. **Google Patents HTML scraping**：頁面結構穩定，claims / description 以 HTML section 呈現，可用 BeautifulSoup 抽取。但需注意 Google 的 rate limiting 和 Terms of Service
2. **USPTO bulk XML**：從 `bulkdata.uspto.gov` 下載 US granted patent XML（1976+），自建本地 claims 索引（SQLite/DuckDB）。工程量大但最穩定、不依賴任何 API
3. 持續觀察 ODP 是否推出 fulltext endpoint 或降低非美國人認證門檻

**不建議**：
- 依賴 PatentsView v2（全線 500）
- 自動化 PPUBS（session token + WAF + reCAPTCHA）
- 為單一 US fulltext 需求引入商業付費源

---

## 給定一個 Patent ID，各來源能拿到什麼？

以 `US-9457009-B2` 為例（acetaminophen formulation patent），假設你手上有 EPO 格式的 publication number。

### ID 轉換邏輯

```
EPO OPS 格式:    US-9457009-B2
                  ↓ 剝掉 country prefix + kind code
PatentsView ID:  9457009
ODP query field: applicationMetaData.patentNumber:9457009
Google Patents:  https://patents.google.com/patent/US9457009B2
PPUBS PDF URL:   ❌ 已失效（需 session token，無法自動生成）
```

### 各來源回傳能力矩陣

| 給定 patent ID 後能拿到的資料 | EPO OPS | ODP API | PPUBS PDF | PatentsView（已關閉） |
|-------------------------------|---------|---------|-----------|----------------------|
| **Title** | ✅ | ✅ | ✅（PDF 內） | ✅ |
| **Abstract** | ✅ | ✅ | ✅（PDF 內） | ✅ |
| **Filing date / Grant date** | ✅ | ✅ | ✅（PDF 內） | ✅ |
| **Inventor / Assignee** | ✅ | ✅ | ✅（PDF 內） | ✅（消歧義版） |
| **CPC / IPC classification** | ✅ | ❌ | ✅（PDF 內） | ✅ |
| **Claims 純文字（structured）** | ❌ US 404 | ❌ | ✅（需 PDF parse） | ✅ `g_claim` |
| **Description 純文字** | ❌ US 404 | ❌ | ✅（需 PDF parse） | ✅ `g_detail_desc_text` |
| **Brief summary 純文字** | ❌ US 404 | ❌ | ✅（需 PDF parse） | ✅ `g_brf_sum_text` |
| **Citations（US/foreign/NPL）** | ✅（部分） | ❌ | ✅（PDF 內） | ✅（結構化） |
| **Family（跨國 INPADOC）** | ✅ | ❌ | ❌ | ❌ |
| **US related docs（cont/div/CIP）** | 部分 | ✅ `continuityData` | ✅（PDF 內） | ✅ |
| **Prosecution history / transactions** | ❌ | ✅ | ❌ | ❌ |
| **File wrapper documents（OA, response）** | ❌ | ✅（PDF 下載） | ❌ | ❌ |
| **回傳格式** | XML | JSON | PDF binary | JSON |
| **需要額外 parse？** | XML parse | 直接用 | PDF → text | 直接用 |
| **認證** | 免費 key | 免費 ODP key（⚠️ 需 ID.me 驗證，非美國人門檻極高） | 無需 | 免費 PV key |
| **現在可用？** | ✅ | ✅ | ✅ | ❌ |

**關鍵結論**：要拿 US claims/description **純文字**，現在唯一穩定的路是 PPUBS PDF → parse。ODP API 的定位不是 fulltext，是 prosecution history。

---

## 可重現的 Query 範例（本機執行用）

### Source A：ODP Application API（✅ 現在可用）

**能拿到**：metadata + prosecution history + file wrapper documents
**拿不到**：claims/description 純文字（需另外下載 PDF 再 parse）
**認證**：需 ODP API key（免費，申請地址 `data.uspto.gov/key/myapikey`）
**覆蓋**：2001 以後的申請案

```python
import requests
import json

ODP_BASE = "https://api.uspto.gov/api/v1"
ODP_KEY = "YOUR_ODP_KEY"  # 申請：data.uspto.gov/key/myapikey
ODP_HEADERS = {"X-API-KEY": ODP_KEY, "Content-Type": "application/json"}

# ── A1: 用 patent number 查 metadata ──
# 注意：ODP query syntax 是 Solr-like，不是 PatentsView 的 JSON filter
patent_number = "9457009"

r = requests.post(
    f"{ODP_BASE}/patent/applications/search",
    headers=ODP_HEADERS,
    json={
        "q": f"applicationMetaData.patentNumber:{patent_number}",
        "fields": [
            "applicationNumberText",
            "applicationMetaData.patentNumber",
            "applicationMetaData.inventionTitle",
            "applicationMetaData.filingDate",
            "applicationMetaData.grantDate",
            "applicationMetaData.firstApplicantName",
            "applicationMetaData.firstInventorName",
            "applicationMetaData.applicantBag",
            "applicationMetaData.inventorBag",
            "applicationMetaData.applicationTypeCode",
        ],
        "limit": 5,
    },
)
data = r.json()
print(f"HTTP {r.status_code}")
print(f"Total found: {data.get('totalNumFound', 'N/A')}")
# Response key: patentFileWrapperDataBag
for app in data.get("patentFileWrapperDataBag", []):
    meta = app.get("applicationMetaData", {})
    print(f"  App#: {app.get('applicationNumberText')}")
    print(f"  Patent#: {meta.get('patentNumber')}")
    print(f"  Title: {meta.get('inventionTitle')}")
    print(f"  Filed: {meta.get('filingDate')}  Granted: {meta.get('grantDate')}")
    print(f"  Applicant: {meta.get('firstApplicantName')}")
    print(f"  Inventor: {meta.get('firstInventorName')}")

# ── A2: Drug repurposing text search（title 搜索） ──
# Solr syntax: 欄位名:值, AND/OR/NOT, "exact phrase", wildcard *
r = requests.post(
    f"{ODP_BASE}/patent/applications/search",
    headers=ODP_HEADERS,
    json={
        "q": 'applicationMetaData.inventionTitle:"pulmonary fibrosis"',
        "sort": "applicationMetaData.filingDate desc",
        "limit": 10,
        "fields": [
            "applicationNumberText",
            "applicationMetaData.patentNumber",
            "applicationMetaData.inventionTitle",
            "applicationMetaData.filingDate",
            "applicationMetaData.grantDate",
            "applicationMetaData.firstApplicantName",
        ],
    },
)
data = r.json()
print(f"\nTitle search 'pulmonary fibrosis': {data.get('totalNumFound', 0)} results")
for app in data.get("patentFileWrapperDataBag", [])[:5]:
    meta = app.get("applicationMetaData", {})
    print(f"  {meta.get('patentNumber', 'pending')} | {meta.get('inventionTitle')}")

# ── A3: 拿 continuity（US related documents）──
# 用 application number（不是 patent number）查
app_number = "14412875"  # 替換成 A1 查到的 applicationNumberText
r = requests.get(
    f"{ODP_BASE}/patent/applications/{app_number}",
    headers=ODP_HEADERS,
)
app_data = r.json()
# continuityData 包含 parent/child continuation, division, CIP 關係
continuity = app_data.get("continuityData", {})
print(f"\nContinuity data for app {app_number}:")
print(json.dumps(continuity, indent=2, default=str)[:1000])

# ── A4: 拿 file wrapper document list（prosecution history）──
r = requests.get(
    f"{ODP_BASE}/patent/applications/{app_number}/documents",
    headers=ODP_HEADERS,
)
docs = r.json()
print(f"\nFile wrapper documents: {len(docs)} items")
# 每個 document 有 documentIdentifier, mailRoomDate, documentCode, documentDescription
# 常見 documentCode: CLM (claims), SPEC (spec), OA (office action), REM (response)
for d in docs[:5] if isinstance(docs, list) else []:
    print(f"  [{d.get('documentCode')}] {d.get('documentDescription')} ({d.get('mailRoomDate')})")
```

**ODP 的限制（probe 過程中確認）**：
- 無 claims/description 純文字 endpoint，只能透過 document list → 下載個別 PDF
- Query 只支援 `applicationMetaData.*` 欄位（title, applicant, inventor, dates, status 等），**不能搜 abstract 或 claims 內文**
- 無 CPC/IPC 查詢欄位
- 2001 以前的申請案不在 database 裡
- 無 403 body message：沒帶 key 就是 `{"message":"Forbidden"}`，不告訴你為什麼

---

### Source B：PPUBS PDF Download + Parse（❌ Direct link 已失效）

**2026-06-02 更新**：PPUBS PDF 下載 URL 已改為需要 session token（JWT），格式為 `ppubs.uspto.gov/api/pdf/downloadPdf/{number}?requestToken={JWT}`。原先的 `dirsearch-public/print/downloadPdf/` 路徑不再有效。**以下 script 無法直接執行**，保留作為參考（如果未來 USPTO 恢復 public direct link）。

**手動操作仍可行**：在 `ppubs.uspto.gov/basic/` 搜尋 patent number → 點 "PDF" → 瀏覽器下載。但無法自動化。

```python
import requests
import re
import subprocess
import json

# ── B1: 直接下載 patent PDF（不需 session、不需 key）──
patent_number = "9457009"
pdf_url = f"https://ppubs.uspto.gov/dirsearch-public/print/downloadPdf/{patent_number}"

r = requests.get(pdf_url, timeout=30)
print(f"PDF download: HTTP {r.status_code}, size={len(r.content)} bytes")

if r.status_code == 200:
    pdf_path = f"/tmp/US{patent_number}.pdf"
    with open(pdf_path, "wb") as f:
        f.write(r.content)
    print(f"Saved to {pdf_path}")

# ── B2: PDF → text extraction（需 pip install pymupdf）──
# pip install pymupdf --break-system-packages
import fitz  # pymupdf

doc = fitz.open(pdf_path)
full_text = ""
for page in doc:
    full_text += page.get_text() + "\n"
doc.close()

print(f"\nExtracted text length: {len(full_text)} chars")
print(f"Preview (first 500 chars):\n{full_text[:500]}")

# ── B3: 從全文中切出 Claims section ──
# USPTO patent PDF 的 claims section 通常以 "What is claimed is:" 或
# "I/We claim:" 開頭，以 drawing descriptions 或文件結尾結束
claims_match = re.search(
    r"(?:What is claimed is|I claim|We claim|The invention claimed is)[:\s]*\n(.*?)(?:\nDESCRIPTION OF|BRIEF DESCRIPTION|\Z)",
    full_text,
    re.DOTALL | re.IGNORECASE,
)
if claims_match:
    claims_text = claims_match.group(1).strip()
    print(f"\n=== CLAIMS (first 1000 chars) ===")
    print(claims_text[:1000])
else:
    # fallback: 找 "Claims" header
    claims_match = re.search(
        r"\bCLAIMS?\b\s*\n(.*?)(?:\nDESCRIPTION|\nDRAWINGS|\Z)",
        full_text,
        re.DOTALL | re.IGNORECASE,
    )
    if claims_match:
        claims_text = claims_match.group(1).strip()
        print(f"\n=== CLAIMS (fallback, first 1000 chars) ===")
        print(claims_text[:1000])
    else:
        print("\n⚠️ Claims section not found by regex — manual inspection needed")

# ── B4: 從全文中切出 individual claims ──
# 每個 claim 通常是 "1. A method of..." 格式
individual_claims = re.findall(
    r"(\d+)\.\s+(.*?)(?=\n\d+\.\s+|\Z)",
    claims_text if claims_match else "",
    re.DOTALL,
)
print(f"\nParsed {len(individual_claims)} individual claims")
for num, text in individual_claims[:3]:
    clean = " ".join(text.split())[:150]
    print(f"  Claim {num}: {clean}...")

# ── B5: PPUBS text search（需 browser / session，這裡只建構 URL）──
# PPUBS 支援 URL deep-link 做 search（但結果要透過 browser 看）
# Field codes: TI=title, AB=abstract, CLM=claims, SPEC=description, AS=assignee
search_query = '("pemirolast" AND "pulmonary fibrosis").CLM.'
search_url = (
    f"https://ppubs.uspto.gov/pubwebapp/external.html"
    f"?q={requests.utils.quote(search_query)}"
    f"&db=USPAT,US-PGPUB"
)
print(f"\nPPUBS search URL (open in browser):\n  {search_url}")
# 注意：這個 URL 需要在瀏覽器中開啟，第一次會彈 Terms of Service modal
# 不適合 headless script，但可以用 Playwright/Selenium 自動化
```

**PPUBS 的限制（2026-06-02 更新）**：
- ❌ **PDF direct link 已失效**：`dirsearch-public/print/downloadPdf/` 不再有效，改為需要 JWT session token
- PDF → text 的品質取決於 patent 格式（1976-2001 的老格式 PDF 文字抽取較差）
- PPUBS text search 是 browser-based SPA，有 AWS WAF + reCAPTCHA 保護；headless 自動化需要 `--verified` session
- 非官方 backend JSON endpoint（`/dirsearch-public/searches/...`）已被 USPTO 封鎖
- informal rate limit 約 ~6-10 queries/min，超過觸發 WAF challenge
- **結論：PPUBS 不適合作為 pipeline 的自動化資料源**

---

### Source C：PatentsView PatentSearch API（❌ 已關閉，留作歷史參考）

以下 script **現在無法執行**（`search.patentsview.org` 返回 500 或 DNS 不解析）。
保留的價值：(1) 如果 ODP 日後推出相容 API，query 語法可能類似；(2) 如果 PatentsView v2 某天恢復穩定。

```python
import requests
import json

PV_BASE = "https://search.patentsview.org/api/v1"
PV_KEY = "YOUR_PATENTSVIEW_KEY"
PV_HEADERS = {"X-Api-Key": PV_KEY, "Content-Type": "application/json"}

# ── C1: Patent metadata lookup ──
r = requests.post(
    f"{PV_BASE}/patent/",
    headers=PV_HEADERS,
    json={
        "q": {"patent_id": "9457009"},
        "f": ["patent_id", "patent_title", "patent_date",
              "patent_abstract", "wipo_kind",
              "cpc_current.cpc_group_id",
              "assignees_at_grant.assignee_organization",
              "inventors.inventor_name_first",
              "inventors.inventor_name_last"]
    }
)
print("Patent metadata:", json.dumps(r.json(), indent=2))

# ── C2: Claims（structured, 每 claim 一筆）──
r = requests.post(
    f"{PV_BASE}/g_claim/",
    headers=PV_HEADERS,
    json={
        "q": {"patent_id": "9457009"},
        "f": ["patent_id", "claim_sequence", "claim_number",
              "claim_text", "claim_dependent", "exemplary"]
    }
)
claims = r.json().get("g_claims", [])
print(f"\nTotal claims: {len(claims)}")
for c in claims[:3]:
    dep = c.get("claim_dependent")
    dep_str = f"dep on #{dep}" if dep else "INDEPENDENT"
    print(f"  [{c['claim_number']}] {dep_str} | {c['claim_text'][:120]}...")

# ── C3: Detailed Description（全文，可能很大）──
r = requests.post(
    f"{PV_BASE}/g_detail_desc_text/",
    headers=PV_HEADERS,
    json={
        "q": {"patent_id": "9457009"},
        "f": ["patent_id", "description_text", "description_length"]
    }
)
desc = r.json().get("g_detail_desc_texts", [])
if desc:
    print(f"\nDescription: {desc[0].get('description_length')} chars")
    print(f"Preview: {desc[0]['description_text'][:300]}...")

# ── C4: Brief Summary ──
r = requests.post(
    f"{PV_BASE}/g_brf_sum_text/",
    headers=PV_HEADERS,
    json={
        "q": {"patent_id": "9457009"},
        "f": ["patent_id", "summary_text"]
    }
)
summary = r.json().get("g_brf_sum_texts", [])
if summary:
    print(f"\nBrief summary: {summary[0]['summary_text'][:300]}...")

# ── C5: Family / cross-reference ──
r = requests.post(
    f"{PV_BASE}/patent/",
    headers=PV_HEADERS,
    json={
        "q": {"patent_id": "9457009"},
        "f": ["patent_id",
              "granted_pregrant_crosswalk.application_number",
              "granted_pregrant_crosswalk.document_number",
              "us_related_documents.related_doc_number",
              "us_related_documents.related_doc_type",
              "foreign_priority.foreign_country_filed",
              "foreign_priority.foreign_application_id",
              "pct_data.pct_doc_number"]
    }
)
print("\nFamily/crosswalk:", json.dumps(r.json(), indent=2))

# ── C6: Drug repurposing text search ──
r = requests.post(
    f"{PV_BASE}/patent/",
    headers=PV_HEADERS,
    json={
        "q": {"_and": [
            {"_text_any": {"patent_abstract": "pemirolast fibrosis"}},
            {"_gte": {"patent_date": "1990-01-01"}}
        ]},
        "f": ["patent_id", "patent_title", "patent_date", "patent_abstract"],
        "o": {"size": 10}
    }
)
results = r.json().get("patents", [])
print(f"\nPemirolast+fibrosis: {len(results)} results")
for p in results:
    print(f"  {p['patent_id']} ({p['patent_date']}): {p['patent_title']}")
```

---

## 三種來源在 pipeline 中的角色定位

```
                     ┌──────────────────────────────────┐
  Drug aliases       │       你的 Pipeline                │
  + Indication  ───▶ │                                    │
                     │  ┌─────────────┐                   │
                     │  │ EPO OPS     │ ◄── 主要來源        │
                     │  │ (search +   │    EP/WO 全文       │
                     │  │  family +   │    INPADOC          │
                     │  │  metadata)  │    US metadata      │
                     │  └──────┬──────┘    (no US ft)       │
                     │         │                            │
                     │         │ patent IDs found            │
                     │         ▼                            │
                     │  ┌──────────────┐                    │
                     │  │ Google       │ ◄── 補強：          │
                     │  │ Patents      │    全球 fulltext     │
                     │  │ (HTML page + │    URL（output 附連結）│
                     │  │  PDF button) │    無需認證          │
                     │  └──────────────┘                    │
                     │                                    │
                     │  無需任何認證 ✓                       │
                     │  ───▶ SQLite cache ───▶ LLM         │
                     └──────────────────────────────────┘

  ┌──────────────┐  ┌──────────────┐
  │ ODP API      │  │ PPUBS        │
  │ (prosecution │  │ (PDF direct  │
  │  history)    │  │  link 需 JWT)│
  └──────────────┘  └──────────────┘
  ⚠️ ID.me 門檻       ❌ 無法自動化
```

| 來源 | 角色 | 何時 call | 認證 | 優先級 |
|------|------|----------|------|--------|
| **EPO OPS** | 主搜索 + family + EP/WO fulltext | 每次 search | 免費 key（已有） | 🟢 主要 |
| **Google Patents** | 全球 fulltext 補強（output 附 URL，或 scrape HTML） | EPO OPS 找到 patent 但 fulltext 404 時 | **無需認證** | 🟢 短期加入 |
| **PPUBS** | 手動 PDF 下載（pipeline 外） | 需要原始 PDF image 時 | 無需認證（手動） | 🟡 手動輔助 |
| **ODP API** | prosecution history、continuity | 深入分析單一 US patent 時 | ODP key（⚠️ ID.me） | 🔴 暫不整合 |

---

*Report generated: 2026-06-01（updated 2026-06-02: PPUBS PDF direct link 確認失效 + Google Patents 升為主要補強路徑）*
*Probe status: 文件分析完整；ODP API key 門檻經實際註冊驗證；PPUBS PDF direct link 經實測確認需 JWT session token；Google Patents URL 為目前唯一可 deterministic 生成的 fulltext 入口*
