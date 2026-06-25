# Patent Expiry Date — Session Summary

> 日期：2026-06-25
> 對話範圍：data source survey → EPO probe → fetch_dates tool → validation → precision boundary analysis
> 關聯：architecture.md Gap #5 / #8
> Committed：`tools/fetch_dates.py`

---

## 1. 背景

Bio team 需要 patent expiry date 做 drug repurposing 優先排序。
Pipeline 目前的 `status` 欄位回傳 `Unknown` fallback，`year` 欄位實際上是空字串（見 §4）。
同事提出 GreyB / Pharsight 作為可能的 data source，觸發了這次完整調查。

---

## 2. Data Source Survey

### 結論表

| Source | Format | 可整合？ | Expiry 精度 | Cost |
|--------|--------|---------|-----------|------|
| **EPO OPS biblio** | REST API (JSON) | ✅ 已整合 | Base term only (filing+20yr) | Free → €2,800/yr |
| **FDA Orange Book Data Files** | ZIP → tilde-delimited TXT | ✅ 可 script | 含 PTE（US NDA-listed only） | Free |
| **FDA PPIV List** | PDF | ⚠️ 需 PDF parse | 藥品層級 last qualifying patent | Free |
| **Google Patents** | Web UI / BigQuery | ⚠️ Web=手動, BQ=工程 | 含 Adjusted expiration (PTE/PTA) | Web=Free, BQ~$10/mo |
| **GreyB / Pharsight** | Web UI only | ❌ 無 API | 有到期清單 | Free (瀏覽) |
| **GreyB Elixir** (paid) | Web dashboard | ❌ 無 API | 有追蹤 | Enterprise 定價不透明 |
| **IntuitionLabs** | PDF report | ⚠️ 需 PDF parse | Drug-level US expiry | TBC |
| **Will's commercial DB** | TBC | TBC | TBC | 公司已付費 |
| **Orange Book Web UI** | Web | ❌ 手動查詢 | 含 PTE + PED | Free |

### GreyB / Pharsight 結論

無公開 API、無 bulk download、無 developer docs。底層資料來自 Orange Book 重新整理。
不可整合進 pipeline。適合手動瀏覽近期到期清單。

### Orange Book 可靠性

FDA 官方出版物，regulatory 層面是權威來源。但有結構性限制：
- FDA 角色是 "ministerial"——照藥廠提交的內容原樣列出，不獨立驗證
- FTC 2023-2025 累計挑戰 400+ 筆不當 listing（device patents、延遲 generic 競爭）
- **到期日本身是可靠的**（客觀計算），但「哪些 patent 被列在某個藥底下」可能有 over-listing
- 只含 unexpired patents（已過期的會移除）
- Maintenance fee 未繳導致的提前失效不會反映

### Orange Book 結構筆記

Orange Book 裡的 `U-802` 等是 **Patent Use Code**（FDA 自定義的用途分類），不是 patent ID。
Patent number 在 `Patent No` 欄位（例如 `7326708`），需補 `US` 前綴 + kind code 才能查 EPO。

---

## 3. Phase A Probe 結果

### Probe 工具

`scratch/probe_date_fields.py`（gitignored，一次性用途）

### Probe 1：EPO biblio endpoint — date fields per jurisdiction

**重大發現：US biblio 不是 404。**

| Jurisdiction | Patent ID | HTTP | publication_date | filing_date | priority_dates |
|---|---|---|---|---|---|
| EP B1 | EP2107907B1 | 200 ✅ | 20120201 | 20071218 | 20061220, 20070705, 20071218 |
| EP B1 | EP4138798B1 | 200 ✅ | 20250709 | 20210420 | 20200420, 20210323, 20210420 |
| WO A1 | WO2023073600A1 | 200 ✅ | 20230504 | 20221027 | 20211029 |
| **US B1** | **US9415051B1** | **200 ✅** | 20160816 | 20151124 | 20151023 |
| CN A | CN103830190A | 200 ✅ | 20140604 | 20121123 | 20121123 |
| EA B1 | EA004311B1 | 200 ✅ | 20040226 | 19990607 | 19980611, 19990607 |

**6/6 jurisdiction 全部 HTTP 200，date 欄位完整。**
這跟 2026-06-15 inspect_patent probe 的結論矛盾（當時 US 8/8 biblio 404）。
差異原因待確認，但實務上 biblio date fields 目前對所有 jurisdiction 可用。

Date 格式統一 `YYYYMMDD`（8 碼純數字）。

### Probe 2：Family API biblio — 帶 date 節點

```
family member top-level keys:
  ['@family-id', 'publication-reference', 'application-reference',
   'priority-claim', 'exchange-document']
```

`application-reference`（filing date）和 `priority-claim`（priority date）都在 family member 節點內。
`_fetch_and_store_family()` 已經拿到這些資料，只是目前沒 parse。
**Phase B 可以 piggyback on family fetch，不需要額外 API call。**

