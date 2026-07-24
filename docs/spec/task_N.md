# Task N — Batch EPO Fetch for skip_jurisdiction Patents (IPF 122 筆)

**Status:** ✅ Completed (2026-07-23)

> Retrospective note — written after execution.
>
> **1. Script 修正（實作途中）：**
> 第一次 `--apply --limit 5` 出現 `database is locked`。
> Root cause：script 的 conn 持有未 commit 的 write transaction，
> `_get_or_fetch` 內部的 upsert 被 SQLite single-writer lock 擋住。
> Fix：每筆 search_log write 後立即 `conn.commit()`。
> 修正後重跑 `--limit 5` 通過，再跑全量。
>
> **2. 差補結果：**
> （source: `batch_epo_fetch --apply` console output）
> 122 EP/WO IDs extracted from JSONL, 7 already in DB (1 原有 + 6 from
> testing runs), 115 fetched from EPO, 0 failures. search_log 全部補寫
> `will_manual_review`（沿用 Task L/M convention）。
> backfill_log run #26 (limit 5 test) + run #27 (full run)。
>
> Spec 預估「大部分 DB hit」— 實際只有 1 筆（`--dry-run` 首次結果：
> `Already in DB: 1, Would fetch: 121`）。原因：這批 EP/WO 的
> title/abstract 不含 pipeline `ta=` search 的關鍵字，是 Will 用
> citation chain / Espacenet 手動找到的，pipeline 從未 discover 過。
>
> **3. Coverage audit（全 CSV 437 筆 vs 122 筆 EP/WO）：**
>
> *全 CSV（probe_coverage_v2 --csv /tmp/ipf_for_probe.csv）：*
> （source: probe_coverage_v2 console output, Q4 section）
> - IPF search_log 582 → 704（+122）
>   （source: `inspect_backfill_log --list-projects`, Task N 前 582 → 後 704）
> - CSV IDs in DB：331/437（75.7%）
>   （source: Q4 `CSV IDs in patents table: 331`）
> - Task M baseline：210/437
>   （source: task_M.md retro, IPF `227 not in DB` → 437 - 227 = 210）
> - 106 筆仍不在 DB
>   （source: Q4 `CSV IDs NOT in patents table: 106`,
>    sample: `['AR122323A1', 'AT1016882T', ...]` — 全是非 EPO 管轄 jurisdiction）
> - EP 21 筆 all_three_empty
>   （source: Q3 `EP epo 35 ... all3_∅ = 21`。
>    原因未驗證 kind code，推測為 EP-A 階段 fulltext 未上架）
>
> *122 筆 EP/WO（scratch/check_122_quality.py）：*
> ```
> channel              NULL  empty   N/A   HAS  total
> abstract                0     24     0    98    122   (80%)
> claims                  0     40     0    82    122   (67%)
> examples_extracted      0    105     0    17    122   (14%)
>
> juris  total  abs_has  claims_has  ex_has  all_empty
> EP        35       11           7       6         21
> WO        87       87          75      11          0
> ```
> WO 表現正常（87/87 有 abstract，75/87 有 claims）。
> EP 35 筆中 21 筆 all_empty，原因待確認（推測 EP-A4 kind code 或 pending fulltext）。
> examples_extracted 偏低（17/122）是預期的：WO A1 無 detailed examples，
> EPO OPS 不提供 WO fulltext description。
>
> **4. 不需要執行的步驟：**
> - `backfill_snippets`：NULL snip = 0
>   （source: `inspect_backfill_log --list-projects`, IPF 行 NULL snip = 0）
>   `_get_or_fetch` 在 fetch 時已填好 formulation_snippets，no-op。

---

## Context

Task M（584 筆 expert JSONL import, shipped 2026-07-21）的 IPF run
產出 122 筆 `skip_jurisdiction`：

```
IPF（437 筆）：
  insert                  206
  skip_jurisdiction       122    (EP/WO, EPO authoritative)
  skip_dirty              89
  skip_no_useful_content  18
  skip_has_claims         2
```

Importer 正確地 skip 了這些 EP/WO 專利——JSONL（Google Patents scraper
產出）不應覆寫 EPO 的 authoritative data。但問題在：**這 122 筆中，
有多少根本不在 DB 裡？**

Pipeline 的 `ta=` search 只搜 title + abstract，且搜尋 index 對
EP-A 和 WO 的覆蓋不完整。Will 的 expert review 清單含這些 patent，
代表它們對 IPF prior art landscape 有意義。如果它們不在 DB 裡，
downstream 分析（LLM scoring、output CSV）就有盲區。

### 為什麼不用 JSONL 的資料？

EP/WO 專利在 EPO 有完整的 claims + description。用 EPO `_get_or_fetch`
走正常 fetch pipeline（含 B1 upgrade、family expansion、snippet
extraction、expiry date piggyback），資料品質優於 Google Patents
scraper 的轉錄。

### 與前置 Task 的差異

| | Task L | Task M | **Task N** |
|--|--------|--------|-----------|
| 對象 | 6 筆 expert-identified | 584 筆 JSONL batch | 122 筆 EP/WO skip_jurisdiction |
| 資料來源 | JSONL + EPO (WO) | JSONL | **EPO only** |
| DB 操作 | insert + update | insert + update | **fetch-if-missing** (DB hit = skip) |
| search_log | 手動 SQL | `--project` auto | Script auto |
| EPO API calls | 1 筆 (WO) | 0 | **up to 122 筆** |

