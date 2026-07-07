# Task L — Expert-Reviewed Patent Import (--allow-insert)

> Retrospective spec — written after implementation.

**Status:** Shipped 2026-07-07

---

## Context

Will（IP 專家）人工審查後提供 6 筆 prior art 專利，這些專利因為
EPO OPS `ta=`（title + abstract）搜尋的限制，pipeline Phase 1 從未
找到。關鍵內容在 claims 或 description 中，不在 title/abstract 裡。

最關鍵的是 **US9415051B1**（high risk）：claim 5 字面寫了 Pemirolast
治 IPF，但 abstract 只提 "airway hyperresponsiveness"——`ta=pemirolast`
搜得到它，但 pipeline 的 query 組合搜不到 IPF 相關性。這筆在 Task I
已匯入 DB（`skip_has_claims`），問題不在匯入而在搜尋。

其餘 5 筆：4 筆從未進過 DB（EPO 搜尋未命中 + 非 family expansion
成員），1 筆是 WO（EPO 是 authoritative source，直接用 `_get_or_fetch`
正常流程拉取）。

### 專利清單

| patent_id | project | risk | EPO OPS 狀態 | 關鍵段落 | 本次處理 |
|-----------|---------|------|-------------|---------|---------|
| CN113164377A | IPF | low | title + abstract | example 圖10A, 圖10B | **insert** (JSONL) |
| CN114470207B | IPF | low | 全空 | disclosure | **insert** (JSONL) |
| US9415051B1 | IPF | high | 全空（Task I 已補） | claim 5 | skip_has_claims |
| CN119384294A | GPP | low | title only | disclosure 0062-0063 | **insert** (JSONL) |
| US9642912B2 | GPP | low | title + abstract | disclosure (combo drug list) | **insert** (JSONL) |
| WO2024123825A1 | GPP | low | EPO authoritative | disclosure | **EPO fetch** |

---

## Goal

將 Will 人工找到的專利匯入 DB，讓 downstream pipeline（LLM analyzer、
output writer）能涵蓋這些 EPO 搜尋死角的專利。

---

## What Changed

### 1. `scripts/import_google_patents_jsonl.py` — 新增 `--allow-insert`

原 Task I importer 只 UPDATE 已存在的 row（`skip_not_in_db` 保護）。
新增 `--allow-insert` flag 允許 INSERT 不在 DB 裡的專利。

`_classify` 新增第七個 verdict：

| # | Verdict | Condition | Reason |
|---|---------|-----------|--------|
| 3a | `insert` | `patent_id` not in DB **AND** `--allow-insert` **AND** has useful content | Create new row |
| 3b | `skip_not_in_db` | `patent_id` not in DB **AND** no `--allow-insert` | Original behavior preserved |

新增 `_apply_insert` 函數：
- JSONL fields → DB columns mapping（title, abstract, claims,
  full_text → examples_extracted, publication_date → year）
- `source = 'google_patents'`
- `family_fetched = 0`（無 family expansion）
- `formulation_snippets = NULL`（待 backfill_snippets 處理）

不帶 `--allow-insert` 時行為完全不變（backward compatible）。

### 2. WO2024123825A1 — EPO direct fetch

WO 專利由 EPO 提供完整資料，不走 JSONL import（importer 正確地
`skip_jurisdiction`）。改用正常 EPO fetch 流程：

```python
from modules.patent_fetcher import _get_or_fetch
r = _get_or_fetch('WO2024123825A1')
# title: NOOTKATONE FOR THE TREATMENT OF PSORIASIS OR ATOPIC DERMATIT...
# claims: 3000 chars
# family timeout (transient, not blocking)
```

### 3. `search_log` 手動補入 5 筆

`backfill_snippets` 的 `--project` filter 靠 `search_log JOIN`，
新 insert / fetch 的 row 不在 `search_log` 裡，需要手動補：

