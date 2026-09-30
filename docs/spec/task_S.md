# Task S — 修 query_builder doc-rot + 標註 reserved config

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 承「Config refactoring」討論串。**修正版**：初版把 `MECHANISMS`/`FORMULATIONS`/`INDICATIONS`
> 當「死 config」要刪；經確認它們是**保留給未來 mechanism/indication 查詢策略**的欄位
> （query_builder docstring 的 Strategy F/G，目前未實作）＋ `FORMULATIONS`/`INDICATIONS`
> 另有 `debug_scoring` probe 在用 → **保留，不刪**。
> 前置條件：無。與 Task R 獨立。

---

## Context

盤點 config → 各 module 使用（見 `design_config_map.md`）時，發現 query_builder 有一組「import 了但
`build_queries()` 沒用」的欄位。初版判定是死 config，看過 docstring + 設計意圖後修正如下。

**真相是「reserved，不是 dead」。** `build_queries()` 目前只實作 Strategy A / D / CUSTOM_QUERIES，
body 實際只用到 `DRUG_ALIASES`、`CUSTOM_QUERIES`、`SEARCH_ONLY_GRANTED`、`SEARCH_YEAR_RANGE`。
但 import 頂端還帶著 `MECHANISMS` / `FORMULATIONS` / `INDICATIONS` —— 這三個正是 docstring 裡
**Strategy F（mechanism-based，cognitive impairment × PDE4）** 與 **Strategy G（indication-based，
spinocerebellar）** 的輸入。那兩個策略後來從 `build_queries()` 拿掉了，但 import 與 docstring 沒清
→ 看起來像死的，其實是「為了未來想這樣查而保留」。所以**這三個 config 欄位保留**
（`FORMULATIONS`/`INDICATIONS` 另外還有 `debug_scoring` 的 keyword probe 在用）。

**真正該修的是「說一套、做一套」。** 現在 query_builder 的 docstring 宣稱 Strategy F/G 存在（還附
US10357486B2、127/23/12/200 筆、Roflumilast、SCA）—— 但 (a) code 根本沒實作 F/G，(b) 專案早換成
Pemirolast × IPF。誰讀 query_builder 想搞懂搜尋策略，會被這段誤導。這才是這張票的主菜。

**唯一真死的是 `MAX_WORKERS`。** 併發設定（=1），全案無人讀，也沒有任何 docstring/註解記載未來要
平行化 —— 沒有 reserved 的理由，可刪（除非你確實要留給未來 parallel fetch，那就比照下面標成 reserved）。

本票只做 doc/註解修正 ＋ 標註 ＋ 刪一個死設定，**不改任何行為、不實作 F/G**。

---

## Goal

1. 修 query_builder 的 doc-rot：docstring 不再宣稱 Strategy F/G 已實作、去掉 Roflumilast/SCA/
   US10357486B2 舊專案內容；`[:1]` 註解去專案綁定。
2. 把 reserved 的 config 欄位標清楚（`MECHANISMS`/`FORMULATIONS`/`INDICATIONS` = 未來
   mechanism/indication 查詢用、目前未接線），讓它們不再看起來像死的、也不再是誤導性的 unused import。
3. 刪真死的 `MAX_WORKERS`（或若要保留給未來平行化，同樣標 reserved）。

---

## Files to Modify

- `modules/query_builder.py` —— 修 docstring ＋ `[:1]` 註解；處理 3 個 unused import（見 #2）。
- `config.py` —— 標註 `MECHANISMS`/`FORMULATIONS`/`INDICATIONS` 為 reserved；刪（或標註）`MAX_WORKERS`。
- （**不動** `tools/debug_scoring.py` —— `FORMULATIONS`/`INDICATIONS` 保留，probe 照舊。）
- （完成後）更新 `design_config_map.md`（reserved 標註、`MAX_WORKERS` 移除反映到主表）＋ 視需要 `docs/architecture.md`。

---

## Required Changes

### 1. `modules/query_builder.py` — 修 doc-rot

- `build_queries` docstring 改成只描述**現行** Strategy A / D / CUSTOM_QUERIES。Strategy F/G 若要保留為
  未來計畫，改寫成明確的「未實作 / 計畫」措辭（例如 `# 未來計畫：F mechanism-based、G indication-based，尚未接線`），
  **不要**寫成好像已在跑。刪掉 Roflumilast、SCA、US10357486B2、以及那些筆數（舊專案跑出來的）。
