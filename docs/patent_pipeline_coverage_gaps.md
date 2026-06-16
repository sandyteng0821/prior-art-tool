# Prior Art Tool — Patent & Pipeline Coverage Gap Analysis

> Standalone reference document. Readable without the slide deck.
> Consolidates: slide deck (pp.1-31), 2026-06-15 probe session, architecture.md, PROJECT_SKILL §4.
> Last updated: 2026-06-16.

---

## Background: What This Tool Does

Drug repurposing prior art radar：一個可程式化、可重跑、低成本的專利搜尋與風險評估 pipeline。

Pipeline 共五個 phase：

1. **Phase 1 — Query Builder**：用 EPO CQL 語法產生搜尋策略（drug alias × indication × mechanism 組合）
2. **Phase 2 — Patent Fetcher**：打 EPO OPS API 抓 title / abstract / claims / description，加上 INPADOC Family API 做跨管轄 family expansion
3. **Phase 3 — Patent Store**：SQLite local cache，存所有抓到的專利內容
4. **Phase 4 — LLM Analyzer**：GPT-4o-mini 初篩（全部摘要）→ GPT-4o 精讀（Medium/High），輸出 fto_risk + gap_opportunity + reasoning
5. **Phase 5 — Output Writer**：CSV / Excel 格式化輸出

資料來源選擇 EPO OPS（同 Espacenet 後端），目前使用 non-paying tier（free，4GB/週 quota），限個人 / evaluation 範圍使用。

---

## 1. EPO OPS API vs Espacenet Website

**同一個 EPO 資料庫，兩個不同的存取層級。**

Espacenet（EPO 的公開網站）和 OPS API（本工具使用的程式化介面）共用底層資料，但授權範圍不同。這是使用者最常遇到的困惑來源：「為什麼我在 Espacenet 網站看得到，你的工具卻沒有？」

| 面向 | Espacenet 網站 | OPS API（本工具） |
|------|-------------|--------------|
| 對象 | 一般大眾免費瀏覽 | 程式自動化存取 |
| Fulltext 覆蓋 | EP + WO + US + CN + 更多 | EP only（非 EP 回 404） |
| 搜尋能力 | `txt=` fulltext search 可用（搜 description + claims） | `txt=` / `ftxt=` 回 400 Bad Request，只支援 `ta=`（title+abstract） |
| 缺資料時的行為 | **自動從 family member 借**，例如顯示「Abstract not available for EP4192518A1 — abstract of corresponding document: WO2022028472A1」 | 老實回空或 404，不做 cross-reference |
| TOS | 禁止 scraping，無法整合進 pipeline | OAuth + REST，可寫 cache，可審計 |

**驗證 case（2026-06-15，Darifenacin × GPP 案例觸發）：**

Jenna 在 Google Patents 找到三筆專利（WO2001010427A2、CN117018194B、US20100184727A1），用 `inspect_patent` 查全部顯示 0 chars。Probe 結果：

- WO2001010427A2：EPO API 連 biblio（title/abstract）都回 404
- CN117018194B：同上（但同號 A 版 CN117018194A 有資料）
- US20100184727A1：同上

原本以為是 inspect_patent 的 bug，probe 後確認是 EPO OPS 對非 EP 專利的直接 Epodoc lookup 覆蓋比預期更差。**不是工具的 bug，是資料源的限制。**

已修正 `inspect_patent.py`：全空時顯示 Espacenet + Google Patents URL，讓使用者可以手動查閱。

---

## 2. EPO OPS Data Coverage

**不是每篇都有 fulltext，某些連 biblio（title/abstract）都沒有。**

### Coverage Table（2026-06-15 probe 後更新版）

| Patent Type | biblio (title/abstract) | claims | description/examples | Search indexing |
|-------------|:-----------------------:|:------:|:--------------------:|:--------------:|
| EP granted (EPB) | ✅ | ✅ | ✅ | ✅ |
| EP application (A1/A2) | ✅ | ✅³ | ✅³ | ✅ representative |
| WO | ✅ | ✅³ | ✅³ | partial |
| US application (A1) | ❌⁴ | ❌¹ | ❌¹ | ✅ representative |
| US granted (B1/B2) | ❌⁴ | ❌¹ | ❌¹ | ⚠ not in search, found via family API |
| CN-A (application) | ✅ | ❌¹ | ❌¹ | partial |
| CN-B (granted) | ❌⁴ | ❌¹ | ❌¹ | partial |
| AU / MX / other | partial | ❌¹/✅² | ❌¹/✅² | partial |

¹ EPO OPS 不授權非 EP fulltext，HTTP 404。Task C（2026-05）probe matrix 驗證——測過 Epodoc / Docdb / Original 三種 model class，都一樣 404。限制在 EPO 資料層，不是我們 client 的問題。

