# Prior Art Tool — Data Source Selection

> Decision record. Last updated: 2026-07-03. See Revision History at bottom.

## Decision

**Primary source: EPO OPS** (OAuth2 + REST + local SQLite cache).
**Commercial-scale supplement: Google Patents 多層次** (L1 deployed; L2/L3 candidate tasks).

對應 web UI 為 Espacenet — 與 OPS 共用後端，本工具不依賴 web scraping。

## Context

Drug repurposing prior art radar 需要可程式化、可重跑、低成本的專利資料源用於：drug alias 批次 query、抓 claims + description 做 FTO risk scoring、跨司法管轄 family expansion、餵 LLM analyzer 與 excipient pipeline eval。

**約束**：個人專案無採購預算 · batch & scriptable · family API 必要 · claims 全文必要。

## Original Decision Provenance

當初選 EPO OPS 主要是事務所實習熟悉度 + 商用工具太貴，沒做正式 competitive analysis。本文件為 retrospective justification — 結論：**站得住，但限制與升級觸發條件需清楚紀錄**。

## Alternatives Considered

### Tier 1 — Commercial Platforms
| 工具 | 不選原因 |
|------|---------|
| PatSnap / Synapse | $20k+/yr · 黑盒 LLM · 無 prompt customization |
| Clarivate Derwent | FTO 業界標準，但 attorney workflow 取向非 drug repurposing |
| Clarivate Cortellis DDI | 訂閱費極高 · 資料結構鎖死 |
| Questel Orbit | Corporate IP 視角，非本 use case |
| IPRally / Patlytics / Solve | LLM-native 但通用 IP、個人預算不友善 |

共同問題：定價不透明、無法 reproducible、無法整合自己的 LLM prompt。

### Tier 2 — Free Programmatic
| 工具 | Coverage | 不選為主源原因 |
|------|---------|--------------|
| **EPO OPS** ✅ | EP fulltext + INPADOC family + 100M+ docs | — |
| Lens.org | 140M+ records · US/CN 部分 fulltext | 學術免費需申請、商用 contact sales、無 INPADOC family |
| Google Patents BigQuery | Bibliographic global + US fulltext | non-US fulltext 缺口跟 EPO 一樣 · 無 family API · BigQuery batch 模型不利互動式 dev |
| USPTO PatentsView | US-only fulltext (2005+) | ❌ **API 已於 2026-03-20 關閉**（probe 2026-06-01 確認）|
| USPTO ODP（PatentsView 繼承者）| US prosecution history | 需 ID.me 驗證，台灣團隊行政門檻極高；且 ODP 無 fulltext endpoint |
| Google Patents HTML | 全球 + US claims/description | 補強用 ✓（見 Combo Strategy）|
| Espacenet web / Google Patents web | 同上 | 無 API，無法整合進 pipeline |

## Why EPO OPS Holds Up

1. **Family API 是核心需求** — INPADOC 是跨 73+ 司法管轄 family 業界金標
2. **官方資料源 + 穩定 API** — EPO 機關級可靠性
3. **可程式化 + 可 cache** — SQLite + 可離線重跑 + 可寫 regression test
4. **免費額度足夠 prototype，paying tier €2,800/年透明** — 升級路徑明確

## Known Limitations

| 限制 | 影響 | 緩解 |
|------|------|------|
| US/CN/KR/JP fulltext 不在 OPS 授權內，回 404 | FTO scoring 對非 EP 專利靠 abstract 推論 | Family expansion 撈 EP 版本；補強 via Google Patents L2 |
| EPO search index 對非 EP 司法管轄不完整 | 漏 US granted（只搜得 representative A1）| Family API 補回（Pemirolast × IPF 已驗證）|
| EA 司法管轄 claims 塞進 abstract field | abstract / claims 區隔不可靠 | 已知；目前不特別處理 |
| Espacenet web ≠ OPS API backend 行為 | 偶爾 web 找得到 API 撈不到 | 接受此限制 |

## License & Commercial Use Boundary

### 現階段狀態（2026-06 確認）
- EPO OPS non-paying tier · 4GB/週 quota
- 個人 / evaluation 範圍 · internal proposal screening only
- 單一使用者（專案 owner 本人）
- 落在 fair use charter 範圍內

### 升級觸發條件
任一成立即升 EPO OPS paying tier (€2,800/年) 或請 IP 部門申請 commercial license：

1. 其他同事 / 團隊開始使用
2. Output 進入正式 IP / investment memo
3. 週 volume > 3GB（4GB 線之前留 buffer）
4. 列入公司 standard internal tooling
5. 進 production / 對外發報告

