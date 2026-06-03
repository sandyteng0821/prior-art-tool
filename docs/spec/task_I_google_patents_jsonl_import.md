# Task I — Google Patents JSONL Import

> Retrospective spec — written after implementation. Captures the actual
> path taken, including where it diverged from Task H assumptions.

**Status:** Shipped 2026-06-03
**Supersedes:** [task_H_google_patents_l2.md](task_H_google_patents_l2.md)
(see superseded note at top of Task H)

---

## Context

Task H assumed Google Patents scraping would run on the production
machine, with risk isolation handled by splitting fetcher (read-only) and
backfill (write op) into two layers. In practice, company IT policy on
automated HTTP scraping made running the scraper on the production
machine unwise — risk of being flagged by network monitoring outweighed
the value.

The actual solution moved scraping **off-machine** to a Kaggle notebook,
producing a JSONL artifact that the production machine imports. This
collapses Task H's two-layer split into a single importer — the
ToS/IP-block concerns that motivated the split no longer apply because
there is no live fetch from the production machine.

---

## Goal

Import Google Patents fulltext (claims + description) from a JSONL
artifact into the local SQLite cache, filling the gap left by EPO OPS's
licensing limit on US/CN/KR/JP/EA fulltext. Downstream pipeline
(`backfill_snippets`, LLM analyzer) automatically benefits.

---

## Architecture

```
[Kaggle notebook]                    [Production machine]
       │                                     │
       │ scrape Google Patents               │
       │ (rate-limited, with                 │
       │  circuit breaker)                   │
       │   ↓                                 │
   global_patents_archive.jsonl ───────────→ scripts/import_google_patents_jsonl.py
       │                                     │   ├─ filter dirty / EP|WO / no-content
   (manual transfer, out of                  │   ├─ classify per row vs DB state
    repo, gitignored)                        │   └─ upsert claims + examples
                                             ↓
                                       cache/patents.db
                                             │
                                             ↓
                                   scripts/backfill_snippets.py
                                       (re-extract snippets)
```

