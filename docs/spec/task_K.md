# Task K — Patent Expiry Date Integration (Complete Spec)

> 存檔備查。本 spec 記錄 Gap #5 全三期的設計決策與實作結果。
> Phase 1-2 已完成並 commit。Phase 3 已完成並 commit (2026-06-30)。
> 本 spec 為事後補寫，目的是留存設計脈絡供未來參考。

> **Expert validation (2026-06-30):**
> 與外部專家 Will 確認：filing_date + 20yr 作為初步篩選足夠。
> 建議統一來源、不混用多個 data source。
> SPC/PTE 精度提升降為 parking lot。
> 詳見 Part 5: Expert Decisions。

---

## Context

Prior Art Tool 的 output 需要顯示專利到期日，讓 bio team 判斷哪些 patent
快到期（對 drug repurposing 有利）。原始 DB schema 沒有到期日相關欄位。

法律依據：
- 35 USC §154 (US)：patent term = filing_date + 20 years
- EPC Article 63 (EU)：同上
- 注意：是 filing date 不是 priority date（probe §6 驗證）

已知精度限制：
- filing+20yr = base term only
- 不含 PTE (Patent Term Extension, US, up to +5yr for FDA drugs)
- 不含 PTA (Patent Term Adjustment, USPTO prosecution delays)
- 不含 SPC (Supplementary Protection Certificate, EU equivalent)
- 不含 maintenance fee lapse, terminal disclaimer

Januvia case study (US7326708B2)：
- Pipeline (filing+20yr): 2024-06-23
- Orange Book / Google Patents (含 PTE): 2026-11-24
- Orange Book PED (含 PTE + Pediatric Exclusivity): 2027-05-24
- 差距：~2.5 年 (PTE) 到 ~3 年 (PTE+PED)

---

## Goal

在 pipeline 中加入 patent expiry date，三個 phase：
1. DB schema + backfill infrastructure
2. Output integration
3. Fetch-time piggyback (zero extra API calls)

---

## Files Modified

### Phase 1 (schema + backfill)
- `modules/patent_store.py` — ALTER TABLE: filing_date, expiry_date, expiry_source
- `scripts/backfill_expiry_dates.py` — two-layer: Orange Book → EPO filing+20yr

### Phase 2 (output)
- `modules/output_writer.py` — expiry columns + conditional formatting

### Phase 3 (fetch-time piggyback)
- `modules/patent_fetcher.py` — date extraction from family response

---

## Part 1: Schema + Backfill (Done)

### Schema changes (patent_store.py)

```sql
ALTER TABLE patents ADD COLUMN filing_date TEXT;
ALTER TABLE patents ADD COLUMN expiry_date TEXT;
ALTER TABLE patents ADD COLUMN expiry_source TEXT;
```

- `filing_date`: YYYY-MM-DD, from EPO biblio or family response
- `expiry_date`: YYYY-MM-DD, filing_date + 20yr or OB override
- `expiry_source`: provenance tracking
  - `'filing_plus_20'` — base term, EPO data
  - `'orange_book'` — FDA OB exact date (含 PTE)

upsert_patent() extended with COALESCE semantics: 不覆蓋已有的 expiry
（避免 OB override 被 filing+20yr 蓋掉）。

### Backfill script (scripts/backfill_expiry_dates.py)

Two-layer enrichment:
1. Orange Book lookup (local JSON, US NDA patents only)
2. EPO biblio API fallback (filing_date + 20yr)

Results (final run):
- Total: 5841 patents
- Success: 5715 (97.8%)
  - Orange Book: 29
  - EPO filing+20yr: 5686
- Failed: 126 (known patterns, see below)

Fail patterns (126):
- CN utility models (...U): EPO 404
- Translation patents (PL/PT/SI/LT/HR/FI/NO T3/T1): not independent filings
- JP/KR B1 (old format): EPO 404
- HU/JOP/TN/SG/IN/MX/GE/PH/UA/CZ/DE utility models: regex fail or 404
- ReadTimeout: ~15, transient

### Commits

```
96f4b40 feat(store): add filing_date / expiry_date / expiry_source columns
bce640e feat(scripts): backfill_expiry_dates — OB priority, EPO fallback
```

---

## Part 2: Output Integration (Done)

### Changes (output_writer.py)

- Added `expiry_date`, `expiry_source` columns to CSV/Excel output
- Conditional formatting:
  - Expired (past today): grey
  - Expiring within 1 year: yellow
  - Active: green

### Commit

```
19aa801 feat(output): add expiry_date + expiry_source to CSV/Excel
```

---

## Part 3: Fetch-time Piggyback (Done)

### Design decisions

問：只改 `_fetch_and_store_family()` 還是 `_get_or_fetch()` 也要改？
答：**兩個都改**。family response 包含 self-reference（parent 自己也是 member），
從 self-ref 抽 filing_date 回寫 parent row，0 extra API calls。

問：OB lookup 要在 fetcher 裡做嗎？
答：**不做**。fetcher 只依賴 EPO，OB 是另一個 data source，留在 backfill。
這也是專家建議的「統一來源」原則。

問：backfill fail pattern 要在 fetcher 裡預防嗎？
答：**不需要**。fail patterns 是 backfill 對已存在 row 補打 biblio 時的問題。
Piggyback 是從已成功的 family response 裡 parse，不同的 population。