### Probe 3：Search result inline — 不帶 date

```
EP2443120A2:
  date (full string):        ← 空的
  all doc-id keys: ['@document-id-type', 'country', 'doc-number', 'kind']
```

Search result 的 `publication-reference` 裡沒有 `date` key。
`patent_fetcher.py` line 99 的 `doc_id.get("date", {}).get("$", "")[:4]` 拿到的是空字串。
**DB 裡的 `year` 欄位一直都是空的。** `--compare-db` 已驗證。

---

## 4. `year` 欄位空值問題

### 資料流追蹤

```
Search result → doc_id.get("date", {}).get("$", "")[:4] → year=""
    ↓
_get_or_fetch(patent_id, year="") → upsert_patent({"year": ""})
    ↓
_fetch_and_store_family() → patent_dict["year"] = year → 空字串
```

`year` 在 search → fetch 主路徑上一直是空字串。
不是 bug（search inline 確實不帶 date），但 Phase B 可以順便修正。

---

## 5. tools/fetch_dates.py（已 commit）

### 功能

- EPO biblio endpoint 查詢：publication_date / filing_date / priority_dates
- 支援單筆、多筆、分號分隔（Espacenet paste）、--file
- `--expiry`：filing_date + 20 年粗估（base term only）
- `--compare-db`：對照 DB year 欄位
- `--json`：JSON 輸出供下游消費
- Rate limit：1 req/sec

### 使用範例

```bash
python3 -m tools.fetch_dates EP2107907B1
python3 -m tools.fetch_dates EP2107907B1 US9415051B1 CN103830190A --expiry
python3 -m tools.fetch_dates 'EP4138798A1;EP4138798B1;US2023157975A1'
python3 -m tools.fetch_dates EP2107907B1 --compare-db
python3 -m tools.fetch_dates EP2107907B1 --json --expiry
```

### Bug fixes applied before commit

1. **Priority country 空值**：epodoc format doc-id 沒有獨立 country 節點，改為遍歷所有 doc-id variants 收集 country
2. **Timeout error 訊息**：timeout / rate_limited 現在顯示乾淨的 status tag

---

## 6. Expiry 計算：filing_date + 20 年（不是 priority）

### 驗證過程

最初實作用 earliest_priority + 20 年。Jenna 的手動 survey 表露出矛盾：

```
EP4138798B1:  priority=2020/4/20  filing=2021/4/20  Jenna's expiry=2041/4/20
                                                     ↑ filing+20, 不是 priority+20
```

其他筆看起來像 priority+20 只是因為 priority date 和 filing date 同年。
EP4138798B1 因為有一年差距而露出真正的計算基準。

**結論：filing_date + 20 年才是正確的 base term 計算。**
已改回 filing_date，docstring 記錄了驗證依據。

---

## 7. Expiry 精度邊界（PTE/SPC）

### Case study：Januvia (Sitagliptin) US7326708B2

| 來源 | Expiry Date | 說明 |
|---|---|---|
| **fetch_dates** (EPO biblio) | 2024-06-23 | filing+20yr base term |
| **Google Patents** | 2026-11-24 | "Adjusted expiration"（含 PTE） |
| **Orange Book** | 11/24/2026 | 含 PTE，跟 Google Patents 一致 |
| **Orange Book (PED)** | 05/24/2027 | 含 PTE + Pediatric Exclusivity (+6 月) |

差距 ~2 年 5 個月 = Patent Term Extension (PTE) 的長度。

**結論**：
- `filing+20yr` 對 **無 PTE 的專利** 是準的（EP4138798B1 跟 Jenna 表完全一致）
- 對 **FDA 核准的藥品專利** 會系統性低估 2-5 年（PTE 最多 5 年）
- PTE 天數沒有單一 API 可拿，需交叉比對 Orange Book 或 USPTO PTA 資料
- Google Patents 的 "Adjusted expiration" 來自 USPTO PAIR/Patent Center 的 PTA + PTE 資料
- 這是 Phase C 暫緩的核心原因

### 工具的精度定位

`--expiry` 的用途是 **rough estimate + 手動 verify**，不是 exact expiry。
括號裡的 `(filing+20yr, no PTE/SPC)` caveat 明確標示精度邊界。

---

## 8. EPO vs Google Patents 差異（Jenna 的比較表）

