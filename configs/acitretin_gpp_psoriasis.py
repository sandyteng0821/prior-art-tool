# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-05] Traditional medicine baseline: Acitretin × GPP / Psoriasis
# Role: Traditional medicine — First priority baseline（對照組）

TARGET_PRODUCT = "Acitretin 治療廣泛性膿皰型銀屑病 (GPP) / 銀屑病 (Psoriasis)"

DRUG_ALIASES = [
    "Acitretin",
    "Soriatane",
    "Neotigason",
    "retinoid",
    "aromatic retinoid",
]

MECHANISMS = [
    "retinoid",
    "retinoic acid receptor agonist",
    "RAR agonist",
    "vitamin A derivative",
    "keratinocyte differentiation",
    "anti-proliferative",
]

FORMULATIONS = [
    "oral",
    "capsule",
    "tablet",
    "oral administration",
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
USE_LLM = False

SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

TARGET_DRUG       = "Acitretin（Retinoid / RAR agonist，已核准傳統藥物）"
TARGET_ROUTE      = "口服（Oral capsule）"
TARGET_INDICATION = "GPP（Generalized Pustular Psoriasis，IL-36 pathway，罕見且嚴重）及 Psoriasis（IL-17/IL-23 pathway，慢性皮膚病），GPP 為優先適應症"

SCREENING_IRRELEVANT_EXAMPLES = (
    "魚鱗病（ichthyosis）、掌蹠角化症（keratoderma）、"
    "完全無 psoriasis / GPP 相關的 general retinoid 專利"
)

RULE_DRUG_KEYWORDS = [
    "acitretin",
    "soriatane",
    "neotigason",
    "retinoid",
    "retinoic acid receptor",
    "rar agonist",
]

RULE_ROUTE_KEYWORDS = [
    "oral",
    "capsule",
    "tablet",
    "oral administration",
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
    "retinol",
    "vitamin a",
    "etretinate",     # 前驅藥,
    "isotretinoin",   # 同族競爭物,
]

CUSTOM_QUERIES = [
    'ta=acitretin AND ta=psoriasis',
    'ta=acitretin AND ta="pustular psoriasis"',
    'ta=acitretin AND ta="generalized pustular psoriasis"',
    'ta=soriatane AND ta=psoriasis',
    'ta=neotigason AND ta=psoriasis',
    'ta="retinoid" AND ta="pustular psoriasis"',
    'ta="retinoid" AND ta="generalized pustular psoriasis"',
    'ta=acitretin AND ta="plaque psoriasis"',
    'ta=acitretin AND ta=oral',
    'ta="aromatic retinoid" AND ta=psoriasis',
    'ta="IL-36" AND ta=psoriasis',
    'ta="IL-36" AND ta="pustular psoriasis"',
    'ta="generalized pustular psoriasis"',
]