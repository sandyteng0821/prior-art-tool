# Task P — Build SureChEMBL Patent Annotation Cache + Lookup

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 註：原草稿誤標 Task D（repo 已有 task_D，內容不同），改為 Task P（接在 task_O 後）。
> 前置條件：SureChEMBL bulk data 已下載，`probe_surechembl_bulk.py` Phase 1/2 基本驗證完成。
> 完成後更新 SureChEMBL tool guide（新增 annotation cache 一節，共用邊界 reference 回主 guide、不重寫）。
> `docs/architecture.md` 暫不更新 —— 本工具與 orange_book / probe_surechembl 同類、未接主 pipeline；
> 等 JSONL 橋接進 Phase 4/5 再補 arch 條目（符合「不為未實作 wiring 寫 arch」的慣例）。

---

## Context

目前 SureChEMBL bulk tooling 已能：

* 將 drug name 外部解析成 InChIKey，再 join SureChEMBL `compounds`
* 執行 drug × indication matrix
* 依 `field_id` 判斷 annotation 出現在 Description / Claims / Abstract / Title
* 用 `--inspect-patent` 檢視單篇 patent 的 compound / biomedical annotation
* 以 `biomedical_entities + biomedical_locations` 找疾病 annotation

SureChEMBL bulk schema 中：

```text
patents
patent_compound_map
compounds
biomedical_entities
biomedical_locations
biomedical_types
fields
```

其中 `field_id` 已驗證對應：

```text
1 = Description
2 = Claims
3 = Abstract
4 = Title
5 = Image
6 = MOL attachment
```

目前 `--inspect-patent` 的用途主要是 debug：

* compound side 只統計每個 field 有多少 compound
* biomedical side 顯示 annotation，但未整理成固定 patent-level schema
* biomedical annotation 未限定只輸出 Disease
* biomedical result 目前只列前 30 筆，不適合作為完整 annotation cache

另一方面，目前 GPSS / prior-art workflow 會累積大量 patent IDs。未來可能需要處理約：

```text
~685 cached patent sets
~50,000 unique patent IDs
```

使用情境不是重新做 drug × indication search，而是：

```text
given patent ID
→ 查 SureChEMBL 已有 annotation
→ 依 Title / Abstract / Claims / Description 整理
→ 列出 identified drugs / diseases
→ 作為 patent reading 前的低成本 screening layer
```

目標是讓 GPSS 2D matrix 找到 patent 後，可以先快速判斷：

```text
這篇在哪些 section 有 known drug？
這篇在哪些 section 有 disease？
drug / disease 是否出現在同一 section？
這篇是不是只有大量 compound laundry list？
是否值得進一步抓 full text / LLM analysis？
```

---

## Goal

建立一個獨立的 SureChEMBL patent annotation cache / lookup tool，使：

```text
Patent ID
→ SureChEMBL patent_id
→ field-level chemical annotations
→ field-level disease annotations
→ known-drug mapping
→ cached structured result
```

具體需支援：

1. 輸入單一 patent ID，列出 Title / Abstract / Claims / Description 中的 identified drugs / diseases
2. 輸入 patent ID list，批次建立 annotation cache
3. 約 50,000 patents 時不得逐篇重新掃描 SureChEMBL parquet
4. chemical annotations 必須區分：

   * all SureChEMBL compounds
   * recognized known drugs
5. disease annotations 僅使用 `biomedical_types.type_name = 'Disease'`
6. 保留 annotation 所在 field 與 occurrence count
7. 建立 patent-level summary，供 GPSS / downstream SQL 快速 filter / ranking
8. 支援日後直接 lookup cache，不需再次掃原始 SureChEMBL bulk data

---

## Files to Add / Modify

新增：

* `build_surechembl_annotation_cache.py`

可重用但原則上不修改核心邏輯：

* `probe_surechembl_bulk.py`

  * reuse `canon_pn()`
  * reuse `_find_table()`
  * reuse `_src()`
  * reuse `connect_duck()`
  * reuse `FIELD_NAMES`