### 與其他 pharma data source 授權對照
| 資料源 | 授權型態 | 內部 R&D 免費？ |
|--------|---------|---------------|
| ChEMBL | CC-BY-SA 3.0 | ✅ 商用允許（需 attribution）|
| PubMed abstracts | US NLM Public Domain | ✅ 完全自由 |
| TxGNN | 學術開源 | ✅ 依 LICENSE |
| EPO OPS | 機構 ToU · volume-based tier | ⚠️ 灰色，volume 內可暫不付費 |
| Lens.org | 機構 ToU · 明確 commercial 定義 | ❌ 內部 R&D 明文歸類為商用，需付費 |

**關鍵原則**：判斷新資料源能否內部使用，**先看 LICENSE / ToU**，不是看公司是否已付費。「同事用 X 沒付費」可能因為 X 是開放授權，不代表所有資料源可比照。

## Combo Strategy (A → D)

| 組合 | 成本 / 年 | 適用階段 | 觸發 |
|------|----------|---------|------|
| **A. EPO OPS only** | Free → €2,800 | 個人 / prototype（目前位置）| — |
| **B1. + Google Patents URL（L1）** | +$0 | 使用者手動查 fulltext | 已可用（standalone 工具完成）|
| **B2. + Google Patents HTML scrape（L2）** | +$0 | LLM 需 US fulltext 進 pipeline | 1-2 天工程 |
| **C. + BigQuery patents-public-data（L3）** | +~$10/月 | 需獨立 drug+indication search engine | 3-5 天工程 |
| **D. 換商用平台主源** | $20k–$50k+ | 需 attorney workflow / SLA | FTO opinion letter 場景 |

**關鍵變化**：商用化第一步 B2 是 **+$0**（Google Patents 免費），與 EPO OPS paying tier 升級觸發**脫鉤**。後者只在 volume / 多人 / production 場景啟動。

**B2 多出來的 trade-off**：Google ToS 對 automated scraping 為灰色。低量 on-demand OK，scrape 需 rate-limit（每 request 2 秒、batch < 50）。L3 BigQuery 完全沒這問題（CC-BY 4.0 official dataset）。

## L3 Sub-Options Under Investigation (2026-07)

Combo C（BigQuery `patents-public-data`）在 2026-06 標記為 Deferred。
觸發重新檢視的原因：Bug Y（orphan patents，architecture.md §Gap 7）與
`ta=` fulltext search 限制（`patent_pipeline_coverage_gaps.md` §3）
都指向同一個根本問題——EPO search 入口只能搜 title + abstract，
description-only mention 的專利（如 bromocriptine × SMA 的
WO2022028472A1 case）完全無法被自動搜尋捕獲。

以下三條子路徑是 L3 的不同實現方式，尚在評估，**均未承諾開發**。

### L3a. 下載 BigQuery dataset，自己 index & query

做法：BigQuery export → 本地 fulltext index（SQLite FTS5 或
Elasticsearch）→ drug × indication fulltext search → 結果接回
fetch pipeline。

| 面向 | 評估 |
|------|------|
| 工時 | 3-5 天（export + schema mapping + FTS indexing + query interface） |
| 成本 | ~$10/月 BigQuery；本地 storage 視 scope（full dataset TB 級，filtered 可壓到 GB 級）|
| 授權 | CC-BY 4.0，無 scraping 問題 |
| 優勢 | 完整控制、可 offline、可跑 regression test |
| 風險 | 工程量大、ongoing maintenance（Google schema 變動 + data refresh）|
| 觸發條件 | 需求 scale 到 2 萬藥 × 多 indication，或 L3b 的 web query 路徑不夠用 |

前置知識：BigQuery SQL / `bq extract`、fulltext indexing、
Google publication_number 格式正規化（與 DB patent_id 對齊，
Task H 的 `_normalize_publication_number` 可複用）。

### L3b. Google Patents web query → ID list → 接回 pipeline

做法：對每個 drug × indication pair，用 Google Patents 的 fulltext
search 拿到 patent ID list，再餵回 EPO fetch + LLM analysis pipeline。
不需要 BigQuery。

| 面向 | 評估 |
|------|------|
| 工時 | 1-2 天（query URL 構造 + ID 抽取 + 正規化 + forced fetch list 入口）|
| 成本 | $0（web query）或 SerpAPI ~$50/月（如需穩定 programmatic access）|
| 授權 | ⚠️ 同 L2 灰色地帶（web scraping ToS） |
| 優勢 | 最快得到 fulltext search 能力、與 Task I 的 Kaggle 模式可共存 |
| 風險 | scale 受限——2 萬藥 × N indication × 分頁 = 數十萬 request，throttle/block 機率高 |
| 觸發條件 | 5-10 個 project 的 targeted Bug Y 補強 |