² Google Patents JSONL supplement（Task I，2026-06）可以補充部分非 EP 專利的 fulltext。覆蓋範圍取決於 Kaggle scraper 跑了哪些 patent ID。

³ EP-A1 和 WO 在 probe 中回傳了 fulltext，但可能因個別專利而異。

⁴ 直接 Epodoc lookup 連 biblio 都回 404。Probe 2026-06-15：US 8/8 全滅、CN-B 也 404。Production path 不受影響——search results 自帶 biblio metadata，所以透過 search 進 DB 的專利有 title/abstract。這只影響 `inspect_patent` 的 sandbox fallback（ad-hoc 手動查詢不在 DB 裡的專利）。

### DB 實際統計（EP 系列，2026-06-15）

| 類別 | 總數 | 有 abstract | 有 claims | 全空 |
|------|------|-----------|---------|-----|
| EP-A (application) | 524 | 167 (32%) | 161 (31%) | 353 (67%) |
| EP-B (granted) | 244 | 68 (28%) | 231 (95%) | 6 (2%) |

EP-B claims 95% 是正常的——EPO 對自家 granted patent 給 fulltext。EP-A 大量全空是因為很多 EP-A 是從 WO/CN 的 PCT 進入 EP 階段的早期申請，EPO 只有 entry metadata，fulltext 在 WO 版本上。Espacenet 網站會自動從 family member 借來顯示，API 不會。

### Cross-project Coverage Probe（2026-06-05，所有專案）

| 專案 | abstract | claims | examples |
|-----|---------|--------|----------|
| IPF × Pemirolast（post-backfill baseline） | 595/685 (86.9%) | 573/685 (83.6%) | 456/685 (66.6%) |
| SCA × Roflumilast | 384/454 (84.6%) | 119/454 (26.2%) | 24/454 (5.3%) |
| Psoriasis × Maxacalcitol | 311/390 (79.7%) | 114/390 (29.2%) | 27/390 (6.9%) |
| Psoriasis × Apremilast | 555/658 (84.3%) | 148/658 (22.5%) | 40/658 (6.1%) |
| Ampicillin | 168/187 (89.8%) | 23/187 (12.3%) | 11/187 (5.9%) |
| Acetaminophen | 571/677 (84.3%) | 150/677 (22.2%) | 69/677 (10.2%) |

IPF × Pemirolast 的 claims 83.6% 是因為已經跑過 Task I（Google Patents JSONL import），其他專案的 claims 覆蓋 12-29% 是 EPO 的正常水平（非 EP fulltext 拿不到）。

---

## 3. Query Strategy Limitation

### 3a. 現有搜尋策略（三步驟）

**步驟 1：藥名搜尋。** 對每個 drug alias 自動產生兩組 query：
- `ta=<藥名>`：全搜（EP + US，含 application）
- `ta=<藥名> AND pn=EPB`：限 EP granted only

**步驟 2：交叉組合搜尋（CUSTOM_QUERIES）。** 手動設計的 EPO CQL query，覆蓋 drug × indication、mechanism × indication、競爭藥 × indication、indication 全掃（不限藥物）。

**步驟 3：Family Expansion（自動，搜尋後觸發）。** 每筆搜到的 A1/A2 專利 → EPO INPADOC Family API → 撈同族所有成員（B1, B2, A1, A2, A，含跨管轄）。US granted B2 只能透過這步找到（不在 EPO search index 中）。

**關鍵限制：所有搜尋都用 `ta=`（title + abstract）。Description 和 claims 的內容不在搜尋範圍。**

### 3b. SMA × Bromocriptine Case Study：Description-only mention

**問題：** WO2022028472A1 的 claims 全部是 AAV vector construct（基因治療載體），bromocriptine 只出現在 description 第 0373 段的 combination therapy 列表裡。Title 和 abstract 完全沒有提到 bromocriptine。

| 欄位 | 提到 bromocriptine？ | 內容 |
|------|-------------------|----|
| Title | ❌ | NUCLEIC ACID CONSTRUCTS AND USES THEREOF FOR TREATING SPINAL MUSCULAR ATROPHY |
| Abstract（326 chars） | ❌ | 只講 SMN protein + microRNA |
| Claims（70 條, 20,306 chars） | ❌ | 全部 AAV vector / miRNA construct |
| Description（239,769 chars） | ✅ 第 209,632 字 | "free radical scavengers that inhibit oxidative stress-induced cell death, such as bromocriptine" |

`ta=bromocriptine` 不會命中這篇，因為 bromocriptine 不在 title 或 abstract 裡。

### 3c. Fulltext search（`ftxt=`）不可用

