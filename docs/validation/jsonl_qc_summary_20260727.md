# JSONL Coverage QC Summary

**Date:** 2026-07-27
**Tool:** `tools/inspect_jsonl.py`
**Purpose:** Data quality check on Google Patents scraper JSONL artifacts
before feeding into `scripts/analyze_jsonl.py` (Phase 4/5).

---

## Summary Table

| Project | File Date | Total | Clean | Dirty | Clean % | title | abstract | claims | full_text |
|---|---|---|---|---|---|---|---|---|---|
| Will's GPP | 2026-07-09 | 147 | 141 | 6 | 95.9% | 100.0% | 73.8% | 98.6% | 98.6% |
| Will's IPF | 2026-07-09 | 437 | 348 | 89 | 79.6% | 100.0% | 89.9% | 87.4% | 87.1% |
| Pioglitazone × EB | 2026-07-24 | 1366 | 1340 | 26 | 98.1% | 99.9% | 70.5% | 88.1% | 86.9% |
| Tiagabine × EB | 2026-07-27 | 1212 | 1186 | 26 | 97.9% | 99.8% | 71.9% | 90.1% | 88.8% |

*Field coverage percentages are computed over clean rows only.*

---

## Definitions

- **Total** — every line in the JSONL file.
- **Dirty** — scraper-level failures: `title` starts with "Not Found"
  (HTTP non-200, usually 404) or "Error" (scraper exception), plus any
  unparseable JSON lines. These rows contain no usable content.
- **Clean** — rows with a real scraped page.
- **Field coverage** — of the clean rows, how many have non-empty content
  in that field (sentinels `N/A` / empty string count as missing).

Dirty ≠ bad content. Dirty means nothing was scraped. A clean row can
still have an empty field (e.g. a supplementary-search document that has
a Google Patents page but no claims full text).

---

## Observations

### Dirty rate

Will's IPF is the outlier at 20.4% dirty. The 89 dirty rows are mostly
A3/A4/B8 kind codes (supplementary search reports) pulled in via family
expansion — Google Patents often has no page for these document types.
The three directly-searched batches (GPP, Pioglitazone, Tiagabine) all
sit at 2–4% dirty, because a manual Google Patents search returns
patents that have real pages.

### Abstract coverage is consistently the lowest field

All four batches show abstract as the weakest field (70–90%). This is a
known Google Patents characteristic: many patents (especially
non-English-origin) have no English abstract on the page, yet claims and
full text are still retrievable. Low abstract coverage has limited impact
on downstream analysis because rule/LLM scoring relies primarily on
claims.

### The two EB projects are highly consistent

Pioglitazone and Tiagabine batches are near-identical (clean ~98%, claims
~88–90%, full_text ~87–89%), indicating stable scraper behavior. The two
batches are directly comparable. Tiagabine claims coverage (90.1%) is
marginally higher than Pioglitazone (88.1%).

### Readiness for analysis

All four batches have claims coverage >87%, sufficient for rule/LLM
analysis. Tiagabine's 1186 clean rows are ready for `analyze_jsonl.py`.

---

## Commands Used

```bash
python3 tools/inspect_jsonl.py data/global_patents_archive_GPP_idlist_20260709.jsonl
python3 tools/inspect_jsonl.py data/global_patents_archive_IPF_idlist_20260709.jsonl
python3 tools/inspect_jsonl.py data/global_patents_archive_EB_pioglitazone_idlist_20260724.jsonl
python3 tools/inspect_jsonl.py data/global_patents_archive_EB_tiagabine_idlist_20260727.jsonl
```
