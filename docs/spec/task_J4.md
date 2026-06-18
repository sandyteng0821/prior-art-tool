# Task J-4 — Compare Endpoint (A/B Rubric Test)

> Status: Spec
> Depends on: J-3 (llm_bridge.py, analysis schemas)
> Design context: `design_api_layer.md` D5

---

## Goal

Side-by-side rubric comparison. Wraps `debug_scoring.py --compare` logic.
Built on J-3's `llm_bridge.py`.

---

## Endpoint

### POST `/api/v1/analysis/compare`

Request body:
```json
{
  "patent_id": "US9415051B1",
  "config_name": "pemirolast_ipf_v3",
  "override_rubric_text": "你是資深生技醫藥專利律師...",
  "analysis_model": null
}
```

Fields:
- `patent_id` (required)
- `config_name` (required)
- `override_rubric_text` (required) — raw text for the B-side rubric.
  Supports `{TARGET_DRUG}`, `{TARGET_ROUTE}`, `{TARGET_INDICATION}` placeholders.
- `analysis_model` (optional) — override config's model for both runs

Response (200):
```json
{
  "patent_id": "US9415051B1",
  "config_name": "pemirolast_ipf_v3",
  "baseline": {
    "rubric": "default",
    "fto_risk": "Low",
    "gap_opportunity": "...",
    "reasoning": "...",
    "is_target_drug": true,
    "delivery_routes": "Oral",
    "indications": "Asthma, COPD, IPF",
    "claim_scope": "..."
  },
  "override": {
    "rubric": "custom",
    "fto_risk": "High",
    "gap_opportunity": "...",
    "reasoning": "...",
    "is_target_drug": true,
    "delivery_routes": "Oral, Inhalation",
    "indications": "IPF, Pulmonary fibrosis",
    "claim_scope": "..."
  },
  "diff": {
    "fto_risk": {"match": false, "baseline": "Low", "override": "High"},
    "gap_opportunity": {"match": true},
    "reasoning": {"match": false},
    "delivery_routes": {"match": false},
    "indications": {"match": false},
    "claim_scope": {"match": true}
  },
  "has_differences": true
}
```

---

## Files to modify / create

- Add `CompareRequest`, `CompareResponse` to `api/schemas/analysis.py`
  (file created in J-3)
- Add compare route to `api/routers/analysis.py` (file created in J-3)

---

## Implementation notes

- Runs Stage 2 twice: default rubric vs override rubric
- Both calls use same patent data + config — only system prompt differs
- Use `asyncio.gather()` to run both LLM calls concurrently (independent).
  Each wrapped in `run_in_executor` since LangChain is synchronous.
- Diff logic: for each field in `ANALYSIS_FIELDS`, compare string values,
  flag mismatches. Mirrors `debug_scoring._print_compare()`.
- `ANALYSIS_FIELDS = ["is_target_drug", "delivery_routes", "indications",
  "claim_scope", "fto_risk", "gap_opportunity", "reasoning"]`

---

## Verification

```bash
curl -X POST localhost:8007/api/v1/analysis/compare \
  -H "Content-Type: application/json" \
  -d '{
    "patent_id": "US9415051B1",
    "config_name": "pemirolast_ipf_v3",
    "override_rubric_text": "你是資深生技醫藥專利律師..."
  }'
# → has_differences: true, fto_risk baseline=Low override=High
```

Cross-check: result should match `debug_scoring --compare` CLI output
for the same patent + config + rubric.

---

## Non-goals

- No dry-run mode for compare (use score endpoint with dry_run=true to
  preview inputs first)
- No more than 2 rubrics per request (A vs B only)

---

## Estimated effort

0.5 day (builds entirely on J-3)
