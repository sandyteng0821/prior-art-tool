# Task H — Google Patents L2 Integration

> 完成後請更新 `docs/architecture.md`。

## Status: Superseded by Task I (2026-06-03)
 
原 spec 假設 Google Patents scraping 在 production 機器執行,核心設計決策
(fetcher/backfill 兩層拆分、rate limit、熔斷、ToS 姿態) 都繞著「如何讓
production 機器安全地 hit Google Patents」打轉。
 
實際評估後因公司 IT policy 對 automated HTTP scraping 的網路層風險顧慮,
改採 **off-machine (Kaggle) scrape + JSONL import** 模式。Production
機器不再對 Google Patents 發出任何 HTTP request,改為讀取 Kaggle notebook
產出的 JSONL artifact 寫進 SQLite cache。
 
[Task I](task_I_google_patents_jsonl_import.md) 取代本 spec 的 fetcher +
backfill 兩層設計,只保留 importer。
 
保留本檔的價值:
- 記錄了當時為什麼考慮 fetcher/backfill 兩層 (risk isolation 推理),
  以及為什麼這個推理在 off-machine 模式下不適用
- 記錄了 EPO OPS 缺口的完整 context (PatentsView/ODP/PPUBS 都不通的證據)
- 「Open decision: description 寫進哪個欄位」的選項分析仍有效
  (Task I 採用「寫進 `examples_extracted`,免 schema migration」這個選項)
- 未來若需求變成 interactive 即時查詢 (不是 batch screening),需要恢復
  in-process fetcher 路徑,這份 spec 是起點
符合 PROJECT_SKILL §"Don't push spec forward when probe reveals 假設崩塌":
Task H 核心假設 (production 機器 scrape 可行) 在 review 階段崩塌,正確
做法是承認、記錄、開新 task,而非把新方案塞進舊 spec 的殼裡。

---

## Context

`modules/patent_fetcher.py` 對 US / CN / KR / JP 司法管轄的 fulltext request 會
回 404（EPO OPS licensing — see `docs/spec/task_C.md` 與 `docs/architecture.md`
§EPO OPS Data Coverage）。後果：

- 非 EP rows 的 `claims` / `examples_extracted` 欄位為空
- `backfill_snippets.py` 對這些 rows 跑出來的 `formulation_snippets` 都是 `'[]'`
- Downstream LLM analyzer 與 excipient eval pipeline 對非 EP 專利
  只能用 abstract 推論，準確度受限

`docs/spec/patentsview_probe_report.md` 確認三條曾考慮的補強路徑全部不通：
PatentsView API 已於 2026-03-20 關閉、USPTO ODP 需 ID.me 對非美國團隊不可用、
PPUBS PDF direct link 需 JWT session token 無法自動化。Google Patents HTML
是目前唯一 deterministic URL + 無認證 + 全球覆蓋的補強來源。

整合分三層（見 `docs/spec/design_data_source_selection.md` §Combo Strategy）：

| Level | 範圍 | 狀態 |
|-------|------|------|
| L1 | Output 加 `google_patents_url` 欄位 | 已 ship（standalone HTML 工具）|
| **L2** | **HTML scrape fulltext 進 SQLite cache** | **本 task** |
| L3 | BigQuery `patents-public-data` 獨立 search engine | Deferred |

---

## Goal

寫 Google Patents fetcher + backfill script，補非 EP rows 的 fulltext。

設計上拆兩個檔，mirror 既有 EPO OPS pattern：

- `modules/google_patents_fetcher.py` — production module，read-only HTTP fetch，
  無 DB 寫入。Pair with `modules/patent_fetcher.py`。
- `scripts/backfill_google_patents.py` — one-off operation，用 fetcher
  + `upsert_patent` 寫回 DB，audit log 進既有 `backfill_log` table。
  Pair with `scripts/backfill_snippets.py`。

---

## Files to Create

