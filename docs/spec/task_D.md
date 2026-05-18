# Task D — Backfill Family Expansion + Formulation Snippets

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 前置條件：Task A 完成、Bug X fix（commit c3206ce）已 merge 到 main。
> 完成後請更新 `docs/architecture.md`。

---

## Context

兩個歷史問題在 Bug X 修完後遺留下來，需要 backfill 既有 DB row：

**Case 1（Gap Analysis row 3a）：`family_of = NULL`**

部分 patent 是在 `family_of` 欄位加入 schema 之前就存進 DB 的，
家族關係沒記錄到。已知 4 筆：

- `EP2443120B1` — Crystalline form of Pemirolast
- `EP2107907B1` — Pemirolast + ramatroban combination
- `EP1285921B1` — Pemirolast preparation process
- `NO20210693B1` — Capsaicin × IPF

**Case 2（Gap Analysis row 3f）：Missing A-series family members**

May 2026 之前 `_fetch_and_store_family()` filter 只收 B1/B2，
跨 jurisdiction 的 A series（TW/KR/AU/JP 等）被 silently skip。
Bug X (commit c3206ce) 已將 filter 放寬到 `{B1, B2, A1, A2, A}`，
但既有 `family_fetched=1` 的 row 走 `[DB hit]` path，不會自動
re-expand。

**Case 3（Gap Analysis row 3b）：Pre-Task-A NULL snippets**

Task A 之前抓的 patent，`formulation_snippets` 欄位是 NULL。
經 Day 1 demo script 確認，DB 1866 patent 中 1857 篇是 NULL。
這個跟 Case 1/2 邏輯獨立——不用打 EPO，直接對既有
`claims + examples_extracted` 重跑 `_extract_formulation_snippets`
就好。

---

## Goal

寫 backfill script 處理三個 case。**Case 1 + 2 共享同一個機制**
（reset `family_fetched=0` 然後重 fetch parent），**Case 3 獨立
不打 EPO**。

設計上分兩個 script：

- `scripts/backfill_family.py` — 處理 Case 1 + Case 2
- `scripts/backfill_snippets.py` — 處理 Case 3

---

## Files to Create

- `scripts/backfill_family.py`（新檔）
- `scripts/backfill_snippets.py`（新檔）
- `scripts/__init__.py`（新檔，空）

**不要動 `modules/` 任何檔案。** Backfill 是 ad-hoc operation，
不該污染 production module。

---

## Required Behavior

### `scripts/backfill_snippets.py`

最簡單，先做這個。

```python
"""
Backfill formulation_snippets for rows where the field is NULL.

Does NOT call EPO API — re-runs _extract_formulation_snippets on
existing claims + examples_extracted in DB.

Usage:
    python -m scripts.backfill_snippets             # all NULL rows
    python -m scripts.backfill_snippets --project Acetaminophen
    python -m scripts.backfill_snippets --dry-run   # show count, don't write
"""
```

行為：

1. 預設用 `config.DRUG_ALIASES`。可以 `--aliases ...` override。
2. 預設處理所有 `formulation_snippets IS NULL`。可以 `--project X`
   過濾（用 `search_log` JOIN）。
3. 對每個 row：
   - 跑 `_extract_formulation_snippets(claims + " " + examples, aliases)`
   - 用 `upsert_patent` 寫回（保留其他欄位）
4. `--dry-run`：只印「會處理 N 筆」，不寫 DB。
5. 印 progress：每 100 筆 print 一次。

### `scripts/backfill_family.py`

處理 Case 1 + Case 2。**比較危險**——會打 EPO API、會新增 row。

```python
"""
Backfill family expansion for patents fetched before May 2026 filter
widening.

Resets family_fetched=0 on identified parents, triggering re-fetch.
This will re-call EPO family API for each parent.

Usage:
    python -m scripts.backfill_family --dry-run       # preview
    python -m scripts.backfill_family --case 1        # only family_of=NULL
    python -m scripts.backfill_family --case 2        # only pre-May-2026
    python -m scripts.backfill_family --case all      # both
    python -m scripts.backfill_family --project Pemirolast  # scope to project
    python -m scripts.backfill_family --max 10        # cap API calls
"""
```

行為：

1. **必要：先 dry-run**。預設行為應該是 `--dry-run`，要 `--apply`
   才真的寫。