| 嘗試 | 結果 |
|------|------|
| OPS API：`ftxt=bromocriptine AND ta="spinal muscular atrophy"` | HTTP 400 Bad Request |
| Espacenet 網站：`txt=bromocriptine AND ta="spinal muscular atrophy"` | ✅ 3 results found |

Espacenet 網站的 `txt=` 可以搜到（3 results），但這是 web-only 功能，OPS API 不支援。即使升級到 paying tier（€2,800/yr），也不保證 `ftxt=` 可用（EPO 文件未明確說明），且 fulltext index 只涵蓋 EP + WO 的 A-document。

### 3d. 寬 query 嘗試（失敗）

嘗試用更寬的 indication query 撈：

| Query | 結果數 | 命中 WO2022028472A1？ |
|-------|-------|---------------------|
| `ta="spinal muscular atrophy" AND ta="pharmaceutical composition"` | 41 | ❌ |
| `ta="SMN protein" AND ta="pharmaceutical"` | 5 | ❌ |
| `ta="spinal muscular atrophy" AND ta="combination"` | 28 | ❌ |

全部沒命中——這筆的 abstract 只提 nucleic acid + microRNA，沒有任何 pharmaceutical / combination keyword。在 `ta=` 的限制下，沒有任何 query 能命中這篇。

### 3e. FTO 風險評估

**WO2022028472A1 對 bromocriptine oral tablet × SMA：Low risk。**

70 條 claims 全部是 AAV vector construct + miRNA。Bromocriptine 只在 description 的 combination therapy 長列表中被順帶提及一次。這是 defensive disclosure pattern——broadens description 但不 create claim scope over bromocriptine。Claim scope 是基因治療載體，不是小分子口服藥。

即使這筆進了 DB，Phase 4 LLM analyzer 也會給 Low risk——因為 LLM 只讀 abstract + claims 前 3000 chars，不讀 description。

### 3f. EP4192518A1 的特殊情況

EP4192518A1 是 WO2022028472A1 的 EP family member，但 EPO API 對它的 abstract / claims / description 全部回空。Espacenet 網站顯示「Abstract not available for EP4192518A1 — abstract of corresponding document: WO2022028472A1」。

這是 §1 提到的 cross-reference 行為差異：Espacenet 會自動借 family member 的內容，API 不會。

---

## 4. Family Expansion 能補什麼、補不了什麼

### 能補的

**Case Study I — SMA × Bromocriptine（AU4245001A family）：**
- AU4245001A 在 Espacenet 顯示「No abstract found. Please consult other publications of this patent family」
- Family members：EP1133993A1 + WO0166129A1
- Pipeline 透過 family expansion 找到了這三筆：AU4245001A 給 Medium risk（abstract 有 SMA），EP/WO 給 Low（初篩判定無關）

**一般 case：**
- EP-A 的內容從 WO family member 拿（但需要 WO 先被搜到）
- US granted B2 不在 search index，只能透過 family API 從 A1 找到
- 跨管轄 TW/KR/AU/JP siblings（2026-05 filter 擴展後支援 A1/A2/A kind codes）

### 補不了的

- **Description-only mention**（如 WO2022028472A1）——search 階段沒命中，family expansion 根本不會觸發
- **完全孤立的專利**——沒有任何 family member 被其他 query 搜到（architecture.md Bug Y）
- **EPO search index 沒收錄的管轄區**——某些 TW/KR/AU 專利即使存在也不在 `ta=` search index 裡（§4.4）

---

## General Limitations

### EPO Fetch（資料抓取）

- 目前只用 EPO 資料作為主要來源
- Patent ID 格式和 Google Patents 可能不一致（e.g. `US2010204296A1` vs `US20100204296A1`——EPO 用前者，Google 用後者，沒有統一）
- 非 EP 管轄的 fulltext（claims / description）不在 OPS 授權範圍，回 404
- Espacenet 網站看得到的內容，API 不一定拿得到（不同授權層級，且 API 不自動做 family cross-reference）
- 部分 EP-A 專利在 API 上全空——因為 fulltext 在同 family 的 WO 版本上，API 不會自動借

### EPO Search（搜尋）

- 只能搜 `ta=`（title + abstract）等 bibliographic 欄位
- **`cl=` 是 IPC/CPC classification code，不是 claims text。** EPO CQL 沒有任何 field 可以搜 claims 文字內容。
- 完全沒有 fulltext search 能力——`ftxt=` 在 non-paying tier 回 400 Bad Request
- 這是 search index 的限制，不是 API call 的問題
- Description 或 claims 裡提到但 title/abstract 沒提到的藥名，無法被自動搜尋捕獲
- Espacenet 網站支援 `txt=` fulltext search，但 OPS API 不支援此語法

**Claim-only mention vs Description-only mention：**

搜尋入口的限制是一樣的——`ta=` 都搜不到。但進了 pipeline 之後行為不同：

