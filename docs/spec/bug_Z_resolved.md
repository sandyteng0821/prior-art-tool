# Bug Z (resolved) — Historical NULL `formulation_snippets` from pre-Task-A family expansion

> **Status:** Resolved. Not an active production bug.
> **Discovered:** 2026-05-25, during Task D Phase 1 backfill execution.
> **Resolved:** 2026-05-25, in the same investigation.
> **Action required:** None. Family backfill (Task D Phase 2) will
> clear remaining 277 historical NULL rows incidentally.

---

## What Bug Z appeared to be

While executing Task D Phase 1 (snippets backfill) on 2026-05-25, the
inspector tool `inspect_backfill_log.py --null-count` showed the
Apremilast project (first production run 2026-05-21, post-Task-A
merge) had 2 rows with `formulation_snippets IS NULL`.

By spec construction, post-Task-A code paths always populate
`formulation_snippets` (either `"[]"` or actual content) — never NULL.
The presence of 2 NULL rows in a post-Task-A project suggested a
silent production bug, with this hypothesis space:

1. `_fetch_and_store_family()` member fetch path occasionally writes
   member rows without going through `_collect_snippets()`.
2. B1 auto-upgrade path writes second-fetch rows without snippets.
3. Some upsert path omits `formulation_snippets` from its input dict,
   relying on default NULL.

---

## What it turned out to be

A read of the actual offending rows (via cross-referencing
`search_log.project` overlap with `--null-count` per-project changes)
revealed:

- The 2 NULL rows attributed to Apremilast were **shared** with the
  Roflumilast project (same `patent_id` in `search_log` twice, under
  different `project` values).
- These rows were originally written by Roflumilast family expansion
  in 2026-03 / 2026-04 — well before Task A merge.
- When Apremilast was searched on 2026-05-21, the search query happened
  to also match those same `patent_id`s, adding new `search_log` rows
  but NOT touching the patents table.
- The Apremilast project itself wrote 244 patent rows post-Task-A; 55 of
  them already had populated `formulation_snippets` and 187 had `"[]"`.
  **Zero** Apremilast rows wrote NULL.

Confirmation via inspector subcommand `--null-provenance` added during
investigation:

```
Remaining NULL formulation_snippets row provenance:
  total rows:                          277
  oldest fetched_at:                   2026-03-23T16:34:09.057006
  newest fetched_at:                   2026-05-12T13:42:48.886087
  fetched_at >= 2026-05-21 (post-Task-A): 0
  fetched_at <  2026-05-21 (pre-Task-A):  277
```

All 277 remaining NULL rows pre-date Apremilast's first production
run (2026-05-21). The newest NULL row (2026-05-12) corresponds to
Acetaminophen's second production run, the last run on the previous
codebase. After Task A merged, no NULL row has been written.

**Bug Z does not exist in current production.** Hypothesis 1 in the
original investigation plan is closest to historical truth: the
pre-Task-A `_fetch_and_store_family()` path did not populate
`formulation_snippets` for newly-upserted family members. Task A
itself was the fix; the 277 historical NULL rows are residue from
before that fix.

---

## Why it looked like a current bug

Two confusion factors made the discovery look like a current issue:

1. **`search_log` JOIN reads patent provenance as plural.** A patent
   matched by N different project search queries appears in N
   `search_log` rows. When `--null-count` joins on `search_log` and
   groups by `project`, the same NULL `patent_id` counts under every
   project that ever searched it. So a pre-Task-A NULL row written by
   Roflumilast in March 2026 appears under both "Roflumilast" and
   "Apremilast" lists if Apremilast's 2026-05-21 search also matched
   the patent. **Per-project NULL counts are not partitioned by
   write-path provenance.**

2. **`fetched_at` is the only reliable provenance signal**, and it
   wasn't being inspected until the question was framed correctly.
   Once `--null-provenance` was added to the inspector, the answer
   was instantaneous.

---

## Lessons

1. **Project membership ≠ write-path provenance.** When auditing
   "rows written by project X", filter on `fetched_at` (and
   sometimes `family_of`) — not on `search_log JOIN project = X`.
   The latter answers "what did project X search hits include",
   which is broader than "what did project X write".

2. **`--null-provenance`-style audits should be a standard step**
   after any backfill, not an afterthought. The remaining-NULL row
   set tells you whether your fix is the last word or just the
   latest patch.

3. **`fetched_at`-bucketed audits are cheap and high-signal.** When
   suspecting a code-path bug, ask "what's the timestamp distribution
   of the bad rows?" before grepping code. If the distribution is
   purely historical, the bug is fixed and you're looking at
   residue, not a leak.

4. **Spec evolution risk.** This investigation went through three
   spec versions in one day (current bug → narrowed bug → historical
   artifact), each rewrite triggered by a more precise probe. The
   right discipline is **delay spec finalization until probe data
   resolves ambiguity** — same lesson as Task D pre-implementation
   probe (PROJECT_SKILL §7 row "Probe → spec-restructure").

---

## Residue handling

277 NULL rows remain in DB as of 2026-05-25 post-Phase-1 backfill.

- 143 have no claims and no examples_extracted → backfilling them
  would write `"[]"`. Possible via `--force-all-projects`, but their
  drug attribution is unknown (orphan rows from multiple projects'
  family expansions). Alias choice would be arbitrary.
- 134 have claims OR examples text → backfilling them would write
  meaningful snippets, but alias choice is again unknown without
  parent-project attribution.

**Decision:** defer to Task D Phase 2 (`backfill_family.py`). When
Phase 2 re-triggers `_get_or_fetch()` on pre-May parents, it will
re-upsert family members through the current Task-A-aware code path,
which populates `formulation_snippets` correctly. The 277 historical
NULL rows should reduce to near zero as a side effect.

After Phase 2, re-run `--null-provenance` to confirm cleanup.

---

## References

- Discovery: `docs/spec/task_D.md` retrospective note §8, §9
- Inspector subcommand: `tools/inspect_backfill_log.py --null-provenance`
- Related lessons: PROJECT_SKILL §3.1 (probe before code), §3.4
  (don't write architecture for unimplemented features), §7
  (collaboration patterns).
- Task A merge (the implicit fix): exact commit unknown to this
  note; per `fetched_at` evidence, merged between 2026-05-12 and
  2026-05-21.