2. **必要：`--max` cap**。預設 `--max 50`，避免一口氣打爆 EPO quota。
3. 識別 Case 1 candidates：
   實作上 hard-code 已知 4 筆 patent ID（見 Context 列表）。
   SQL 描述上是「family_fetched=1 但部分 family member family_of=NULL」，
   但這個 query 複雜且只有 4 筆，hard-code 更簡單。
4. 識別 Case 2 candidates：
```sql
   SELECT patent_id FROM patents
   WHERE family_fetched = 1
     AND fetched_at < '2026-05-18'  -- Bug X 修法當天
     AND patent_id LIKE '%A1' OR patent_id LIKE '%A2'
```
5. 對每個 candidate：
   - `UPDATE patents SET family_fetched=0 WHERE patent_id=?`
   - 清掉相關 diskcache key
   - 呼叫 `_get_or_fetch(patent_id, year)` 觸發重 expansion
6. 印 progress + 統計：新加進來幾筆、跳過幾筆。

### `backfill_log` table (shared by both scripts)

加一個 audit table 記錄每次 backfill 執行：

```sql
CREATE TABLE IF NOT EXISTS backfill_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    script        TEXT NOT NULL,         -- 'backfill_snippets' | 'backfill_family'
    case_type     TEXT,                  -- 'snippets' | 'family_case_1' | 'family_case_2'
    args          TEXT,                  -- JSON of CLI args
    rows_affected INTEGER,
    git_commit    TEXT,                  -- output of `git rev-parse HEAD`
    notes         TEXT
);
```

兩個 script 都應該：

1. 啟動時 INSERT 一筆 row，記錄 `started_at`, `script`, `args`, `git_commit`
2. 結束時 UPDATE 該 row，填上 `completed_at`, `rows_affected`
3. **Dry-run 不寫 log table**——dry-run 是 "preview"，不是真的 backfill，
   不該污染 audit trail。Verification 範例的 log queries 應該永遠
   只 return 真實執行過的 row。
4. 若 crash 中斷，row 留下 `completed_at IS NULL`，後續可以追

migration：第一次跑 backfill script 時 `CREATE TABLE IF NOT EXISTS` 順便建。

---

## Expected Outcome

`backfill_snippets.py` 跑完：
- 大部分（不是全部）NULL row 變成 JSON 字串（多半是 `[]`，因為很多
  patent 的 claims/examples 也是空的，特別是 US/CN）
- Acetaminophen project 的 EP granted patent 應該有實質 snippet

`backfill_family.py` 跑完：
- Case 1 的 4 個 known patent 的 family member 都有 `family_of` 值
- Case 2 的 pre-May parent 都 trigger 過 re-expansion
- DB 新增約 200-600 筆 cross-jurisdiction A series patent

---

## Verification

### Snippets backfill

```bash
# Before
sqlite3 cache/patents.db "
  SELECT COUNT(*) FROM patents WHERE formulation_snippets IS NULL
"

# Dry run
python -m scripts.backfill_snippets --dry-run --project Acetaminophen

# Apply
python -m scripts.backfill_snippets --project Acetaminophen

# After
sqlite3 cache/patents.db "
  SELECT COUNT(*) FROM patents
  WHERE formulation_snippets IS NULL AND ...
"

# 跑 demo script 確認覆蓋率上升
python -m scratch.demo_print_snippets | head -50
```

### Family backfill

```bash
# Dry run first
python -m scripts.backfill_family --case all --dry-run

# Apply Case 1 only（小、安全）
python -m scripts.backfill_family --case 1 --apply

# 驗證 Case 1 的 4 筆 family member 都有 family_of
sqlite3 cache/patents.db "
  SELECT patent_id, family_of FROM patents
  WHERE patent_id IN ('EP2443120B1', 'EP2107907B1', 'EP1285921B1', 'NO20210693B1')
"

# Apply Case 2 with cap（測試水溫）
python -m scripts.backfill_family --case 2 --apply --max 10
# 觀察行為，確認 EPO quota 還夠

# 完整跑
python -m scripts.backfill_family --case 2 --apply
```

### Backfill log inspection

```bash
# 看歷史 backfill 紀錄
sqlite3 cache/patents.db "
  SELECT started_at, script, case_type, rows_affected, git_commit
  FROM backfill_log
  ORDER BY started_at DESC
"

# 找有沒有中斷的 backfill
sqlite3 cache/patents.db "
  SELECT * FROM backfill_log WHERE completed_at IS NULL
"
```

---

## Non-Goals

