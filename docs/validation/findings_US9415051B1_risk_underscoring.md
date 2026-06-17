# US9415051B1 Risk 評估偏低 — 調查 Findings

> 調查日期：2026-06-17（pipeline 於 06-16 晚間 nohup 重跑，06-17 下午開始調查細節）
> 狀態：調查完成，prompt 修改待專家確認

---

## 一句話總結

US9415051B1 "Use of pemirolast" 的 claim 5 字面寫了 "idiopathic pulmonary
fibrosis"，但 pipeline 歷次跑出來都是 Low。根因是三層問題疊加：diskcache 繞過了
DB 更新、screening 只看 abstract 看不到 claims 裡的 IPF、Stage 2 rubric
把 dependent claim 降權導致即使看到也只給 Low（gpt-5）或 Medium（gpt-4o）。

06-16 清 diskcache 重跑後，LLM 確實讀到 claims 且正確辨識出 claim 5 的 IPF，
但仍然判 Low。**確認主因是 Root Cause 3（rubric 問題）。**

---

## 專利內容摘要

```
Patent:  US9415051B1 "Use of pemirolast"
Claims:  1229 chars, 7 claims
Abstract: 313 chars — 只提 "airway hyperresponsiveness"

Claim 1 (independent): pemirolast 口服 ≥350mg/day 治療 AHR
Claim 2: AHR = asthma
Claim 3: AHR = COPD
Claim 4: AHR = asthma-COPD overlap
Claim 5: AHR = idiopathic pulmonary fibrosis  ← 關鍵
Claim 6: 劑量 350-600 mg/day
Claim 7: 併用其他藥物

Examples (38732 chars): 明確列出 "(iv) IPF patients"，
  包含 PK 數據、臨床試驗、劑型配方（MCC、mannitol、copovidone）
```

專家認為此專利對「Pemirolast 吸入劑治 IPF」構成重要前案威脅，
但 pipeline 兩次跑出 Low risk。

---

## 三層根因

### Root Cause 1：Diskcache 繞過 DB 更新（資料層）

**時序：**

| 時間 | 事件 |
|------|------|
| 5月28日 | Pipeline run → EPO fetch US9415051B1 → claims 404（US patent 已知限制）→ 空字串存入 DB + diskcache（7天過期）|
| 6月3日 13:27 | Task I import 完成 → DB 裡 claims 更新為 1229 chars（source = google_patents）|
| 6月3日 22:32 | Pipeline 重跑 → `fetch_patents()` 命中 diskcache search key（5月28日建立，< 7天未過期）→ 回傳舊 patent dict（claims 空）→ DB 更新被繞過 |

**證據：**
- `backfill_log`: Task I import completed at `2026-06-03T13:27:14`
- `patents.fetched_at`: `2026-06-03T13:27:14` (source=google_patents)
- CSV 產出: `2026-06-03 22:32`（晚於 import）
- 但 CSV reasoning 寫 "claims missing so abstract limits scope"
- diskcache `claims::US9415051B1` 內容為空字串（EPO 404 快取）

**機制：** `fetch_patents()` 第 46-48 行：
```python
cache_key = f"search::{cql_query}::{size}"
if cache_key in cache:
    return cache[cache_key]  # ← 直接回傳舊的 patent dict list，不查 DB
```

### Root Cause 2：Screening 只看 abstract（架構層）

**Stage 1 screening 只餵 title + abstract 給 LLM，不看 claims。**

Abstract 只寫 "airway hyperresponsiveness"（umbrella term），
claim 5 的 "idiopathic pulmonary fibrosis" 在 abstract 層級不可見。
加上 `SCREENING_IRRELEVANT_EXAMPLES` 寫了排除「單純氣喘」，
LLM 合理地將 AHR 歸為氣喘類 → short-circuit 到 Low。

**Keyword probe 驗證（hardcode，不受 config 影響）：**

```
title+abstract+claims: drug ✓, indication ✓, route ✓, additional ✓ → 4/4 = High
title+abstract ONLY:   drug ✓, indication ✗, route ✗, additional ✗ → 1/4 = Low
```

indication 命中完全來自 claims 裡的 "idiopathic pulmonary fibrosis"。