* `resolve_inchikey.py`

  * 作為 drug dictionary 建置流程的既有來源
  * 不要求本 Task 改寫 resolver

新增資料檔格式：

* `drug_dictionary.tsv`

輸出：

* `surechembl_annotations.duckdb`

文件：

* `surechembl_tool_guide.md`（新增 annotation cache 一節；共用邊界 reference 回主 guide、不重寫）
* `docs/architecture.md` —— **暫不動**（未接主 pipeline，見 header 說明）

---

## Required Changes

### 1. Add patent ID normalization + batch resolution

輸入可接受：

```text
US9415051B1
US09415051B1
US-9415051-B1
WO2011161255A2
WO-2011161255-A2
```

沿用既有 `canon_pn()` normalization 規則。

現有工具已特別處理 US leading-zero 問題，且不得把同樣規則套到 EP / EA 等 jurisdiction。

新增支援：

```bash
python3 build_surechembl_annotation_cache.py \
    --patent US9415051B1 \
    --data-dir ./sc_bulk \
    --drug-dictionary drug_dictionary.tsv
```

以及：

```bash
python3 build_surechembl_annotation_cache.py \
    --patents patent_ids.txt \
    --data-dir ./sc_bulk \
    --drug-dictionary drug_dictionary.tsv \
    --cache surechembl_annotations.duckdb
```

Batch mode 不得：

```python
for patent in patent_ids:
    inspect_patent(...)
```

應先將所有 normalized patent IDs 一次 join `patents.parquet`，建立 target patent table，再批次 join downstream annotation tables。

Suggested pattern:

```sql
CREATE TEMP TABLE target_patents AS
SELECT
    id AS patent_id,
    patent_number,
    family_id,
    publication_date,
    title
FROM patents
WHERE normalized_patent_number IN (...);
```

後續所有 annotation query 只針對 `target_patents`。

---

### 2. Extract all chemical annotations by field

chemical annotation 來源：

```text
target_patents
→ patent_compound_map
→ compounds
```

SureChEMBL `compounds` table 只有：

```text
id
smiles
inchi
inchi_key
mol_weight
```

沒有 compound name / drug name。

因此不得將：

```text
all annotated compounds
```

直接輸出為：

```text
identified drugs
```

每筆 raw chemical annotation 至少保留：

```text
patent_number
patent_id
field_id
field_name
compound_id
inchi_key
smiles
mol_weight
```

例如：

```sql
SELECT
    p.patent_number,
    m.patent_id,
    m.field_id,
    m.compound_id,
    c.inchi_key,
    c.smiles,
    c.mol_weight
FROM patent_compound_map m
JOIN target_patents p
  ON p.patent_id = m.patent_id
JOIN compounds c
  ON c.id = m.compound_id;
```

只需處理：

```text
field_id IN (1,2,3,4)
```

本 Task 不需要 Image / MOL attachment annotations。

---

### 3. Add known-drug reverse mapping

建立：

```text
drug_dictionary.tsv
```

最低 schema：

```text
drug_name
inchikey
inchikey_skeleton
source
```

可選欄位：

```text
chembl_id
drug_status
alias
```

`resolve_inchikey.py` 已有：

```text
drug name
→ PubChem / ChEMBL
→ InChIKey
```

以及 14-character connectivity skeleton handling。

本 Task 反向使用 dictionary：

```text
SureChEMBL compound InChIKey
→ drug_dictionary
→ canonical drug_name
```

matching 優先順序：

```text
1. exact 27-char InChIKey
2. 14-char skeleton match
```

必須保留 match provenance：

```text
drug_name
drug_match_type = exact | skeleton
```

例如：

```text
compound_id = 12345
inchi_key = XXXXX...
drug_name = pemirolast
drug_match_type = exact
```

未 match drug dictionary 的 chemical annotation 仍需保留為 compound，但：

```text
is_known_drug = false
drug_name = NULL
```

---

### 4. Extract Disease annotations by field

Disease annotation 來源：