- 不改 `modules/patent_fetcher.py` 或其他 production module
- 不處理 EPO US/CN/EA fulltext 缺失問題（Task C 結論，無解）
- 不重抓既有 patent 的 claims/description（diskcache 30 天 TTL 內
  reuse；過期就過期）
- 不做 Bug Y（query strategy 對 mechanism-described patent）
- 不寫 generic backfill framework——這兩個 script 是 one-off

---

## Risks

**EPO API quota：** Case 2 backfill 對每個 candidate parent 打
family API + N 個 member 的 biblio/abstract endpoint。預估 50-200
個 parent，每個 1+N call。若 N 平均 3，total ~200-800 API call。
每週 quota 3.5GB，單次 call ~30-100KB，不至於炸但要監控。

**DB 一致性：** Backfill 過程中如果 crash，可能有些 row `family_fetched=0`
但實際 family 已經部分展開。下次跑會 retry 同一 parent，因為
`get_by_id(member_id)` 會找到 existing row → 走 `existing` branch
無事發生。所以 idempotent，可以放心 retry。

**主管 demo 影響：** 跑完 Case 3 (snippet backfill) 後 DB 內容會有
意義性的變動，這對未來重跑 main.py 的 FTO 分析結果有影響（如果
analyzer 之後加入讀 `formulation_snippets` 的 feature）。短期不
影響——目前 analyzer 還沒讀這個欄位。

---

## Design Decisions

### Why two scripts instead of one merged tool?

`backfill_snippets.py` 跟 `backfill_family.py` 在「拿 patent → 跑邏輯
→ 寫回 DB」這個 high-level pattern 上類似，曾考慮合併成單一
`backfill.py --mode snippets|family`。

決定**切開**，理由：

1. **Risk profile 截然不同**
   - Snippets backfill：純 local computation，retry safe，無 quota 影響
   - Family backfill：打 EPO API、新增 row、有 quota 風險
   - 合併會逼使用者每次都要選 mode 並評估 risk

2. **執行頻率不同**
   - Snippets backfill 可能會 re-run（換 aliases、調 keywords 重跑）
   - Family backfill 預期一次性（除非未來再有 filter 變動）

3. **獨立可丟棄**
   - 兩個都是 one-off migration script，跑完未來可能刪除
   - 分開時其中一個過時只刪那個，不影響另一個

### Why scripts/ instead of integrating into tools/inspect_patent.py?

`tools/inspect_patent.py` 已存在且功能類似（read patent + run extraction）。
曾考慮把 backfill 模式併入 inspect tool（`--write-back` flag）。

決定**保持分離**，理由：

1. **Read-only invariant 是 inspect 的核心承諾**
   使用者隨手跑 inspect 是因為「絕對不會動 DB」。加 `--write-back`
   破壞這個保證。

2. **Mental model 不同**
   - Inspect = exploratory（「想看看 X 是什麼樣」）
   - Backfill = transactional（「要把 DB 從狀態 A 變成狀態 B」）

3. **演化方向會打架**
   - Inspect 演化方向：更好用、更快、更多 visualization
   - Backfill 演化方向：更安全、更可控、更 atomic
   合在一起會逼妥協

### What IS shared between the two CLIs?

底層 extraction logic 共用：

- `_extract_formulation_snippets()` from `modules/patent_fetcher.py`
- `_get_or_fetch()`, `_fetch_*()` from `modules/patent_fetcher.py`

Audit infrastructure 共用：
- `backfill_log` table — 兩個 script 都應該 INSERT 進去，記錄
  每次 backfill 的 metadata

兩個 CLI（inspect + backfill_snippets）都直接 import 並使用既有的
module-level functions。CLI wrapper 本身很薄（~50 行），各自處理
自己的 I/O 和 args。

若未來出現第三個工具用同樣 pattern（DB-or-sandbox-fetch → extract
→ do something），考慮抽出 `modules/patent_ops.py` 當 shared
library。今天不做，避免 over-engineering。

---

## Implementation Order

0. `backfill_log` table schema + `CREATE TABLE IF NOT EXISTS` helper
   - Implement in whichever script runs first; subsequent scripts reuse
   - Add to both scripts' startup code
1. `scripts/backfill_snippets.py`（先做這個，沒風險、不打 EPO、easy win）
2. `scripts/backfill_family.py` Case 1（4 個 known patent，small + bounded）
3. `scripts/backfill_family.py` Case 2 with `--max 10` 試水溫
4. `scripts/backfill_family.py` Case 2 完整跑（觀察 quota）

每個 step 之間 commit 一次。