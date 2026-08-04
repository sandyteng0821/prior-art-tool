# Task O — Fetch-Layer Failure Caching

**Phase 1: SHIPPED** 2026-08-04 (`fix/fetch-failure-caching`)
**Phase 2: OPEN** — not scheduled
Created 2026-08-03. Baseline tag: `pre-task-O-fetch-cache-fix`.

> **This document is self-contained on purpose.** The probes that produced
> the evidence below live in `scratch/`, which is gitignored — they exist
> only on the workstation they were run on and will not survive a fresh
> clone. Every number that justified a decision is therefore reproduced
> inline, with its measurement date. Don't go looking for probe output
> files; read the tables here.
>
> If this ever needs to be re-measurable, the move is to promote
> `probe_wo_fulltext.py` from `scratch/` into `tools/`. Not done, because
> it was written to answer one question and that question is answered.

---

## One paragraph

`modules/patent_fetcher.py` cached *every* fetch failure as an empty
string for 30 days, so a network timeout became indistinguishable from
EPO legitimately not licensing that document's fulltext. `_get_or_fetch()`
then wrote that empty value into `patents.db`, where it is permanent. One
transient timeout therefore produced a silently empty field that no later
audit could tell apart from real data. Phase 1 makes the fetch layer
distinguish permanent (HTTP 404) from transient failure and stops caching
the transient ones. Phase 2 — finding and repairing already-damaged rows
— is open, and is hard for a reason explained below.

---

## The bug

All three cached fetch functions had this shape:

```python
def _fetch_claims(patent_id: str) -> str:
    cache_key = f"claims::{patent_id}"
    if cache_key in cache:
        return cache[cache_key]          # (3) and never retries

    try:
        ...
    except Exception as e:               # (1) any failure at all
        print(...)
        result = ""                      # (2) collapses to empty

    cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
```

Three problems compound:

1. **No discrimination.** A 404 (permanent — EPO doesn't license US/CN/
   KR/JP/EA fulltext, PROJECT_SKILL §4.1) and a `ReadTimeout` (transient)
   produce the same value.
2. **Failure is persisted.** `cache.set` runs unconditionally.
3. **The read path short-circuits.** Once cached, the function is never
   re-entered for that patent.

### Scope: three functions, not one

Verified by reading every fetch function, not generalised from
`_fetch_claims`:

| Function | Cached? | Expiry | Logged failure? | Poisoned? |
|---|---|---|---|---|
| `_fetch_claims` | yes | 30d | yes | yes |
| `_fetch_abstract` | yes | 30d | **no** — bare `except Exception:` | yes, silently |
| `_fetch_title` | yes | 30d | **no** — bare `except Exception:` | yes, silently |
| `_fetch_description` | **no cache** | — | no | no — re-fetches, self-heals |

`_fetch_claims` was the *best* of the three: it at least printed.
`_fetch_abstract` and `_fetch_title` didn't bind the exception at all — a
direct §3.2 violation.

`_fetch_abstract` was the most damaging. Per §4.1, snippet extraction for
non-EP jurisdictions relies **entirely** on `abstract`. A timeout there
removes a US/CN patent's only evidence channel, with no log line.

### Why cache expiry didn't bound it

`_get_or_fetch()` writes fetched values to DB via `upsert_patent()`, then
on later runs returns early:

```python
stored = get_by_id(patent_id)
if stored:
    print(f"  [DB hit] {patent_id}")
    return stored          # _fetch_* is never called again
```

So the 30-day expiry is irrelevant once an empty value reaches DB. The DB
write is permanent. Damage propagates through three layers — diskcache →
`patents.db` → `formulation_snippets` (computed as `[]` from empty text) —
and `backfill_snippets.py` can't repair the third, because its candidate
filter is `formulation_snippets IS NULL` and these rows hold `'[]'`. That
is the recovery gap already recorded in `task_D_operation.md` §7.

---

## Evidence

### The failure that started this (2026-08-03)

While probing whether WO fulltext is available via EPO OPS — needed
because ~66% of the HS project's search hits are WO — one patent failed
mid-probe:

```
WO2020049327A1    claims: ReadTimeout    description: ok, 24,699 chars
```

Same run, same credentials, adjacent calls. On the production path this
would have stored `claims=''` next to a populated `examples_extracted` —
a shape **identical** to the legitimate non-EP licensing pattern.
Undetectable after the fact.

### WO fulltext availability (2026-08-03, n=10 + 4 controls)

| Bucket | claims | description |
|---|---|---|
| WO (n=10) | 9/10 (90%) | 10/10 (100%) |
| EP-A control (n=2) | 0/2 | 0/2 |
| US control (n=2) | 0/2 | 0/2 |

