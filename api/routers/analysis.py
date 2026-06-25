"""
api/routers/analysis.py — Analysis endpoints.

J-3: POST /api/v1/analysis/score — single-patent LLM scoring
J-4: POST /api/v1/analysis/compare — A/B rubric comparison (future)

LLM logic ported from tools/debug_scoring.py via api/core/llm_bridge.py.
Does NOT import modules/llm_analyzer.py (D1: module-level side effects).

Refs: task_J3.md, design_api_layer.md
"""

import asyncio
import logging
import sqlite3
from functools import partial

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db_conn
from api.core.llm_bridge import (
    load_config,
    list_configs,
    build_screening_schema,
    build_analysis_schema,
    screening_system_prompt,
    analysis_system_prompt,
    interpolate_rubric,
    make_chain,
    invoke_with_retry,
    preprocess_claims,
)
from api.schemas.analysis import (
    ScoreRequest,
    ScoreResponse,
    DbState,
    ScreeningOutput,
    AnalysisOutput,
    ScreeningDryRunInput,
    AnalysisDryRunInput,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analysis"])


# ═══════════════════════════════════════════════════════════════════════════════
# DB helpers (inline, same pattern as J-1/J-2 — no modules/ import)
# ═══════════════════════════════════════════════════════════════════════════════

def _lookup(conn: sqlite3.Connection, patent_id: str) -> dict | None:
    """Look up a patent by ID. Returns dict or None."""
    row = conn.execute(
        "SELECT * FROM patents WHERE patent_id = ?", (patent_id,)
    ).fetchone()
    return dict(row) if row else None


def _char_count(val) -> int:
    """Safe char count for nullable text fields."""
    if val is None:
        return 0
    return len(str(val).strip())


# ═══════════════════════════════════════════════════════════════════════════════
# Score endpoint
# ═══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/score",
    response_model=ScoreResponse,
)
async def score_patent(
    req: ScoreRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    """
    Single-patent LLM scoring endpoint.

    Reproduces debug_scoring.py as REST:
    - dry_run=true → zero cost, returns LLM input preview
    - dry_run=false → calls LLM, returns screening + analysis results

    Config resolved from configs/{config_name}.py via importlib.util.
    """
    # ── Config resolution ────────────────────────────────────────────────
    try:
        cfg = load_config(req.config_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Apply model overrides
    if req.screening_model:
        cfg.SCREENING_MODEL = req.screening_model
    if req.analysis_model:
        cfg.ANALYSIS_MODEL = req.analysis_model

    # ── Patent lookup ────────────────────────────────────────────────────
    loop = asyncio.get_event_loop()
    patent = await loop.run_in_executor(
        None, partial(_lookup, conn, req.patent_id)
    )
    if not patent:
        raise HTTPException(
            status_code=404,
            detail=f"Patent {req.patent_id} not in DB",
        )

    # ── DB state (always included) ──────────────────────────────────────
    db_state = DbState(
        title=patent.get("title"),
        abstract_chars=_char_count(patent.get("abstract")),
        claims_chars=_char_count(patent.get("claims")),
        source=patent.get("source"),
    )

    # ── Claims preprocessing ─────────────────────────────────────────────
    claims_input = preprocess_claims(patent, cfg.CLAIMS_MAX_CHARS)

    # ── Rubric override interpolation ────────────────────────────────────
    rubric_override_text = None
    if req.rubric_override:
        rubric_override_text = interpolate_rubric(req.rubric_override, cfg)

    # ── Stage flags ──────────────────────────────────────────────────────
    run_stage_1 = req.stage in ("1", "both")
    run_stage_2 = req.stage in ("2", "both")

    # ── DRY RUN ──────────────────────────────────────────────────────────
    if req.dry_run:
        return _build_dry_run_response(
            req, cfg, db_state, patent, claims_input,
            rubric_override_text, run_stage_1, run_stage_2,
        )

    # ── LIVE RUN ─────────────────────────────────────────────────────────
    return await _run_live(
        req, cfg, db_state, patent, claims_input,
        rubric_override_text, run_stage_1, run_stage_2,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Dry-run builder
# ═══════════════════════════════════════════════════════════════════════════════

def _build_dry_run_response(
    req, cfg, db_state, patent, claims_input,
    rubric_override_text, run_stage_1, run_stage_2,
) -> ScoreResponse:
    """Build dry-run response: LLM input preview, zero API calls."""
    response = ScoreResponse(
        patent_id=req.patent_id,
        config_name=req.config_name,
        dry_run=True,
        db_state=db_state,
    )

    if run_stage_1:
        sys_prompt = screening_system_prompt(cfg)
        response.screening_input = ScreeningDryRunInput(
            system_prompt_chars=len(sys_prompt),
            title=patent.get("title", ""),
            abstract_preview=(patent.get("abstract") or "")[:300],
        )

    if run_stage_2:
        if rubric_override_text:
            sys_prompt = rubric_override_text
        else:
            sys_prompt = analysis_system_prompt(cfg)
        response.analysis_input = AnalysisDryRunInput(
            system_prompt_chars=len(sys_prompt),
            claims_preview=claims_input[:800],
            claims_total_chars=len(claims_input),
        )

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Live LLM run
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_live(
    req, cfg, db_state, patent, claims_input,
    rubric_override_text, run_stage_1, run_stage_2,
) -> ScoreResponse:
    """Execute LLM calls and return structured results."""
    loop = asyncio.get_event_loop()

    response = ScoreResponse(
        patent_id=req.patent_id,
        config_name=req.config_name,
        dry_run=False,
        db_state=db_state,
    )

    # ── Stage 1: Screening ───────────────────────────────────────────────
    if run_stage_1:
        try:
            screening_result = await loop.run_in_executor(
                None,
                partial(
                    _run_screening_sync,
                    cfg, patent,
                ),
            )
            response.screening = ScreeningOutput(
                model=cfg.SCREENING_MODEL,
                is_relevant=screening_result.is_relevant,
                quick_risk=screening_result.quick_risk,
            )
        except Exception as e:
            logger.error("Stage 1 LLM error: %s", e)
            raise HTTPException(
                status_code=502,
                detail=f"LLM call failed: {e}",
            )

    # ── Stage 2: Analysis ────────────────────────────────────────────────
    if run_stage_2:
        sys_prompt = (
            rubric_override_text
            if rubric_override_text
            else analysis_system_prompt(cfg)
        )
        rubric_label = "override" if rubric_override_text else "default"

        try:
            analysis_result = await loop.run_in_executor(
                None,
                partial(
                    _run_analysis_sync,
                    cfg, patent, claims_input, sys_prompt,
                ),
            )
            response.analysis = AnalysisOutput(
                model=cfg.ANALYSIS_MODEL,
                rubric=rubric_label,
                is_target_drug=analysis_result.is_target_drug,
                delivery_routes=analysis_result.delivery_routes,
                indications=analysis_result.indications,
                claim_scope=analysis_result.claim_scope,
                fto_risk=analysis_result.fto_risk,
                gap_opportunity=analysis_result.gap_opportunity,
                reasoning=analysis_result.reasoning,
            )
        except Exception as e:
            logger.error("Stage 2 LLM error: %s", e)
            raise HTTPException(
                status_code=502,
                detail=f"LLM call failed: {e}",
            )

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Sync LLM runners (called inside run_in_executor — D3)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_screening_sync(cfg, patent: dict):
    """Run Stage 1 screening (synchronous, for run_in_executor)."""
    ScreeningResult = build_screening_schema(cfg)
    chain = make_chain(
        cfg.SCREENING_MODEL,
        "screening",
        screening_system_prompt(cfg),
        ScreeningResult,
    )
    return invoke_with_retry(
        chain,
        {
            "title": patent.get("title", ""),
            "abstract": patent.get("abstract", ""),
        },
        cfg.LLM_MAX_RETRIES,
        cfg.LLM_RETRY_BASE_SECONDS,
    )


def _run_analysis_sync(cfg, patent: dict, claims_input: str, system_prompt: str):
    """Run Stage 2 analysis (synchronous, for run_in_executor)."""
    PatentAnalysis = build_analysis_schema(cfg)
    chain = make_chain(
        cfg.ANALYSIS_MODEL,
        "analysis",
        system_prompt,
        PatentAnalysis,
    )
    return invoke_with_retry(
        chain,
        {
            "title": patent.get("title", ""),
            "abstract": patent.get("abstract", ""),
            "claims": claims_input,
            "status": patent.get("status", "Unknown"),
        },
        cfg.LLM_MAX_RETRIES,
        cfg.LLM_RETRY_BASE_SECONDS,
    )