```sql
INSERT OR IGNORE INTO search_log (patent_id, project, query) VALUES
  ('CN113164377A',  'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)',    'will_manual_review'),
  ('CN114470207B',  'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)',    'will_manual_review'),
  ('CN119384294A',  'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_',   'will_manual_review'),
  ('US9642912B2',   'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_',   'will_manual_review'),
  ('WO2024123825A1','Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_',   'will_manual_review');
```

`query = 'will_manual_review'` 標記來源為人工審查，區別於 EPO 自動搜尋。

### 4. JSONL 資料檔

`data/will_review_ipf_gpp_patents.jsonl` — Kaggle scraper 產出，6 筆。
沿用 Task I 的 off-machine scraping 模式。Gitignored。

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/import_google_patents_jsonl.py` | 新增 `--allow-insert` flag、`_apply_insert` 函數、`_classify` 新增 `insert` verdict |
| `data/will_review_ipf_gpp_patents.jsonl` | 新增（gitignored，Kaggle scraper 產出） |

No new modules. No schema migration.

---

## Verification

```bash
# 1. JSONL import dry-run
python -m scripts.import_google_patents_jsonl \
    --input data/will_review_ipf_gpp_patents.jsonl \
    --allow-insert --dry-run
# Result: insert=4, skip_has_claims=1, skip_jurisdiction=1

# 2. JSONL import apply
python -m scripts.import_google_patents_jsonl \
    --input data/will_review_ipf_gpp_patents.jsonl \
    --allow-insert --apply
# Result: insert=4, skip_has_claims=1, skip_jurisdiction=1

# 3. WO2024123825A1 — EPO direct fetch (manual, no scripted tool)
#    importer correctly skip_jurisdiction for WO; use EPO fetch directly
python3 -c "from modules.patent_fetcher import _get_or_fetch; r = _get_or_fetch('WO2024123825A1'); print(f'claims={len(r.get(\"claims\",\"\"))} chars')"
# Result: claims=3000 chars (family API timeout, transient)

# 3a. Confirm WO is in DB with content
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/patents.db')
r = conn.execute(
    'SELECT patent_id, formulation_snippets, source FROM patents WHERE patent_id = ?',
    ('WO2024123825A1',)
).fetchone()
print(f'{r[0]}  snippets={r[1]!r:.40}  source={r[2]}')
"
# Result: WO2024123825A1  snippets='["[0244]    [0243] In another embodimen  source=epo
# Note: EPO fetch pipeline already populated formulation_snippets,
# so backfill_snippets Step 7 correctly returns 0 candidates.

# 4. Insert search_log entries (manual, 5 rows — 4 JSONL + 1 WO)
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/patents.db')
entries = [
    ('CN113164377A',  'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)',  'will_manual_review'),
    ('CN114470207B',  'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)',  'will_manual_review'),
    ('CN119384294A',  'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_', 'will_manual_review'),
    ('US9642912B2',   'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_', 'will_manual_review'),
    ('WO2024123825A1','Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_', 'will_manual_review'),
]
for pid, proj, query in entries:
    conn.execute(
        'INSERT OR IGNORE INTO search_log (patent_id, project, query) VALUES (?, ?, ?)',
        (pid, proj, query)
    )
conn.commit()
print(f'Inserted {conn.total_changes} rows into search_log')
"
# Result: Inserted 5 rows (ran in two batches: 4 + 1)

# 5. Backfill snippets — IPF
python -m scripts.backfill_snippets \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --aliases Pemirolast BMY-26517 TBX Alegysal \
    --apply
# Result: 2/2 rows, 0 non-empty snippets, 2 got '[]'
# Expected: CN113164377A & CN114470207B are GDF-15 biomarker patents,
# don't mention Pemirolast → '[]' is correct.

# 6. Backfill snippets — GPP (round 1: JSONL-imported rows)
python -m scripts.backfill_snippets \
    --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_' \
    --aliases Darifenacin Enablex Emselex \
    --apply