---

## Goal

確保 Will 的 IPF expert review 清單中的 122 筆 EP/WO 專利都在
`cache/patents.db` 裡，有 EPO authoritative data，並在 `search_log`
中有 project entry 使 `backfill_snippets --project` 能找到它們。

---

## Design

### Input

從 IPF JSONL 中提取 `skip_jurisdiction` 的 patent IDs。

方法一（preferred）：script 直接讀 JSONL，重跑 classify logic 的
jurisdiction check 部分（`requested_id[:2] in ("EP", "WO")`），
不依賴 importer 的 log output。

方法二：從 JSONL 提取 EP/WO IDs 存成 txt，script 讀 txt。

選方法一——self-contained，不需要中間檔案，JSONL 是 source of truth。

### Processing Logic

Per patent ID:

1. **DB check**: `get_by_id(patent_id)`
   - If exists → `[DB hit]`, skip EPO fetch
   - If missing → proceed to step 2

2. **EPO fetch**: `_get_or_fetch(patent_id)`
   - 走正常 pipeline：biblio + claims + description + parse_examples
     + collect_snippets + upsert_patent
   - A1/A2 觸發 auto B1 upgrade + family expansion
   - source = 'epo'
   - `_get_or_fetch` 已內建 DB persist（upsert_patent），不需額外寫入

3. **search_log**: 無論 DB hit 或 EPO fetch，都檢查/補 search_log
   - `(patent_id, project)` 不存在 → INSERT
   - 已存在 → skip（idempotent）
   - `query = 'will_manual_review'`（沿用 Task L/M convention，patents.source='epo' 已能區分入 DB 路徑）

4. **Rate limiting**: `time.sleep(0.6)` between EPO fetches
   （比 `_get_or_fetch` 內建的 0.3-0.5s 多一點，safety margin）

### Output

Console report:

```
=== Task N: Batch EPO Fetch for skip_jurisdiction ===
  JSONL: data/global_patents_archive_IPF_idlist_20260709.jsonl
  Project: Pemirolast_吸入劑治療特發性肺纖維化_(IPF)

  Total EP/WO IDs in JSONL:  122
  Already in DB (skip):       98
  EPO fetched (new):          20
  EPO fetch failed:            4
  search_log written:         118  (2 already existed, 2 failed)

  Failures:
    EP1234567A1  — HTTPError 404 (biblio)
    WO2024999999A1 — timeout
    ...
```

### Audit Trail

- `backfill_log` entry via `_backfill_common.start_run / finish_run`
- `script = 'batch_epo_fetch_skip_jurisdiction'`
- `case_type = 'epo_fetch_skip_jurisdiction'`

### CLI

```bash
# Dry-run: report which IDs are in DB, which need fetch
python -m scripts.batch_epo_fetch \
    --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --dry-run

# Apply: actually fetch from EPO + write search_log
python -m scripts.batch_epo_fetch \
    --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --apply

# Optional: limit for testing
python -m scripts.batch_epo_fetch \
    --jsonl data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --apply --limit 5
```

### Safety

- `--dry-run` is default (no `--apply` = dry-run, same pattern as
  backfill_snippets and import_google_patents_jsonl)
- EPO fetch uses `_get_or_fetch` which is DB-first — if patent is
  already in DB, zero API calls
- `search_log` write uses duplicate check (same pattern as Task M
  importer's `_log_search`)
- No overwrites: `_get_or_fetch` calls `upsert_patent` which does
  ON CONFLICT UPDATE — but for a new row this is just INSERT; for an
  existing row it updates with EPO data (which is what we want for
  these EP/WO patents anyway)
- Rate limit: 0.6s between EPO fetches + `_get_or_fetch` internal
  0.3s sleeps = ~1s total per patent

### EPO Quota Impact

Worst case: 122 patents × (biblio + claims + description + family API)
= ~488 API calls. At ~1s each = ~8 minutes. Well within EPO 3.5 GB
weekly quota.

Expected case: most are already in DB (found by `ta=` search or family
expansion), so actual new fetches << 122.

---

## Post-Run Steps

After the script completes:

1. ✅ **Verify**: `python -m tools.check_db --file scratch/task_n_fetched.txt`
   (script writes a list of newly fetched IDs for verification)
   → 用 `batch_epo_fetch --dry-run` 確認 122 筆全部 DB hit。

2. ⏭ **Backfill snippets**: 不需要。
   `inspect_backfill_log --list-projects` 顯示 IPF NULL snip = 0，
   `_get_or_fetch` 在 fetch 時已填好 formulation_snippets。

3. ✅ **Coverage audit**:
   ```bash
   python -m tools.probe_coverage_v2 --csv /tmp/ipf_for_probe.csv
   ```
   結果見 retro note §3。另用 `scratch/check_122_quality.py` 查
   122 筆本身的資料品質。

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/batch_epo_fetch.py` | **新增** — batch EPO fetch for skip_jurisdiction IDs |
| `docs/spec/task_N.md` | 本文件 |

No schema migration. No module changes. Uses existing `_get_or_fetch`
and `_backfill_common` infrastructure.

---

## What This Does NOT Do

- Does not modify `import_google_patents_jsonl.py`
- Does not re-import JSONL data for these IDs (EPO is authoritative)
- Does not touch non-EP/WO patents (those are Task M scope)
- Does not run LLM scoring (blocked by OpenAI API key, separate task)
- Does not handle GPP project (GPP had 0 skip_jurisdiction in Task M)