### Root Cause 3：Stage 2 rubric 把 dependent claim 降權（prompt 層）

即使 claims 被正確餵入 Stage 2，current prompt 說
「重點分析 independent claim（通常是 claim 1），而非 dependent claims」。

LLM 遵守指令：claim 1 = AHR + oral，跟目標產品（inhalation + IPF）
不同 route 不同 indication → Medium。Claim 5 的 IPF 被當成
dependent claim 降權，甚至沒列進 indications 欄位。

---

## 測試結果

使用 `scratch/test_production_scoring.py`，model = gpt-4o-mini / gpt-4o，
Pemirolast × IPF 常數 hardcode（不受 config.py 影響）。

| Test | 做什麼 | 結果 |
|------|--------|------|
| **A** | Stage 1 screening（current：title + abstract only）| is_relevant=True, quick_risk=Medium → 進入 Stage 2 |
| **B** | Stage 2 analysis（current rubric，直接餵 claims）| **fto_risk=Medium** — reasoning: "claims focus on oral for airway conditions, not inhalation for IPF" |
| **C** | Stage 1 + claims[:1000]（proposed screening fix）| is_relevant=True, quick_risk=Medium → 進入 Stage 2 |
| **D** | Stage 2 + amended rubric（dependent claims matter + 同藥同適應症 → High）| **fto_risk=High** — reasoning: "Claim 5 explicitly mentions IPF with pemirolast, posing a high FTO risk" |

**注意：** Test A 用 gpt-4o-mini 沒有 short-circuit（判了 Medium），
但 production 用 gpt-5-mini，不同 model 可能判 Low。
歷史 CSV 有兩筆 reasoning = "初篩判定無關，未進行 claims 精讀" 
確認 production 確實有被 short-circuit 過。

---

## 歷史 CSV 中 US9415051B1 的所有結果

| 日期 | Model | fto_risk | reasoning 摘要 |
|------|-------|----------|---------------|
| 5/28 | GPT-4o | Low | "focuses on AHR, not IPF, lacks inhalation" |
| 5/28 | GPT-4o | Low | "初篩判定無關，未進行 claims 精讀" ← **Stage 1 short-circuit** |
| 5/28 | GPT-4.1 | Medium | "may broadly cover IPF" |
| 5/28 | GPT-4.1 | Medium | "may cover IPF if broadly interpreted" |
| 5/28 | GPT-5 | Low | "僅涵蓋氣道過度反應；未及IPF" |
| 5/28 | GPT-5 | Low | "主題為氣道高反應性，非IPF" |
| 6/3 | GPT-5 | Low | "Only AHR; **claims missing** so abstract limits scope" ← **diskcache 繞過 DB** |
| 6/3 | GPT-5 | Low | "初篩判定無關，未進行 claims 精讀" ← **Stage 1 short-circuit** |
| **6/16** | **GPT-5** | **Low** | "Claim 1: oral ≥350 mg/day; inhalation excluded." ← **claims 有讀到，rubric 問題** |
| **6/16** | **GPT-5** | **Low** | "Claim 1 is oral-only; target is inhaled IPF." ← **同上，indications 列出 IPF 但仍判 Low** |

**6/16 rerun 的關鍵觀察：**
- `indications` 欄位正確列出 "Idiopathic pulmonary fibrosis (IPF)" → claims 確實被讀到
- `delivery_routes` 正確列出 "Oral" → LLM 正確解析 claim 1
- `is_target_drug` = True（兩筆都是）→ 不再有 Stage 1 short-circuit
- 但 `fto_risk` = Low → **LLM 看到了 IPF 卻仍判 Low，因為 claim 1 限定 oral**
- 這確認 Root Cause 3（rubric）是主因，不是資料問題

---

## 06-16 Rerun — 整體影響（probe_rerun_diff.py 結果）

### Risk 分佈

| | High | Medium | Low | Total |
|---|---|---|---|---|
| 6/3（舊）| 0 | 45 | 644 | 689 |
| 6/16（新）| 2 | 40 | 592 | 634 |

### 篇數差異

- Old: 685 distinct IDs → New: 631 distinct IDs
- Old only: 54 筆（EPO 搜尋 index 變動，無新增）
- New only: 0 筆

