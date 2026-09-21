# config.py — spesolimab_gpp_v1
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-09] LLM 判斷邏輯驗證：spesolimab 作為 POSITIVE CONTROL
#   目的：spesolimab 是已上市 GPP 原研藥（Spevigo, Boehringer Ingelheim），
#        在 5×3 GPSS matrix 中有 14 篇真實命中（含 3 篇 granted B2）。
#        理論上 LLM 應把這些判成 is_target_drug=True / fto_risk=High/Medium。
#        若仍全 Low → 抓到 LLM 判斷 bug。
#   ⚠ 這不是 FTO 分析，是對照實驗。rubric 與 fto_risk 語意刻意「不動」，
#     沿用 darifenacin v2 的 FTO rubric，才能公平對照。
# Base: configs/darifenacin_gpp_psoriasis_v2.py（以本機實際檔案為準複製）

TARGET_PRODUCT = "Spesolimab 治療廣泛性膿皰型銀屑病 (GPP) / 銀屑病 (Psoriasis)"

# ← SPESOLIMAB 改動 1：alias（來自 ChEMBL_37 pref_name, CHEMBL4297911, max_phase 4）
DRUG_ALIASES = [
    "Spesolimab",
    "BI 655130",
    "BI-655130",
    "BI655130",
    "Spesolimab-sbzo",
    "Spevigo",
    "Spesolimab sbzo",
]

# ← SPESOLIMAB 改動 2：機制軸。spesolimab = anti-IL-36R 單株抗體。
#   把 darifenacin 的 muscarinic / M3R / anticholinergic 全部移除（完全無關）。
MECHANISMS = [
    "IL-36 receptor antagonist",
    "anti-IL-36R",
    "IL-36R blockade",
    "IL-36R antibody",
    "interleukin-36 receptor",
    "IL36R",
    "monoclonal antibody",
    "IL-36 inhibitor",
    "IL-36 pathway",
]

# ← SPESOLIMAB 改動 3：劑型軸。抗體是 IV/SC 注射，不是 oral tablet。
#   移除 darifenacin 的 oral / tablet / extended-release / capsule。
FORMULATIONS = [
    "injection",
    "intravenous",
    "subcutaneous",
    "infusion",
    "antibody formulation",
    "liquid formulation",
    "lyophilized",
    "parenteral",
]

# 適應症軸：與 darifenacin 相同（同一個 GPP/psoriasis 標的），不動。
INDICATIONS = [
    "generalized pustular psoriasis",
    "GPP",
    "psoriasis",
    "plaque psoriasis",
    "pustular psoriasis",
    "palmoplantar psoriasis",
    "erythrodermic psoriasis",
    "psoriatic arthritis",
    "skin inflammation",
    "IL-36 mediated disease",
]

# 基礎設定：原封不動照抄 darifenacin v2
SCREENING_MODEL = "gpt-4o-mini"
ANALYSIS_MODEL  = "gpt-4o"

MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

FETCH_SIZE = 200
CLAIMS_MAX_CHARS = 3000
USE_LLM = True

SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

TARGET_DRUG       = "Spesolimab（anti-IL-36R 單株抗體 / IL-36 pathway）"
TARGET_ROUTE      = "注射（IV infusion / SC injection）"
TARGET_INDICATION = "GPP（Generalized Pustular Psoriasis，IL-36 pathway，罕見且嚴重）及 Psoriasis，GPP 為優先適應症"

# ← SPESOLIMAB 改動 4：初篩排除範例。spesolimab 的其他適應症要排除
#   （Crohn's / UC / hidradenitis / Netherton 等 IL-36 相關但非 psoriasis）。
SCREENING_IRRELEVANT_EXAMPLES = (
    "克隆氏症（Crohn's disease）、潰瘍性結腸炎（ulcerative colitis）、"
    "化膿性汗腺炎（hidradenitis suppurativa）、Netherton syndrome —— "
    "這些是 spesolimab 的其他 IL-36 適應症，但非 psoriasis / GPP，"
    "完全無 psoriasis / GPP 相關的 general IL-36 抗體專利亦排除"
)

# ← SPESOLIMAB 改動 1 延伸：規則關鍵字對齊新 alias + 機制
RULE_DRUG_KEYWORDS = [
    "spesolimab",
    "bi 655130",
    "bi-655130",
    "bi655130",
    "spevigo",
    "spesolimab-sbzo",
    "spesolimab sbzo",
    "il-36",
    "il-36r",
    "anti-il-36r",
    "il-36 receptor",
]

# ← SPESOLIMAB 改動 3 延伸：規則劑型關鍵字改注射
RULE_ROUTE_KEYWORDS = [
    "injection",
    "intravenous",
    "subcutaneous",
    "infusion",
    "parenteral",
    "antibody",
]

# 適應症規則關鍵字：與 darifenacin 相同，不動
RULE_INDICATION_KEYWORDS = [
    "psoriasis",
    "pustular psoriasis",
    "generalized pustular psoriasis",
    "gpp",
    "plaque psoriasis",
    "erythrodermic psoriasis",
    "psoriatic arthritis",
    "il-36",
    "skin inflammation",
]

# 額外適應症/通路關鍵字：imsidolimab 從「競爭藥」升為主要對手，
#   darifenacin 的 M3-selective 競爭藥全部移除。
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "keratinocyte",
    "il-36",
    "il-36ra",
    "il-36 receptor",
    "neutrophil",
    "il-17",
    "il-23",
    "tnf-alpha",
    "t-cell",
    "imsidolimab",    # ← 直接競爭對手（另一 anti-IL-36R 抗體）
    "spevigo",
    "biologic",
    "adalimumab",
    "secukinumab",
    "acitretin",      # 傳統治療背景
    "cyclosporine",
    "methotrexate",
]

# ← SPESOLIMAB 改動 2 延伸：機制搜尋改成 IL-36 抗體軸。
#   ⚠ "BI 655130" 含空格：GPSS query 已用引號包整組 alias（見 matrix probe），
#     但這裡的 CUSTOM_QUERIES 若走 OPS，空格 token 要留意（見 anti-pattern）。
CUSTOM_QUERIES = [
    'ta=spesolimab AND ta=psoriasis',
    'ta=spesolimab AND ta="pustular psoriasis"',
    'ta=spesolimab AND ta="generalized pustular psoriasis"',
    'ta=spevigo AND ta=psoriasis',
    'ta="IL-36" AND ta=psoriasis',
    'ta="IL-36" AND ta="pustular psoriasis"',
    'ta="IL-36 receptor" AND ta="generalized pustular psoriasis"',
    'ta="anti-IL-36R" AND ta=psoriasis',
    'ta="generalized pustular psoriasis"',
    # 直接競爭對手交叉
    'ta=imsidolimab AND ta=psoriasis',
    'ta=imsidolimab AND ta="generalized pustular psoriasis"',
    'ta="IL-36 receptor antibody" AND ta=psoriasis',
]
