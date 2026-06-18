# Task J-3 — Score Endpoint (Single-Patent LLM Analysis)

> Status: Spec
> Depends on: J-0 (scaffolding), J-1 (DB read pattern from deps.py)
> Design context: `design_api_layer.md` (especially D1, D2)

---

## Goal

Reproduce `debug_scoring.py` as REST endpoint. Most complex task —
LLM integration with config isolation.

---

## Endpoint

### POST `/api/v1/analysis/score`

Request body:
```json
{
  "patent_id": "US9415051B1",
  "config_name": "pemirolast_ipf_v3",
  "stage": "both",
  "dry_run": false,
  "rubric_override": null,
  "screening_model": null,
  "analysis_model": null
}
```

Fields:
- `patent_id` (required)
- `config_name` (required) — resolves to `configs/{config_name}.py`
- `stage` (optional, enum: 1 / 2 / both) — default "both"
- `dry_run` (optional, bool) — return LLM input without calling API
- `rubric_override` (optional, str) — raw text replacing ANALYSIS_SYSTEM.
  Supports `{TARGET_DRUG}`, `{TARGET_ROUTE}`, `{TARGET_INDICATION}` placeholders.
- `screening_model` / `analysis_model` (optional) — override config

Response (200, live run):
```json
{
  "patent_id": "US9415051B1",
  "config_name": "pemirolast_ipf_v3",
  "dry_run": false,
  "db_state": {
    "title": "Use of pemirolast...",
    "abstract_chars": 313,
    "claims_chars": 1229,
    "source": "epo"
  },
  "screening": {
    "model": "gpt-5-mini",
    "is_relevant": true,
    "quick_risk": "Medium"
  },
  "analysis": {
    "model": "gpt-5",
    "rubric": "default",
    "is_target_drug": true,
    "delivery_routes": "Oral",
    "indications": "Asthma, COPD, IPF",
    "claim_scope": "Treatment of AHR using oral pemirolast ≥350mg/day",
    "fto_risk": "Low",
    "gap_opportunity": "Inhalation route not claimed",
    "reasoning": "Claim 1 is oral-only; target is inhaled IPF."
  }
}
```

Response (200, dry run):
```json
{
  "patent_id": "US9415051B1",
  "config_name": "pemirolast_ipf_v3",
  "dry_run": true,
  "db_state": {"..."},
  "screening_input": {
    "system_prompt_chars": 450,
    "title": "Use of pemirolast...",
    "abstract_preview": "first 300 chars..."
  },
  "analysis_input": {
    "system_prompt_chars": 800,
    "claims_preview": "first 800 chars...",
    "claims_total_chars": 1229
  }
}
```

Error responses:
- 400: config not found → `{"detail": "Config 'xxx' not found. Available: [...]"}`
- 404: patent not in DB → `{"detail": "Patent US0000000X0 not in DB"}`
- 502: LLM error → `{"detail": "LLM call failed: <error message>"}`

---

## Files to create

- `api/core/__init__.py`
- `api/core/llm_bridge.py` — extracted LLM invocation logic
- `api/schemas/analysis.py` — `ScoreRequest`, `ScoreResponse` (+ Compare schemas for J-4)
- `api/routers/analysis.py` — score endpoint

---

## Key: `api/core/llm_bridge.py`

Extracts reusable functions from `debug_scoring.py`:

```
load_config(config_name)           → isolated module instance
build_screening_schema(cfg)        → dynamic Pydantic ScreeningResult
build_analysis_schema(cfg)         → dynamic Pydantic PatentAnalysis
screening_system_prompt(cfg)       → formatted system prompt string
analysis_system_prompt(cfg)        → formatted system prompt string
make_chain(model, role, prompt, schema) → LangChain structured-output chain
invoke_with_retry(chain, payload, retries, base_s) → Pydantic result
```

**Why a new file instead of importing `debug_scoring.py`?**
`debug_scoring.py` is a CLI tool: `argparse`, `print()`, `sys.exit()`.
Extracting the reusable logic into `llm_bridge.py` avoids pulling in CLI
concerns. Functions are copied because `tools/` and `api/` have different
conventions (see PROJECT_SKILL §2).

**Why not import `llm_analyzer.py`?**
Module-level `from config import ...` + `ChatOpenAI()` init at import time.
See `design_api_layer.md` D1 and `spec_debug_scoring.md` §Architecture.

---

## Config resolution

1. Request sends `config_name: "pemirolast_ipf_v3"`
2. API resolves to `configs/pemirolast_ipf_v3.py`
3. Validates file exists → 400 if not, with list of available configs
4. Loads via `importlib.util` → isolated module instance per request
5. Required attributes: `TARGET_DRUG`, `TARGET_ROUTE`, `TARGET_INDICATION`,
   `TARGET_PRODUCT`, `SCREENING_MODEL`, `ANALYSIS_MODEL`, `CLAIMS_MAX_CHARS`,
   `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS`, `SCREENING_IRRELEVANT_EXAMPLES`

---

## Verification

```bash
# Dry-run (zero cost — test first)
curl -X POST localhost:8007/api/v1/analysis/score \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"US9415051B1","config_name":"pemirolast_ipf_v3","dry_run":true}'

# Live (costs LLM tokens)
curl -X POST localhost:8007/api/v1/analysis/score \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"US9415051B1","config_name":"pemirolast_ipf_v3"}'

# Stage 2 only + rubric override
curl -X POST localhost:8007/api/v1/analysis/score \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"US9415051B1","config_name":"pemirolast_ipf_v3","stage":"2","rubric_override":"你是..."}'

# Bad config name → 400
curl -X POST localhost:8007/api/v1/analysis/score \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"US9415051B1","config_name":"nonexistent"}'
```

---

## Non-goals

- No batch scoring (multiple patents)
- No streaming (SSE/WebSocket)
- No writing results to DB or CSV

---

## Estimated effort

1.5 days