- `modules/google_patents_fetcher.py`（新檔）
- `scripts/backfill_google_patents.py`（新檔）

**不需要新增 schema、不需要新 audit table。** 既有 `_backfill_common.py`
infrastructure 直接重用（`start_run` / `finish_run` / `backfill_log` table —
see `docs/spec/task_D.md` §`backfill_log table`）。

---

## Required Behavior

### `modules/google_patents_fetcher.py`

```python
"""
Google Patents HTML fetcher for US (and other non-EP) fulltext supplement.

Read-only HTTP layer. No DB I/O. No CLI. Consumed by
scripts/backfill_google_patents.py.

URL pattern (deterministic):
    https://patents.google.com/patent/{normalized_id}/en
    where normalized_id = publication_number with dashes removed,
    plus zero-padding of US pub-number serial to 7 digits.
    Examples:
        "US-9457009-B2"     -> "US9457009B2"      (grant, no padding)
        "US-2007225293-A1"  -> "US20070225293A1"  (pub, 6→7 digit serial)
    See _normalize_publication_number() for the rule.

HTML structure used (schema.org itemprops, stable across all third-party
scrapers — see docs/spec/patentsview_probe_report.md §Probe 2):
    <meta name="DC.title" content="...">
    <div class="abstract">...</div>
    <section itemprop="claims">
      <div class="claim"><div class="claim-text">1. A method...</div></div>
      ...
    </section>
    <section itemprop="description">...</section>

Refs: docs/spec/patentsview_probe_report.md
"""
```

Public surface（最小集合）：

```python
@dataclass
class GooglePatentsResult:
    publication_number: str          # input, e.g. "US-9457009-B2"
    url: str                         # https://patents.google.com/...
    title: Optional[str] = None
    abstract: Optional[str] = None
    claims_text: Optional[str] = None      # 整個 claims section 純文字
    description_text: Optional[str] = None # 整個 description section 純文字
    fetched_at: Optional[str] = None       # ISO timestamp
    error: Optional[str] = None            # None = success; 否則記錄錯誤類別

def fetch_google_patents(
    publication_number: str,
    *,
    timeout: int = 30,
    user_agent: str = "Mozilla/5.0 (compatible; DrugRepurposingRadar/1.0)",
) -> GooglePatentsResult:
    """Single fetch. Caller responsible for rate limiting between calls."""

def _normalize_publication_number(raw: str) -> str:
    """Normalize raw publication number to Google Patents URL form.

    Handles edge case found via scratch prototype (N=13 sample sweep,
    2026-06-02): US publication numbers (e.g. "US2007225293A1") have a
    6-digit serial in raw form but Google Patents URL expects 7-digit
    (zero-padded): "US20070225293A1". US grant numbers and other
    jurisdictions don't have this quirk.

    Caller-facing: fetch_google_patents() invokes this internally;
    exposed for testability.
    """
```

行為要求：

1. **No retries on HTTP layer.** 失敗就 return result with `error` set；
   是否 retry 由 caller 決定。理由：fetcher 是 read-only 純函數，
   retry policy 屬 operational concern，應在 backfill script 控制。
2. **No silent except.** 任何 exception → 寫進 `result.error` field 並 return。
   不允許 swallow（per PROJECT_SKILL §"Don't silent-except"）。
3. **No sleep / rate limit in module.** 速率控制在 backfill script。
   理由同上：fetcher 是 pure read，不該知道 caller 的 batch context。
4. **Parser invariant**：當 HTML 結構解不出 claims section 時，
   `claims_text` 為 `None` 並在 `error` 標記 `"PARSE_NO_CLAIMS"`。
   不假設「沒抓到 = patent 沒 claims」（這兩者意義差很多）。
5. **Test fixtures**：建議在 `tests/fixtures/` 存 2-3 個真實 Google
   Patents HTML 樣本（US-9457009-B2 + 一個 KR + 一個 JP），跑 regression
   test 偵測 HTML 結構變動。

