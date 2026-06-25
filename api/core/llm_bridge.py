"""
api/core/llm_bridge.py — Extracted LLM invocation logic.

Ported from tools/debug_scoring.py (Approach D — self-defined schemas,
no import of modules/llm_analyzer.py which crashes on module-level init).

Functions:
    load_config(config_name)            → isolated module instance
    list_configs()                      → list of available config names
    build_screening_schema(cfg)         → dynamic Pydantic ScreeningResult
    build_analysis_schema(cfg)          → dynamic Pydantic PatentAnalysis
    screening_system_prompt(cfg)        → formatted system prompt string
    analysis_system_prompt(cfg)         → formatted system prompt string
    make_chain(model, role, prompt, schema)  → LangChain structured-output chain
    invoke_with_retry(chain, payload, retries, base_s) → Pydantic result

Design decisions:
    - D1: No import of llm_analyzer.py (module-level side effects)
    - D2: Config via importlib.util (isolated module per request)
    - Config path resolved relative to project root, not CWD
    - Required config fields validated at load time

Refs: task_J3.md, design_api_layer.md, spec_debug_scoring.md
"""

import importlib.util
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Config resolution
# ═══════════════════════════════════════════════════════════════════════════════

# Configs dir: resolved from CONFIGS_DIR env var, or default to configs/
# relative to the project root (two levels up from this file in production,
# but for Docker the mount point is more reliable).
def _configs_dir() -> Path:
    env = os.environ.get("CONFIGS_DIR")
    if env:
        return Path(env).resolve()
    # Default: configs/ relative to CWD (project root in normal usage)
    return Path("configs").resolve()


REQUIRED_CONFIG_FIELDS = [
    "TARGET_PRODUCT", "TARGET_DRUG", "TARGET_ROUTE", "TARGET_INDICATION",
    "SCREENING_IRRELEVANT_EXAMPLES", "SCREENING_MODEL", "ANALYSIS_MODEL",
    "CLAIMS_MAX_CHARS", "LLM_MAX_RETRIES", "LLM_RETRY_BASE_SECONDS",
]


def list_configs() -> list[str]:
    """Return sorted list of available config names (without .py extension)."""
    d = _configs_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.stem for p in d.glob("*.py")
        if p.is_file() and not p.stem.startswith("_")
    )