| Patent No. | Corrected Patent No. | Priority date | Expiration (anticipated) | EPO_Priority date (earlist one) | EPO_Filing date | EPO_Expiration (anticipated) | Priority Date 差異 (天數) | Expiration Date 差異 (天數) |
|---|---|---|---|---|---|---|---|---|
| EP4138798B1 | EP4138798B1 | 2020/4/20 | 2041/4/20 | 2020/4/20 | 2021/4/20 | 2041/4/20 | 無差異 | 無差異 |
| US9415051 | US9415051B1 | 2015/10/23 | 2035/10/23 | 2015/10/23 | 2015/11/24 | 2035/11/24 | 無差異 | 無差異 |
| US9492454B2 | US9492454B2 | 2015/10/23 | 2035/10/23 | 2014/10/23 | 2016/2/4 | 2036/2/4 | 1年0個月 | 1年0個月 |
| US10561635B2 | US10561635B2 | 2016/11/4 | 2036/11/4 | 2016/10/7 | 2019/3/29 | 2039/3/29 | 0年0個月 | 0年0個月 |
| US10583113B2 | US10583113B2 | 2016/11/4 | 2036/11/4 | 2016/10/7 | 2019/3/29 | 2039/3/29 | 0年0個月 | 0年0個月 |
| WO2023073600A1 | WO2023073600A1 | 2022/10/27 | 2042/10/27 | 2021/10/29 | 2022/10/27 | 2042/10/27 | 0年11個月 | 0年11個月 |
| TW202328118A | TW202328118A | 2023/7/16 | 2043/7/16 | 2021/10/29 | 2022/10/27 | 2042/10/27 | 1年8個月 | 1年8個月 |
| KR20230062785A | KR20230062785A | 2023/5/9 | 2043/5/9 | 2021/10/29 | 2022/10/27 | 2042/10/27 | 1年6個月 | 1年6個月 |
| AU2020203515A1 | AU2020203515A1 | 2020/6/18 | — | 2014/2/10 | 2020/5/28 | 2040/5/28 | 6年4個月 | 6年4個月 |

Source: Google Patents (Jenna's Search) vs EPO OPS client (Pipeline result)

---

## 9. 簡報整理狀態（Jenna's Expiry Date Survey PDF）

| Page | 內容 | 本次對話是否 cover |
|---|---|---|
| p.1 | SOP2 discussion outline | ✅ GreyB/Pharsight/IntuitionLabs/PPIV 全部調查過 |
| p.2 | Will 討論議程 | — (context only) |
| p.3-4 | Orange Book Web UI | ✅ + 加了 bulk download path |
| p.5 | Jenna 手動 survey | ✅ 用來驗證 filing vs priority |
| p.6 | fetch_dates EP4138798B1 demo | ✅ 已 commit |
| p.7 | fetch_dates US9492454B2 + EPO vs Google 差異 | ✅ |
| p.8 | EPO vs Google Patents 差異總表 | ✅ 見 §8 |
| p.9-10 | IntuitionLabs drug-level patent status | — (本次未深入) |
| p.11 | PPIV Paragraph IV Certifications | ✅ FDA 來源確認 |
| p.12-13 | Orange Book query (Sitagliptin) + Patent Use Code | ✅ U-802 不是 patent ID |
| p.14 | ChEMBL Drug Synonyms | — (本次未涉及) |

### 簡報未 cover 但本次對話有的

1. `year` 欄位是空的（search inline 無 date）→ DB 已知問題
2. US biblio 不是 404（contradicts earlier assumption）
3. Family API 已帶 date 節點（Phase B piggyback 機會）
4. Orange Book bulk download TXT（`https://www.fda.gov/media/76860/download`）
5. PTE 導致 filing+20yr 低估的完整 case study（US7326708B2）

---

## 10. 未 commit / 不進 repo 的產出

| 檔案 | 用途 | 位置 |
|---|---|---|
| `scratch/probe_date_fields.py` | Phase A probe script | gitignored |
| `patent_expiry_data_sources.md` | Data source landscape 整理 | 本次對話產出，working note |
| `spec_review_patent_date_fields.md` | 同事 spec 的評估 | 本次對話產出，回覆用 |

---

## 11. 待決定 / Next Steps

### 跟 Will 確認

- [ ] Commercial DB 能否 export structured patent expiry data
- [ ] Bio team 對 expiry 精度需求：rough (±2 年) vs exact (含 PTE/SPC)
- [ ] 如果需要 exact → 整合 Orange Book data files 或 Google Patents Adjusted expiration

### Phase B 開發方向（依據 probe 結果修正）

- [ ] **DB 架構決策**：加 column 到 `patents.db` vs 獨立 `patent_dates.db`
  - 加 column 更簡單（ALTER TABLE ADD COLUMN = zero-cost）
  - 獨立 DB 更安全（不動主 DB）但需要 Python-side join 或 ATTACH
- [ ] **Family fetch piggyback**：`_fetch_and_store_family()` 已拿到 date 節點，Phase B 可以順便 parse，不需額外 API call
- [ ] **US fallback**：probe 顯示 US biblio 目前 200，但建議再測 5-10 筆確認不是個案。如果穩定，US fallback 路徑不需要
- [ ] **`year` 欄位修正**：Phase B 順便把空的 `year` 升級為完整的 `publication_date`

### Phase C（暫緩）

- PTE / SPC / terminal disclaimer / annuity 計算
- 前置條件：Phase B 穩定 + bio team 確認需要 exact expiry
- 可能路徑：Orange Book data files (US only) 或 Google Patents BigQuery (global)

---