---

### `scripts/backfill_google_patents.py`

```python
"""
Backfill fulltext for non-EP rows from Google Patents HTML.

Targets rows where:
- patent_id starts with non-EP country code (US, CN, KR, JP, EA, ...)
- claims field is empty or NULL
- This is the gap documented in scripts/backfill_snippets.py docstring
  ("Enabling abstract-as-source is a separate enhancement, not Task D scope")

Does NOT re-extract formulation_snippets. After this backfill completes,
re-run scripts/backfill_snippets.py to extract snippets from the newly
populated claims/description text.

Usage:
    python -m scripts.backfill_google_patents --dry-run
    python -m scripts.backfill_google_patents --dry-run --project Ampicillin
    python -m scripts.backfill_google_patents --apply
    python -m scripts.backfill_google_patents --project Ampicillin --apply --limit 10

Refs: docs/spec/task_H_google_patents_l2.md
"""
```

行為要求：

1. **Query identifying target rows**：

   ```sql
   SELECT patent_id, publication_number FROM patents
   WHERE substr(publication_number, 1, 2) NOT IN ('EP', 'WO')
     AND (claims IS NULL OR claims = '')
     AND family_fetched = 1  -- 跳過尚未 expand 的，避免抓到 representative-only rows
   ```

   選擇性的 `--project X` filter via `search_log` JOIN（同 backfill_snippets pattern）。

2. **Per-row flow**：
   - Call `fetch_google_patents(publication_number)`
   - 若 `result.error` 非 None → log 並 skip（不寫 DB）
   - 否則 → `upsert_patent` 寫回 `claims` 與 `description` 欄位
   - **Open decision**：是否把 `description_text` 寫進現有 `examples_extracted`
     欄位（這樣 `backfill_snippets` 重跑時會自動處理）？或新增 column？
     **Implementation 時驗證後決定，不在 spec 預設答案**。

3. **Rate limit**：`time.sleep(2.0)` between fetches（per Google Patents 觀察
   到的合理速率；scrape > 50/session 容易被 throttle）。
   `--rate-delay` CLI arg override（default 2.0 秒）。

4. **`--dry-run`**：只印「會處理 N 筆」+ 列前 5 筆 publication_number。
   **不打 Google**，不寫 DB，不寫 backfill_log。

5. **`--apply`**：啟動時 `start_run(...)`，結束時 `finish_run(...)`，
   per task_D pattern。

6. **Audit log**：
   - `script = 'backfill_google_patents'`
   - `case_type = 'google_patents_fulltext'`
   - `args = json.dumps(cli_args)`
   - `notes` 紀錄 successful / skipped / error counts

7. **`--limit N`**：先小批量試跑，避免一次抓 1000+ 觸發 Google rate limit。

---

## Verification

```bash
# Pre-flight: count rows that need backfill
sqlite3 cache/patents.db "
SELECT substr(publication_number, 1, 2) AS cc, COUNT(*)
FROM patents
WHERE substr(publication_number, 1, 2) NOT IN ('EP', 'WO')
  AND (claims IS NULL OR claims = '')
  AND family_fetched = 1
GROUP BY cc ORDER BY 2 DESC;
"

# Dry-run（不打 Google）
python -m scripts.backfill_google_patents --dry-run --project Ampicillin

# Small-batch apply
python -m scripts.backfill_google_patents --project Ampicillin --apply --limit 10

# Audit log 應該有一筆新 row
sqlite3 cache/patents.db "
SELECT id, started_at, completed_at, script, rows_affected, notes
FROM backfill_log
WHERE script = 'backfill_google_patents'
ORDER BY id DESC LIMIT 5;
"

# 隨機抽一筆驗證 fulltext 進來了
python -m tools.inspect_patent <patent_id_from_log>
# 應該看到 claims 不再是空的

# 觸發 snippet 重抽
python -m scripts.backfill_snippets --project Ampicillin --apply

# 確認 formulation_snippets 不再全是 '[]'
sqlite3 cache/patents.db "
SELECT patent_id, formulation_snippets FROM patents
WHERE patent_id IN (SELECT patent_id FROM patents WHERE substr(publication_number, 1, 2) = 'US' LIMIT 10);
"
```