```text
target_patents
→ biomedical_locations
→ biomedical_entities
→ biomedical_types
```

必須限制：

```sql
WHERE biomedical_types.type_name = 'Disease'
```

避免 GeneOrProtein 等 biomedical entities 混入疾病列表。現有工具已發現僅靠文字搜尋可能混入 GeneOrProtein。

每筆至少輸出：

```text
patent_number
patent_id
field_id
field_name
entity_id
original_text
corrected_text
resolved_form
count
```

其中：

```text
corrected_text
```

作為 annotation display name，

```text
resolved_form
```

目前通常作為 MeSH identifier 使用。

不得 `LIMIT 30`。

Batch cache 需保存該 patent 的完整 Disease annotation。

---

### 5. Build normalized field-level output

每篇 patent 對使用者顯示：

```json
{
  "patent_number": "US9415051B1",

  "Title": {
    "known_drugs": [],
    "diseases": []
  },

  "Abstract": {
    "known_drugs": [],
    "diseases": []
  },

  "Claims": {
    "known_drugs": [],
    "diseases": []
  },

  "Description": {
    "known_drugs": [],
    "diseases": []
  }
}
```

known drug entry suggested schema：

```json
{
  "name": "pemirolast",
  "compound_id": 12345,
  "inchikey": "XXXXXXXXXXXXXX-XXXXXXXXXX-X",
  "match_type": "exact"
}
```

disease entry suggested schema：

```json
{
  "name": "idiopathic pulmonary fibrosis",
  "entity_id": 12345,
  "mesh_id": "D054990",
  "count": 8
}
```

同一 drug / disease 在同一 field 重複 annotation 時需 deduplicate，但 occurrence count 不得遺失。

---

### 6. Build DuckDB annotation cache

不要輸出約 50,000 個 independent JSON files。

預設 cache：

```text
surechembl_annotations.duckdb
```

至少建立四張表。

#### `patent_meta`

```text
patent_number
patent_id
country
publication_date
family_id
title
surechembl_release
```

#### `patent_compound_annotation`

```text
patent_number
patent_id
field_id
field_name
compound_id
inchi_key
smiles
mol_weight
drug_name
is_known_drug
drug_match_type
```

#### `patent_disease_annotation`

```text
patent_number
patent_id
field_id
field_name
entity_id
original_text
corrected_text
resolved_form
count
```

#### `patent_annotation_summary`

至少：

```text
patent_number

title_compound_count
abstract_compound_count
claims_compound_count
description_compound_count

title_known_drug_count
abstract_known_drug_count
claims_known_drug_count
description_known_drug_count

title_disease_count
abstract_disease_count
claims_disease_count
description_disease_count

has_known_drug
has_disease
has_drug_and_disease
drug_disease_same_field

drug_in_claims
disease_in_claims

drug_in_tiabcl
disease_in_tiabcl

drug_description_only
disease_description_only
```

`drug_tiabcl / disease_tiabcl / same_field` 可比照既有 Phase 2 定義，不重新定義語意。現有 Phase 2 已用 field 2/3/4 對應 GPSS TI/AB/CL scope。

---

### 7. Add cache lookup mode

建立 cache 後：

```bash
python3 build_surechembl_annotation_cache.py \
    --lookup US9415051B1 \
    --cache surechembl_annotations.duckdb
```

必須：

* 不重新讀取 SureChEMBL 15+ GB parquet
* 從 local cache 回傳 field-level annotation
* 同時顯示 summary

Suggested CLI output：

```text
Patent: US9415051B1

Title
  Drugs:
    -
  Diseases:
    -

Abstract
  Drugs:
    pemirolast
  Diseases:
    idiopathic pulmonary fibrosis [D054990] (2)

Claims
  Drugs:
    pemirolast
  Diseases:
    idiopathic pulmonary fibrosis [D054990] (5)

Description
  Drugs:
    pemirolast
    nintedanib
  Diseases:
    idiopathic pulmonary fibrosis [D054990] (17)

Summary
  compounds: 81
  known drugs: 2
  diseases: 1
  drug + disease same field: yes
```

