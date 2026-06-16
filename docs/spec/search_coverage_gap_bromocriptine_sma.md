# Search Coverage Gap Analysis — Bromocriptine × SMA (v3)

> Investigation note. 2026-06-15.
> Triggered by: manual Google Patents search found WO2022028472A1 mentioning
> bromocriptine in description, but the tool's automated search did not capture it.

---

## The Case

**Patent:** WO2022028472A1 (EP family member: EP4192518A1)
**Title:** NUCLEIC ACID CONSTRUCTS AND USES THEREOF FOR TREATING SPINAL MUSCULAR ATROPHY
**Applicant:** Hangzhou Exegenesis Bio Ltd

**Where bromocriptine appears:**
Description paragraph [0373], in a combination therapy list:

> "compounds for treating SMA which may be used in combination with the
> vectors described herein include, but are not limited to, [... long list ...]
> Pramipexole (a dopamine agonist); [...] free radical scavengers that
> inhibit oxidative stress-induced cell death, such as bromocriptine; [...]"

**Where bromocriptine does NOT appear:**
- Title: ❌ (nucleic acid constructs)
- Abstract: ❌ (SMN protein + microRNA)
- Claims (70 claims, 20,306 chars): ❌ (all AAV vector / miRNA constructs)

---

## Why The Tool Missed It

1. **EPO CQL `ta=` only searches title + abstract.** Bromocriptine is not
   in either field. No `ta=` query can match this patent for bromocriptine.

2. **`ftxt=` (fulltext search) is not available.** Tested on non-paying tier:
   returns HTTP 400 Bad Request. Even if available on paying tier (€2,800/yr),
   EPO fulltext index only covers EP + WO A-documents, and results are
   unreliable for phrase search per EPO's own documentation.

3. **Widening `ta=` queries to indication-only terms didn't help.** Tested:
   - `ta="spinal muscular atrophy" AND ta="pharmaceutical composition"` → 41 results, target not included
   - `ta="SMN protein" AND ta="pharmaceutical"` → 5 results, target not included
   - `ta="spinal muscular atrophy" AND ta="combination"` → 28 results, target not included

   The patent's abstract only mentions "nucleic acid" and "microRNA" — none of
   the pharmaceutical/combination keywords appear in title or abstract.

4. **EPO does have the description (239,769 chars) and bromocriptine is at
   char 209,632.** If this patent had entered the DB through any search hit,
   `_get_or_fetch` → `_fetch_description` → `_collect_snippets` would have
   found it. The bottleneck is the search entry point, not the fetch/analysis
   pipeline.

---

## FTO Risk Assessment

**Low.** The 70 claims exclusively cover:
- Nucleic acid constructs with SMN coding sequence + miRNA target segments
- AAV vectors (rAAV9) carrying these constructs
- Pharmaceutical compositions of the above
- Methods of treating SMA using gene therapy vectors

Bromocriptine appears only in a non-limiting disclosure paragraph listing
compounds that "may be used in combination with" the claimed vectors.
This is a standard defensive disclosure pattern — it broadens the patent's
description but does not create claim scope over bromocriptine itself.

**For a bromocriptine oral tablet targeting SMA:** this patent does not
block. The claim scope is gene therapy vectors, not small molecule oral drugs.

---

## Prepared Response for Expert Questions

> "為什麼你的 report 沒有這篇 WO2022028472A1？我在 Google Patents 搜到了。"

**回答：**

這篇專利的 70 條 claims 全部是 AAV 基因治療載體的構造與方法。Bromocriptine
只出現在 description 第 0373 段的 combination therapy 列表裡，title 和 abstract
都沒有提到。

我的工具用 EPO OPS API 做自動搜尋，搜尋範圍是 title + abstract（EPO CQL 的
`ta=` field）。Description 層級的全文搜尋在 EPO API 上不可用（`ftxt=` 回傳
400 Bad Request）。所以這種「藥名只出現在 description 的 combination list」
的專利，自動搜尋不會命中。

不過：

1. **FTO 風險很低。** Claim scope 是基因治療載體，不是小分子口服藥。
   Bromocriptine 只是 disclosure 裡的 combination option，不構成對
   bromocriptine oral tablet 的權利限制。

2. **用 inspect_patent 可以手動驗證。** EPO 的 description 有 239,769 字，
   bromocriptine 確認在第 209,632 字處出現。如果專家提供特定 patent ID，
   工具可以即時抓取並分析。

3. **補充搜尋方案。** Google Patents 支援 fulltext search，可以用
   `bromocriptine "spinal muscular atrophy"` 做一次性補充掃描，結果
   可以匯入 DB（Task I pattern）。

---

## Systemic Implication

This is an instance of **architecture.md Roadmap item Bug Y**: patents where
the target drug appears only in description (not title/abstract/claims) are
invisible to `ta=` CQL search.

**Affected patent types:** defensive disclosure patents, combination therapy
kitchen-sink lists, background art sections that name-drop drugs.

**Characteristic:** low FTO risk (disclosure ≠ claim scope) but relevant to
landscape completeness.

**Possible future mitigations (not currently prioritized):**
- Post-hoc description scan on existing DB rows
- Google Patents fulltext search as supplemental data source (Task I L2/L3)
- Google Patents BigQuery integration

None of these are blocking for the current Bromocriptine × SMA analysis.
The tool correctly captures title/abstract-level matches, which correspond
to the highest-risk patents (those that actually claim the drug × indication
intersection).

---

## Verification Commands

```bash
# Confirm WO2022028472A1 description has bromocriptine
python3 -m tools.inspect_patent WO2022028472A1 --force-refetch --raw --source claims

# Check description directly
python3 -c "
from modules.patent_fetcher import _fetch_description
desc = _fetch_description('WO2022028472A1')
print(f'description: {len(desc)} chars')
idx = desc.lower().find('bromocriptine')
print(f'bromocriptine at char {idx}')
print(f'context: ...{desc[max(0,idx-100):idx+150]}...')
"

# EP4192518A1 (EP family member) — EPO has no content for this one
python3 -m tools.inspect_patent EP4192518A1 --force-refetch --raw
```
