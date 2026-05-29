# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-05] txGNN repurposing validation: Darifenacin × GPP / Psoriasis
# Mechanism: Muscarinic acid receptor M3R antagonist, IL-36 pathway
# Role: Repurposing candidate (主角)
# [2026-05 v2] 補入 M3-selective 競爭藥：Solifenacin、Oxybutynin、Trospium

TARGET_PRODUCT = "Oxybutynin 治療廣泛性膿皰型銀屑病 (GPP) / 銀屑病 (Psoriasis)"

DRUG_ALIASES = [
    "Oxybutynin",
    "Oxytrol",
    "Ditropan",
    "Gelnique",
    "muscarinic antagonist",
]

MECHANISMS = [
    "muscarinic receptor antagonist",
    "M3R antagonist",
    "muscarinic M3 antagonist",
    "IL-36 inhibitor",
    "IL-36 pathway",
    "anticholinergic",
]

FORMULATIONS = [
    "oral",
    "tablet",
    "extended-release",
    "modified-release",
    "capsule",
    "topical",
    "cream",
    "ointment",
]

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

TARGET_DRUG       = "Oxybutynin（Non-selective muscarinic antagonist）"
TARGET_ROUTE      = "口服（Oral tablet）+ 外用貼片（Transdermal patch）"
TARGET_INDICATION = "GPP（Generalized Pustular Psoriasis，IL-36 pathway，罕見且嚴重）及 Psoriasis（IL-17/IL-23 pathway，慢性皮膚病），GPP 為優先適應症"

SCREENING_IRRELEVANT_EXAMPLES = (
    "膀胱過動症（overactive bladder）、尿失禁（urinary incontinence）、完全無 psoriasis / GPP / IL-36 相關的 general 抗膽鹼專利"
)

RULE_DRUG_KEYWORDS = [
    "oxybutynin",
    "oxytrol",
    "ditropan",
    "gelnique",
    "muscarinic antagonist",
    "il-36",
]

RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "extended-release",
    "modified-release",
    "capsule",
    "topical",
    "ointment",
]

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
    "spesolimab",     # 競爭藥物（IL-36R 抗體）,
    "imsidolimab",    # 競爭藥物,
    "biologic",
    "adalimumab",     # 競爭 biologic,
    "secukinumab",    # 競爭 biologic,
    "acitretin",      # 傳統治療,
    "cyclosporine",   # 傳統治療,
    "methotrexate",   # 傳統治療,
]

CUSTOM_QUERIES = [
    # Drug-specific queries
    'ta=oxybutynin AND ta=psoriasis',
    'ta=oxybutynin AND ta="pustular psoriasis"',
    'ta=oxybutynin AND ta="generalized pustular psoriasis"',
    'ta=ditropan AND ta=psoriasis',
    'ta=oxytrol AND ta=psoriasis',
    # Shared queries（與 Darifenacin v2 完全相同）
    'ta="muscarinic" AND ta="psoriasis"',
    'ta="M3R" AND ta="psoriasis"',
    'ta="IL-36" AND ta=psoriasis',
    'ta="IL-36" AND ta="pustular psoriasis"',
    'ta="generalized pustular psoriasis"',
    'ta="muscarinic antagonist" AND ta=psoriasis',
    'ta="muscarinic antagonist" AND ta="generalized pustular psoriasis"',
    'ta="muscarinic antagonist" AND ta="IL-36"',
    'ta="M3 selective" AND ta=psoriasis',
]
