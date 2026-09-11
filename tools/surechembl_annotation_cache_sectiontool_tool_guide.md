# SureChEMBL Patent Annotation Cache — 使用說明

> 這是 `surechembl_tool_guide.md`（bulk 工具 probe / resolve）的延伸，講 annotation
> cache（`build_surechembl_annotation_cache.py`）。**共用的邊界不重寫，直接指回主
> guide `surechembl_tool_guide.md`**；這節只講 cache 特有的部分。

---

## 這個工具解決什麼問題

主 guide 那兩支是「**找**」：InChI × disease matrix，找出哪些專利同時提到藥和病
（含 GPSS 撈不到的 description-only）。

這支是「**篩已有的**」：手上已經有一票 patent IDs（GPSS 命中、EPO family、
expert 清單…），讀全文之前先低成本知道每一篇——

```
哪個 field（Title / Abstract / Claims / Description）有 known drug？
哪個 field 有 disease？drug 和 disease 有沒有落在同一個 field？
是不是只有一大坨 compound laundry list？值不值得再花 full-text / LLM 成本？
```

輸出是**可重複查的 DuckDB cache**，建一次之後 `--lookup` 不必再碰 15GB parquet。

三支工具的分工：

| 角色 | 工具 | 解決 |
|---|---|---|
| Retrieval 找 | matrix（`probe_surechembl_bulk.py --phase 2`）| 找 EPO/GPSS 漏掉的，含 description-only |
| **Screening 篩** | **本工具 annotation cache** | 已有 patent 逐 field 標註 + 排序訊號 |
| Interpretation 判 | 主 pipeline Phase 4/5（full-text / LLM）| 關係到底是什麼 |

---

## 沿用主 guide 的邊界（不重寫，只提醒一樣適用）

- **疾病斷在 2025-12-31** → 主 guide 邊界 1（含每版重驗的 query）。本工具把它
  收成一個欄位 `biomedical_annotation_status`（見下 C）。
- **compounds 表無名稱** → 主 guide 邊界 2 / `resolve_inchikey` 段。所以 known
  drug 只能靠字典（見下 B）。
- **撈得到 ≠ 分析得到** → 主 guide 邊界 3。本工具是 screening / 排序層，**不下語意
  結論**（同 field 共現 ≠ teaching），關係判斷留 Phase 4/5。
- **family 是 DOCDB simple、非 INPADOC、只含有化學標註的成員**；JP 日期哨兵；
  無法律狀態 / 到期日 / 原文 → 主 guide 「資料來源與限制」表。

---

## ★ 本工具特有、額外要知道的

### A. `not_in_bulk_patents` 是覆蓋率**上界**，不是「沒有」

`patents.parquet` = 有化學抽取的專利（主 guide inspect-patent 第 1 層）。所以
`not_in_bulk` 只代表**本篇**沒抽到小分子；其中一批是發明其實在庫裡、只是換一個
family member 才有標。

實例（Bug Y）：`WO2022028472A1` 缺席，但同家族 `US20230293730A1` 有標
（bromocriptine + SMA 在 description）。

→ 報「真覆蓋率」用**發明層級**（手上的 EPO family 清單裡「≥1 成員 found」的比例），
不要用公開案層級的 found%。本工具刻意不自動用 family 補（見 task_P non-goals）。

### B. `has_known_drug` = 命中字典，不是「有藥」

因為 compounds 無名稱（主 guide 邊界 2），known drug = 抽到的 InChIKey 命中你給的
`--drug-dictionary`。字典外的藥即使有標，也只算 compound、`is_known_drug=false`。

→ 23 藥字典就只認得那 23 藥；跨 685 藥的 SOP2 前，先用 `resolve_inchikey.py` 把
字典擴到 685 再 `--replace-cache`（cache 會 hash 字典、拒絕混用不同字典）。

### C. `biomedical_annotation_status`（把邊界 1 變成欄位）

