# Task M — Expert Patent Batch Import (584 筆 GP JSONL → DB)

> Retrospective spec — written after implementation.

**Status:** Shipped 2026-07-21

---

## Context

接續 Task L（6 筆手動 expert import, shipped 2026-07-07）。

Will 提供兩份 Google Patents 專利清單，涵蓋兩個 drug repurposing
project 的完整 expert-reviewed prior art：

| Project | JSONL | 專利數 | 來源 |
|---------|-------|-------|------|
| GPP (Darifenacin × 廣泛性膿皰型銀屑病) | `global_patents_archive_GPP_idlist_20260709.jsonl` | 147 | Will expert review |
| IPF (Pemirolast × 特發性肺纖維化) | `global_patents_archive_IPF_idlist_20260709.jsonl` | 437 | Will expert review |

Task L 處理 6 筆時，手動用 SQL 補 `search_log`（5 筆 INSERT）。
584 筆不能手動——需要自動化。

### 與 Task L 的差異

| | Task L | Task M |
|--|--------|--------|
| 規模 | 6 筆 | 584 筆 |
| search_log | 手動 SQL insert | importer 自動補（`--project`） |
| JSONL 來源 | `will_review_ipf_gpp_patents.jsonl` | 2 份 GP archive JSONL |
| Provenance query | `will_manual_review` | `will_manual_review`（沿用 Task L convention） |

---

## What Changed

### 1. Importer 改進：`--project` + `--query` 自動補 search_log

Task L「未來改善方向」指出 `--allow-insert` 後需手動補 search_log，
容易遺漏。本次直接解決。

**新增 `_log_search()` 函數：**
- 在 `_apply_insert` / `_apply_update` 完成後呼叫
- `INSERT INTO search_log (project, query, patent_id, searched_at)`
- 寫入前檢查 `(patent_id, project)` 是否已存在，避免重複（re-run safe）
- 在同一個 transaction 內完成（不會出現 patent 寫了但 search_log 沒寫的狀態）

**新增 CLI args：**
- `--project`：project name for search_log（不提供時不碰 search_log，backward compatible）
- `--query`：search_log.query 欄位（default `'will_manual_review'`，沿用 Task L convention）

**output 新增一行報告：**
```
search_log entries written: 139  (project=..., query=will_manual_review)
```

**Backward compatibility：** 不帶 `--project` 時行為完全不變。

### 2. JSONL Import 結果

**GPP（147 筆）：**
```
insert                  138
apply                   1      (TW202535404A, existing row updated)
skip_dirty              6
skip_no_useful_content  2
search_log written      139
```

**IPF（437 筆）：**
```
insert                  206
skip_jurisdiction       122    (EP/WO, EPO authoritative)
skip_dirty              89
skip_no_useful_content  18
skip_has_claims         2
search_log written      206
```

合計：345 筆寫入 DB，345 筆 search_log。

**Note:** IPF dry-run 原為 insert=207 / skip_has_claims=1。
Apply 時變 206/2，因為 GPP 先跑，有一筆專利同時出現在兩份 JSONL，
GPP insert 後 IPF 看到 DB 已有 claims → skip_has_claims。正確行為。

### 3. Backfill Snippets

```
GPP: 139/139 updated, 22 non-empty snippets (15.8%), 117 got '[]'
     aliases: [Darifenacin, Enablex, Emselex]

IPF: 206/206 updated, 7 non-empty snippets (3.4%), 199 got '[]'
     aliases: [Pemirolast, BMY-26517, TBX, Alegysal]
```

Non-empty 比例低是預期行為：Will 的清單是用疾病篩的（GPP / IPF
相關專利），大多數專利提到疾病但不一定提到目標藥物名。
`formulation_snippets = '[]'` 代表「processed, no drug name mention」，
不是 error。Downstream LLM analyzer 會從 abstract/claims 全文評估。

---

## Verification

### Audit Trail (backfill_log)

```
#21  import GPP      139 rows  ok
#22  import IPF      206 rows  ok
#23  snippets GPP    139 rows  ok  (non_empty=22)
#24  snippets IPF    206 rows  ok  (non_empty=7)
```

### probe_coverage_v2

**GPP (147 CSV IDs):**
- 139 in DB, 8 not in DB (= 6 dirty + 2 no_content) ✓
- claims 100%, examples_extracted 100%
- 0 all_three_empty

**IPF (437 CSV IDs):**
- 210 in DB, 227 not in DB (= 122 jurisdiction + 89 dirty + 18 no_content) ✓
- claims 99.0%, examples_extracted 99.0%
- 1 all_three_empty (EP row, epo source, pre-existing — not Task M)

---

## Commands Used

```bash
# Dry-run
python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive_GPP_idlist_20260709.jsonl \
    --allow-insert --dry-run \
    --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_'

python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --allow-insert --dry-run \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)'

# Apply
python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive_GPP_idlist_20260709.jsonl \
    --allow-insert --apply \
    --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_'

python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --allow-insert --apply \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)'

# Backfill snippets
python -m scripts.backfill_snippets \
    --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_' \
    --aliases Darifenacin Enablex Emselex --apply

python -m scripts.backfill_snippets \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --aliases Pemirolast BMY-26517 TBX Alegysal --apply

# Verify
python -m tools.inspect_backfill_log --show -n 5
python -m tools.probe_coverage_v2 --csv /tmp/gpp_for_probe.csv
python -m tools.probe_coverage_v2 --csv /tmp/ipf_for_probe.csv
```

---

## DB 從零重建時的恢復

1. 跑兩份 JSONL import（含 `--project`）：
   ```bash
   python -m scripts.import_google_patents_jsonl \
       --input data/global_patents_archive_GPP_idlist_20260709.jsonl \
       --allow-insert --apply \
       --project 'Darifenacin_治療廣泛性膿皰型銀屑病_(GPP)_'
   python -m scripts.import_google_patents_jsonl \
       --input data/global_patents_archive_IPF_idlist_20260709.jsonl \
       --allow-insert --apply \
       --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)'
   ```
2. Backfill snippets（GPP + IPF 分開跑）
3. WO2024123825A1 仍需 EPO direct fetch（Task L 那筆，不在本次 JSONL 裡）

與 Task L 不同：不再需要手動 SQL 補 search_log，`--project` 自動處理。

---

## 未解決 / 後續

- **IPF 122 筆 EP/WO skip_jurisdiction**：importer 正確地 skip（EPO
  是 authoritative source），但這些專利可能不在 DB 裡（pipeline `ta=`
  search 未命中）。需要一隻 batch script 對這些 ID 跑 EPO
  `_get_or_fetch`，persist 到 DB。可能是 Task N。
- **LLM scoring**：OpenAI API key 暫時沒加值，345 筆新 patent 尚未
  跑 scoring。等 key 恢復後處理。
- **architecture.md 更新**：importer 的 `--project` / `--query` 描述
  待補入。

---

## Files Changed

- `scripts/import_google_patents_jsonl.py` — 新增 `--project`, `--query`, `_log_search()`
- `docs/spec/task_M.md` — 本文件（retro spec）