Failure rate on fulltext endpoints: **1/10** — and that one failure was
the transient timeout above.

> **The EP control was mis-designed.** It sampled `EP2575884A2` and
> `EP4237411A1`, both EP-**A** — a class already known to be ~67% empty
> (`patent_pipeline_coverage_gaps.md` §2), because many EP-A rows are
> PCT-entry shells whose text lives on the WO sibling. A valid positive
> control needs EP-**B**. The WO 200s independently prove client and
> credentials were fine, so the verdict stands, but don't read "EP control
> 0/2" as evidence of anything.

Side finding, recorded because it matters for HS and contradicts a doc:
**WO fulltext IS available.** PROJECT_SKILL §4.1 lists WO among the 404
jurisdictions; that's wrong. `patent_pipeline_coverage_gaps.md` §2
footnote 3 is right. Fulltext follows the *original publication* — an EP-A
shell is empty while its WO sibling carries the text.

### For comparison: biblio endpoint

`task_K.md` recorded `ReadTimeout: ~15` out of 5841 on biblio — far lower.
Fulltext responses are much larger (the probe saw description payloads up
to 334KB), so timeouts are correspondingly likelier. Use ~10% for
fulltext, not for biblio.

### DB fingerprint at baseline tag (2026-08-03)

Also embedded in the `pre-task-O-fetch-cache-fix` tag message.

```
total_patents         8757   (integrity ok)
with_examples         1360
without_examples      7397
family_fetched        1293
family_members_in_db  4781
by_source             epo 7990 / google_patents 767
with_expiry_date      7671   (filing_plus_20 7642 / orange_book 29)
without_expiry_date   1086
formulation_snippets IS NULL   515   (384 have claims-or-examples text,
                                      131 would resolve to '[]')
```

Snapshot: `patents_pre_taskO_20260803.db`, copied to
`~/backups/prior_art_tool/` — outside `cache/`, which is gitignored and
shares an rm-blast-radius with the live DB.

> DB was 8757 rows here, well above `task_D.md` (2914, 2026-05-25) and
> `task_K.md` (5841, 2026-06-30). Any Phase 2 population estimate taken
> from those docs is stale.
>
> Unrelated observation from the same capture: all 515 NULL-snippet rows
> failed to join `search_log` — family-expansion orphans (277 in May, 515
> now). That's Task D Phase 2 (`backfill_family`) territory, still
> deferred. Not Task O's problem.

---

## Phase 1 — SHIPPED 2026-08-04

### What changed

`modules/patent_fetcher.py`, four edit sites.

**(1)** Two new module-level helpers, after `_parse_patent_id()`:

- `_classify_fetch_failure(exc) -> "permanent" | "transient"` — reads
  `exc.response.status_code` when present, else matches the exception
  class name against `_TRANSIENT_EXC_NAMES`, else falls back to `"404" in
  str(exc)` (the same string check `fetch_patents()` already relies on).
- `_cache_result(cache_key, result, failure)` — the single decision point
  for whether an outcome may be cached. Logs and returns without writing
  when the failure is transient.

The code itself is in `modules/patent_fetcher.py` — deliberately not
copied here, because an inline copy would drift from the real thing while
still looking authoritative. `git log -S_classify_fetch_failure` finds
the history.

**(2)(3)(4)** In each of `_fetch_claims`, `_fetch_abstract`,
`_fetch_title`: initialise `failure: Exception | None = None` before the
`try`, set `failure = e` in the outer `except`, and replace
`cache.set(...)` with `_cache_result(cache_key, result, failure)`.
`_fetch_abstract` and `_fetch_title` additionally gained the missing
`as e` and a print.

> Both edits must land together per function. Swapping in `_cache_result`
> without initialising `failure` raises `UnboundLocalError` on the success
> path, since `failure` is only assigned inside `except`. (This is how the
> first attempt half-landed: `failure = e` was added to all three, but the
> initialiser and the `_cache_result` swap only to `_fetch_claims`. Tests
> caught it as exactly 4 failures.)

Only the **outermost** `except` in each function was touched. The inner
`except Exception:` blocks (json → xmltodict fallback) are legitimate and
untouched. `_fetch_description` unchanged: no cache, so failures
self-heal on the next call.

### Cache policy

| Outcome | Cached? | Expiry | Value |
|---|---|---|---|
| success | yes | 30d | result |
| permanent (404) | yes | 30d | `""` — unchanged from before |
| transient | **no** | — | `""` returned, not stored |

### Verification (2026-08-04)