| | 搜尋入口 | 進 DB 後 Phase 4 LLM 會讀到嗎？ |
|---|---|---|
| Title/Abstract mention | ✅ `ta=` 命中 | ✅ LLM 讀 abstract |
| Claims-only mention | ❌ `ta=` 搜不到 | ✅ 如果透過其他路徑進 DB，LLM 讀 claims 前 3000 chars |
| Description-only mention | ❌ `ta=` 搜不到 | ❌ LLM 不讀 description（只用於 snippet extraction） |

所以 claims-only mention 的問題只在「能不能被搜到進入 pipeline」。一旦進了 DB（例如透過 family expansion 或 indication-only query），Phase 4 LLM analyzer 會讀 claims 並給出正確的風險評估。Description-only mention 則是雙重盲區——搜不到，進了 DB 也不會被 LLM 分析。

### 補充搜尋

- 全文搜尋目前參考 Task I（透過 Google Patents 人工檢索 + Kaggle scraper 產出 JSONL → `import_google_patents_jsonl.py` 匯入 DB）
- Google Patents 網站支援 fulltext search，可以搜到 description-only mention
- 但這是 manual supplement，不是 automated pipeline
- 已完成一次 689 筆的 Pemirolast 專案匯入（2026-06-02），claims 覆蓋從 26% → 84%

---

## 回應專家的 FAQ

### Q: 為什麼你的 report 沒有 WO2022028472A1？我在 Google Patents 搜到了。

這篇專利的 70 條 claims 全部是 AAV 基因治療載體的構造與方法。Bromocriptine 只出現在 description 第 0373 段的 combination therapy 列表裡，title 和 abstract 都沒有提到。

我的工具用 EPO OPS API 做自動搜尋，搜尋範圍是 title + abstract（`ta=` field）。Description 層級的全文搜尋在 EPO API 上不可用。這種「藥名只出現在 description 的 combination list」的專利，自動搜尋不會命中。

從 FTO 角度，這篇對 bromocriptine oral tablet 的風險很低——claim scope 是基因治療，不是小分子口服藥。如果需要 fulltext 層級的 landscape scan，可以用 Google Patents 做一次性補充搜尋，結果可匯入 DB。

### Q: Espacenet 上看得到內容，為什麼工具顯示空的？

EPO 的 Espacenet 網站和 OPS API 是兩個不同的存取層級，授權範圍不一樣。Espacenet 網站會自動從同 family 的其他專利借 abstract / claims 來顯示，API 不會做這個 cross-reference。API 對非 EP 管轄的專利，fulltext 一律 404，某些連 biblio（title/abstract）都拿不到。

工具已加入自動檢測：遇到 API 回空的情況會顯示 Espacenet 和 Google Patents URL，讓使用者可以直接點過去手動查閱。

### Q: 這個工具跟商用平台（PatSnap, Cortellis）比差在哪？

定位不同。商用平台（$20k-$50k/yr）提供 curated drug→patent mapping、完整 US/CN/KR/JP fulltext、法律狀態追蹤、attorney workflow。這個工具（EPO free tier → 升級 €2,800/yr）提供 drug repurposing 專用 pipeline、完全可客製的 LLM prompt 和 rule、可重現可審計的搜尋流程。

不是「便宜版商業平台」——是不同 use case 的不同產品。已知缺口（US/CN fulltext）用 Google Patents supplement 補。

---

## 相關 Commits & 文件

| 日期 | Commit / 文件 | 說明 |
|------|------------|------|
| 2026-06-15 | `fix(inspect): detect empty sandbox fallback...` | inspect_patent 全空檢測 + URL 提示 |
| 2026-06-15 | `feat(inspect): add --force-refetch...` | Debug 用強制重打 EPO（bypass DB + diskcache） |
| 2026-06-15 | `docs/architecture.md` | Coverage table 更新（footnote ³⁴） |
| 2026-06-15 | `docs/search_coverage_gap_bromocriptine_sma.md` | SMA × Bromocriptine gap analysis + 專家回答模板 |
| 2026-06-15 | `docs/validation/probe_session_20260615.md` | **所有 probe commands + SQL + 原始輸出**（重現用） |
| 2026-06-05 | Probe analysis Q1 | Cross-project coverage baseline 統計 |
| 2026-06-02 | Task I Kaggle scraper | 689 筆 Pemirolast 專利 JSONL import 完成 |

> **重現注意：** 本文件中的 EP-A/EP-B 統計數字來自即時 SQL 查詢，不是工具自動產出。
> 完整的 SQL、shell commands、patent IDs 記錄在 `docs/validation/probe_session_20260615.md`。
> DB 內容會隨 backfill / import 改變，重跑同一 SQL 數字可能不同。
