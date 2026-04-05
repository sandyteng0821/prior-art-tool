# modules/llm_analyzer.py
# Module 3：LangChain + Pydantic 結構化輸出
#
# 兩段式設計：
#   Stage 1 (screening)  — gpt-4o-mini，處理所有摘要，過濾 Low risk
#   Stage 2 (analysis)   — gpt-4o，精讀 Medium / High 的完整 claims

from typing import Literal
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from config import (
    SCREENING_MODEL, ANALYSIS_MODEL, TARGET_PRODUCT, USE_LLM,
    TARGET_DRUG, TARGET_ROUTE, TARGET_INDICATION,
    SCREENING_IRRELEVANT_EXAMPLES,
    RULE_DRUG_KEYWORDS, RULE_ROUTE_KEYWORDS,
    RULE_INDICATION_KEYWORDS, RULE_ADDITIONAL_INDICATION_KEYWORDS,
)

# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ScreeningResult(BaseModel):
    """Stage 1：快速初篩，只看 title + abstract。"""
    is_relevant: bool = Field(
        description=(
            f"是否與以下任一有任何關聯：\n"
            f"- 藥物：{TARGET_DRUG}\n"
            f"- 給藥途徑：{TARGET_ROUTE}\n"
            f"- 適應症：{TARGET_INDICATION}\n"
            f"如果完全無關（例如 {SCREENING_IRRELEVANT_EXAMPLES}），回傳 False。"
        )
    )
    quick_risk: Literal["High", "Medium", "Low"] = Field(
        description=(
            "根據摘要的快速風險評估。\n"
            f"High   = 同時涵蓋 {TARGET_DRUG} + {TARGET_ROUTE} + {TARGET_INDICATION}\n"
            f"Medium = 部分重疊（有 {TARGET_DRUG} 但無 {TARGET_ROUTE}，\n"
            f"         或有 {TARGET_INDICATION} 但藥物不同）\n"
            "Low    = 與本案高度無關"
        )
    )

