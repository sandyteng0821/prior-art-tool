"""
debug_scoring — 單筆專利 LLM scoring 重現工具

給定一個 patent_id + config，重現 pipeline 的 Stage 1 screening + Stage 2
analysis，完整顯示 LLM 收到的 input 和回傳的 structured output。

定位：看「判斷層」（LLM input/output/reasoning）。
搭配 inspect_patent（看「資料層」）使用。

不做的事：
  - 不改任何 production module
  - 不寫入 DB（純讀取）
  - 不寫入 output/（不產生 CSV）
  - 不做批量分析

Usage:
    # 基本用法
    python3 -m tools.debug_scoring US9415051B1 \\
        --config configs/pemirolast_ipf_v3.py

    # 只跑 Stage 2
    python3 -m tools.debug_scoring US9415051B1 \\
        --config configs/pemirolast_ipf_v3.py \\
        --stage 2

    # Dry-run（零成本，只印 LLM input）
    python3 -m tools.debug_scoring US9415051B1 \\
        --config configs/pemirolast_ipf_v3.py \\
        --dry-run

    # 用不同 rubric 跑 Stage 2（A/B test prompt）
    python3 -m tools.debug_scoring US9415051B1 \\
        --config configs/pemirolast_ipf_v3.py \\
        --stage 2 --rubric-override scratch/rubric_v2.txt

    # A/B 並排比較（default rubric vs override，跑兩次 Stage 2）
    python3 -m tools.debug_scoring US9415051B1 \\
        --config configs/pemirolast_ipf_v3.py \\
        --compare scratch/rubric_v2.txt

Spec: docs/spec/spec_debug_scoring.md
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

# patent_store 只用 get_by_id，不觸發 LLM init
# 需要 project root 在 sys.path 上
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from modules.patent_store import get_by_id


# ═══════════════════════════════════════════════════════════════════════════════
# Config loading（importlib.util，不汙染全域 config.py）
# ═══════════════════════════════════════════════════════════════════════════════

def load_config(config_path: str):
    """
    載入指定 config 檔為獨立 module instance。
    不改 sys.modules，不影響 workstation 上的 config.py。
    """
    p = Path(config_path).resolve()
    if not p.exists():
        print(f"[ERROR] Config not found: {config_path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("_debug_config", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic Schemas（方案 D：自己定義，未來加 drift detection）
#
# 欄位 name + type 必須跟 llm_analyzer.py 的 ScreeningResult / PatentAnalysis
# 保持一致。Schema drift 會在 Step 6 加入 AST-based detection。
# ═══════════════════════════════════════════════════════════════════════════════

def _build_screening_schema(cfg):
    """
    動態建構 ScreeningResult schema，用指定 config 的常數填入 description。
    回傳一個 Pydantic model class。
    """
    target_drug = cfg.TARGET_DRUG
    target_route = cfg.TARGET_ROUTE
    target_indication = cfg.TARGET_INDICATION
    irrelevant_examples = cfg.SCREENING_IRRELEVANT_EXAMPLES

    class ScreeningResult(BaseModel):
        """Stage 1：快速初篩，只看 title + abstract。"""
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


def _build_analysis_schema(cfg):
    """
    動態建構 PatentAnalysis schema，用指定 config 的常數填入 description。
    回傳一個 Pydantic model class。
    """
    target_drug = cfg.TARGET_DRUG
    target_indication = cfg.TARGET_INDICATION
    target_product = cfg.TARGET_PRODUCT

    class PatentAnalysis(BaseModel):
        """Stage 2：精讀 claims，完整 FTO 分析。"""

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
# System prompts（production mirror — 用 config 常數填入）
# ═══════════════════════════════════════════════════════════════════════════════

def _screening_system(cfg) -> str:
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


def _analysis_system(cfg) -> str:
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


# ═══════════════════════════════════════════════════════════════════════════════
# LLM invocation（mirrors llm_analyzer.py patterns）
# ═══════════════════════════════════════════════════════════════════════════════

NO_TEMPERATURE_MODELS = {"o3-mini", "o3", "o4-mini", "gpt-5", "gpt-5-mini"}

REASONING_MODEL_TOKENS = {
    "screening": 4000,
    "analysis":  8000,
}


def _make_chain(model: str, role: str, system_prompt: str, schema_cls):
    """Build a LangChain structured-output chain."""
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


def _invoke_with_retry(chain, payload, max_retries: int, base_seconds: int):
    """LLM call with exponential backoff on 429 / rate_limit."""
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
            print(f"  [retry] rate limited, waiting {wait_s}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(wait_s)


# ═══════════════════════════════════════════════════════════════════════════════
# Output formatting
# ═══════════════════════════════════════════════════════════════════════════════

def _header(patent_id: str, config_path: str, cfg):
    """Print the top banner."""
    print()
    print("═" * 70)
    print(f"  debug_scoring: {patent_id}")
    print(f"  Config: {config_path}")
    print(f"  Target: {cfg.TARGET_PRODUCT}")
    print("═" * 70)


def _section(title: str):
    print()
    print(f"── {title} " + "─" * max(1, 66 - len(title)))


def _print_db_state(patent: dict):
    """Print DB state summary."""
    _section("DB State")
    fields = [
        ("title",              patent.get("title", "")[:80]),
        ("abstract",           f"{len(patent.get('abstract', '') or '')} chars"),
        ("claims",             f"{len(patent.get('claims', '') or '')} chars"),
        ("examples_extracted", f"{len(patent.get('examples_extracted', '') or '')} chars"),
        ("status",             patent.get("status", "Unknown")),
        ("source",             patent.get("source", "")),
        ("fetched_at",         patent.get("fetched_at", "")),
    ]
    for label, val in fields:
        print(f"  {label:<22} {val}")


def _print_screening_input(patent: dict):
    """Print what Stage 1 will receive."""
    title = patent.get("title", "")
    abstract = patent.get("abstract", "")
    print(f"  Input:    title ({len(title)} chars) + abstract ({len(abstract)} chars)")
    print()
    print(f"  [Title]")
    print(f"    {title[:200]}")
    print()
    print(f"  [Abstract - first 300 chars]")
    print(f"    {abstract[:300]}")


def _print_analysis_input(patent: dict, claims_input: str, cfg):
    """Print what Stage 2 will receive."""
    title = patent.get("title", "")
    abstract = patent.get("abstract", "")
    status = patent.get("status", "Unknown")
    print(f"  Input:    title + abstract + claims[:{cfg.CLAIMS_MAX_CHARS}] ({len(claims_input)} chars) + status")
    print()
    # Show enough to see the structure; cap at 800 for readability
    preview_len = min(800, len(claims_input))
    truncated = "..." if len(claims_input) > preview_len else ""
    print(f"  [Claims sent to LLM - first {preview_len} chars]")
    print(f"    {claims_input[:preview_len]}{truncated}")


# ═══════════════════════════════════════════════════════════════════════════════
# Stage 2 runner (reused by normal mode and --compare)
# ═══════════════════════════════════════════════════════════════════════════════

ANALYSIS_FIELDS = [
    "is_target_drug", "delivery_routes", "indications",
    "claim_scope", "fto_risk", "gap_opportunity", "reasoning",
]


def _run_stage2(model, system_prompt, schema_cls, patent, claims_input, cfg):
    """Run Stage 2 analysis and return the Pydantic result object."""
    chain = _make_chain(model, "analysis", system_prompt, schema_cls)
    return _invoke_with_retry(
        chain,
        {
            "title":    patent.get("title", ""),
            "abstract": patent.get("abstract", ""),
            "claims":   claims_input,
            "status":   patent.get("status", "Unknown"),
        },
        cfg.LLM_MAX_RETRIES,
        cfg.LLM_RETRY_BASE_SECONDS,
    )


def _print_stage2_result(result):
    """Print Stage 2 result fields."""
    for field_name in ANALYSIS_FIELDS:
        val_str = str(getattr(result, field_name))
        if len(val_str) > 120:
            val_str = val_str[:120] + "..."
        print(f"    {field_name:<17} {val_str}")


def _print_compare(result_a, result_b, label_a, label_b):
    """Print side-by-side comparison of two Stage 2 results."""
    col_w = 40
    print(f"  {'[A] ' + label_a:<{col_w}} │ {'[B] ' + label_b}")
    print(f"  {'─' * col_w}┼{'─' * col_w}")
    for field_name in ANALYSIS_FIELDS:
        va = str(getattr(result_a, field_name))
        vb = str(getattr(result_b, field_name))
        # Truncate for side-by-side display
        if len(va) > col_w - 2:
            va = va[:col_w - 5] + "..."
        if len(vb) > col_w - 2:
            vb = vb[:col_w - 5] + "..."
        # Highlight differences
        marker = " " if va == vb else "≠"
        print(f" {marker}{va:<{col_w}}│ {vb}")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Reproduce LLM scoring for a single patent (debug tool).",
        prog="python3 -m tools.debug_scoring",
    )
    parser.add_argument("patent_id", help="Patent ID to debug")
    parser.add_argument(
        "--config", required=True,
        help="Path to config file (required, e.g. configs/pemirolast_ipf_v3.py)",
    )
    parser.add_argument(
        "--stage", choices=["1", "2", "both"], default="both",
        help="Which stage(s) to run. Default: both",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print LLM input without calling API (zero cost)",
    )
    parser.add_argument(
        "--rubric-override", type=str, metavar="FILE",
        help="Replace ANALYSIS_SYSTEM prompt with content from this file",
    )
    parser.add_argument(
        "--compare", type=str, metavar="FILE",
        help="A/B test: run Stage 2 twice (default rubric vs FILE) and show side-by-side",
    )
    parser.add_argument(
        "--screening-model", type=str, metavar="MODEL",
        help="Override config's SCREENING_MODEL (e.g. gpt-4o-mini)",
    )
    parser.add_argument(
        "--analysis-model", type=str, metavar="MODEL",
        help="Override config's ANALYSIS_MODEL (e.g. gpt-4o)",
    )
    args = parser.parse_args()

    # ── Flag validation ───────────────────────────────────────────────────────
    if args.compare and args.rubric_override:
        print("[ERROR] Use --compare or --rubric-override, not both.")
        sys.exit(1)
    if args.compare and args.dry_run:
        print("[ERROR] --compare requires LLM calls; cannot combine with --dry-run.")
        sys.exit(1)

    # Load rubric override file if specified (interpolation deferred until after config load)
    rubric_file = args.rubric_override or args.compare
    rubric_override_text = None
    if rubric_file:
        rp = Path(rubric_file)
        if not rp.exists():
            print(f"[ERROR] Rubric file not found: {rubric_file}")
            sys.exit(1)
        rubric_override_text = rp.read_text().strip()
        if not rubric_override_text:
            print(f"[ERROR] Rubric file is empty: {rubric_file}")
            sys.exit(1)

    # ── Load config ───────────────────────────────────────────────────────────
    cfg = load_config(args.config)

    # Validate required config fields
    required_fields = [
        "TARGET_PRODUCT", "TARGET_DRUG", "TARGET_ROUTE", "TARGET_INDICATION",
        "SCREENING_IRRELEVANT_EXAMPLES", "SCREENING_MODEL", "ANALYSIS_MODEL",
        "CLAIMS_MAX_CHARS", "LLM_MAX_RETRIES", "LLM_RETRY_BASE_SECONDS",
    ]
    missing = [f for f in required_fields if not hasattr(cfg, f)]
    if missing:
        print(f"[ERROR] Config missing fields: {', '.join(missing)}")
        sys.exit(1)

    # ── Apply model overrides ─────────────────────────────────────────────────
    if args.screening_model:
        cfg.SCREENING_MODEL = args.screening_model
    if args.analysis_model:
        cfg.ANALYSIS_MODEL = args.analysis_model

    # ── Interpolate rubric override with config values ────────────────────────
    if rubric_override_text:
        try:
            rubric_override_text = rubric_override_text.format(
                TARGET_DRUG=cfg.TARGET_DRUG,
                TARGET_ROUTE=cfg.TARGET_ROUTE,
                TARGET_INDICATION=cfg.TARGET_INDICATION,
                TARGET_PRODUCT=cfg.TARGET_PRODUCT,
                SCREENING_IRRELEVANT_EXAMPLES=cfg.SCREENING_IRRELEVANT_EXAMPLES,
            )
        except KeyError as e:
            print(f"[WARNING] Rubric placeholder {e} not found in config; left as-is.")

    # ── Load patent from DB ───────────────────────────────────────────────────
    patent = get_by_id(args.patent_id)
    if not patent:
        print(f"[ERROR] Patent {args.patent_id} not found in DB.")
        print(f"        Run inspect_patent first to check data availability.")
        sys.exit(1)

    # ── Header + DB state ─────────────────────────────────────────────────────
    _header(args.patent_id, args.config, cfg)
    _print_db_state(patent)

    # ── Load API key (only if not dry-run) ────────────────────────────────────
    if not args.dry_run:
        from dotenv import load_dotenv
        load_dotenv()

    # ── Claims preprocessing (shared by dry-run and live) ─────────────────────
    raw_claims = patent.get("claims") or ""
    if not raw_claims.strip():
        claims_input = (
            f"(Claims missing, analysis based on Abstract): "
            f"{patent.get('abstract', '')}"
        )
    else:
        claims_input = raw_claims[:cfg.CLAIMS_MAX_CHARS]

    # ── Build schemas ─────────────────────────────────────────────────────────
    ScreeningResult = _build_screening_schema(cfg)
    PatentAnalysis = _build_analysis_schema(cfg)

    run_stage_1 = args.stage in ("1", "both")
    run_stage_2 = args.stage in ("2", "both")

    # --compare implies Stage 2
    if args.compare:
        run_stage_2 = True

    screening_result = None  # used by stage 2 gate logic

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 1: Screening
    # ══════════════════════════════════════════════════════════════════════════
    if run_stage_1:
        _section(f"Stage 1: Screening")
        screening_model = cfg.SCREENING_MODEL
        print(f"  Model:    {screening_model}")
        _print_screening_input(patent)

        if args.dry_run:
            print()
            print(f"  [DRY-RUN] Skipping LLM call.")
            print(f"  System prompt ({len(_screening_system(cfg))} chars):")
            print(f"    {_screening_system(cfg)[:300]}...")
        else:
            chain = _make_chain(
                screening_model, "screening",
                _screening_system(cfg), ScreeningResult,
            )
            screening_result = _invoke_with_retry(
                chain,
                {"title": patent.get("title", ""),
                 "abstract": patent.get("abstract", "")},
                cfg.LLM_MAX_RETRIES,
                cfg.LLM_RETRY_BASE_SECONDS,
            )
            print()
            print(f"  [LLM Response]")
            print(f"    is_relevant: {screening_result.is_relevant}")
            print(f"    quick_risk:  {screening_result.quick_risk}")

            # Gate logic
            if (not screening_result.is_relevant) or screening_result.quick_risk == "Low":
                print()
                print(f"  → Stage 1 short-circuit: irrelevant or Low risk. Stage 2 would be skipped in production.")
                if args.stage == "both":
                    print(f"    (Continuing to Stage 2 anyway for debug purposes.)")
            else:
                print()
                print(f"  → Proceed to Stage 2")

    # ══════════════════════════════════════════════════════════════════════════
    # Stage 2: Analysis
    # ══════════════════════════════════════════════════════════════════════════
    if run_stage_2:
        # Determine rubric source
        if args.compare:
            rubric_label = "default (ANALYSIS_SYSTEM from config)"
        elif rubric_override_text:
            rubric_label = f"override ({args.rubric_override})"
        else:
            rubric_label = "default (ANALYSIS_SYSTEM from config)"

        _section(f"Stage 2: Analysis")
        analysis_model = cfg.ANALYSIS_MODEL
        print(f"  Model:    {analysis_model}")
        print(f"  Rubric:   {rubric_label}")
        _print_analysis_input(patent, claims_input, cfg)

        if args.dry_run:
            print()
            print(f"  [DRY-RUN] Skipping LLM call.")
            if rubric_override_text:
                sys_prompt = rubric_override_text
                print(f"  System prompt OVERRIDE ({len(sys_prompt)} chars):")
            else:
                sys_prompt = _analysis_system(cfg)
                print(f"  System prompt ({len(sys_prompt)} chars):")
            print(f"    {sys_prompt[:300]}...")

            # Keyword probe on claims_input (matches spec validation case)
            print()
            print(f"  [Keyword Probe — claims text sent to LLM]")
            drug_aliases = getattr(cfg, "DRUG_ALIASES", [])
            indication_kw = getattr(cfg, "INDICATIONS", [])
            route_kw = getattr(cfg, "FORMULATIONS", [])
            text_lower = claims_input.lower()
            for label, keywords in [
                ("drug",       drug_aliases),
                ("indication", indication_kw),
                ("route",      route_kw),
            ]:
                hits = [k for k in keywords if k.lower() in text_lower]
                miss = "✓" if hits else "✗"
                print(f"    {label:<12} {miss}  {', '.join(hits) if hits else '(none)'}")

        elif args.compare:
            # ── A/B comparison mode: run twice ────────────────────────────
            _section("Stage 2: A/B Comparison")
            print(f"  Model:    {analysis_model}")
            print(f"  [A] Default rubric (from config)")
            print(f"  [B] Override: {args.compare}")
            print()

            print(f"  Running [A] default rubric...")
            result_a = _run_stage2(
                analysis_model, _analysis_system(cfg),
                PatentAnalysis, patent, claims_input, cfg,
            )

            print(f"  Running [B] override rubric...")
            result_b = _run_stage2(
                analysis_model, rubric_override_text,
                PatentAnalysis, patent, claims_input, cfg,
            )

            print()
            _print_compare(result_a, result_b, "Default rubric", args.compare)

        else:
            # ── Normal Stage 2 (with optional rubric override) ────────────
            sys_prompt = rubric_override_text if rubric_override_text else _analysis_system(cfg)
            result = _run_stage2(
                analysis_model, sys_prompt,
                PatentAnalysis, patent, claims_input, cfg,
            )
            print()
            print(f"  [LLM Response]")
            _print_stage2_result(result)

    # ── Footer ────────────────────────────────────────────────────────────────
    print()
    print("═" * 70)


if __name__ == "__main__":
    main()