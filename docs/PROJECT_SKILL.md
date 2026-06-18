# Prior Art Tool — Project Skill

> Collaboration conventions, anti-patterns, and known limits accumulated
> from real working sessions. Source of truth for how this repo is
> developed and how the human + LLM collaboration is structured.
>
> Last updated: 2026-05-21

---

## 1. Document Hierarchy

| Layer | Filename pattern | Role | Update cadence |
|-------|------------------|------|----------------|
| Strategic context | `docs/spec/design_<topic>.md` | Why a subsystem exists; design rationale; written once and locked at major architectural shifts | Slowly |
| Implementation state | `docs/architecture.md` | Current state of the implementation; pipeline diagram; gap analysis; known limitations | Per-feature |
| Tactical work | `docs/spec/task_<X>.md` | One spec per ticket; what to do, how to verify, what NOT to do | Per-task |
| Collaboration meta | `docs/PROJECT_SKILL.md` | This file. Conventions and lessons | When pattern emerges |

> **Important:** `task_<X>.md` exists ≠ feature implemented. Specs can be committed ahead of code. Check `git log` or `architecture.md` gap analysis to determine real implementation status.

---

## 2. Code Conventions

### Directory layout

| Path | Purpose | Git tracked? |
|------|---------|--------------|
| `modules/` | Production code; stable; touched only with regression tests | Yes |
| `tools/` | Daily-use utilities (read-only or sandbox-write) | Yes |
| `scripts/` | One-off migrations / backfills / setup; written-once, may be deleted later | Yes |
| `tests/` | Regression tests; pytest-style or runnable as module | Yes |
| `scratch/` | Debugging, probes, experiments | **Gitignored** |
| `cache/` | SQLite DB, diskcache | Gitignored |
| `output/` | Generated reports | Gitignored |
| `outputs/ground_truth/` | Evaluation artifacts | Tracked, but data may be regenerated |

### Naming

- `modules/` files: single noun (`patent_fetcher.py`, `patent_store.py`)
- `tools/` files: verb-led (`inspect_patent.py`)
- `scripts/` files: `backfill_<thing>.py`, `migrate_<thing>.py`
- Spec files: `task_<letter>.md` for tickets; `design_<topic>.md` for design rationale

### Commit message format

```
type(scope): short summary

Body paragraph 1: what changed and why
Body paragraph 2: root cause if it's a fix
Body paragraph 3: verification done

Refs: spec name / commit hash / investigation chain
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`.

Examples (real commits from this repo):

- `fix(snippets): match comprising/comprised, not just comprises`
- `fix(fetch): widen family expansion kind filter; skip self-references`
- `docs: archive Task C spec and record findings in architecture`

### Branch / merge

- Open feature branch from `main` for non-trivial work
- `--ff-only` merge if no main conflict
- Keep `config.py` modified locally; never commit project-specific config

---

## 3. Anti-patterns (with concrete examples)

### 3.1 Don't trust spec assumptions — probe first

**Symptom:** Spec describes a bug. You implement the fix. The bug isn't fixed because the diagnosis was wrong.

**Concrete case (Task C):** Spec described two bugs:

1. `_fetch_claims` returns 404
2. Sentence splitter fails on `a)/b)` claim structures

Probe (5 min, run `_fetch_description` against test patents) revealed:

1. Was an EPO licensing limit (US/CN/EA fulltext not in subscription), not a code bug
2. Splitter worked fine; real bug was keyword `comprises` not matching `comprising`

**Rule:** Before implementing any spec, run a 5-minute probe to verify the assumptions. If probe contradicts spec, push back before coding.

---

### 3.2 Don't silent-except

**Symptom:** Function quietly returns empty string on error. Bug invisible for weeks.

**Concrete case (Day 1):** `_fetch_claims` had `except Exception: return ""`. This hid that EPO returns 404 for US/CN fulltext. Cost ~1 day of confused debugging during Task A validation.

**Rule:** Every `except` block should at minimum `print(f"[function] failed: {e}")`. Use `log.warning()` if a logger exists. Silent failures accumulate as ghost bugs.

---

### 3.3 Don't merge inspect tools and migration scripts

**Symptom:** "Both read patents from DB; can we combine into one CLI?"

