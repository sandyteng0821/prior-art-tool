# Ampicillin — Ground Truth Source Notes

> Cross-reference between auto-generated and human-curated ground truth
> for Ampicillin formulation evaluation.
> Created: 2026-05-22 (after Task F ship)

## Two ground truth sources

| Source | Type | Coverage | Strength | Reproducible |
|---|---|---|---|---|
| `docs/ampicillin_formulation.md` Evidence Table | Human-curated | 11 patents | Strong / Moderate / Weak / irrelevant | ❌ |
| `outputs/ground_truth/Ampicillin_*_v1.json` | Auto (V1 pipeline) | 187 patents | Binary (substring match) | ✅ |

These are complementary, not competing. The MD is the gold standard for
known cases; the JSON is a mechanically reproducible baseline.

## Findings from V1 ship (2026-05-22)

V1 ran against the 187-patent CSV
(`output/gap_analysis_20260508_1645.xlsx`) and produced P@5=0.40,
P@10=0.20 — numerically identical to V0 but hitting different keywords.
Three causes documented in `task_F.md` post-implementation note. Most
relevant for this document:

- V0's `US2013029965A1` (the patent that contributed V0's
  polymethacrylate evidence) was **manually added** from
  `ampicillin_formulation.md`, not from auto search
- V0's 6-patent set = 3 auto-found + 3 human-curated
- V1's 187-patent CSV is purely auto-derived; `US2013029965A1` is
  **NOT** in it

This means the MD contains evidence that auto pipelines cannot
discover. The MD is not redundant with V1 output; it captures something
V1 cannot reach.

## Source-of-truth decisions

- For **pipeline mechanics validation** → use V1 JSON
- For **correctness audits** ("did the recommender suggest something
  patent-backed?") → use MD Evidence Table as gold
- For **new drugs** (no MD exists yet) → V1 JSON is the only available
  baseline; accept the abstract-only / curation-gap caveats from
  `task_F.md` post-impl note

## Open items (likely Task G)

- Should MD Evidence Table be converted to machine-readable form (CSV)
  to enable automated P@k against human gold?
- Should V1 weight evidence by strength? MD distinguishes Strong vs Weak
  vs irrelevant; V1 currently does not.
- For drugs without MD, what is the bootstrap path? Is V1 weak label
  acceptable as standalone ground truth, or does every project need a
  human-curated baseline first?

## References

- `docs/ampicillin_formulation.md` — 2026-05-11 human-curated evidence
- `docs/spec/task_F.md` — V1 spec + post-implementation note
- `docs/architecture.md` — Validation Log entry for V1 ship
- Commit `0148454` — V1 code ship
- Commit `79cbdc5` — V1 docs ship