- `tests/test_fetch_cache.py` — **27/27**. Fully offline: swaps
  `patent_fetcher.client` for a stub that raises, `patent_fetcher.cache`
  for a temp diskcache, `time.sleep` for a no-op. Asserts, for all three
  functions: transient → key absent; transient → client re-invoked next
  call; 404 → key present with `""`; 404 → second call served from cache.
  Plus classifier unit cases and one success-path case.
- `tests/test_debug_tools.py` — **43/43**, unchanged.

The test deliberately does **not** use `WO2020049327A1` as a fixture.
That timeout was transient and will likely succeed on retry; a test
depending on a network failure isn't a test. It's this task's provenance,
not its fixture.

Phase 1 does not improve coverage on existing rows. It prevents new
poisoning.

---

## Phase 2 — OPEN

Not blocking the HS project: of 85 HS patent IDs sampled at probe time,
only 4 (5%) were already in DB. HS is almost entirely new fetches, so
historical poisoning doesn't reach it, and Phase 1 protects the new ones.

### 2a. Cache purge — moved here from Phase 1, and probably not worth doing

Originally specced into Phase 1, reasoning that the fix changes only
*write* behaviour while the read path short-circuits on existing entries
— so the fix is inert for anything already cached as `""`. That reasoning
is correct. The conclusion was wrong, because the population wasn't
measured first.

Measured 2026-08-04 over `cache/epo`:

| prefix | scanned | empty | in DB | not in DB |
|---|---|---|---|---|
| `claims` | 3131 | 2527 (81%) | 1956 | 571 |
| `abstract` | 3131 | 927 (30%) | 358 | 569 |
| `title` | 3131 | 596 (19%) | 27 | 569 |
| **total** | 9393 | 4050 | | |

Same 3131 patent IDs × 3 endpoints. (`search::` keys — 32, list-valued,
7-day expiry — are out of scope and untouched. 3131 ≈ what was fetched in
the last 30 days, versus 8757 permanent DB rows; 767 of those came from
Google Patents and never went through `_fetch_*` at all.)

Three readings:

**The 81/30/19 split is jurisdiction licensing, not corruption.** `claims`
hits `ftxt:fulltext-documents`, which is what §4.1's US/CN/KR/JP/EA 404s
apply to. `abstract` and `title` hit biblio-family endpoints, broadly
available everywhere. So the great majority of those 2527 empty `claims`
entries are genuine 404s.

**The ~570 not-in-DB IDs are the same IDs across all three prefixes**
(569/571/569). `_get_or_fetch`'s main path calls `upsert_patent()`
unconditionally, so anything that went through it has a row even if every
field is empty. These have no row, so they never went through it. The only
call sites that fetch without necessarily upserting are the speculative
ones:

```python
b1_id = f"{number}B1"          # a guessed ID, may not exist
...
if b1_title or b1_claims:      # both empty → no upsert
```

and the equivalent `if title or abstract:` in `_fetch_and_store_family`.
So these are speculative probes for documents that mostly **don't exist**
— every endpoint 404s together, which is exactly why the three counts
match. `title` is the clearest tell: 596 empty, only 27 in DB. The biblio
endpoint almost always returns something for a document that exists.

**Therefore purging costs real quota for near-zero gain.** The in-DB
entries will never be read again (DB-hit short-circuit), so deleting them
is decoration. The ~570 not-in-DB entries would trigger ~2000 re-probes,
nearly all of which 404 again.

A genuinely poisoned row *could* hide in that 570 — a family member whose
title and abstract both timed out fails `if title or abstract` and is
never stored, leaving no trace at all. But separating those from real 404s
is the same undecidable problem as 2b. So: same phase, or neither.

`scripts/purge_empty_cache_entries.py` is committed and works (dry-run
default, `--apply` required, `--json` report). It has deliberately **not**
been run.

### 2b. Row audit — the hard part

**Don't attempt to enumerate all poisoned rows.** A poisoned row isn't
distinguishable from a legitimately empty one by inspection; deciding
would require re-fetching all 8757.

What *is* tractable is jurisdiction-conditioned anomaly detection, using
coverage rates as priors:

| Bucket | Expected claims coverage | Source |
|---|---|---|
| EP-B | ~95% | coverage_gaps §2 |
| WO | ~90% | this task's probe, 9/10 |
| EP-A | ~31% | coverage_gaps §2 |
| US / CN / KR / JP / EA | ~0% | §4.1 licensing |

So `EP-B AND claims=''`, and `WO AND description non-empty AND claims=''`,
are anomalies worth re-fetching. US/CN empties are expected — leave them
alone.

Both the EP-B and WO figures are thin: one from a ~2900-row DB in June,
the other from a 10-patent sample. Recompute over the current DB before
setting thresholds. No rush — **Phase 1 touches only diskcache, never the
DB**, so these statistics are identical before and after it. (An earlier
draft of this spec said to capture them "before the DB is touched." Wrong
— nothing touches the DB.)