---

## Non-Goals

- 不做 L1（已 ship 為 standalone HTML 工具，與 pipeline 解耦）
- 不做 L3（BigQuery `patents-public-data` integration — 未來 task）
- 不做 schema migration（不新增 column，除非 implementation 驗證後判定必要）
- 不做 full-text indexing / search engine 功能（Google Patents 是補強，不是主搜索）
- 不做 LLM analyzer 介面變更（fulltext 進 DB 後，analyzer 自動受益）
- 不為了補 CN/KR/JP 而新增 translation pipeline（Google Patents 自帶機翻，
  寫進 description_text 即可，後續分析時看品質再決定）
- 不打 Google Patents 高量 batch（>50 / session 違反 ToS 灰色地帶；
  本 task 的目標是補 cache 中已知缺口的個別 patent，不是 bulk scrape）

---

## Design Decisions

### Why fetcher is backfill-only, not main-pipeline fallback?

Fetcher 不被 `modules/patent_fetcher.py` 在 fulltext 404 時自動 fallback
呼叫；只在 `scripts/backfill_google_patents.py` 內被觸發。

考慮過的替代方案是 main pipeline 整合：EPO OPS fulltext 404 → 同 request
內 fallback 到 Google Patents → 直接寫進 cache。決定**不採此方案**，理由：

1. **Risk isolation** — Google Patents ToS / IP block / HTML 大改若發生，
   只影響 backfill operation，主 pipeline 與既有 EPO OPS 流程不受波及
2. **ToS 姿態** — Backfill 是「有意識的批次補資料」，可控、可暫停、可說明
   batch size；main pipeline fallback 每次跑都隱含 hit Google，volume
   難以解釋
3. **Pattern 對位** — `backfill_snippets`、`backfill_family` 都是 backfill-
   only，不進主 pipeline。Google Patents 採同模式，整個 backfill 家族
   保持一致風格
4. **Reversibility** — A 要停用 Google Patents：不跑 backfill 即可。
   若改 B（main pipeline 整合），停用需改 `patent_fetcher.py` 並驗證
   沒副作用
5. **PROJECT_SKILL §"Don't merge inspect tool + backfill" 精神延伸** —
   fetcher（read-only API call）與「主 pipeline auto-store」混在同一條
   路徑，risk profile 也會打架

何時應考慮改 B：use case 變成需要互動式查詢（搜尋當下就要看到 US claims）。
目前是 batch screening 模式，backfill 模式完全夠用。

### Why fetcher in `modules/`, backfill in `scripts/`?

Mirror 既有 EPO OPS pattern：
- `modules/patent_fetcher.py`（production）+ `scripts/backfill_snippets.py`（operation）
- `modules/google_patents_fetcher.py`（production）+ `scripts/backfill_google_patents.py`（operation）

Risk profile 切開（per PROJECT_SKILL §"Don't merge inspect tool + backfill"）：
- Fetcher = read-only，pure function，未來 LLM analyzer 也可直接 import 使用
- Backfill = write operation，需 dry-run + audit log + scoped to non-EP gap

### Why reuse `_backfill_common.py` instead of writing new infrastructure?

`backfill_log` table 與 `start_run` / `finish_run` 已是穩定 audit pattern
（task_D 建立、task_F 後沿用）。新增 source 不該另起爐灶。

`case_type = 'google_patents_fulltext'` 是 backfill_log 的新值，但
schema 沒變（`case_type` 是 free-text column），不需 migration。

### Why NOT re-extract formulation_snippets in this script?

關注點分離（separation of concerns）：

- `backfill_google_patents.py` = fetch + 寫 raw text
- `backfill_snippets.py` = extract from raw text