---

### 8. Handle patent-not-present separately

SureChEMBL `patents.parquet` 的定義不是所有 patent publications，而是有 chemical extraction 的 patent subset。

現有 `--inspect-patent` 已確認：

```text
patent 不在 patents.parquet
≠ SureChEMBL web UI 沒有這篇
≠ patent 不存在

可能代表 SureChEMBL 沒從該 publication 抽到 chemical structure
```

因此 batch output 必須區分：

```text
status = found
status = not_in_bulk_patents
status = input_not_resolved
```

不得把：

```text
not_in_bulk_patents
```

寫成：

```text
no drugs
```

或：

```text
no annotation
```

---

### 9. Add biomedical coverage flag

目前 tool guide 已驗證：

```text
SureChEMBL disease annotation stops at 2025-12-31
```

2026 patents chemical annotations仍有資料，但 biomedical / disease annotation 為 0，不能解讀成「沒有 disease mention」。

因此 `patent_meta` 或 summary 必須增加：

```text
biomedical_annotation_status
```

Suggested values：

```text
available
outside_known_coverage
unknown
```

例如：

```text
publication_date = 2024-06-01
→ biomedical_annotation_status = available

publication_date = 2026-03-01
→ biomedical_annotation_status = outside_known_coverage
```

若：

```text
outside_known_coverage
```

則：

```text
disease_count = 0
```

不得在 user-facing output 顯示成：

```text
No disease identified
```

應顯示：

```text
Disease annotation unavailable for this publication period
```

或 equivalent status。

每次更換 SureChEMBL release，coverage boundary 仍應依 tool guide 重新驗證，而不是永久 hardcode 2025-12-31。

---

### 10. Keep annotation separate from semantic relationship

SureChEMBL bulk 沒有 Title / Abstract / Claims / Description 原文。

它提供的是：

```text
entity X
→ appears in patent Y
→ field Z
→ occurrence count N
```

而不是：

```text
patent explicitly teaches drug X for disease Y
```

tool guide 已明確指出 bulk data 是 entity annotation index，不是 patent fulltext index。

因此本工具不得產生：

```text
supports indication
claims treatment
prior art positive
drug treats disease
```

之類 semantic conclusion。

只能輸出：

```text
drug X annotated in Claims
disease Y annotated in Claims
drug X and disease Y occur in same field
```

真正 relationship / prior-art 判斷留給 downstream full-text + LLM analysis。

---

### 11. Add cheap screening features for GPSS workflow

本 Task 需要讓 cache 可用於 GPSS candidate screening。

GPSS results 可：

```text
GPSS patent list
LEFT JOIN patent_annotation_summary
```

先依 structural annotation 做排序，例如：

```text
drug + disease both in Claims
> drug + disease both in Abstract
> drug + disease in same field
> drug + disease anywhere in patent
> drug only
> disease only
```

同時保留：

```text
description_compound_count
description_known_drug_count
```

用來辨認可能的 laundry-list patent。

不得在本 Task 實作 hard delete，例如：

```text
if description_compound_count > 1000:
    drop patent
```

只提供 feature / flag 給 downstream ranking 使用。

Suggested summary fields：

```text
large_compound_list
```

門檻若要設定，必須沿用或重新以 ground truth 校準，不得 arbitrary hardcode。

---

## Expected Outcome

完成後：

```bash
python3 build_surechembl_annotation_cache.py \
    --patent US9415051B1 \
    --data-dir ./sc_bulk \
    --drug-dictionary drug_dictionary.tsv
```

應可直接看到：

```text
Title
  known drugs
  diseases

Abstract
  known drugs
  diseases

Claims
  known drugs
  diseases

Description
  known drugs
  diseases
```

Batch：

```bash
python3 build_surechembl_annotation_cache.py \
    --patents gpss_patent_ids.txt \
    --data-dir ./sc_bulk \
    --drug-dictionary drug_dictionary.tsv \
    --cache surechembl_annotations.duckdb
```

應：