- `DRUG_ALIASES[:1]` 註解去專案綁定，例如：「只用第一個別名 `DRUG_ALIASES[0]`；其餘別名 EPO 多半查無結果」
  —— 別再寫死 Roflumilast，免得下次換專案又爛。

### 2. `modules/query_builder.py` — 處理 unused import（二選一）

`MECHANISMS` / `FORMULATIONS` / `INDICATIONS` 目前 import 了但 body 沒用。二選一：
- **(建議) 移除 import ＋ 留註解**：從 `from config import (...)` 拿掉這三個，並加一行
  `# 未來加 mechanism/indication 查詢策略時，從 config import MECHANISMS/FORMULATIONS/INDICATIONS`。
  → 沒有 unused-import cruft、意圖仍有記錄。
- **(或) 留 import ＋ `# noqa`**：保留但標明「reserved for future strategies」，壓下 linter。

### 3. `config.py` — 標註 reserved ＋ 刪死設定

- `MECHANISMS` / `FORMULATIONS` / `INDICATIONS`：集中並標上
  ```
  # ── reserved：未來 mechanism/indication 查詢策略用（query_builder 目前未接線）；
  #    FORMULATIONS / INDICATIONS 另供 debug_scoring keyword probe ──
  ```
  讓換專案的人知道它們現在不影響搜尋、但別亂刪。
- `MAX_WORKERS`：刪。（若要保留給未來 parallel fetch，改標 `# reserved：未來平行抓取用，目前未接線`。）

---

## Expected Outcome

- query_builder 讀起來與實際行為一致：docstring 不再宣稱 F/G 已實作、沒有 Roflumilast/SCA。
- `config.py` 的 reserved 欄位有明確標註，不再看起來像死的；`MAX_WORKERS` 消失（或標 reserved）。
- **行為零變化** —— 只改 docstring / 註解 / import ＋ 標註；搜尋 / 抓取 / 分析輸出不變。
- `FORMULATIONS`/`INDICATIONS` 保留 → `debug_scoring` 的 keyword probe 照舊可用。

---

## Verification

```bash
# doc-rot 掃描：不該再有舊專案字樣
grep -in "roflumilast\|spinocerebellar\|\bSCA\b\|US10357486B2" modules/query_builder.py   # 預期：空

# 若選「移除 import」：query_builder 不炸、queries 數不變
python -c "import modules.query_builder as q; print(len(q.build_queries()), 'queries')"

# MAX_WORKERS 若刪：無殘留引用
grep -rn "MAX_WORKERS" *.py    # 預期：空（或只剩 config 的 reserved 標註列）

# reserved 欄位仍在、debug probe 仍讀得到
python -c "import config; print(bool(config.MECHANISMS), bool(config.FORMULATIONS), bool(config.INDICATIONS))"

# 回歸：清理前後 build_queries() 輸出逐條相同（只動註解/import，不動邏輯）
```

---

## Non-Goals

- **不實作 Strategy F/G**（mechanism/indication 查詢策略是未來 feature，不在本票）。
- **不刪 `MECHANISMS`/`FORMULATIONS`/`INDICATIONS`**（reserved ＋ debug 在用）—— 修正自本票初版。
- **不修 30 字截斷根因**（DB 資料 bug，另開 ticket；寫入點已確認 = `patent_fetcher.py:37`）。
- **不做 config 分區重排**（更大的 refactor，見 `design_config_map.md`）。
- **不動 `config.py` 的 provenance header**。
- 與 **Task R 無關**。

---

## Commit plan

- `docs(query_builder): 修 docstring doc-rot（去 Roflumilast/SCA/未實作的 F-G 宣稱）`
- `chore(config): 標註 reserved 欄位（MECHANISMS/FORMULATIONS/INDICATIONS）+ 移除死設定 MAX_WORKERS`
  - body: 根因（reserved 欄位看似死；MAX_WORKERS 真死）/ 處理（標註 vs 刪）/ 驗證（grep 無舊字樣、queries 數不變）/ refs: task_S.md #2 #3
