# Task D — Backfill Operation Playbook

> Operational runbook for `scripts/backfill_snippets.py` and (when shipped)
> `scripts/backfill_family.py`. Use this at execution time, not at design time.
>
> **Aliases per project are NOT listed here** — look them up at runtime via
> `cat configs/<project>.py | grep -A 10 DRUG_ALIASES`. Config evolves; this
> playbook does not.

---

## 0 · Pre-flight

Before any backfill operation, run the inspector once to capture pre-state:

```bash
python -m tools.inspect_backfill_log --list-projects
python -m tools.inspect_backfill_log --null-count
python -m tools.inspect_backfill_log --case2-count
python -m tools.inspect_backfill_log --dangling
```

Save the output (paste into a chat or a `scratch/` note) — this is your
baseline. Re-run `--null-count` and `--show` after each apply to confirm
the diff matches expectations.

If `--dangling` shows rows, **stop and investigate** before adding more
backfill runs. Hard crashes should be understood before being layered over.

---

## 1 · Identify scope: which projects need snippets backfill?

Run:

```bash
python -m tools.inspect_backfill_log --null-count
```

Expected initial output (from probe A7, 2026-05-25):
```
DB total patents:                          2914
formulation_snippets IS NULL:              1857
  of which have claims OR examples text:   485
  of which will get '[]' (no text):        1372
```

Per-project breakdown will tell you which `--project` flags to use.

**`<project>`** values come from `search_log.project` — these are not the
config filename, they are the `TARGET_PRODUCT` string as it appeared when
the search was run (some have Chinese / parens / underscores). Copy
exactly from `--list-projects` output. Quote with single quotes (or
escape parens) when passing on CLI.

---

## 2 · Snippets backfill — per project

For each project in scope, the recipe is:

### 2.a · Look up aliases for THIS project

```bash
cat configs/<config_filename>.py | grep -A 10 'DRUG_ALIASES'
```

Read the list, write it down for the next two commands. **Do not assume
the current `config.py` (active config) matches the project you are
backfilling** — the active config might be set to a different project,
and the safety rail in `backfill_snippets.py` will refuse to run with
default aliases on a multi-project DB anyway.

### 2.b · Dry-run

```bash
python -m scripts.backfill_snippets \
    --project '<exact project string from --list-projects>' \
    --aliases <alias1> <alias2> <alias3> ... \
    --dry-run
```

Expected output:
- `candidates: N row(s) (project=...)` — confirm N matches `--null-count`
  per-project number
- `aliases: [...]` — confirm matches what you grep'd
- `dry-run: of N, M have non-empty claims+examples` — `N - M` rows will
  get `'[]'`; that's expected

### 2.c · Apply

Same command, replace `--dry-run` with `--apply`:

```bash
python -m scripts.backfill_snippets \
    --project '<exact project string>' \
    --aliases <alias1> <alias2> <alias3> ... \
    --apply
```

Expected output:
- `[progress] 100/N written, ...` every 100 rows
- `done: N/N rows updated, M have non-empty snippets, ... got '[]'`

### 2.d · Verify

```bash
# Audit log shows new entry
python -m tools.inspect_backfill_log --show -n 5

# Per-project NULL count for THIS project should now be 0
python -m tools.inspect_backfill_log --null-count
```

If audit log row shows `alias_source: cli_override` and
`aliases_used: [...]` matches what you passed → good.

### 2.e · Inspect a sample row

```bash
python -m tools.inspect_patent <one_known_patent_id_from_this_project>
```

Confirm `stored snippets: yes` appears (rather than `empty/NULL`).

---

## 3 · Project iteration order

Verified state (2026-05-25 snapshot via `--null-count`):

| Order | Project (exact `search_log.project` string) | Config file | NULL count |
|---|---|---|---|
| 1 | `Roflumilast_鼻噴劑治療小腦萎縮症_(SCA)` | `roflumilast_sca.py` | 647 |
| 2 | `Acetaminophen_配方佐證搜尋` | `acetaminophen_formulation_evidence.py` | 528 |
| 3 | `Pemirolast_吸入劑治療特發性肺纖維化_(IPF)` | `pemirolast_ipf.py` | 258 |
| 4 | `Ampicillin_配方佐證搜尋` | `ampicillin_formulation_evidence.py` | 148 |
| 5 | `Apremilast_口服治療銀屑病_(Psoriasis)` | `apremilast_psoriasis.py` | 2 |
| skip | `Maxacalcitol_外用治療銀屑病_(Psoriasi` | `maxacalcitol_psoriasis.py` | 0 (post-Task-A; no NULL) |
| skip | (Vorinostat) | `vorinostat_gaucher.py` | n/a (no rows in search_log) |
| skip | `<no search_log entry>` (orphan rows) | — | ~274 NULL (defer to family backfill, §4) |

Sum: 647 + 528 + 258 + 148 + 2 = 1583. Plus ~274 orphan NULL = 1857
DB-wide NULL count. Matches `--null-count` output.