def load_config(config_name: str):
    """
    Load a config file as an isolated module instance.

    Args:
        config_name: e.g. "pemirolast_ipf_v3" (no .py extension)

    Returns:
        Module instance with config attributes.

    Raises:
        FileNotFoundError: if config file doesn't exist
        ValueError: if config is missing required fields
    """
    config_path = _configs_dir() / f"{config_name}.py"
    if not config_path.exists():
        available = list_configs()
        raise FileNotFoundError(
            f"Config '{config_name}' not found. "
            f"Available: {available}"
        )

    spec = importlib.util.spec_from_file_location(
        f"_api_config_{config_name}", str(config_path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Validate required fields
    missing = [f for f in REQUIRED_CONFIG_FIELDS if not hasattr(mod, f)]
    if missing:
        raise ValueError(
            f"Config '{config_name}' missing required fields: {', '.join(missing)}"
        )

    return mod


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas (Approach D — mirrors llm_analyzer.py's types)
# ═══════════════════════════════════════════════════════════════════════════════

def build_screening_schema(cfg):
    """
    Dynamically build ScreeningResult Pydantic model using config constants.

    Returns a Pydantic model class (not an instance).
    """
    target_drug = cfg.TARGET_DRUG
    target_route = cfg.TARGET_ROUTE
    target_indication = cfg.TARGET_INDICATION
    irrelevant_examples = cfg.SCREENING_IRRELEVANT_EXAMPLES

    class ScreeningResult(BaseModel):
        """Stage 1: quick screening, title + abstract only."""
        is_relevant: bool = Field(
            description=(
                f"是否與以下任一有任何關聯：\n"
                f"- 藥物：{target_drug}\n"
                f"- 給藥途徑：{target_route}\n"
                f"- 適應症：{target_indication}\n"
                f"如果完全無關（例如 {irrelevant_examples}），回傳 False。"
            )
        )
        quick_risk: Literal["High", "Medium", "Low"] = Field(
            description=(
                "根據摘要的快速風險評估。\n"
                f"High   = 同時涵蓋 {target_drug} + {target_route} + {target_indication}\n"
                f"Medium = 部分重疊（有 {target_drug} 但無 {target_route}，\n"
                f"         或有 {target_indication} 但藥物不同）\n"
                "Low    = 與本案高度無關"
            )
        )

    return ScreeningResult


def build_analysis_schema(cfg):
    """
    Dynamically build PatentAnalysis Pydantic model using config constants.

    Returns a Pydantic model class (not an instance).
    """
    target_drug = cfg.TARGET_DRUG
    target_indication = cfg.TARGET_INDICATION
    target_product = cfg.TARGET_PRODUCT

    class PatentAnalysis(BaseModel):
        """Stage 2: full claims analysis for FTO risk."""

        is_target_drug: bool = Field(
            description=(
                f"Claims 或摘要是否明確提及 {target_drug} 或其衍生物、同類藥物？"
            )
        )

        delivery_routes: str = Field(
            description=(
                "列出 claims 中明確出現的給藥途徑（僅限前 3 個，其餘省略）。\n"
                "例如：'Oral, Nasal, Intravenous'\n"
                "若未提及，回傳 'Not specified'。"
            )
        )

        indications: str = Field(
            description=(
                "列出涉及的適應症（僅限前 3 個關鍵疾病）。\n"
                f"若可能涵蓋 {target_indication}，請加註。\n"
                "例如：'Neurodegenerative disease (may cover SCA), Alzheimer'"
            )
        )

        claim_scope: str = Field(
            description=(
                "用一句話描述核心保護範圍（50 字以內）。\n"
                "若使用摘要請加註 (based on abstract)。"
            )
        )

        fto_risk: Literal["High", "Medium", "Low"] = Field(
            description=f"對『{target_product}』的 FTO 阻擋風險評等。"
        )

        gap_opportunity: str = Field(
            description="一兩句說明本專利『未涵蓋』的空白區域。"
        )

        reasoning: str = Field(
            description="50 字以內給出評分理由，禁止列出化學式或序列。"
        )

    return PatentAnalysis


# ═══════════════════════════════════════════════════════════════════════════════
# System prompts (production mirror — filled from config constants)
# ═══════════════════════════════════════════════════════════════════════════════

def screening_system_prompt(cfg) -> str:
    """Build the screening (Stage 1) system prompt from config."""
    return (
        f"你是資深生技醫藥專利分析師。\n"
        f"我們的目標產品：{cfg.TARGET_PRODUCT}\n"
        f"- 藥物：{cfg.TARGET_DRUG}\n"
        f"- 給藥途徑：{cfg.TARGET_ROUTE}\n"
        f"- 適應症：{cfg.TARGET_INDICATION}\n"
        f"\n"
        f"請根據 title 和 abstract 快速判斷此專利的相關性，不需要深入分析。\n"
        f"原則：\n"
        f"- 完全無關（{cfg.SCREENING_IRRELEVANT_EXAMPLES}）→ is_relevant=False\n"
        f"- 有任何 {cfg.TARGET_DRUG}、{cfg.TARGET_ROUTE}、{cfg.TARGET_INDICATION} 相關 → is_relevant=True\n"
    )


def analysis_system_prompt(cfg) -> str:
    """Build the analysis (Stage 2) system prompt from config."""
    return (
        f"你是資深生技醫藥專利律師，專門評估 FTO（Freedom to Operate）風險。\n"
        f"\n"
        f"我們的目標產品：\n"
        f"- 活性成分：{cfg.TARGET_DRUG}\n"
        f"- 給藥途徑：{cfg.TARGET_ROUTE}\n"
        f"- 適應症：{cfg.TARGET_INDICATION}\n"
        f"\n"
        f"分析原則：\n"
        f"1. 嚴格依據文字，不過度推論\n"
        f"2. 廣泛字眼需謹慎（可能有同義詞廣義涵蓋 {cfg.TARGET_INDICATION}）\n"
        f"3. 重點分析 independent claim（通常是 claim 1），而非 dependent claims 或僅摘要\n"
        f"4. 若 claims 為空，以摘要為準並在 reasoning 中標注資料不完整\n"
    )


def interpolate_rubric(rubric_text: str, cfg) -> str:
    """
    Interpolate {TARGET_DRUG}, {TARGET_ROUTE}, {TARGET_INDICATION},
    {TARGET_PRODUCT}, {SCREENING_IRRELEVANT_EXAMPLES} placeholders
    in a rubric override text using config values.

    Non-fatal on missing placeholders — logs warning, leaves as-is.
    """
    try:
        return rubric_text.format(
            TARGET_DRUG=cfg.TARGET_DRUG,
            TARGET_ROUTE=cfg.TARGET_ROUTE,
            TARGET_INDICATION=cfg.TARGET_INDICATION,
            TARGET_PRODUCT=cfg.TARGET_PRODUCT,
            SCREENING_IRRELEVANT_EXAMPLES=cfg.SCREENING_IRRELEVANT_EXAMPLES,
        )
    except KeyError as e:
        logger.warning("Rubric placeholder %s not found in config; left as-is.", e)
        return rubric_text


# ═══════════════════════════════════════════════════════════════════════════════
# LLM invocation (mirrors llm_analyzer.py patterns)
# ═══════════════════════════════════════════════════════════════════════════════

NO_TEMPERATURE_MODELS = {"o3-mini", "o3", "o4-mini", "gpt-5", "gpt-5-mini"}

REASONING_MODEL_TOKENS = {
    "screening": 4000,
    "analysis":  8000,
}

# Fields returned by Stage 2 analysis (used for compare diff in J-4)
ANALYSIS_FIELDS = [
    "is_target_drug", "delivery_routes", "indications",
    "claim_scope", "fto_risk", "gap_opportunity", "reasoning",
]


def make_chain(model: str, role: str, system_prompt: str, schema_cls):
    """
    Build a LangChain structured-output chain.

    Args:
        model: OpenAI model name (e.g. "gpt-4o-mini")
        role: "screening" or "analysis" (affects prompt template and token budget)
        system_prompt: system message text
        schema_cls: Pydantic model class for structured output

    Returns:
        LangChain chain (prompt | llm.with_structured_output)
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate

    is_reasoning = model in NO_TEMPERATURE_MODELS
    max_tokens = (
        REASONING_MODEL_TOKENS[role] if is_reasoning
        else (120 if role == "screening" else 400)
    )

    if is_reasoning:
        llm = ChatOpenAI(model=model, max_tokens=max_tokens)
    else:
        llm = ChatOpenAI(model=model, temperature=0, max_tokens=max_tokens)

    if role == "screening":
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "標題：{title}\n\n摘要：{abstract}"),
        ])
    else:
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "標題：{title}\n\n摘要：{abstract}\n\n請求項：{claims}\n\n法律狀態：{status}"),
        ])

    return prompt | llm.with_structured_output(schema_cls)


def invoke_with_retry(chain, payload: dict, max_retries: int, base_seconds: int):
    """
    LLM call with exponential backoff on 429 / rate_limit errors.

    Args:
        chain: LangChain chain
        payload: dict passed to chain.invoke()
        max_retries: max number of attempts
        base_seconds: base wait time for exponential backoff

    Returns:
        Pydantic model instance (structured output)

    Raises:
        Exception: on non-rate-limit errors or exhausted retries
    """
    import time
    for attempt in range(max_retries):
        try:
            return chain.invoke(payload)
        except Exception as e:
            msg = str(e).lower()
            if ("429" not in msg) and ("rate_limit" not in msg):
                raise
            if attempt == max_retries - 1:
                raise
            wait_s = base_seconds * (2 ** attempt)
            logger.warning(
                "Rate limited, waiting %ds (attempt %d/%d)",
                wait_s, attempt + 1, max_retries,
            )
            time.sleep(wait_s)


# ═══════════════════════════════════════════════════════════════════════════════
# Claims preprocessing (shared by dry-run and live paths)
# ═══════════════════════════════════════════════════════════════════════════════

def preprocess_claims(patent: dict, claims_max_chars: int) -> str:
    """
    Prepare claims text for LLM input.

    If claims are empty, falls back to abstract with a note.
    Truncates to claims_max_chars.
    """
    raw_claims = patent.get("claims") or ""
    if not raw_claims.strip():
        return (
            f"(Claims missing, analysis based on Abstract): "
            f"{patent.get('abstract', '')}"
        )
    return raw_claims[:claims_max_chars]