Kaggle scraper is **out-of-repo** by design — it's the artifact-producing
side, and we treat the JSONL as the contract. Schema documented in
`docs/spec/scraper_jsonl_schema.md` (TODO if needed; for now, the
importer's `_classify`/`_apply_update` documents which fields it consumes).

---

## Files

- `scripts/import_google_patents_jsonl.py` — the importer
- `scratch/probe_jsonl_join.py` — probe artifact (kept for reproducibility)
- `scratch/reset_snippets_for_reimport.py` — one-off cleanup, used once,
  no longer needed (importer now resets `formulation_snippets` to NULL
  automatically on update; see lifecycle note below)

No new modules. No schema migration.

---

## Behavior

### Classify layer (per JSONL row)

Six possible verdicts, evaluated in order; first match wins:

| # | Verdict | Condition | Reason |
|---|---------|-----------|--------|
| 1 | `skip_dirty` | `title` starts with "Not Found" or "Error" | Scraper sentinel for HTTP failures. Also catches CSV-header-as-data pollution (verified: 1 row in our JSONL was the literal string "PATENT_ID") |
| 2 | `skip_jurisdiction` | `requested_id[:2]` in `{EP, WO}` | EPO OPS is authoritative for these; don't overwrite |
| 3 | `skip_not_in_db` | `patent_id` not in `patents` table | Don't introduce orphan rows; they wouldn't be tied to any search_log entry |
| 4 | `skip_has_claims` | DB row has non-empty `claims` | Idempotent — don't clobber existing content |
| 5 | `skip_no_useful_content` | Both `claims` and `full_text` are missing sentinels | Writing N/A masks the "needs re-fetch" signal |
| 6 | `apply` | Everything else | Proceed to update |

Missing sentinel: `{"N/A", "", None}`. Distinguishing "scraped but parse
failed" (writes "N/A") from "truly empty content" from "field absent"
preserves the `PARSE_NO_CLAIMS` invariant Task H called out.

### Apply layer (per row that passed classify)

Two-column independent protection:

- **claims** — written if JSONL provides a non-missing value. Classify
  guarantees existing is empty by the time we reach here, so no
  additional check.
- **examples_extracted** — written only if JSONL provides non-missing AND
  existing is empty. Required because Classify gates on `claims`, but
  some rows have `claims = ''` and `examples_extracted = 'some content'`
  (N=7 verified pre-import). Without this check, the importer would
  overwrite EPO-captured examples while filling Google-sourced claims.
- **formulation_snippets** — **always set to NULL on update** (regardless
  of input). Without this, the downstream `backfill_snippets` candidate
  filter (`WHERE formulation_snippets IS NULL`) wouldn't pick up rows
  whose snippets were previously written as `'[]'` (based on then-empty
  claims) — making them invisible to re-extraction. See "Lifecycle note"
  below.

### Source tagging

- `'google_patents'` — both claims and examples written from JSONL
- `'mixed_epo_google_patents'` — claims from JSONL, examples preserved
  from EPO (hybrid row). Distinguishes hybrid lineage for downstream
  forensics.

The mixed-source code path was added for safety but **did not trigger in
the actual run** — the 7 at-risk hybrid candidates were all EP/WO and
filtered out by `skip_jurisdiction`. Kept anyway as future-proofing.

### Audit log

Uses existing `_backfill_common.start_run / finish_run`:
- `script = 'import_google_patents_jsonl'`
- `case_type = 'google_patents_jsonl_import'`
- `args` records `--input` path, `--apply` / `--dry-run`, `--limit`
- `notes` records `applied=N from <filename>`

Dry-run does **not** write to backfill_log (per task_D convention).

---

## Probe Evidence

`scratch/probe_jsonl_join.py` verified the import design before code was
written:

| Probe | Result |
|-------|--------|
| Join key candidate | `requested_id` ↔ `patents.patent_id`, case-sensitive match |
| Match rate | 502/502 non-EP/WO entries match the DB (100%) |
| Unmatched entries | 184 records — all WO (114), EP (69), and 1 dirty (`'patent_id'` literal string from CSV header). All accounted for. |
| `source` column existing values | `'epo'` (5249 rows). New tags chosen to not collide |
| At-risk rows (empty claims, non-empty examples) | N=7, all EP/WO. Hybrid code path never triggered in real run, but kept defensive. |

---

## Verification

```bash
# 1. Dry-run before applying
python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive.jsonl --dry-run
# Result: apply=411, skip_jurisdiction=182, skip_no_useful_content=64,
#         skip_has_claims=29, skip_dirty=4, skip_not_in_db=0

# 2. Apply
python -m scripts.import_google_patents_jsonl \
    --input data/global_patents_archive.jsonl --apply
# Result: apply=408 (3 fewer than dry-run because of intra-JSONL dups
#         caught idempotently)

# 3. Re-extract snippets (Pemirolast project)
python -m scripts.backfill_snippets \
    --project 'Pemirolast_吸入劑治療特發性肺纖維化_(IPF)' \
    --aliases Pemirolast BMY-26517 TBX Alegysal \
    --apply
# Result: 168/168 rows processed, 26 yielded non-empty snippets, 142 '[]'
```

### Pemirolast project: claims coverage delta

| Country | Total | Pre-import (EPO only) | Post-import (Google) | Mixed | Gap |
|---------|-------|----------------------:|---------------------:|------:|----:|
| CN | 139 |   0 | 108 | 0 | 31 |
| WO | 101 |  99 |   0 | 0 |  2 |
| CA |  46 |  40 |   5 | 0 |  1 |
| US |  27 |   0 |  20 | 0 |  7 |
| JP |  17 |   0 |  16 | 0 |  1 |
| KR |  11 |   0 |   8 | 0 |  3 |
| EP |  10 |   8 |   0 | 0 |  2 |
| TW |   6 |   0 |   5 | 0 |  1 |
| Others | 11 |   4 |   6 | 0 |  1 |
| **Total** | **369** | **151** | **168** | **0** | **50** |

168 rows of new claims content for non-EP jurisdictions where EPO OPS
returned 404. EP/WO unchanged (skip_jurisdiction protection working as
designed).

The remaining 50-row gap breaks down as: ~46 rows where Google Patents
also has no useful content (`skip_no_useful_content`), ~4 EP/WO rows
where EPO didn't capture content either (separate investigation, not
Task I scope).

---

## Lifecycle Note — Why `formulation_snippets` is reset on import

Discovered during apply phase: 406 rows that should have been
re-extracted by `backfill_snippets` were not picked up. Root cause:

