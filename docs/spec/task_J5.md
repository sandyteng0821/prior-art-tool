# Task J-5 — Docker Production + Integration Tests

> Status: Spec
> Depends on: J-1 through J-4
> Design context: `design_api_layer.md`

---

## Goal

Production-ready Docker image, deployment docs, integration test suite.

---

## Deliverables

### 1. Final Dockerfile

- Base: `python:3.11-slim`
- All dependencies: `fastapi`, `uvicorn`, `pydantic`, `langchain-openai`,
  `epo_ops`, `diskcache`, `python-dotenv`
- Working directory: `/app`
- Entry: `python run_api.py`

### 2. docker-compose.yml

- `cache/` volume mount (DB persistence across restarts)
- `configs/` bind mount (config files accessible inside container)
- `.env` injection: `API_HOST`, `API_PORT`, `DATABASE_PATH`, `OPENAI_API_KEY`
- Port mapping per `.env`

### 3. Integration tests

`tests/test_api.py` using `httpx` / FastAPI `TestClient`.
Mirrors `test_debug_tools.py` pattern: dry-run checks are free, live
LLM checks gated behind `--live` flag.

Test cases:
- Health check (`GET /`)
- DB lookup: known patent ID → 200
- DB lookup: nonexistent ID → 404
- DB stats → 200 with expected structure
- Inspect: DB hit → 200, check snippet count > 0
- Inspect: nonexistent patent → 200 with `data_source: "epo_sandbox"`
- Score: dry-run → 200, check `screening_input` / `analysis_input` present
- Score: bad config name → 400
- Score: nonexistent patent → 404
- [--live] Score: live run → 200, check `fto_risk` in {High, Medium, Low}
- [--live] Compare: live run → 200, check `has_differences` is bool

Run: `python -m pytest tests/test_api.py` (dry-run only)
Run: `python -m pytest tests/test_api.py --live` (includes LLM calls)

### 4. Deployment guide

`docs/api_deployment.md` — mirrors existing DOCKER.md style from the
Gemini discussion. Covers:
- Quick start (cp .env, docker compose up)
- Port mapping explanation
- Common issues table
- Useful commands

---

## Verification

```bash
# Full cycle
cp .env.example .env
# edit .env: set OPENAI_API_KEY
docker compose build
docker compose up -d
docker compose logs api  # should show Uvicorn running

# Smoke test
curl localhost:8007/
curl localhost:8007/api/v1/db/stats

# Integration tests (inside or outside container)
python -m pytest tests/test_api.py -v
python -m pytest tests/test_api.py -v --live  # costs LLM tokens
```

---

## Non-goals

- No CI/CD pipeline (GitHub Actions etc.) — add if needed later
- No multi-stage Docker build optimization — not worth the complexity yet
- No Nginx / reverse proxy setup — direct Uvicorn for internal use

---

## Estimated effort

1 day
