# API Deployment Guide

> Prior Art Tool — REST API layer
> Last updated: 2026-06-25

---

## Quick Start

```bash
# 1. Copy env template and fill in your keys
cp .env.example .env
# Edit .env: set OPENAI_API_KEY (required for /analysis/* endpoints)

# 2. Build and start
docker compose build
docker compose up -d

# 3. Verify
curl localhost:8007/
# → {"status":"running","db_path":"cache/patents.db","patents_count":3954}
```

---

## Environment Variables

| Variable | Default | Required | Notes |
|----------|---------|----------|-------|
| `API_HOST` | `0.0.0.0` | No | Bind address |
| `API_PORT` | `8007` | No | Bind port |
| `DATABASE_PATH` | `cache/patents.db` | No | Path to SQLite DB inside container |
| `OPENAI_API_KEY` | — | For LLM endpoints | Required by `/analysis/score` and `/analysis/compare` |
| `EPO_CONSUMER_KEY` | — | For EPO fallback | Required by `/patents/inspect` EPO sandbox fallback |
| `EPO_CONSUMER_SECRET` | — | For EPO fallback | Same as above |

---

## Endpoints

| Method | Path | Cost | Description |
|--------|------|------|-------------|
| GET | `/` | Zero | Health check |
| GET | `/api/v1/db/patents/{id}` | Zero | Patent DB lookup |
| GET | `/api/v1/db/stats` | Zero | DB-wide statistics |
| POST | `/api/v1/patents/inspect` | Zero / 1 EPO call | Patent inspection + snippet extraction |
| POST | `/api/v1/analysis/score` | 1-2 LLM calls | Single-patent LLM scoring (dry_run=true for zero cost) |
| POST | `/api/v1/analysis/compare` | 2 LLM calls | A/B rubric comparison |

Swagger UI available at `http://localhost:8007/docs`.

---

## Volume Mounts

```yaml
volumes:
  - ./cache:/app/cache:ro       # SQLite DB (read-only)
  - ./configs:/app/configs:ro   # Config snapshots (read-only)
```

Both are read-only (`:ro`) — the API never writes to DB or configs.

---

## Running Without Docker

```bash
# Activate your venv
source venv/bin/activate

# Start directly
python run_api.py

# Or with custom port
API_PORT=9000 python run_api.py
```

---

## Smoke Test

```bash
# 46 checks, zero cost, runs against live server
python tests/test_api_smoke.py

# Against a different URL
python tests/test_api_smoke.py --base-url http://localhost:9000
```

---

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `patents_count: null` | DB file not found at `DATABASE_PATH` | Check volume mount, verify `cache/patents.db` exists on host |
| 400 "Config not found" | `configs/` not mounted or config file missing | Verify `configs/` bind mount in docker-compose.yml |
| 502 "LLM call failed" | `OPENAI_API_KEY` not set or invalid | Check `.env` file |
| 500 on inspect DB miss | EPO keys not set | Set `EPO_CONSUMER_KEY` and `EPO_CONSUMER_SECRET` in `.env` |
| Connection refused | Server not running or wrong port | Check `docker compose logs api` or `API_PORT` |

---

## Useful Commands

```bash
# View logs
docker compose logs -f api

# Restart after code change
docker compose down && docker compose build && docker compose up -d

# Stop
docker compose down

# Check container status
docker compose ps
```
