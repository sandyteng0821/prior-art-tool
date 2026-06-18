# Design: API Layer

> Strategic context for the REST API subsystem.
> Written once; locked at major architectural shifts.
> Individual tasks: `task_J0.md` through `task_J5.md`.
> 
> Created: 2026-06-18
> Origin: Gemini discussion (2026-06-18), refined by source code review.
> Gap Analysis row: #10 (Priority P3 → promoted to P2)

---

## Why this subsystem exists

The Prior Art Tool runs as CLI-only: `main.py` orchestrator, `tools/*.py`
for ad-hoc inspection, `scripts/*.py` for one-off operations. Bio team
integration (Gap #10) and multi-user access require a REST API layer that
wraps existing logic without modifying production modules.

---

## Gemini discussion corrections

An initial plan (Gemini, 2026-06-18) proposed 4 endpoints with 5 sub-tasks.
Source code review surfaced corrections:

| Gemini assumption | Actual state | Impact |
|---|---|---|
| `compare_runs.py` = A/B rubric test | It's a CSV diff tool. The A/B rubric test is `debug_scoring.py --compare`. | Compare endpoint wraps `debug_scoring` logic, not `compare_runs`. |
| `llm_analyzer.py` can be imported and wrapped | Module-level `from config import ...` + `ChatOpenAI()` init at import time → crash without API key or with wrong config. | API must use Approach D (self-defined schemas), same as `debug_scoring.py`. Do NOT import `llm_analyzer.py`. |
| Need `aiosqlite` for async DB | SQLite queries are sub-millisecond (local file, indexed). Async overhead adds complexity for negligible gain. | Use `run_in_executor` for SQLite calls, not full async rewrite. |
| EPO fallback needs `httpx.AsyncClient` rewrite | `patent_fetcher._fetch_*` functions use `epo_ops` library (sync, session-based). Rewriting to httpx breaks the client. | Wrap EPO sandbox calls in `run_in_executor`. |
| Config race condition requires per-request instances | `debug_scoring.py` already solved this: `importlib.util` loads config as isolated module. API reuses this pattern. | No new architecture needed — borrow `load_config()`. |

---

## Architecture decisions

**D1: Don't import `llm_analyzer.py`.**
Same rationale as `debug_scoring.py` Approach D (spec_debug_scoring.md
§Architecture). The API defines its own Pydantic schemas mirroring
`ScreeningResult` and `PatentAnalysis`. Schema drift detection (AST-based)
is tracked as future work (debug_scoring Step 6).

**D2: Config via request body, not global state.**
Borrow `debug_scoring.py`'s `load_config()` pattern. The `--config` CLI
arg becomes a `config_name` field in the request body. The API resolves
it to `configs/{config_name}.py` and loads it as an isolated module
instance. No global config mutation, no race condition.

**D3: Sync modules wrapped in `run_in_executor`.**
SQLite and EPO API calls are synchronous. Rather than rewriting to async
(which would require replacing `epo_ops` and `sqlite3`), wrap blocking
calls in `asyncio.loop.run_in_executor(None, fn)`. FastAPI's event loop
stays responsive while blocking I/O runs in a thread pool.

**D4: DB path from environment, not hardcoded.**
`patent_store.py` hardcodes `DB_PATH = "cache/patents.db"`. The API reads
`DATABASE_PATH` from `.env` (defaulting to `cache/patents.db`). The deps
layer opens a connection to that path. Production modules are not modified.

**D5: `compare_runs.py` is NOT an API endpoint.**
It's a CSV diff tool for comparing pipeline output files. The A/B rubric
comparison lives in `debug_scoring --compare` logic, which the
`/api/v1/analysis/compare` endpoint wraps.

---

## Directory structure

```
prior-art-tool/
├── modules/                    # UNCHANGED — production code
│   ├── patent_fetcher.py
│   ├── patent_store.py
│   ├── llm_analyzer.py         # NOT imported by API (module-level init crash)
│   ├── output_writer.py
│   └── query_builder.py
├── tools/                      # UNCHANGED — CLI tools remain functional
├── api/                        # NEW — FastAPI layer
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, router registration, lifespan
│   ├── deps.py                 # Dependency injection (DB conn, config loader)
│   ├── core/
│   │   └── llm_bridge.py       # Extracted LLM invocation logic (from debug_scoring)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── database.py         # Pydantic models for check_db / stats
│   │   ├── inspect.py          # Pydantic models for inspect
│   │   └── analysis.py         # Pydantic models for score / compare
│   └── routers/
│       ├── __init__.py
│       ├── database.py         # /api/v1/db/* endpoints
│       ├── inspect.py          # /api/v1/patents/* endpoints
│       └── analysis.py         # /api/v1/analysis/* endpoints
├── run_api.py                  # Uvicorn entry point
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── configs/                    # UNCHANGED — per-project config snapshots
```

---

## Endpoint overview

| # | Method | Path | Wraps | Cost | Task |
|---|--------|------|-------|------|------|
| 0 | GET | `/` | health check | Zero | J-0 |
| 1 | GET | `/api/v1/db/patents/{patent_id}` | `check_db.py` logic | Zero (DB read) | J-1 |
| 2 | GET | `/api/v1/db/stats` | `patent_store.stats()` | Zero (DB read) | J-1 |
| 3 | POST | `/api/v1/patents/inspect` | `inspect_patent.py` logic | Zero / 1 EPO call | J-2 |
| 4 | POST | `/api/v1/analysis/score` | `debug_scoring.py` Stage 1+2 | 1-2 LLM calls | J-3 |
| 5 | POST | `/api/v1/analysis/compare` | `debug_scoring.py --compare` | 2-3 LLM calls | J-4 |

---

## Task dependency graph

```
J-0  scaffolding        (no deps)
 ↓
J-1  database endpoints  (deps: J-0)
J-2  inspect endpoint    (deps: J-0)
 ↓         ↓
J-3  score endpoint      (deps: J-0, J-1 for DB read pattern)
 ↓
J-4  compare endpoint    (deps: J-3)
 ↓
J-5  docker + tests      (deps: J-1..J-4)
```

J-1 and J-2 can run in parallel. Total estimated effort: 4–5 days.

---

## Scope boundaries (what the API does NOT cover)

- **Batch analysis** — `main.py` orchestrator handles batch runs.
- **Write operations** — per PROJECT_SKILL §3.3, write ops stay as CLI.
- **Authentication** — internal tool, trusted network.
- **WebSocket / SSE streaming** — standard HTTP with timeout (60s screening, 120s analysis).
- **Multi-drug pipeline** (Gap #4) — config-level change, not API change.

---

## Risk assessment

| Risk | Mitigation |
|---|---|
| `llm_analyzer.py` schema drift | Same risk as `debug_scoring.py`. Tracked as future AST-based detection. |
| EPO sandbox timeout blocking worker | `run_in_executor` isolates to thread pool. Set `--workers 4` for production. |
| SQLite concurrent writes | API is read-only. No write contention. `check_same_thread=False`. |
| Config file not found | Validate at request time, return 400 with available config names. |
| LLM timeout / rate limit | `invoke_with_retry` with exponential backoff. LLM errors → 502. |

---

## References

- `docs/spec/spec_debug_scoring.md` — Approach D rationale
- `docs/architecture.md` — Gap Analysis row #10
- `docs/PROJECT_SKILL.md` §3.3 — risk profile separation
