# Prior Art Tool — Formulation Intelligence (DailyMed + Orange Book)

> Investigation note. 2026-07-03. Status: under evaluation, not committed.
> 關聯：`docs/spec/design_formulation_evidence.md`（snippet extraction 的 why）、
> `docs/validation/probe_expiry_date_20260625.md`（Orange Book 結構筆記）

---

## Problem

Pipeline 目前能回答「哪些專利可能 block 這個 drug × indication pair」，
但不能回答「這個 drug × indication pair 目前市場上有哪些已核准的
formulation」。

Bio team 需要這個資訊做 formulation strategy：如果已經有 oral tablet
在市場上，repurposed drug 要走不同的 delivery route 或 novel
excipient 組合才有 differentiation。知道「已知 formulation 地圖」
跟「專利 claims 的 formulation scope」之間的交集，才能判斷 FTO 風險
的實際影響範圍。

---

## Data Sources

### DailyMed

- 維護者：NLM（National Library of Medicine）
- 內容：所有 FDA-approved drug labeling（SPL XML 格式）
- 覆蓋：每個 approved product 的完整 formulation（成分、劑型、劑量、
  route of administration）
- API：`https://dailymed.nlm.nih.gov/dailymed/services/`
  - `/v2/drugnames.json?drug_name=X` → 列出所有含 X 的 product
  - `/v2/spls.json?drug_name=X` → SPL document list
  - SPL XML 內含 `<formCode>`、`<ingredientSubstance>`、`<routeCode>` 等結構化欄位
- 授權：Public domain（NLM）

### Orange Book Bulk Data

- 已在 pipeline 中使用（`backfill_expiry_dates.py`）
- 欄位：`Ingredient`、`DF;Route`（dosage form + route）、`Trade_Name`、
  `Applicant`、`Patent_No`、`Patent_Expire_Date_Text`、`Appl_Type`（NDA/ANDA）、
  `Appl_No`
- 每筆 patent 掛在特定 NDA + product 下

### Mapping 挑戰

| 面向 | DailyMed | Orange Book |
|------|----------|-------------|
| 主鍵 | NDC / SPL Set ID | NDA/ANDA number |
| Drug 識別 | drug name + SPL | `Ingredient` + `Trade_Name` |
| 橋接 | `application_number` 欄位（部分 SPL 有）| `Appl_No` |
| 外部橋接 | FDA `drugsfda` API：NDA ↔ product info | 同 |

DailyMed ↔ Orange Book 不是 1:1：
- 一個 NDA 可對應多個 NDC（不同 strength/package）
- 一個 drug 可有多個 NDA（不同 applicant、不同 formulation）
- Generic（ANDA）在 DailyMed 有完整 labeling 但 Orange Book 裡
  通常沒有 patent listing（只有 reference NDA 有）

可能需要 RxNorm 做 drug name normalization（DailyMed 用品牌名 +
generic name 混合，Orange Book 用 `Ingredient` 欄位）。

---

## 如果要做，產出是什麼

最小可行產出：一個 lookup tool / script，輸入 drug name，回傳：

1. **Approved formulations**（from DailyMed）：
   dosage form、route、strength、applicant
2. **Patent coverage per formulation**（from Orange Book）：
   哪些 patent 掛在哪個 NDA/product 下，expiry date
3. **Gap map**：哪些 approved formulation 沒有 patent coverage
   （= generic entry 機會）；哪些 patent 的 claims scope 超出
   目前 approved formulation（= novel formulation 的 FTO 風險）

### 工時估算

| 步驟 | 估算 |
|------|------|
| DailyMed API integration（drug name → formulation list）| 1 天 |
| Orange Book parser 擴展（drug → NDA → patent mapping）| 半天（已有 partial parser）|
| NDA 橋接邏輯（DailyMed SPL ↔ OB Appl_No）| 1 天（含 edge case 處理）|
| Drug name normalization（RxNorm 或 manual alias mapping）| 半天 - 1 天 |
| Output 格式 + 整合進 pipeline output | 半天 |
| **合計** | **3.5 - 4.5 天** |

如果不做 NDA 橋接（只是平行列出 DailyMed formulations + OB patents，
不做 cross-reference），可以壓到 2 天。但這樣產出的 actionability
明顯降低。

---

## 優先順序判斷

這個方向屬於 **output enrichment**，不是 search coverage 的 blocker。
Pipeline 不會因為沒有 DailyMed 資料而漏掉專利——它只是讓使用者
更難判斷「這個專利對我的 formulation strategy 有多重要」。

建議排在 `design_search_strategy.md`（Orange Book forced fetch）之後。
如果 forced fetch 做完，formulation intelligence 是很自然的 next step
（同一份 Orange Book data，parser 可以共用）。

---

## 前置 probe（建議在開發前做）

1. 拿一個已跑過的 drug（e.g. Pemirolast），打 DailyMed API，
   看回傳的 formulation 資訊結構是否足夠 parse
2. 確認 DailyMed 的 `application_number` 欄位跟 OB 的 `Appl_No`
   格式是否一致（e.g. 前者可能帶 `NDA` prefix，後者只有數字）
3. 評估 RxNorm 是否真的需要——如果 drug alias list
   （pipeline 已有的 `DRUG_ALIASES`）能直接 match DailyMed，
   可能不需要額外的 normalization layer

---

## Revision History

- **2026-07-03** — Initial investigation note. DailyMed + Orange Book
  formulation intelligence concept + feasibility assessment.
