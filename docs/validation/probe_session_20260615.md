# Probe Session Log — 2026-06-15

> All commands and outputs from the inspect_patent sandbox fallback investigation.
> Purpose: reproducibility. Every number cited in the standalone note can be re-derived from here.

---

## 1. EP-A / EP-B content statistics

```sql
-- Run from project root:
-- python3 -c "<paste below>"
--
-- NOTE: WHERE patent_id LIKE 'EP%' ensures all rows are EP.
-- The CASE LIKE '%B1' is safe because it only sees EP rows.

import sqlite3
conn = sqlite3.connect('cache/patents.db')
conn.row_factory = sqlite3.Row

rows = conn.execute('''
    SELECT
        CASE
            WHEN patent_id LIKE 'EP%B1' OR patent_id LIKE 'EP%B2' THEN 'EP-B (granted)'
            WHEN patent_id LIKE 'EP%A1' OR patent_id LIKE 'EP%A2' THEN 'EP-A (application)'
            ELSE 'EP-other'
        END AS kind_group,
        COUNT(*) AS total,
        SUM(CASE WHEN COALESCE(abstract,'') != '' THEN 1 ELSE 0 END) AS has_abstract,
        SUM(CASE WHEN COALESCE(claims,'') != '' THEN 1 ELSE 0 END) AS has_claims,
        SUM(CASE WHEN COALESCE(examples_extracted,'') != '' THEN 1 ELSE 0 END) AS has_examples,
        SUM(CASE WHEN COALESCE(abstract,'')='' AND COALESCE(claims,'')='' AND COALESCE(examples_extracted,'')='' THEN 1 ELSE 0 END) AS all_empty
    FROM patents
    WHERE patent_id LIKE 'EP%'
    GROUP BY kind_group
''').fetchall()

print(f"{'kind_group':<20s} {'total':>6s} {'abstract':>9s} {'claims':>7s} {'examples':>9s} {'all_∅':>6s}")
print('-' * 60)
for r in rows:
    print(f"{r['kind_group']:<20s} {r['total']:>6d} {r['has_abstract']:>9d} {r['has_claims']:>7d} {r['has_examples']:>9d} {r['all_empty']:>6d}")
```

**Output (2026-06-15):**
```
kind_group            total  abstract  claims  examples  all_∅
------------------------------------------------------------
EP-A (application)      524       167     161       132    353
EP-B (granted)          244        68     231       166      6
```

---

## 2. EP all-empty row provenance

```sql
import sqlite3
conn = sqlite3.connect('cache/patents.db')
conn.row_factory = sqlite3.Row

rows = conn.execute('''
    SELECT
        CASE
            WHEN patent_id LIKE "%B1" OR patent_id LIKE "%B2" THEN "EP-B"
            WHEN patent_id LIKE "%A1" OR patent_id LIKE "%A2" THEN "EP-A"
            ELSE "EP-other"
        END AS kind,
        COALESCE(source, "<NULL>") AS source,
        COALESCE(family_of, "<none>") != "<none>" AS has_family_of,
        COUNT(*) AS n,
        MIN(fetched_at) AS earliest,
        MAX(fetched_at) AS latest
    FROM patents
    WHERE patent_id LIKE "EP%"
      AND COALESCE(abstract,"") = ""
      AND COALESCE(claims,"") = ""
      AND COALESCE(examples_extracted,"") = ""
    GROUP BY kind, source, has_family_of
    ORDER BY n DESC
''').fetchall()

print(f"{'kind':<8s} {'source':<12s} {'family_of':>10s} {'count':>6s}  {'earliest':>24s}  {'latest':>24s}")
print('-' * 100)
for r in rows:
    fam = 'yes' if r['has_family_of'] else 'no'
    print(f"{r['kind']:<8s} {r['source']:<12s} {fam:>10s} {r['n']:>6d}  {(r['earliest'] or '?'):>24s}  {(r['latest'] or '?'):>24s}")
```

**Output (2026-06-15):**
```
kind     source        family_of  count                  earliest                    latest
----------------------------------------------------------------------------------------------------
EP-A     epo                 yes    246  2026-05-21T15:09:18.173778  2026-06-12T15:55:21.286185
EP-A     epo                  no    107  2026-03-23T16:31:53.409295  2026-06-12T15:24:32.203571
EP-B     epo                  no      4  2026-05-26T14:13:19.560462  2026-05-26T15:28:25.495534
EP-B     epo                 yes      2  2026-05-26T15:03:24.144400  2026-05-26T16:50:34.391119
```

---

## 3. EPO biblio endpoint probe (three Jenna patents)

Script: `scratch/probe_sandbox_fetch.py`

**Output (2026-06-15):**
```
PROBING: WO2001010427A2
  biblio FAILED: HTTPError: 404 ... /epodoc/biblio
  abstract FAILED: HTTPError: 404 ... /epodoc/abstract
  claims FAILED: HTTPError: 404 ... /epodoc/claims

PROBING: CN117018194B
  biblio FAILED: HTTPError: 404 ... /epodoc/biblio
  abstract FAILED: HTTPError: 404 ... /epodoc/abstract
  claims FAILED: HTTPError: 404 ... /epodoc/claims

PROBING: US20100184727A1
  biblio FAILED: HTTPError: 404 ... /epodoc/biblio
  abstract FAILED: HTTPError: 404 ... /epodoc/abstract
  claims FAILED: HTTPError: 404 ... /epodoc/claims
```

---

## 4. Diskcache inspection