Suggested split, per §3.3 (separate CLIs by risk profile):

- `tools/audit_empty_channels.py` — read-only, lists anomalies by bucket,
  run freely
- `scripts/refetch_suspect_channels.py` — writes DB, `--dry-run` default,
  `--apply` required, audit-logged via `_backfill_common.start_run`

Don't merge them.

---

## Decisions and why

**Only 404 counts as permanent.** Every other failure, including ones the
classifier has never seen, defaults to transient and is retried. The
asymmetry is deliberate: poisoning is silent and unrecoverable, while
excess re-fetching is visible in logs and costs only quota. If a novel
permanent error shows up at volume it'll be obvious from repeated retries,
and can be added to the allowlist then — with an actual exception type in
hand rather than a guess.

**404 stays cached.** Non-EP fulltext 404 is permanent and affects
thousands of rows. Dropping that cache means re-probing every non-EP
patent's claims endpoint on every run. The bug was the *absence of
discrimination*, not the presence of caching.

**No `'N/A'` sentinel in Phase 1.** A third class for "HTTP 200 but
unexpected response shape" is reasonable, and would match the vocabulary
`probe_coverage_v2.py` already uses. But `'N/A'` is truthy:
`backfill_snippets`'s dry-run counts `if (r["claims"] or "")` as having
text, and other call sites likely do the same. Introducing it needs its
own audit of every consumer. Parking lot.

**`purge_empty_cache_entries.py` writes no `backfill_log` row.** That
table lives in `patents.db`; the script touches only diskcache. Logging a
DB audit row for a cache-only operation would misrepresent what happened.
The printed report and `--json` are the record. Deliberate departure from
the Task D backfill convention, noted so it doesn't read as an oversight.

---

## What NOT to do

- Don't remove the 404 cache — quota.
- Don't add caching to `_fetch_description`. Its lack of a cache is
  exactly why it self-heals. Adding one reintroduces this bug class.
- Don't touch the inner `except Exception:` json/xml fallbacks.
- Don't try to enumerate all historical poisoned rows — undecidable.
- Don't run the purge expecting a coverage improvement. See 2a.
- Don't merge the Phase 2 audit tool with its repair script.
- Don't read "EP control 0/2" in the probe results as a signal. See the
  note under the WO table.

---

## Docs to update

- `PROJECT_SKILL.md` §4.1 — remove WO from the 404 jurisdiction list. WO
  fulltext is available (claims 9/10, description 10/10). Consequence
  worth noting: `import_google_patents_jsonl.py` treats EP/WO as
  `skip_jurisdiction` on the grounds that EPO is authoritative, and for WO
  that turns out to be correct.
- `PROJECT_SKILL.md` §3.2 — the current rule ("every `except` should at
  minimum print") is necessary but not sufficient. `_fetch_claims`
  satisfied it and the bug happened anyway: printing makes a failure
  visible at runtime but doesn't stop it being persisted. Proposed added
  clause:

  > A failure must not be written to the same persistence layer as a
  > successful result. If failures are cached, permanent and transient
  > causes must be distinguishable.

- `architecture.md` — new Gap row for Phase 2. Don't add to the pipeline
  diagram (§3.4) until Phase 2 ships.

---

## Non-Goals

- Retry/backoff inside the fetch functions. Not caching the failure is
  sufficient — the next run retries. Automatic retry is a separate design
  question.
- Fixing the `formulation_snippets` re-extraction gap
  (`task_D_operation.md` §7). Related, since a repaired `claims` value
  should invalidate derived snippets, but it belongs to Phase 2 if Phase 2
  ships.
- Any change to `_get_or_fetch`'s DB-hit short-circuit.

---

## Artifact status

Committed and durable:

- `modules/patent_fetcher.py` — the fix
- `tests/test_fetch_cache.py` — 27 offline checks
- `scripts/purge_empty_cache_entries.py` — present, not run
- tag `pre-task-O-fetch-cache-fix` — DB fingerprint in the tag message

Local-only, **will not survive a fresh clone** (`scratch/` and `cache/`
are both gitignored):

- `scratch/probe_wo_fulltext.py`, `scratch/probe_hs_queries.py` — the
  probes. Promote to `tools/` if they ever need re-running.
- `scratch/probe_hs_20260803.json`, `scratch/taskO_wip.md`
- `cache/patents_pre_taskO_20260803.db` — snapshot; the durable copy is
  `~/backups/prior_art_tool/`, outside the repo entirely

Every number those artifacts produced is transcribed into this document.
