"""
api/schemas/analysis.py — Pydantic models for analysis endpoints.

J-3: ScoreRequest, ScoreResponse (+ dry-run variants)
J-4: CompareRequest, CompareResponse
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional


# ═══════════════════════════════════════════════════════════════════════════════
# J-3: Score endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class ScoreRequest(BaseModel):
    patent_id: str
    config_name: str
    stage: Literal["1", "2", "both"] = "both"
    dry_run: bool = False
    rubric_override: Optional[str] = None
    screening_model: Optional[str] = None
    analysis_model: Optional[str] = None


class DbState(BaseModel):
    """Patent DB state summary included in every score response."""
    title: Optional[str] = None
    abstract_chars: int = 0
    claims_chars: int = 0
    source: Optional[str] = None


class ScreeningOutput(BaseModel):
    """Stage 1 LLM result."""
    model: str
    is_relevant: bool
    quick_risk: Literal["High", "Medium", "Low"]


class AnalysisOutput(BaseModel):
    """Stage 2 LLM result."""
    model: str
    rubric: str = "default"
    is_target_drug: bool
    delivery_routes: str
    indications: str
    claim_scope: str
    fto_risk: Literal["High", "Medium", "Low"]
    gap_opportunity: str
    reasoning: str


class ScreeningDryRunInput(BaseModel):
    """What Stage 1 would receive (dry-run preview)."""
    system_prompt_chars: int
    title: Optional[str] = None
    abstract_preview: Optional[str] = None


class AnalysisDryRunInput(BaseModel):
    """What Stage 2 would receive (dry-run preview)."""
    system_prompt_chars: int
    claims_preview: Optional[str] = None
    claims_total_chars: int = 0


class ScoreResponse(BaseModel):
    """
    Response for POST /api/v1/analysis/score.

    Live run: screening and/or analysis populated.
    Dry run: screening_input and/or analysis_input populated.
    """
    patent_id: str
    config_name: str
    dry_run: bool = False
    db_state: DbState

    # Live run fields (populated when dry_run=False)
    screening: Optional[ScreeningOutput] = None
    analysis: Optional[AnalysisOutput] = None

    # Dry run fields (populated when dry_run=True)
    screening_input: Optional[ScreeningDryRunInput] = None
    analysis_input: Optional[AnalysisDryRunInput] = None


# ═══════════════════════════════════════════════════════════════════════════════
# J-4: Compare endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class CompareRequest(BaseModel):
    patent_id: str
    config_name: str
    override_rubric_text: str
    analysis_model: Optional[str] = None


class CompareSideOutput(BaseModel):
    """One side of the A/B comparison (baseline or override)."""
    rubric: str
    is_target_drug: bool
    delivery_routes: str
    indications: str
    claim_scope: str
    fto_risk: Literal["High", "Medium", "Low"]
    gap_opportunity: str
    reasoning: str


class CompareFieldDiff(BaseModel):
    """Per-field diff entry."""
    match: bool
    baseline: Optional[str] = None
    override: Optional[str] = None


class CompareResponse(BaseModel):
    patent_id: str
    config_name: str
    baseline: CompareSideOutput
    override: CompareSideOutput
    diff: dict[str, CompareFieldDiff]
    has_differences: bool
