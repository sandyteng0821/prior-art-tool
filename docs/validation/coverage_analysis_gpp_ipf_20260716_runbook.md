# Reproduction Runbook: GPP+IPF Coverage Analysis (2026-07-16)

## Prerequisites

- Patent ID lists in `data/plainid/{GPP,IPF}_idlist_20260709.txt`
- API running (`docker compose up -d`)
- Kaggle notebook access for Google Patents scraper

---

## Step 0: Prior coverage baseline (referenced in analysis doc)

The analysis doc's Background compares against two prior pipeline runs.
To reproduce those numbers:

```bash
# Orphenadrine (EPO-only baseline) — claims 97/576 = 16.8%
python3 -m tools.probe_coverage_v2 \
    --csv output/gap_analysis_20260707_1454.csv --query 1

# Pemirolast (EPO + Google Patents) — claims 234/293 = 79.9%
python3 -m tools.probe_coverage_v2 \
    --csv output/gap_analysis_20260508_1551.csv --query 1
```

Note: these read current DB state, not historical snapshots. Numbers
match only if DB content hasn't changed since the original runs.

---

## Step 1: Check DB baseline

```bash
python3 -m tools.check_db --file data/plainid/GPP_idlist_20260709.txt
python3 -m tools.check_db --file data/plainid/IPF_idlist_20260709.txt
```

Expected: almost all NOT IN DB (580/584 missing on 2026-07-16).

---

## Step 2: Google Patents scrape (Kaggle)

Use Kaggle scraper notebook. Set `INPUT_FILE` to each ID list, run via **Save & Run All** (Commit) for background execution.

Output files:
- `global_patents_archive_GPP_idlist_20260709.jsonl`
- `global_patents_archive_IPF_idlist_20260709.jsonl`

Download to `data/` on workstation.

Scraper has checkpoint resume — if session breaks, re-run cell and it skips completed IDs.

---

## Step 3: Quick GP coverage check

Before import, verify Google Patents content coverage:

```bash
python3 -c "
import json
for name, path in [
    ('GPP', 'data/global_patents_archive_GPP_idlist_20260709.jsonl'),
    ('IPF', 'data/global_patents_archive_IPF_idlist_20260709.jsonl'),
]:
    total=ok=no_claims=no_content=err=0
    for line in open(path):
        r=json.loads(line); total+=1
        if r['title'].startswith(('Not Found','Error')): err+=1
        elif r['claims'] in ('N/A','',None) and r['full_text'] in ('N/A','',None): no_content+=1
        elif r['claims'] in ('N/A','',None): no_claims+=1
        else: ok+=1
    print(f'{name}: {total} total | {ok} has claims | {no_claims} desc only | {no_content} empty | {err} error/404')
"
```

Optional — check error/404 by jurisdiction:

```bash
python3 -c "
import json, re
from collections import Counter
for name, path in [
    ('GPP', 'data/global_patents_archive_GPP_idlist_20260709.jsonl'),
    ('IPF', 'data/global_patents_archive_IPF_idlist_20260709.jsonl'),
]:
    errs = []
    for line in open(path):
        r=json.loads(line)
        if r['title'].startswith(('Not Found','Error')):
            cc = re.match(r'^([A-Z]{2,4})', r['requested_id'])
            errs.append(cc.group(1) if cc else '??')
    print(f'{name} Error/404 by jurisdiction:')
    for k,v in Counter(errs).most_common(): print(f'  {k}: {v}')
    print()
"
```

---

## Step 4: EPO probe

```bash
mkdir -p scratch

# Run sequentially to avoid EPO rate limit
nohup bash -c '
python3 tools/batch_epo_probe.py data/plainid/GPP_idlist_20260709.txt \
    -o scratch/epo_probe_gpp.jsonl && \
python3 tools/batch_epo_probe.py data/plainid/IPF_idlist_20260709.txt \
    -o scratch/epo_probe_ipf.jsonl
' > scratch/epo_probe_all.log 2>&1 &
```

Check progress / results:

```bash
tail scratch/epo_probe_all.log
grep -A 20 "SUMMARY" scratch/epo_probe_all.log
```

---

## Step 5: Compare coverage

```bash
python3 tools/compare_coverage.py \
    --gp data/global_patents_archive_GPP_idlist_20260709.jsonl \
    --epo scratch/epo_probe_gpp.jsonl \
    --mode all -o scratch/coverage_compare_gpp.txt

python3 tools/compare_coverage.py \
    --gp data/global_patents_archive_IPF_idlist_20260709.jsonl \
    --epo scratch/epo_probe_ipf.jsonl \
    --mode all -o scratch/coverage_compare_ipf.txt
```

---

## Step 6: Verify EPO abstract coverage (raw count)

The `compare_coverage` summary table uses mutually exclusive buckets
(`EPO✓clm` vs `EPO✓abs`). For true abstract coverage, count directly:

```bash
python3 -c "
import json
for name, path in [
    ('GPP', 'scratch/epo_probe_gpp.jsonl'),
    ('IPF', 'scratch/epo_probe_ipf.jsonl'),
]:
    total=abs_has=0
    for line in open(path):
        r=json.loads(line); total+=1
        if r.get('abstract_chars',0) > 0: abs_has+=1
    print(f'{name} EPO abstract_chars > 0: {abs_has}/{total} ({abs_has/total*100:.1f}%)')
"
```

---

## Output files (not committed, reproducible)

| File | Description |
|------|-------------|
| `data/global_patents_archive_GPP_idlist_20260709.jsonl` | GP scrape output (GPP) |
| `data/global_patents_archive_IPF_idlist_20260709.jsonl` | GP scrape output (IPF) |
| `scratch/epo_probe_gpp.jsonl` | EPO probe output (GPP) |
| `scratch/epo_probe_ipf.jsonl` | EPO probe output (IPF) |
| `scratch/coverage_compare_gpp.txt` | compare_coverage full output (GPP) |
| `scratch/coverage_compare_ipf.txt` | compare_coverage full output (IPF) |