class PatentAnalysis(BaseModel):
    """Stage 2：精讀 claims，完整 FTO 分析。"""

    is_target_drug: bool = Field(
        description=(
            f"Claims 或摘要是否明確提及 {TARGET_DRUG} 或其衍生物、同類藥物？"
        )
    )

    # 關鍵改動 1：改為 str 並限制數量，防止 LLM 吐出長列表導致斷頭
    delivery_routes: str = Field(
        description=(
            "列出 claims 中明確出現的給藥途徑（僅限前 3 個，其餘省略）。\n"
            "例如：'Oral, Nasal, Intravenous'\n"
            "若未提及，回傳 'Not specified'。"
        )
    )

    # 關鍵改動 2：改為 str 並限制數量
    indications: str = Field(
        description=(
            "列出涉及的適應症（僅限前 3 個關鍵疾病）。\n"
            f"若可能涵蓋 {TARGET_INDICATION}，請加註。\n"
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
        description=f"對『{TARGET_PRODUCT}』的 FTO 阻擋風險評等。"
    )

    gap_opportunity: str = Field(
        description="一兩句說明本專利『未涵蓋』的空白區域。"
    )

    reasoning: str = Field(
        description="50 字以內給出評分理由，禁止列出化學式或序列。"
    )


# ── System Prompts ────────────────────────────────────────────────────────────

SCREENING_SYSTEM = f"""你是資深生技醫藥專利分析師。
我們的目標產品：{TARGET_PRODUCT}
- 藥物：{TARGET_DRUG}
- 給藥途徑：{TARGET_ROUTE}
- 適應症：{TARGET_INDICATION}

請根據 title 和 abstract 快速判斷此專利的相關性，不需要深入分析。
原則：
- 完全無關（{SCREENING_IRRELEVANT_EXAMPLES}）→ is_relevant=False
- 有任何 {TARGET_DRUG}、{TARGET_ROUTE}、{TARGET_INDICATION} 相關 → is_relevant=True
"""

ANALYSIS_SYSTEM = f"""你是資深生技醫藥專利律師，專門評估 FTO（Freedom to Operate）風險。

我們的目標產品：
- 活性成分：{TARGET_DRUG}
- 給藥途徑：{TARGET_ROUTE}
- 適應症：{TARGET_INDICATION}

分析原則：
1. 嚴格依據文字，不過度推論
2. 廣泛字眼需謹慎（可能有同義詞廣義涵蓋 {TARGET_INDICATION}）
3. 重點分析 independent claim（通常是 claim 1），而非 dependent claims 或僅摘要
4. 若 claims 為空，以摘要為準並在 reasoning 中標注資料不完整
"""

# ── Prompts ───────────────────────────────────────────────────────────────────

screening_prompt = ChatPromptTemplate.from_messages([
    ("system", SCREENING_SYSTEM),
    ("human", "標題：{title}\n\n摘要：{abstract}"),
])

analysis_prompt = ChatPromptTemplate.from_messages([
    ("system", ANALYSIS_SYSTEM),
    ("human", "標題：{title}\n\n摘要：{abstract}\n\n請求項：{claims}\n\n法律狀態：{status}"),
])

# ── Chains（只在 USE_LLM=True 時初始化，避免沒有 API key 時 crash） ──────────

if USE_LLM:
    screening_llm = ChatOpenAI(model=SCREENING_MODEL, temperature=0, max_tokens=4000)
    analysis_llm  = ChatOpenAI(model=ANALYSIS_MODEL,  temperature=0, max_tokens=4000)
    screening_chain = screening_prompt | screening_llm.with_structured_output(ScreeningResult)
    analysis_chain  = analysis_prompt  | analysis_llm.with_structured_output(PatentAnalysis)


# ── 公開介面 ──────────────────────────────────────────────────────────────────

def analyze_patent(patent: dict) -> dict:
    """
    對單筆專利執行兩段式分析。
    回傳原始 patent dict + 分析結果欄位。
    """
    # Stage 1：初篩
    screening = screening_chain.invoke({
        "title":    patent.get("title", ""),
        "abstract": patent.get("abstract", ""),
    })

    if not screening.is_relevant:
        # Low risk，直接回傳，不花 gpt-4o 費用
        return {
            **patent,
            "is_target_drug":   False,
            "delivery_routes":  ["Not specified"],
            "indications":      [],
            "fto_risk":         "Low",
            "gap_opportunity":  "與目標產品無關，跳過精讀。",
            "reasoning":        "初篩判定無關，未進行 claims 精讀。",
        }

    # Stage 2：精讀（只有 Medium / High 才到這裡）
    # claims preprocessing
    raw_claims = patent.get("claims") or ""    
    if not raw_claims.strip():
        # 如果沒 Claims，改用 Abstract 頂替
        claims_input = f"(Claims missing, analysis based on Abstract): {patent.get('abstract', '')}"
    else:
        # 截斷至 6000 字元，這對於判斷 Independent Claims (通常在最前面) 非常夠用了
        # 既省錢又能避免 Context Window 爆炸
        claims_input = raw_claims[:6000]
    # llm analysis
    analysis = analysis_chain.invoke({
        "title":    patent.get("title", ""),
        "abstract": patent.get("abstract", ""),
        "claims":   claims_input,  # 使用截斷後的字串
        "status":   patent.get("status", "Unknown"),
    })

    return {**patent, **analysis.model_dump()}


# ── 規則評分（免費，不需要 LLM） ─────────────────────────────────────────────

def rule_based_analyze(patent: dict) -> dict:
    """
    用關鍵字規則對專利評分，完全免費。
    適合預算有限或快速初篩時使用。
    """
    text = " ".join([
        patent.get("title", ""),
        patent.get("abstract", ""),
        patent.get("claims", ""),
    ]).lower()

    drug_match       = any(k in text for k in RULE_DRUG_KEYWORDS)
    route_match      = any(k in text for k in RULE_ROUTE_KEYWORDS)
    indication_match = any(k in text for k in RULE_INDICATION_KEYWORDS)
    cns_match        = any(k in text for k in RULE_ADDITIONAL_INDICATION_KEYWORDS)

    score = sum([drug_match, route_match, indication_match, cns_match])

    if score >= 3:
        risk = "High"
    elif score >= 2:
        risk = "Medium"
    else:
        risk = "Low"

    matched = [
        k for k, v in [
            ("drug", drug_match),
            ("route", route_match),
            ("indication", indication_match),
            ("cns", cns_match),
        ] if v
    ]

    return {
        **patent,
        "is_target_drug":   drug_match,
        "delivery_routes":  [TARGET_ROUTE] if route_match else ["Not specified"],
        "indications":      [TARGET_INDICATION] if indication_match else [],
        "claim_scope":      "規則評分，未解析 claim scope。",
        "fto_risk":         risk,
        "gap_opportunity":  "規則評分，需人工確認。",
        "reasoning":        f"命中 {score}/4 類關鍵字：{', '.join(matched) if matched else '無'}",
    }

# ── 公開入口（根據 USE_LLM 切換） ────────────────────────────────────────────

_original_analyze_patent = analyze_patent


def analyze_patent(patent: dict) -> dict:
    if USE_LLM:
        return _original_analyze_patent(patent)
    return rule_based_analyze(patent)
