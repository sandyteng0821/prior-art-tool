# Prior Art Tool — Data Source Selection

> Decision record. Last updated: 2026-06-02. See Revision History at bottom.

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

## References

- EPO OPS: https://ops.epo.org/
- Lens.org commercial use: https://about.lens.org/individual-commercial-use/
- USPTO ODP: https://data.uspto.gov/
- Google Patents Public Datasets: https://github.com/google/patents-public-data
- ChEMBL licensing: https://chembl.gitbook.io/chembl-interface-documentation/about
- 本 repo：`docs/architecture.md` §EPO OPS Data Coverage · `docs/PROJECT_SKILL.md` §"Known EPO OPS limits" · `docs/spec/bug_Z_resolved.md`
- 衍生 spec：`docs/spec/patentsview_probe_report.md` · `docs/spec/task_H_google_patents_l2.md`