# Result: 2/2 rows, 2 non-empty snippets, 0 got '[]'
# Expected: CN119384294A & US9642912B2 mention Darifenacin → snippets found.

# 7. Backfill snippets — GPP (round 2: after WO fetch + search_log)
python -m scripts.backfill_snippets \
    --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_' \
    --aliases Darifenacin Enablex Emselex \
    --apply
# Result: 0/0 rows — WO2024123825A1 already had formulation_snippets
# from EPO fetch pipeline, candidate filter (IS NULL) correctly skipped.
```

---

## Operational Notes

### DB 從零重建時的恢復

如果 `cache/patents.db` 被刪除重建，EPO `ta=` 搜尋仍然不會找到
這些專利。恢復步驟：

1. JSONL import: `python -m scripts.import_google_patents_jsonl --input data/will_review_ipf_gpp_patents.jsonl --allow-insert --apply`
2. WO fetch: `python3 -c "from modules.patent_fetcher import _get_or_fetch; _get_or_fetch('WO2024123825A1')"`
3. 手動補 `search_log`（見上方 SQL，5 筆）
4. 重跑 `backfill_snippets`（IPF + GPP 分開跑）

`data/will_review_ipf_gpp_patents.jsonl` 是 4 筆 CN/US 專利的 source
of truth，勿刪除。WO2024123825A1 從 EPO 拉取，不依賴 JSONL。

### Provenance convention（本 Task 確立）

專利的 audit trail 由兩個欄位分工：

| 問題 | 欄位 | 說明 |
|------|------|------|
| 誰找到的（discovery） | `search_log.query` | 搜尋 query 或手動來源標記 |
| 資料從哪來（data source） | `patents.source` | `epo` / `google_patents` / `mixed_epo_google_patents` |

本次 6 筆的 provenance：

| patent_id | search_log.query | patents.source |
|-----------|-----------------|----------------|
| CN113164377A | `will_manual_review` | `google_patents` |
| CN114470207B | `will_manual_review` | `google_patents` |
| US9415051B1 | （Task I 已存在） | `google_patents` |
| CN119384294A | `will_manual_review` | `google_patents` |
| US9642912B2 | `will_manual_review` | `google_patents` |
| WO2024123825A1 | `will_manual_review` | `epo` |

手動操作（`_get_or_fetch` + SQL insert）不經 `backfill_log`，
provenance 靠 `search_log.query` + 本 spec 記錄。

**Convention going forward：** 任何手動介入 DB 的操作都要補
`search_log`，`query` 用描述性字串標來源，例如：
- `will_manual_review` — IP 專家人工審查
- `manual_epo_fetch` — 手動 EPO 拉取
- `ta=drug AND ta=disease` — pipeline 自動搜尋
- `family_of:EP1234567A1` — family expansion

### 與 Task I 的關係

本次操作是 Task I 的延伸，差別在：

| | Task I | Task L |
|--|--------|--------|
| 來源 | Kaggle batch scrape（400+ 筆）| Will 人工審查（6 筆） |
| DB 操作 | UPDATE existing rows | INSERT new rows（`--allow-insert`）+ EPO fetch |
| search_log | 已存在（EPO 搜尋進來的）| 需手動補入 |
| 觸發原因 | EPO 不授權非 EP fulltext | EPO `ta=` query 搜不到（內容在 claims/description）|

### 未來改善方向

- **`--allow-insert` 應自動補 `search_log`**：目前 insert 後需手動補
  search_log 才能被 `--project` filter 撈到，容易遺漏。importer 可以
  在 insert 時自動寫一筆 search_log（需要知道 project name，可能要加
  `--project` arg 到 importer）。
- **Google Patents search probe（L3b）**：本次附帶產出
  `probe_google_patents_search.py`，產 Google Patents search URL 供
  手動驗證。如果 probe 結果正面，L3b 可以 scale 到自動搜尋，減少對
  人工審查的依賴。見 `design_data_source_selection.md` §L3b。