問：需要獨立 spec 嗎？
答：一個 commit 就夠。改動小且自包含。（本 spec 為事後補寫）

### New functions (patent_fetcher.py)

```python
_parse_date_from_member(member, ref_key)
    # Generic date parser for family member dict
    # ref_key = 'application-reference' or 'publication-reference'
    # Returns 'YYYY-MM-DD' or None

_compute_expiry(filing_date_str)
    # filing_date + 20yr
    # Returns 'YYYY-MM-DD' or None
```

### Modified: _fetch_and_store_family()

- Return type: `None` → `tuple[list[dict], dict]`
  - Second element: `parent_dates = {filing_date, expiry_date, year}`
- Member loop changes:
  - Self-reference: collect parent_filing_date before continue (no fetch)
  - Normal members: parse filing_date + pub_date → year + expiry_date
  - Existing DB members: also get dates backfilled if missing
  - New members: upsert with filing_date, expiry_date, expiry_source

### Modified: _get_or_fetch()

- Both call sites (DB-hit path + fresh-fetch path):
  - Accept tuple return from _fetch_and_store_family()
  - parent_dates → UPDATE parent row with filing_date, expiry_date, year

### Year bug fix (included in same commit)

Root cause: `year` 一直是空字串，因為 search inline 不帶 date（probe §3）。
Fix: 從 family response 的 `publication-reference` 取 pub_date[:4] 填 year。
Output fallback: old patents with empty year → `filing_date[:4]` at display time
（output_writer.py, DB 不修改）。

### Verification

```bash
# Clean test data
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/patents.db')
conn.execute(\"DELETE FROM patents WHERE patent_id LIKE 'EP4138798%' OR patent_id IN ('US2023157975A1','WO2021214451A1')\")
conn.commit(); conn.close()
"

# Run fetch
python3 -c "from modules.patent_fetcher import _get_or_fetch; r = _get_or_fetch('EP4138798A1'); print(f'year={r.get(\"year\")}  members={len(r.get(\"_family_members\", []))}')"

# Verify dates in DB
python3 -c "
import sqlite3
conn = sqlite3.connect('cache/patents.db')
for r in conn.execute('''
    SELECT patent_id, year, filing_date, expiry_date, expiry_source
    FROM patents WHERE patent_id LIKE \"EP4138798%\"
    OR patent_id IN (\"US2023157975A1\", \"WO2021214451A1\")
''').fetchall():
    print(r)
"
```

Expected results:
```
('EP4138798A1', '2023', '2021-04-20', '2041-04-20', 'filing_plus_20')
('EP4138798B1', '2025', '2021-04-20', '2041-04-20', 'filing_plus_20')
('US2023157975A1', '2023', '2021-04-20', '2041-04-20', 'filing_plus_20')
('WO2021214451A1', '2021', '2021-04-20', '2041-04-20', 'filing_plus_20')
```

### Commits

```
(Phase 3) feat(fetcher,output): piggyback dates from family response + year fallback
(docs)    docs(arch): update Gap #5 for Phase 3 fetch-time date extraction
```

---

## Part 4: OB Enrichment Flag (Not Implemented)

### Design (approved, deferred to Q3)

`main.py --enrich-ob`: 跑完 fetch+analyze 後自動跑 OB enrichment。
不改 fetcher、不加 dependency。Import backfill script 的 core function。

### Decision: fetcher 不依賴 OB

- fetcher 的 data source 只有 EPO，zero OB dependency
- OB 是 optional post-processing enrichment layer
- 更新 OB JSON 後跑 `--enrich-ob` 即可 refresh
- 未來 OB 要加其他欄位（PTE detail, Patent Use Code, NDA number），
  改 enrichment layer 不改 fetcher

Status: deferred to Q3. Not blocking.

---

## Part 5: Expert Decisions (2026-06-30)

### Validated

- filing_date + 20yr 作為 base term：**correct** (35 USC §154, EPC Art.63)
- filing_date（不是 priority_date）：**correct** (probe §6 驗證)
- 對 drug repurposing FTO 篩選，base term 夠用：**confirmed by Will**

### Expert recommendations

- 統一來源方便參考，不要混用多個 data source
- 擔心的話可以往後加 1-2 year buffer
- OB integration 有用但非必要，目前作為 optional enrichment 正確

### Deferred (parking lot)

- SPC data integration (EU) — only if bio team needs EU precision
- Continuation chain tracking — low practical impact per Will
- Terminal disclaimer detection — no public data source
- Maintenance fee lapse check — would need USPTO PAIR integration
- PTA (Patent Term Adjustment) — minor, US-only

---

## Non-Goals

- 不追求精確到含 PTE/SPC 的 actual expiry（專家確認 base term 夠用）
- 不在 fetcher 裡加 OB dependency
- 不處理 continuation chain 的 earliest filing date
- 不整合商用工具的 adjusted expiration date
- 不 scrape Google Patents

---

## Related Documents

- `docs/validation/probe_expiry_date_20260625.md` — pre-implementation probe (§1-§8)
- `docs/architecture.md` — Gap #5 entry, Fetch Priority Logic, Expiry paragraph
