# Task J-1 — Database Endpoints (check_db + stats)

> Status: Spec
> Depends on: J-0
> Design context: `design_api_layer.md`

---

## Goal

Read-only DB lookup and stats. Zero cost, high confidence starting point.

---

## Endpoints

### GET `/api/v1/db/patents/{patent_id}`

Wraps: `check_db._lookup()` + `_lookup_family()`

Query params:
- `detail` (bool, default false) — include all metadata fields
- `family` (bool, default false) — include family members

Response (200):
```json
{
  "patent_id": "US9415051B1",
  "found": true,
  "title": "Use of pemirolast...",
  "abstract_chars": 313,
  "claims_chars": 1229,
  "examples_chars": 38732,
  "has_snippets": true,
  "source": "epo",
  "detail": {
    "status": "Unknown",
    "year": "2012",
    "fetched_at": "2026-06-15T...",
    "family_fetched": 1,
    "family_of": null
  },
  "family_members": ["EP...", "WO..."]
}
```

`detail` and `family_members` fields only present when query params are true.

Response (404):
```json
{"patent_id": "XX0000000X0", "found": false}
```

### GET `/api/v1/db/stats`

Wraps: `patent_store.stats()`

Response (200):
```json
{
  "total_patents": 3954,
  "with_examples": 1200,
  "without_examples": 2754,
  "family_fetched": 280,
  "family_members_in_db": 1500,
  "by_source": {"epo": 3200, "google_patents": 168}
}
```

---

## Files to create

- `api/schemas/database.py` — `PatentStatusResponse`, `PatentStatsResponse`
- `api/routers/database.py` — two GET endpoints
- `api/deps.py` — `get_db_conn()` dependency (sqlite3, `check_same_thread=False`)

---

## Implementation notes

- Port `check_db._lookup()` and `_lookup_family()` logic directly — they
  use raw sqlite3, not `patent_store.py`
- Stats endpoint: safe to import `patent_store.stats()` (no module-level
  side effects, unlike `llm_analyzer.py`)
- Wrap sync DB calls in `run_in_executor` for safety, though sub-millisecond
- DB path from `DATABASE_PATH` env var (set in J-0's `.env`)

---

## Verification

```bash
curl localhost:8007/api/v1/db/patents/US9415051B1
curl localhost:8007/api/v1/db/patents/US9415051B1?detail=true\&family=true
curl localhost:8007/api/v1/db/patents/NONEXISTENT123
# → 404 {"patent_id":"NONEXISTENT123","found":false}
curl localhost:8007/api/v1/db/stats
```

---

## Non-goals

- No write endpoints (upsert, backfill)
- No batch lookup (POST with multiple IDs) — add later if needed

---

## Estimated effort

0.5 day
