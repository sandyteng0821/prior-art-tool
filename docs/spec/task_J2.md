# Task J-2 — Inspect Endpoint

> Status: Spec
> Depends on: J-0
> Design context: `design_api_layer.md`

---

## Goal

Patent inspection with snippet extraction, including EPO sandbox fallback.
Wraps `inspect_patent.py` logic.

---

## Endpoint

### POST `/api/v1/patents/inspect`

Request body:
```json
{
  "patent_id": "EP4138798A1",
  "drug_aliases": ["Pemirolast", "BMY-26517"],
  "keywords": ["compris", "formulation", "excipient"],
  "source_filter": "all",
  "force_refetch": false
}
```

Fields:
- `patent_id` (required)
- `drug_aliases` (required, list[str]) — no default; caller must specify
- `keywords` (optional, list[str]) — defaults to `DEFAULT_KEYWORDS`
- `source_filter` (optional, enum: all / claims / examples / abstract) — default "all"
- `force_refetch` (optional, bool) — skip DB + diskcache, fetch fresh from EPO

Response (200, DB hit):
```json
{
  "patent_id": "EP4138798A1",
  "data_source": "db",
  "title": "...",
  "data_completeness": {
    "abstract_chars": 450,
    "claims_chars": 3200,
    "examples_chars": 12000
  },
  "alias_counts": {
    "Pemirolast": {"claims": 5, "examples": 12, "abstract": 2},
    "BMY-26517": {"claims": 0, "examples": 3, "abstract": 0}
  },
  "snippets": {
    "claims": ["sentence 1...", "sentence 2..."],
    "examples": ["sentence 3..."],
    "abstract": []
  },
  "total_snippet_count": 3
}
```

Response (200, EPO sandbox no content):
```json
{
  "patent_id": "CN1234567B",
  "data_source": "epo_sandbox",
  "title": null,
  "data_completeness": {"abstract_chars": 0, "claims_chars": 0, "examples_chars": 0},
  "alias_counts": {},
  "snippets": {"claims": [], "examples": [], "abstract": []},
  "total_snippet_count": 0,
  "fallback_urls": {
    "espacenet": "https://worldwide.espacenet.com/patent/search?q=pn%3DCN1234567B",
    "google_patents": "https://patents.google.com/patent/CN1234567B/en"
  }
}
```

---

## Files to create

- `api/schemas/inspect.py` — `InspectRequest`, `InspectResponse`
- `api/routers/inspect.py` — POST endpoint

---

## Implementation notes

- Core logic from `inspect_patent.py`: `get_patent_with_fallback()`,
  keyword counting, snippet extraction
- `_extract_formulation_snippets` imported from `patent_fetcher.py` — safe
  (pure function, no side effects)
- Custom keywords: if user provides `keywords`, replicate the inline
  extraction logic from `inspect_patent.py` lines 233-244 (same as CLI)
- EPO sandbox fallback (`_fetch_title`, `_fetch_abstract`, etc.) is sync.
  Wrap in `run_in_executor`. Blocks one thread — acceptable for ad-hoc use.
- `force_refetch`: clears diskcache entry via `_clear_epo_cache()`, then
  fetches fresh from EPO

**Critical: Do NOT persist sandbox results to DB.** Same read-only
invariant as the CLI tool.

---

## Verification

```bash
# DB hit (fast)
curl -X POST localhost:8007/api/v1/patents/inspect \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"EP2089013B1","drug_aliases":["acetaminophen"]}'

# Custom keywords
curl -X POST localhost:8007/api/v1/patents/inspect \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"EP2089013B1","drug_aliases":["acetaminophen"],"keywords":["tablet","capsule"]}'

# DB miss → EPO sandbox
curl -X POST localhost:8007/api/v1/patents/inspect \
  -H "Content-Type: application/json" \
  -d '{"patent_id":"EP9999999A1","drug_aliases":["test"]}'
```

---

## Non-goals

- No batch inspect (multiple patents in one call)
- No raw dump mode (`--raw` stays CLI-only; API returns structured JSON)

---

## Estimated effort

1 day
