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
from config import SCREENING_MODEL, ANALYSIS_MODEL, TARGET_PRODUCT, USE_LLM


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ScreeningResult(BaseModel):
    """Stage 1：快速初篩，只看摘要。"""
    is_relevant: bool = Field(
        description="是否與 PDE4 抑制劑、鼻腔給藥或神經/小腦疾病有任何關聯？"
                    "如果完全無關（例如純 COPD 口服或皮膚科外用），回傳 False。"
    )
    quick_risk: Literal["High", "Medium", "Low"] = Field(
        description=(
            "根據摘要的快速風險評估。\n"
            "High   = 同時涵蓋 PDE4 + 鼻腔/CNS + 神經/小腦疾病\n"
            "Medium = 部分重疊（CNS 但非鼻腔，或鼻腔但非 CNS）\n"
            "Low    = 與本案高度無關"
        )
    )


class PatentAnalysis(BaseModel):
    """Stage 2：精讀 claims，完整分析。"""
    is_target_drug: bool = Field(
        description="是否明確提及 Roflumilast、PDE4 inhibitor 或其衍生物？"
    )
    delivery_routes: list[str] = Field(
        description="所有出現的給藥途徑。若未提及，回傳 ['Not specified']。"
    )
    indications: list[str] = Field(
        description="所有涉及的適應症或疾病名稱。"
    )
    fto_risk: Literal["High", "Medium", "Low"] = Field(
        description=(
            f"對『{TARGET_PRODUCT}』的 FTO 阻擋風險。\n"
            "High   = Active 狀態，且 claims 同時涵蓋 PDE4 + 鼻腔/CNS + 神經/小腦疾病\n"
            "Medium = 部分重疊，或狀態不明\n"
            "Low    = 與本案高度無關，或已過期"
        )
    )
    gap_opportunity: str = Field(
        description="本專利『未涵蓋』的空白區域，一到兩句說明。"
    )
    reasoning: str = Field(
        description="給出 fto_risk 評分的理由，50 字以內。"
    )


# ── System Prompts ────────────────────────────────────────────────────────────

SCREENING_SYSTEM = f"""你是資深生技醫藥專利分析師。
我們的目標產品：{TARGET_PRODUCT}

請根據摘要快速判斷此專利的相關性，不需要深入分析。
原則：
- 完全無關的專利（純 COPD 口服、皮膚科外用）直接標記 is_relevant=False
- 有任何 CNS、鼻腔給藥、PDE4 相關內容都標記 is_relevant=True
"""

ANALYSIS_SYSTEM = f"""你是資深生技醫藥專利律師，專門評估 FTO（Freedom to Operate）風險。

我們的目標產品：
- 活性成分：Roflumilast（PDE4 抑制劑）
- 給藥途徑：鼻噴劑（Nasal spray / Nose-to-brain）
- 適應症：小腦萎縮症（Spinocerebellar Ataxia, SCA）

分析原則：
1. 嚴格依據文字，不過度推論（寫 Oral 不等於涵蓋 Nasal）
2. 廣泛字眼需謹慎（「神經退化性疾病」可能廣義涵蓋 SCA）
3. 重點分析 Claims，而非僅摘要
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
    screening_llm = ChatOpenAI(model=SCREENING_MODEL, temperature=0)
    analysis_llm  = ChatOpenAI(model=ANALYSIS_MODEL,  temperature=0)
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
    analysis = analysis_chain.invoke({
        "title":    patent.get("title", ""),
        "abstract": patent.get("abstract", ""),
        "claims":   patent.get("claims", ""),
        "status":   patent.get("status", "Unknown"),
    })

    return {**patent, **analysis.model_dump()}


# ── 規則評分（免費，不需要 LLM） ─────────────────────────────────────────────

DRUG_KEYWORDS       = ["roflumilast", "pde4", "phosphodiesterase 4", "pde-4"]
ROUTE_KEYWORDS      = ["nasal", "intranasal", "nose-to-brain", "transmucosal",
                       "nasal spray", "intranasal spray"]
INDICATION_KEYWORDS = ["spinocerebellar", "ataxia", "cerebellar", "sca",
                       "machado-joseph"]
CNS_KEYWORDS        = ["neurodegenerat", "cerebellum", "purkinje",
                       "cognitive", "neuroprotect", "central nervous"]


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

    drug_match       = any(k in text for k in DRUG_KEYWORDS)
    route_match      = any(k in text for k in ROUTE_KEYWORDS)
    indication_match = any(k in text for k in INDICATION_KEYWORDS)
    cns_match        = any(k in text for k in CNS_KEYWORDS)

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
        "delivery_routes":  ["Nasal"] if route_match else ["Not specified"],
        "indications":      ["SCA/Ataxia"] if indication_match else [],
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