1. Pre-Task-I, these rows had empty claims/examples; `backfill_snippets`
   ran and wrote `formulation_snippets = '[]'` (meaning: "processed, no
   evidence found")
2. Task I import filled claims/examples with real Google Patents content
3. `backfill_snippets` re-ran with candidate filter
   `WHERE formulation_snippets IS NULL` — the rows with `'[]'` (now
   stale) were invisible

First-pass fix: one-off `scratch/reset_snippets_for_reimport.py` cleared
406 stale `'[]'` values back to NULL, then `backfill_snippets` picked
them up.

Permanent fix (now in the importer): `_apply_update` always sets
`formulation_snippets = NULL` on any row it touches. Conceptually
correct — if raw text changed, snippets derived from it must be
recomputed. Atomic with the import, no separate cleanup step needed.

A more principled fix would adjust `backfill_snippets`'s candidate filter
to detect staleness (e.g. compare `fetched_at` vs the previous snippet
extraction's `completed_at`), but that's a bigger change to a production
path. The importer-side reset achieves the same effect with one line.

---

## Design Decisions

### Why off-machine scraping (vs Task H's in-process fetcher)

Task H assumed scraping on the production machine, with risk isolation
via the fetcher/backfill split. The actual constraint was different:
**company IT might flag automated scraping at the network level**, not
just the application level. Risk-isolating Python modules doesn't help
if the entire IP gets blocked.

Off-machine (Kaggle) scraping makes this a non-issue. The production
machine never makes outbound HTTP to Google Patents — only reads a local
JSONL file. The two-layer architectural complexity of Task H became
unnecessary.

Tradeoff: the JSONL becomes a contract between two systems (Kaggle
scraper, importer) that aren't in the same repo. Schema drift could
break the importer silently. Mitigation: the importer treats all fields
defensively (every column is optional, missing values handled
explicitly).

### Why no `modules/` module

Task H specified `modules/google_patents_fetcher.py` as a pure-function
HTTP fetcher consumable by future code (LLM analyzer, etc.). In the
off-machine model, there's no in-process fetcher to consume. The
importer is the entire production-side surface; a fetcher module would
have no callers.

If a future need emerges (e.g. on-demand fetch of one patent for
interactive inspection), revisit. For batch-screening use case, the
importer is sufficient.

### Why `source` gets new tags instead of staying `'epo'`

Without distinct tagging, downstream queries can't distinguish
EPO-original from Google-imported rows. This matters for:
- Trusting claims text for legal-adjacent decisions (EPO is authoritative,
  Google is best-effort)
- Debugging quality issues ("are weird snippets coming from
  Google-machine-translated CN/JP descriptions?")
- Future re-import or rollback (knowing which rows were touched)

The `mixed_epo_google_patents` tag adds further forensic granularity for
hybrid rows. Did not fire in this run but kept defensive.

### Why JSONL/CSV/data are gitignored

`global_patents_archive.jsonl` is 142 MB — too large for git, and
reproducible from the Kaggle scraper. Treated as runtime artifact, not
source. Kaggle scraper itself is the source-of-truth for what produced
this JSONL (versioned in Kaggle notebook history, not in this repo).

---

## Known Gaps / Future Work

1. **No automated link between Kaggle scraper and this repo.** Schema
   drift detection relies on importer defensiveness. If we run this more
   than 2-3 times, formalize the JSONL schema in `docs/spec/`.

2. **`backfill_snippets` staleness check is informal.** Importer-side
   reset works but couples two scripts. A proper fix would have
   `backfill_snippets` detect stale `'[]'` via timestamp comparison.

3. **No re-extraction trigger for non-Pemirolast projects.** The 408
   imported rows span multiple projects, but only Pemirolast was
   re-extracted post-import. Other projects' snippets will refresh next
   time someone explicitly runs `backfill_snippets --project X
   --aliases ...` for them.

4. **Mixed-source code path is untested in the wild.** Defensive code
   for a case that didn't occur. First time it fires (some future JSONL
   that includes a non-EP/WO at-risk row), verify behavior.

5. **No retry on failed JSONL lines.** If a JSON parse fails mid-file,
   that row is logged and skipped. No mechanism to flag for re-scrape
   from Kaggle side.

---

## References

- [task_H_google_patents_l2.md](task_H_google_patents_l2.md) — original
  spec, superseded by this approach
- [design_data_source_selection.md](design_data_source_selection.md) —
  why Google Patents won as supplement source
- [patentsview_probe_report.md](patentsview_probe_report.md) — why three
  alternative routes (PatentsView, USPTO ODP, PPUBS) were not viable
- [task_D.md](task_D.md) — `backfill_log` table and
  `_backfill_common.py` pattern reused here
- `scripts/import_google_patents_jsonl.py` — implementation
- `scratch/probe_jsonl_join.py` — probe evidence