### Risk 變動

- Upgrades: 6 筆（含 2 筆升到 High：CN116531383A, WO2005102346A2）
- Downgrades: 8 筆（之前 claims 空靠 abstract 猜高，現在有 claims 修正）
- Unchanged: 617 筆

### Claims missing 改善

- Old: 71 筆 "claims missing" → New: 12 筆
- 修復 69 筆（Task I 補的 claims 生效）

---

## Config 陷阱

調查過程中發現 workstation 的 `config.py` 是 Bromocriptine × SMA 版本，
不是 Pemirolast × IPF。導致：
- `RULE_DRUG_KEYWORDS` 不含 "pemirolast" → keyword probe 初次全空
- `ANALYSIS_MODEL` 等常數是 Bromocriptine 設定

test_production_scoring.py 用 hardcode 常數繞過此問題。
重跑 pipeline 前需 `cp configs/pemirolast_ipf_v3.py config.py`。

---

## 重跑 Pipeline（已完成）

```bash
# 1. 換回 config
cp configs/pemirolast_ipf_v3.py config.py

# 2. 清 diskcache（確保走 DB hit）
python3 -c "
import diskcache
cache = diskcache.Cache('cache/epo')
to_delete = [k for k in cache if 'pemirolast' in str(k).lower() or 'US9415051' in str(k)]
for k in to_delete: cache.delete(k)
"

# 3. nohup 跑
nohup python3 -u main.py > logs/pemirolast_ipf_v3_rerun_20260616.log 2>&1 &
```

**結果：** Diskcache 問題確認修復。US9415051B1 的 claims 被正確餵入 LLM，
indications 列出 IPF，但 fto_risk 仍為 Low。
**確認 Root Cause 3（rubric）是剩餘的主要問題。**

Output: `output/gap_analysis_20260616_2108.xlsx`（634 筆）

---

## 建議改動方向（待專家確認）

### 改動 1：Diskcache invalidation（資料層 — 低風險）

`search::` level cache 會繞過 DB 更新。Task I 之類的 backfill
跑完後應該清除相關 diskcache，或者 DB hit 路徑應該優先於 diskcache。

### 改動 2：Screening 加入 claims hint（架構層 — 中風險）

Stage 1 screening 從「只看 title + abstract」改為
「title + abstract + claims[:1000]」。成本低（claims 前 1000 chars
對 gpt-5-mini 增加不多），能讓 screening 看到 dependent claims 裡的
具體適應症。

或者更簡單的規則：**title/abstract 提到 target drug → 必定進入 Stage 2**。
同藥的前案永遠值得精讀，數量不多，成本可控。

### 改動 3：Stage 2 rubric 修改（prompt 層 — 需專家確認）

現行：「重點分析 independent claim，而非 dependent claims」
提議：「分析所有 claims（含 dependent）。若任何 claim 明確提及
目標適應症且活性成分為目標藥物，即使給藥途徑不同，也應判 High —
同藥同適應症的前案構成 novelty/obviousness 威脅。」

**這條需要跟專家討論：**
- Risk 定義要偏 FTO（claim scope 直接覆蓋）還是偏 patentability（prior art 威脅）？
- 改了 rubric 後其他「同藥不同 route」的專利也會被升級，是否可接受？

---

## 相關檔案

| 檔案 | 用途 | 狀態 |
|------|------|------|
| `tools/debug_scoring.py` | 單筆專利 LLM scoring 重現（吸收 test_production_scoring.py）| pending |
| `tools/compare_runs.py` | 兩次 pipeline run 的 CSV diff 比較 | pending |
| `configs/pemirolast_ipf_v3.py` | Pemirolast × IPF 的 production config | ✅ |
| `output/gap_analysis_20260603_2232.xlsx` | 6/3 run（diskcache 問題，claims missing）| ✅ |
| `output/gap_analysis_20260616_2108.xlsx` | 6/16 rerun（diskcache 修復後，確認 rubric 問題）| ✅ |
| `logs/pemirolast_ipf_v3_rerun_20260616.log` | 6/16 rerun log | ✅ |
| `docs/spec/spec_debug_scoring.md` | debug_scoring 工具設計 spec | ✅ |
