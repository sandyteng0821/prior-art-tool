# Prior Art Tool — Search Strategy Enhancement

> Investigation note. 2026-07-03. Status: under evaluation, not committed.

---

## Problem

Pipeline 的搜尋入口是 EPO CQL `ta=`（title + abstract only）。
這表示 claims-only 或 description-only mention 的專利無法被自動搜尋
捕獲（`patent_pipeline_coverage_gaps.md` §3）。Bug Y（architecture.md
§Gap 7）是這個限制的直接症狀。

Family expansion 和 Google Patents JSONL supplement（Task I）能補一部分，
但前提是同 family 至少有一筆被搜到。完全孤立的專利仍然 miss。

**核心矛盾**：fulltext search（L3 系列）工程量大、授權灰色；
但 regulatory 層面最重要的專利（Orange Book listed）名單已知、公開、
數量有限——不需要 fulltext search 就能保證覆蓋。

---

## Proposed Direction: Orange Book Forced Fetch List

### 概念

從 Orange Book bulk data 拿到某個 drug 對應的 patent number list，
直接塞進 fetch pipeline，bypass search 入口。
保證 FDA 認定的、對 generic entry 有實際阻擋力的專利一定被分析到。

### 已有的 infrastructure

- Orange Book bulk download path 已知（`https://www.fda.gov/media/76860/download`，
  tilde-delimited TXT）
- `scripts/backfill_expiry_dates.py` 已在 parse Orange Book data（expiry_source = 'orange_book'）
- Patent number 正規化經驗：OB 格式 `7326708` → `US7326708`（需補 kind code，
  通常 B1 或 B2；可用 EPO family API 確認）

### 需要新增的

1. **Orange Book parser → patent ID list**：從 OB bulk data 抽出
   特定 drug（by `Ingredient` 或 `Trade_Name`）的所有 patent numbers
2. **Forced fetch list 入口**：一個新的 pipeline 入口，接受 patent ID list，
   跳過 search，直接進入 fetch → store → analyze flow
3. **De-duplication**：forced list 裡的 patent 可能已經在 DB（透過 search
   進來的）——需要 merge 而非重複

### 工時估算

| 步驟 | 估算 |
|------|------|
| OB parser（已有 expiry 的 partial parser，擴展即可）| 半天 |
| Forced fetch list 入口（`main.py` 或新 script）| 半天 - 1 天 |
| Kind code resolution（OB 只給 patent number 不給 kind code）| 半天 |
| 整合測試 + 驗證（1-2 個 drug 跑完整 pipeline）| 半天 |
| **合計** | **1.5 - 2.5 天** |

### Scope 與限制

- Orange Book 只有 US NDA-listed patents（不涵蓋 EP/CN/JP）
- 只含 unexpired patents（已過期的會被移除）
- 只含 drug substance / drug product / method of use patents
  （不含 device / packaging patents）
- FTC 曾挑戰 400+ 筆不當 listing，但到期日本身是可靠的
  （見 `docs/validation/probe_expiry_date_20260625.md` §2）

**對 drug repurposing FTO 而言，US market 通常是最重要的市場，
Orange Book listed patents 是 generic entry 的直接法律障礙。
即使不涵蓋其他管轄區，確保這些專利不漏已經是很高 ROI。**

### 與 L3 的關係

不衝突。Orange Book forced fetch 解決的是「已知重要專利不漏」，
L3 系列解決的是「發現未知的 relevant patents」。
建議先做 forced fetch（低成本、高確定性），再評估 L3 是否值得。

### 前置 probe（建議在開發前做）

1. 拿一個已跑過的 drug（e.g. Pemirolast），從 OB 抓 patent list，
   跟 DB 已有的 patent 做 diff——看有多少是 OB 有但 pipeline 漏掉的
2. 確認 kind code resolution 策略：OB 的 `7326708` 查 EPO family API
   能否自動 resolve 成 `US7326708B2`

---

## Future Direction: Mechanism-Based Query Strategy

如果 Orange Book forced fetch + L3b targeted web query 仍不夠，
下一步是 mechanism-based query expansion：
用 drug 的 MoA（mechanism of action）或 target protein 作為
search term，而非只用 drug name。

這需要 DrugBank / ChEMBL integration 提供 MoA 資訊，工程量更大，
暫不展開。列為「想清楚再做」。

---

## Revision History

- **2026-07-03** — Initial investigation note. Orange Book forced fetch
  concept + feasibility assessment.
