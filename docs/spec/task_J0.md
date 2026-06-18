# Task J-0 — API Scaffolding + Health Check

> Status: Spec
> Depends on: nothing
> Design context: `design_api_layer.md`

---

## Goal

Bootable FastAPI app with Docker, returning health check.
**No business logic.** Pure infrastructure.

---

## Deliverables

- `api/__init__.py`
- `api/main.py` — FastAPI instance, lifespan, router registration stub
- `run_api.py` — Uvicorn entry point, reads `API_HOST` / `API_PORT` from env
- `.env.example` — `API_HOST=0.0.0.0`, `API_PORT=8007`, `DATABASE_PATH=cache/patents.db`
- `Dockerfile` (python:3.11-slim)
- `docker-compose.yml` — `cache/` volume mount, `.env` injection

---

## Endpoint

`GET /` → `{"status": "running", "db_path": "cache/patents.db", "patents_count": 3954}`

`patents_count` reads from `patent_store.stats()["total_patents"]`.
If DB file doesn't exist, return `"patents_count": null`.

---

## Verification

```bash
# Local
pip install fastapi uvicorn
python run_api.py
curl localhost:8007/

# Docker
cp .env.example .env
docker compose build
docker compose up -d
curl localhost:8007/
# → {"status":"running","db_path":"cache/patents.db","patents_count":3954}
```

---

## Non-goals

- No routers, no schemas, no business logic endpoints
- No authentication
- No production Dockerfile optimization (multi-stage build is J-5)

---

## Estimated effort

0.5 day