* 一次 resolve 所有 target patents
* 一次批次 join chemical annotations
* 一次批次 join Disease annotations
* 建立 cache tables
* 建立 patent summary
* 不逐 patent 重複掃描原始 parquet

之後：

```bash
python3 build_surechembl_annotation_cache.py \
    --lookup US9415051B1 \
    --cache surechembl_annotations.duckdb
```

應只讀 cache 即可完成查詢。

---

## Verification

### Test 1 — single patent field annotation

```bash
python3 build_surechembl_annotation_cache.py \
    --patent US9415051B1 \
    --data-dir ./sc_bulk \
    --drug-dictionary drug_dictionary.tsv
```

確認：

```text
Title / Abstract / Claims / Description
```

四個 field 分開顯示。

確認 known drugs 只來自 `drug_dictionary` match，不是把所有 SureChEMBL compounds 當 drug。

---

### Test 2 — raw compound vs known drug distinction

SQL：

```sql
SELECT
    patent_number,
    field_name,
    count(*) AS compounds,
    count(*) FILTER (WHERE is_known_drug) AS known_drugs
FROM patent_compound_annotation
WHERE patent_number = 'US9415051B1'
GROUP BY patent_number, field_name
ORDER BY field_name;
```

Expected：

```text
compounds >= known_drugs
```

且一般 medicinal chemistry patent 應可能出現：

```text
many compounds
few or zero known drugs
```

---

### Test 3 — Disease-only biomedical entities

```sql
SELECT DISTINCT type_name
FROM biomedical_types t
JOIN biomedical_entities e
  ON e.type_id = t.id
JOIN biomedical_locations l
  ON l.entity_id = e.id
JOIN target_patents p
  ON p.patent_id = l.patent_id;
```

cache 中的 `patent_disease_annotation` 必須只包含：

```text
Disease
```

不得混入：

```text
GeneOrProtein
```

或其他 biomedical type。

---

### Test 4 — same-field summary

```sql
SELECT
    patent_number,
    drug_in_claims,
    disease_in_claims,
    drug_disease_same_field
FROM patent_annotation_summary
WHERE patent_number = 'US9415051B1';
```

人工對照 raw compound / disease annotation 的 field_id，確認 summary 正確。

---

### Test 5 — 2026 biomedical coverage guard

選一篇 2026 publication。

確認：

```text
biomedical_annotation_status = outside_known_coverage
```

若 Disease annotation 為空，不得顯示為 reliable negative。

---

### Test 6 — batch performance behavior

準備：

```text
100
1,000
```

個 patent IDs。

確認 batch implementation 使用：

```text
target_patents
→ batch join
```

而不是：

```python
for patent_id in patents:
    scan patent_compound_map
    scan biomedical_locations
```

不要求固定 runtime threshold，但 SQL / execution plan 必須是 set-based batch processing。

---

### Test 7 — cache-only lookup

build cache 後暫時移開：

```text
./sc_bulk
```

再執行：

```bash
python3 build_surechembl_annotation_cache.py \
    --lookup US9415051B1 \
    --cache surechembl_annotations.duckdb
```

Expected：

```text
lookup succeeds without original SureChEMBL parquet files
```

---

## Non-Goals

* 不抓 patent full text
* 不抽 Description / Claims 原文句子
* 不做 drug × disease semantic relation extraction
* 不判斷 patent 是否構成 prior art
* 不判斷 drug 是否真的用於該 disease
* 不修改 GPSS search logic
* 不取代既有 Phase 2 drug × indication matrix
* 不把所有 SureChEMBL compounds 視為 drugs
* 不建立全球完整 drug ontology
* 不處理 biologics / antibodies 的 chemical annotation coverage
* 不處理 SureChEMBL 不收錄的 zero-small-molecule patents
* 不以 DOCDB simple family 自動替代 missing publication
* 不做 laundry-list hard filtering
* 不改 `resolve_inchikey.py` 的 PubChem / ChEMBL resolver 邏輯
* 不處理 2026 disease annotation 缺口本身，只需正確標示 coverage status