**Concrete case (Day 2):** Considered merging `inspect_patent` with `backfill_snippets`. Rejected because:

- Inspect = exploratory, read-only, run-many-times
- Backfill = transactional, writes DB, run-once
- Mental models conflict
- Inspect's "read-only" invariant is a core promise

**Rule:** Tools that share underlying logic should share `modules/` functions, not CLI entry points. Separate CLIs by **risk profile**, not by **code similarity**.

---

### 3.4 Don't write architecture.md entries for unimplemented features

**Symptom:** Architecture reads like aspirational document. Reader can't tell what's actually in main vs. what's planned.

**Rule:** Architecture reflects **current state of implementation**. Specs describe future state. If a feature exists only as spec, mention it under "Gap Analysis ⚠️ Pending" or "Roadmap"; don't put it in the main pipeline diagram or schema description as if it exists.

---

### 3.5 Don't conflate "spec committed" with "feature shipped"

**Symptom:** You see `docs/spec/task_X.md` in the repo and assume the feature works.

**Concrete case (Day 2 evening):** After committing `task_E.md`, briefly thought eval V0 was done. It wasn't — only the spec was. Code came later.

**Rule:** Always check `git log` for the actual code commit before claiming a task is done. Specs are forward-looking; commits are evidence.

---

### 3.6 Don't push spec forward when probe reveals assumption collapse

**Symptom:** Spec assumes X. Probe shows X is false. You modify spec to "handle X being false" rather than questioning whether the task makes sense at all.

**Concrete case (Task E rejected portion):** Spec planned extending `inspect_patent` to fetch raw description for evidence verification. Probe showed US fulltext unavailable via EPO. Original instinct was to add fallback logic; correct response was to recognize the task doesn't solve the immediate need (10/11 patents are US/CN, EPO can't help) and reject.

Replaced with 5-line patch (Espacenet/Google Patents URL in inspect header) that actually serves the use case.

**Rule:** When probe shows the spec premise is wrong, reconsider whether the task should be done at all — not just how to patch around it.

---

## 4. Known Limits (verified, don't re-investigate)

### 4.1 EPO OPS subscription does not include non-EP fulltext

US, CN, EA, KR, JP, WO fulltext (`claims` and `description` endpoints) returns HTTP 404. This is a data licensing limit, **not a code bug**.

Verified 2026-05 via probe matrix (5 model class variants × multiple patent types). No combination of `Epodoc / Docdb / Original` unlocks non-EP fulltext.

**Implications:**

- US/CN/EA/KR/WO patents in DB have empty `claims` and `examples_extracted`
- Snippet extraction for these patents relies entirely on `abstract`
- Family expansion of EP-A1 to EP-B1 is the primary path to obtain granted-patent fulltext

**Future option (out of scope):** Integrate Google Patents Public Datasets for non-EP fulltext coverage.

### 4.2 Espacenet web UI ≠ OPS API backend