```bash
python3 -c "
import diskcache
c = diskcache.Cache('cache/epo')
for pid in ['WO2001010427', 'CN117018194', 'US20100184727']:
    stale = [k for k in c if pid in k]
    if stale:
        print(f'{pid}: {stale}')
        for k in stale:
            val = c[k]
            print(f'  {k} -> {repr(val)[:100]}')
    else:
        print(f'{pid}: no cache entries')
"
```

**Output:**
```
WO2001010427: ['title::WO2001010427A2', 'abstract::WO2001010427A2', 'claims::WO2001010427A2']
  title::WO2001010427A2 -> ''
  abstract::WO2001010427A2 -> ''
  claims::WO2001010427A2 -> ''
CN117018194: ['abstract::CN117018194A', 'claims::CN117018194A', 'title::CN117018194A', 'title::CN117018194B', 'abstract::CN117018194B', 'claims::CN117018194B']
  abstract::CN117018194A -> 'The invention discloses an application of a muscarinic receptor type 5 antagonist. In the applicati
  claims::CN117018194A -> ''
  title::CN117018194A -> 'Use of muscarinic receptor type 5 antagonists'
  title::CN117018194B -> ''
  abstract::CN117018194B -> ''
  claims::CN117018194B -> ''
US20100184727: ['title::US20100184727A1', 'abstract::US20100184727A1', 'claims::US20100184727A1']
  title::US20100184727A1 -> ''
  abstract::US20100184727A1 -> ''
  claims::US20100184727A1 -> ''
```

Key finding: CN117018194**A** has title + abstract, CN117018194**B** is all empty.

---

## 5. US biblio coverage probe (N=8)

Script: `scratch/find_us_sandbox_case.py`

**Output:**
```
patent                  DB      biblio      claims  verdict
---------------------------------------------------------------------------
US20200001001A1         no         404        skip  no biblio
US20190002002A1         no         404        skip  no biblio
US20180100100A1         no         404        skip  no biblio
US20170200200A1         no         404        skip  no biblio
US20160300300A1         no         404        skip  no biblio
US20150100100A1         no         404        skip  no biblio
US20230001001A1         no         404        skip  no biblio
US20210001001A1         no         404        skip  no biblio
WO2020001001A1          no         YES         YES  has fulltext too
WO2019100100A1          no         YES         YES  has fulltext too
WO2023000001A1          no         YES         YES  has fulltext too
CN110000001A            no         YES         404  ← PERFECT
```

---

## 6. Change 2 verification (partial content hint)

```bash
python3 -m tools.inspect_patent CN110000001A --aliases darifenacin
```

**Output:**
```
[!] CN110000001A not in DB — fetching from EPO (not persisted)

======================================================================
Patent: CN110000001A  (?, fetched_from=epo_sandbox)
Title:  Garbage classification device of road motor sweeper
======================================================================
  claims:                  0 chars
  examples_extracted:      0 chars
  abstract:             1427 chars
  stored snippets:    empty/NULL

  ⚠  EPO API returned title/abstract only (no fulltext).
     The OPS API does not license non-EP claims/description.
     (Espacenet website may still show them — different backend.)
     For full text, try:
       Espacenet: ...
       Google:    ...
```

---

## 7. WO2022028472A1 description probe (SMA × Bromocriptine)

```bash
python3 -c "
from modules.patent_fetcher import _fetch_description
desc = _fetch_description('WO2022028472A1')
print(f'description length: {len(desc)} chars')
idx = desc.lower().find('bromocriptine')
print(f'bromocriptine found at char {idx}')
print(f'context: ...{desc[max(0,idx-100):idx+100]}...')
"
```

**Output:**
```
description length: 239769 chars
bromocriptine found at char 209632
context: ...lone derivatives; free radical scavengers that inhibit oxidative stress-induced cell death, such as bromocriptine; phenyl carbamate compounds; neuroprotective compounds; and glycopeptides.
[0374]    I...
```

---

## 8. ftxt= CQL probe

```bash
python3 -c "
from modules.patent_fetcher import client
try:
    r = client.published_data_search(cql='ftxt=bromocriptine AND ta=\"spinal muscular atrophy\"', range_begin=1, range_end=10)
    print(f'HTTP {r.status_code}, {len(r.text)} bytes')
except Exception as e:
    print(f'FAILED: {e}')
"
```

**Output:**
```
FAILED: 400 Client Error: Bad Request for url: https://ops.epo.org/3.2/rest-services/published-data/search
```

---

## Patent IDs referenced in this session

| Patent ID | Context | EPO API result |
|-----------|---------|---------------|
| WO2001010427A2 | Jenna's GPP × Darifenacin search | biblio 404, all empty |
| CN117018194B | Jenna's GPP × Darifenacin search | biblio 404 (A version has data) |
| US20100184727A1 | Jenna's GPP × Darifenacin search | biblio 404, all empty |
| EP4192518A1 | SMA × Bromocriptine case II | title only, no abstract/claims/description (content is on WO family member) |
| WO2022028472A1 | SMA × Bromocriptine case II | claims 20306, description 239769, abstract 326, bromocriptine at char 209632 |
| CN110000001A | Change 2 verification case | title+abstract only (partial content hint triggered) |
| EP3456789A1 | Change 2 test (EP-A1) | full content (biblio+claims+examples) — EP has everything |
| CN117018194A | Diskcache finding | has title+abstract in cache (A version of CN117018194B) |

---

## Scratch scripts produced (not committed)

| Script | Purpose | Reusable? |
|--------|---------|-----------|
| `scratch/probe_sandbox_fetch.py` | Bypass `_fetch_*` silent except, show raw EPO response | Yes |
| `scratch/find_sandbox_test_case.py` | Find EP-A1 not in DB with EPO biblio | One-time |
| `scratch/find_us_sandbox_case.py` | Find US/WO/CN with biblio=YES + claims=404 | One-time |