| 值 | 意義 |
|---|---|
| `available` | pub ≤ 斷點，disease 負值可信 |
| `outside_known_coverage` | pub 在斷點後，`disease_count=0` **不可**當「沒提到病」|
| `unknown` | pub_date 是哨兵 / 缺 |

斷點別 hardcode：`--biomedical-through` 可覆寫，每版依主 guide 邊界 1 重驗。

### D. 比對是 publication 層級精確配（號碼 + kind code）

沿用 `canon_pn`（US 剝前導零、EP/EA 不剝）。餵 `US9415051B1` 只配到
`US-9415051-B1`。US 的 application 與 grant **是不同號碼**，所以餵 grant、但 SC 只標
了 application 公開案時 → not_in_bulk（發明其實在庫裡，記進 A）。沒帶 kind code 的
input 判 `input_not_resolved`，不會靜默併進別的公開案。

---

## 常用指令

```bash
# 單篇（直接印）
python3 tools/build_surechembl_annotation_cache.py \
    --patent US9415051B1 --data-dir ./sc_bulk \
    --drug-dictionary ./data/drug_inchikey.tsv

# 批次（set-based：一次 join patents 建 target，下游批次 join，不逐篇掃）
python3 tools/build_surechembl_annotation_cache.py \
    --patents ./data/patent_ids.txt --data-dir ./sc_bulk \
    --drug-dictionary ./data/drug_inchikey.tsv \
    --cache surechembl_annotations.duckdb \
    --tmp-dir ./duckdb_tmp --mem-limit 9GB

# 查（不碰 parquet；--data-dir 移走也能查）
python3 tools/build_surechembl_annotation_cache.py \
    --lookup US9415051B1 --cache surechembl_annotations.duckdb
```

id 清單接受 `US9415051B1` / `US09415051B1` / `US-9415051-B1` / `WO2011161255A2` 混寫。

---

## ★ 建完先看資料品質（server 無 SQL client，用 script）

```bash
python3 tools/report_annotation_cache.py --cache surechembl_annotations.duckdb
# 加 --data-dir ./sc_bulk 會多做一項 US 補零 join 檢查（需讀 patents.parquet）
```

只讀 cache（read_only），不碰 15GB parquet，印四段：

- **① 覆蓋率三桶** — found / not_in_bulk_patents / input_not_resolved（not_in_bulk 是上界，見 A）
- **② found 品質** — 依 `biomedical_annotation_status` 分，各自的 known-drug / disease 數（C 的斷點在這裡看得到）
- **③ laundry-list 分佈** — `description_compound_count` 分桶，校準 `large_compound_list` 門檻
- **④ 候選排序** — drug+disease 同 field 優先，給 GPSS candidate 用

（每段對應的 SQL 就寫在 `report_annotation_cache.py` 各函式的 docstring 裡，有 SQL client 也可直接抄。）

---

## Cache 結構

一個 release + 一份字典 = 一個 cache（工具會擋混用）。四張表：

| 表 | 內容 |
|---|---|
| `patent_meta` | patent_id、number、country、pub_date、family_id、release、`status`、`biomedical_annotation_status` |
| `patent_compound_annotation` | 每個 (patent, field, compound)：inchi_key / smiles / mw / drug_name / is_known_drug / match_type |
| `patent_disease_annotation` | 每個 (patent, field, Disease entity)：corrected_text / resolved_form(MeSH) / count（**只 `type_name='Disease'`**）|
| `patent_annotation_summary` | 每篇一列：各 field compound / known-drug / disease 數 + `has_*` / `same_field` / `*_in_claims` / `*_tiabcl` / `*_description_only` / `large_compound_list` |

`drug_tiabcl / disease_tiabcl / same_field` 沿用 Phase 2 定義（field 2/3/4 = GPSS
TI/AB/CL scope），不重新定義。

限制沿用主 guide 「資料來源與限制」表；非目標見 `docs/spec/task_P.md`。