Espacenet (EPO's public web) shows full US claims and description. OPS API does not. **They use different licensing backends.**

When users report "but I see the data on Espacenet, why can't your tool get it?" — the answer is the two are separate access tiers.

### 4.3 EA / certain non-EP jurisdictions stuff content into abstract

EA (Eurasian Patent Office) and some others return the full claims set inside the `abstract` field (6000+ chars). This is not a code bug or malformed response — it's the upstream data shape.

`tools/inspect_patent.py` already handles this by treating abstract as a searchable text field.

### 4.4 EPO search indexing is incomplete for non-EP/US jurisdictions

`ta=<drug>` queries reliably return EP/US patents. TW/KR/AU patents that exist in the system can be retrieved by `pn=<patent_id>` directly but don't appear in `ta=` searches.

**Workaround:** Family expansion. When an EP A1 has a TW/KR/AU sibling, the family API returns it. This is why Task A's `_fetch_and_store_family` matters.

### 4.5 Family expansion filter widened May 2026

`_fetch_and_store_family()` previously accepted only `{B1, B2}` kind codes (granted patents). This silently skipped cross-jurisdiction application versions (TW/KR/AU/JP typically publish as bare `A`).

Fixed (Bug X, commit `c3206ce`): filter now accepts `{B1, B2, A1, A2, A}`.

Pre-fix DB rows are not retroactively re-expanded. See Task D backfill spec.

### 4.6 V1 P@k quality scales with evidence base size, not pipeline correctness

實證 (2026-05-22, after Task F ship): V1 在不同 (drug, target excipient)
組合跑出來的 P@k 數字差異極大，不代表 pipeline 機制有問題。

| Run | Candidate set | Avg text/patent | Ground truth size | P@5 | P@10 |
|---|---|---|---|---|---|
| Ampicillin × Lactose, Anhydrous | 187 | 2,565 chars | 3 keywords | 0.40 | 0.20 |
| Acetaminophen × MCC | 677 | 3,116 chars | 15 keywords | 1.00 | 0.90 |

差異來自 weak-label evaluation 的本質限制：

- 主流 (drug × excipient) 組合（e.g. acetaminophen tablet + MCC）在 patent
  corpus 有密集 evidence，V1 substring match 容易命中
- 邊緣組合（e.g. ampicillin oral 多元劑型）evidence 稀薄、人工策展補強
  的關鍵 patent 不在 auto-derived CSV 裡，V1 結果不可靠

**實作 implications:**

- 不要把 V1 P@k 當作絕對品質指標
- 跨 (drug, target) 比較 V1 P@k 需要 control for evidence base size
- 弱 evidence 場景（新藥、冷門 excipient、稀有劑型）需要 human-curated
  supplement（e.g. `ampicillin_formulation.md` pattern）
- task_G 設計時應考慮把 evidence base size 當成 metadata 一起記錄,
  不只記 P@k

**Related:**
- `docs/spec/task_F.md` post-implementation note (Ampicillin baseline)
- `outputs/ground_truth/Acetaminophen_Cellulose_Microcrystalline_v1.json`
  (Acetaminophen baseline)
- `docs/ampicillin_ground_truth_notes.md`

---

## 5. Investigation Playbook

### Before any new task or bugfix

1. **Read the relevant task spec** (if exists)
2. **Read `architecture.md`** Gap Analysis and Known Limitations
3. **Probe assumptions before code** (5-min sanity check)
4. **Check this file (`PROJECT_SKILL.md`)** for prior anti-pattern hits

### Probe template

```python
# scratch/probe_<assumption>.py
# Use to verify spec assumptions before implementation.
# Run with: python -m scratch.probe_<assumption>
# Goal: confirm or refute one specific claim from the spec.
```

Probes go in `scratch/` (gitignored). They're disposable — keep them small (~50 lines), single-purpose, deletable.

### When the probe contradicts the spec

1. Don't implement
2. Update spec with retrospective note OR reject task
3. Open new task if the underlying need still exists
4. Record the lesson in `PROJECT_SKILL.md` if it's a new failure mode

### When stuck for >30 min

- Run `inspect_patent.py` to look at real data
- Run a SQL query directly on `cache/patents.db`
- Check `architecture.md` Known Limits before deeper debug
- Read commit messages of related past fixes (`git log --all --grep=<keyword>`)

---

## 6. Workflow Rules

### Ship now, optimize later

- Working code in `main` is worth more than perfect code in branch
- Polish incrementally via follow-up commits
- Don't block ship on cosmetic concerns (docstring phrasing, micro-naming)

### Spec-first for non-trivial work

- New feature (>1 day): write spec first, even if rough
- Bug fix (<1 hour): commit directly with thorough commit message
- Bug fix that touches multiple modules: short spec (~1 page)

### Specs are immutable; lessons go in retrospective notes

If spec is wrong after implementation reveals truth:

- Don't edit the spec body
- Add a retrospective note at the top (see `task_C.md` for example)
- Keep original spec text as record of "what was thought at design time"
- This is research value for understanding spec-vs-reality drift

### Probe-first for new tasks; don't trust spec assumptions

See section 3.1.

### Commit messages document root cause, not just symptoms

- **Bad:** `fix: snippets bug`
- **Good:** `fix(snippets): match comprising/comprised, not just comprises` + body explaining substring match semantics

### Daily commits, not week-long branches

Smaller commits = easier review = easier revert. Multi-day branches accumulate too many changes; future readers (including you) struggle to understand what each commit does.

---

## 7. Collaboration Patterns Observed

Recorded for personal LLM-collaboration research dataset.

| Pattern | Concrete instance | Outcome |
|---------|-------------------|---------|
| **Spec → straightforward implement** | Task A | LLM-written spec, implemented as written, regression tests added. Ship in <1 day. |
| **Spec error → user pushback → redirect** | Task C | LLM-written spec had two wrong diagnoses. User probe revealed truth. Real fix was 1 keyword change instead of spec's planned restructure. Spec kept with retrospective note. |
| **User + LLM co-write spec** | Task D | User noticed audit-trail gap; iterated with LLM to add `backfill_log` table to spec. Implementation pending. |
| **Spec assumption collapse → reject** | "Task E rejected portion" (inspect description fetch) | LLM spec assumed EPO has US fulltext. Probe disproved. Spec abandoned, replaced with 5-line Espacenet URL patch. No spec committed. |
| **Tool reveals systemic bug** | `inspect_patent` revealing Bug X | Tool built for evidence verification incidentally exposed family expansion filter bug that had been silent for months. Pipeline-level bug found via exploration, not via spec. |
| **Probe → spec-restructure (not reject)** | Task D | Pre-implementation probe (`scratch/probe_task_d.py`) showed Case 1 framing was wrong (4 IDs are `family_fetched=0` parents, not legacy members with NULL `family_of`), spec's Case 2 SQL had a precedence bug (71 spurious rows), and `family::*` diskcache invalidation was a no-op. Spec body preserved per §6; implementation collapsed Case 1 → Case 2 with a retrospective note explaining the corrections. Distinct from Task C ("spec error → user pushback → redirect", where the fix was a 1-keyword change) and the rejected Task E portion ("spec assumption collapse → reject", where the task was abandoned). Here the underlying need (backfill pre-May parents) was real and confirmed by probe (318 candidates DB-wide); only the spec's internal taxonomy was wrong. |
| **Operation surfaces apparent bug → probe resolves in same session** | Task D execution → Bug Z (resolved) | During Task D backfill execution, `--null-count` revealed that the Apremilast project (first run 2026-05-21, post-Task-A) had 2 NULL `formulation_snippets` rows where spec said none should exist. User flagged this as potential scope creep before running apply. Two-track response: (a) Task D backfill still processed those rows for DB consistency, (b) `bug_Z_silent_null_snippets.md` opened to investigate. Subsequent inspector enhancement (`--null-provenance`) showed all 277 remaining NULL rows pre-date Task A merge (newest 2026-05-12, oldest 2026-03-23). Bug Z reclassified as historical artifact, file renamed `bug_Z_resolved.md`, no fix needed. Lesson: spec evolution should wait on probe data; the right discipline is `fetched_at`-bucketed audit before grepping code. |
| **Investigation → tool creation → validation loop** | US9415051B1 scoring investigation → spec_debug_scoring.md → debug_scoring.py + check_db.py + test suite | Investigation revealed three-layer root cause (diskcache/screening/rubric). Rather than patching production code immediately, built diagnostic tools first (debug_scoring for judgment layer, check_db for data layer). Tools validated the root cause analysis (default rubric → Low, amended rubric → High) and provide reusable infrastructure for future scoring investigations. 58-check test suite committed alongside tools. Pattern: diagnose → build diagnostic tooling → validate diagnosis with tooling → then decide on production fix. |

**Meta-observation:** LLM collaboration quality varies by *what kind of task it is*. Spec-driven implementation works well for additive features. Diagnosis-heavy work (bugs, edge cases) benefits from human-driven probe + LLM verification, not LLM-driven hypothesis generation.

> Probe-driven correction also helps distinguish "spec wrong in detail
> but task still valid" (Task D) from "spec wrong because task itself
> doesn't make sense" (Task E rejected portion); both look the same
> until the probe runs. A third variant — "execution surfaces apparent
> bug, but probe resolves it as historical artifact" (Bug Z in same
> Task D session) — shows that the discipline of inspecting `fetched_at`
> distribution before grepping code can short-circuit a multi-day
> investigation into a 30-second SQL.

---

## 8. References

- `docs/architecture.md` — current system state
- `docs/spec/design_formulation_evidence.md` — formulation evidence subsystem rationale
- `docs/spec/task_A.md` ~ `task_E.md` — individual task specs
- Git log — implementation chronology