合在一起會：
1. 違反「one script one risk profile」原則
2. 讓 future re-run snippet extraction 跟 re-fetch fulltext 綁死
3. Snippet extraction 邏輯改了之後沒辦法 re-run（不會想再打 1000 次 Google）

執行順序：先 backfill_google_patents → 再 backfill_snippets。Verification
那節已示範。

### Open decision deferred to implementation

`description_text` 寫進哪個欄位是 implementation 時要 probe 後決定的：

| 選項 | 優點 | 缺點 |
|------|------|------|
| 寫進現有 `examples_extracted` | snippet extraction 自動受益、無 schema 變動 | 名稱誤導（"examples_extracted" 是 EPO 特定的 examples section） |
| 新增 `description_text` column | 語意清楚 | 需 schema migration、`backfill_snippets` 也要改 |
| 不寫 description，只寫 claims | 最簡單，足以解 LLM analyzer 缺口 | 失去 description 中的 example 段落（formulation 資訊主源）|

**不在 spec 預先決定。** Implementation 時抽 5-10 個樣本看 description 內容
品質，再判斷。

---

## Risks

| 風險 | 嚴重度 | 緩解 |
|------|--------|------|
| Google ToS 對 automated scraping 灰色 | 中 | Rate limit 2 秒、batch ≤ 50、UA 標識身分；用 case 是補既有 cache 而非 bulk scrape |
| Google 封 IP | 低（給定 rate limit） | `--limit N` 小批量試跑；遇 HTTP 429 立即停止 |
| HTML 結構變動 | 低（schema.org `itemprop` 業界穩定） | tests/fixtures + regression test |
| 非 US 專利的 claims 不完整 | 已知 | Google 對非 US patent 提供 abstract + 機翻；接受品質差，標記為「fulltext from GP」於 audit notes |
| 抓到的內容跟 EPO 對應 EP family 不一致 | 低 | Spot check 5-10 筆對應 EP 家族成員（如有），確認 claims 等價 |

---

## Implementation Order

1. **Probe L2 fulltext selectors**（US / KR / JP / CN 各 1）。確認
   `<section itemprop="claims">` 與 `<section itemprop="description">`
   selector 在每個 jurisdiction 上都成立、抽出的純文字對 LLM analyzer
   實用。**Metadata fields**（title / abstract / dates / assignee）已於
   scratch prototype 驗證（N=13）；本步驟針對 fulltext，prototype 未涵蓋。
2. **Decide description 寫入策略**（above Open decision）。寫成 implementation
   note 加進這份 spec 的 retrospective section。
3. **`modules/google_patents_fetcher.py` + tests/fixtures**
4. **`scripts/backfill_google_patents.py` dry-run mode only**
5. **Small-batch `--apply --limit 10`**，確認 audit log + DB rows + rate
   limit 行為都對
6. **Full run per project**（Ampicillin → Pemirolast → Acetaminophen）
7. **Re-run `backfill_snippets.py`** per project
8. **更新 `docs/architecture.md`**：新增 `modules/google_patents_fetcher.py`
   到 module map、`scripts/backfill_google_patents.py` 到 backfill scripts
   list、EPO OPS Data Coverage 那節註明缺口已有 Google Patents 補強路徑

---

## References

- `docs/spec/patentsview_probe_report.md` — 為什麼選 Google Patents
  （三條替代路徑全部不通的證據）
- `docs/spec/design_data_source_selection.md` — Combo Strategy 整體位置
- `docs/spec/task_D.md` — `backfill_log` table + `_backfill_common.py` pattern
- `docs/spec/task_C.md` — EPO OPS US/CN/KR/JP fulltext 缺口的原始紀錄
- `modules/patent_fetcher.py` — EPO OPS fetcher，本 task 的對位 module
- `scripts/backfill_snippets.py` — Snippet extraction backfill，本 task 完成後需 re-run