小規模（目前 5-6 個 project）非常可行。Scale 問題在 2 萬藥場景才浮現。
ID 正規化可複用 Task H/I infrastructure。

### L3c. 不用 BigQuery 的其他替代

| 路徑 | 狀態 |
|------|------|
| Lens.org API | 需 commercial license（已排除，見 §Alternatives Considered）|
| USPTO PAIR bulk XML | US-only、需自行 parse/index、~200GB raw |
| EPO paying tier `ftxt=` | Free tier 回 400；paying tier（€2,800/yr）行為未驗證——需 probe |

EPO `ftxt=` 是唯一可能不需額外資料源就解決 fulltext search 的路徑，
但 probe 前不假設可行（per PROJECT_SKILL §"Probe before code"）。
如果 paying tier 的 `ftxt=` 能用且覆蓋 EP + WO fulltext，
可能比 L3a/L3b 更乾淨——但 US/CN fulltext 仍然不在 EPO 授權內。

### L3 建議順序

**L3b（targeted web query）→ L3a（BigQuery，only if scale demands）**

L3c 的 EPO `ftxt=` probe 可以在升級 paying tier 時順便測，不需獨立排程。

---

## 兩個架構性 Findings（probe 驗證）

### 1. Probe before code 抓到純書面分析看不到的事實

三個原本看好的 US fulltext 補強源，在 3-6 個月內全部變動或失效：
- **PatentsView PatentSearch API 2026-03-20 關閉**（文件主頁仍能找到但 API 已停）
- **USPTO ODP 對非美國團隊行政門檻極高**（API key 需 ID.me，台灣團隊實質不可用）
- **PPUBS PDF direct link 改為需 JWT session token**（原 `dirsearch-public/print/downloadPdf` 失效）

三條都是「文件描述 X，實際是 Y」型落差，web search + LLM 知識完全偵測不出來。**Probe 前若直接寫 fetcher code 整合，會耗 1-2 週工程才發現假設崩塌。**

### 2. Data source = commodity, pipeline = moat

本工具 differentiation 90% 不在資料源，在 pipeline 邏輯（snippet extraction、two-layer analyzer、excipient eval、family expansion 邏輯、LLM collaboration meta）。

三個補強源短期內失效是 commodity thesis 的強驗證 — 架構只需 swap 一個 fetcher，不需大規模重寫。

**架構含義**：`modules/patent_fetcher.py` 目前對 EPO OPS 寫死。長期應 refactor 為 data source 可插拔。觸發條件：進入 Combo B2 或 C，或 EPO OPS API 重大變動。**未列為任務，屬「想清楚再做」階段。**

## 明確不做的事

- 不 scrape Google Patents 高量（ToS 風險）
- 不為了補 fulltext 切換到 Lens.org 為主源（INPADOC family 仍是 EPO OPS 金標）
- 不評估 Reaxys / SciFinder（化學文獻而非專利，scope 不同）
- 不評估 PQAI（通用 prior art，無 drug repurposing 概念）

## Revision History

- **2026-06-01** — Initial decision record. Retrospective justification of EPO OPS. Combo strategy candidate: "EPO OPS + USPTO PatentsView."
- **2026-06-02** — Probe-driven revision. PatentsView API closed / ODP barrier / PPUBS broken. Combo strategy updated to "EPO OPS + Google Patents multi-level." Two architectural findings appended.
- **2026-07-03** — L3 sub-options expanded into three paths (L3a BigQuery self-index, L3b web query → ID list, L3c non-BQ alternatives). All under investigation, none committed. Triggered by Bug Y + `ta=` fulltext search limitation revisit. Directions 4 (Orange Book forced fetch) and 5 (DailyMed formulation mapping) scoped out to separate design docs.

## References

- EPO OPS: https://ops.epo.org/
- Lens.org commercial use: https://about.lens.org/individual-commercial-use/
- USPTO ODP: https://data.uspto.gov/
- Google Patents Public Datasets: https://github.com/google/patents-public-data
- ChEMBL licensing: https://chembl.gitbook.io/chembl-interface-documentation/about
- 本 repo：`docs/architecture.md` §EPO OPS Data Coverage · `docs/PROJECT_SKILL.md` §"Known EPO OPS limits" · `docs/spec/bug_Z_resolved.md`
- 衍生 spec：`docs/spec/patentsview_probe_report.md` · `docs/spec/task_H_google_patents_l2.md`
- Coverage gaps：`docs/patent_pipeline_coverage_gaps.md` §3（query strategy limitation）· `docs/spec/search_coverage_gap_bromocriptine_sma.md`（Bug Y case study）