**Stop after each project, verify NULL count drops, audit log appended.**
Do not chain multiple projects in one go.

> Note on Maxacalcitol project name truncation: the `(Psoriasi` ending
> is from `TARGET_PRODUCT[:30]` slicing inside `patent_fetcher.py`
> (line 37). Cosmetic; does not affect backfill correctness. Out of
> Task D scope. Track as future cleanup.

---

## 4 · Snippets backfill — orphan rows (no search_log entry)

`--list-projects` shows a "no search_log entry" line: in current DB,
968 patents with no direct search_log row. These are family-member
patents upserted by `_fetch_and_store_family` — they have a `family_of`
reference but no search query attribution.

Of those 968, approximately 274 have `formulation_snippets IS NULL`
(derived: 1857 DB-wide NULL minus 1583 across the 5 known projects).

For these rows, `--project X` filter cannot reach them (the JOIN
excludes them). Two handling options:

**Option A: defer to family backfill** (recommended for Task D)
After `scripts/backfill_family.py` ships and runs, re-processing
parent A1 patents will trigger the production write path (which
already populates `formulation_snippets` correctly). Newly added
family members hit the right code path; existing orphans that
re-process via family expansion get their snippets filled then.

**Option B: bare `--force-all-projects` with a chosen alias set**
Risky — orphans came from many different drugs (Pemirolast family
members, Roflumilast family members, etc.). A single alias set would
write meaningful snippets for one drug's orphans but `[]` for the
rest. **Not recommended.**

**Decision for current Task D scope: defer (Option A).** Document
residual NULL count after step 2 completes for all projects; revisit
after family backfill ships. Most orphans are non-EP patents
(US/CN/EA/KR — see PROJECT_SKILL §4.1) with empty claims and examples,
so even an "ideal" backfill would write `[]` for the majority.

---

## 5 · Family backfill (when `backfill_family.py` ships)

*(Pending implementation — placeholder. Will be filled in when
`scripts/backfill_family.py` is shipped per task_D.md Implementation
Order step 3-5.)*

Expected scope from probe A3:
- Acetaminophen: 163 Case-2 candidates
- Pemirolast: 139
- Ampicillin: 16
- Plus 4 Case-1 hard-coded IDs (will be UNION'd into candidate set)

EPO API cost estimate: 318 × ~4 calls average ≈ 1300 API calls ≈
40-130 MB out of 3.5 GB weekly quota.

---

## 6 · Post-backfill audit

After all projects done:

```bash
# Should be ~277 (orphan rows + truly-no-text rows)
python -m tools.inspect_backfill_log --null-count

# Confirm remaining NULL rows are all historical (pre-Task-A).
# Should report all rows pre-date 2026-05-21. If any post-Task-A
# NULL row appears, that IS a current production bug — investigate
# per the (now-resolved) bug_Z_resolved.md pattern.
python -m tools.inspect_backfill_log --null-provenance
```

If the residual NULL count differs significantly from probe A7's
1857 - (sum of per-project NULL counts processed), investigate:
- New patents fetched between probe and apply?
- Orphan rows not counted under any project?
- A project skipped accidentally?

```bash
# Final audit summary
python -m tools.inspect_backfill_log --show -n 20
```

Save the output — this is your record of what was done.

---

## 7 · Recovery / rerun

The snippets backfill is **retry-safe**. `formulation_snippets IS NULL`
filter excludes rows already processed, so running the same command
twice writes only the rows that genuinely still need processing (zero,
on second run).

If you discover the wrong aliases were used (e.g. you forgot
"acetaminophen" alias and only used "paracetamol"), the rows are no
longer NULL — they have `'[]'` or partial snippets. To **re-extract**
with corrected aliases, the current script cannot help (it only targets
NULL). Two options:

**Option A: manual SQL reset, then rerun**
```python
# Read-only inspector won't do this; this requires a separate
# scratch/reset_snippets_for_reextraction.py one-off script with
# explicit project + alias-mismatch scope. Not Task D scope; tracked
# as a future need (see task_D.md retrospective note).
```

**Option B: accept the partial extraction**
For ad-hoc inspection, use `tools/inspect_patent.py` with `--aliases`
to see what additional snippets would have been found. The stored
column stays as-is.

---

## Appendix · Quick reference

```bash
# Inspector subcommands
python -m tools.inspect_backfill_log --show              # recent runs
python -m tools.inspect_backfill_log --show-id <ID>      # one full row
python -m tools.inspect_backfill_log --list-projects     # project inventory
python -m tools.inspect_backfill_log --null-count        # snippets scope
python -m tools.inspect_backfill_log --case2-count       # family scope
python -m tools.inspect_backfill_log --dangling          # crash check

# Snippets backfill canonical form
python -m scripts.backfill_snippets \
    --project '<EXACT project string>' \
    --aliases <alias1> <alias2> ... \
    --apply

# Always do --dry-run first.
```