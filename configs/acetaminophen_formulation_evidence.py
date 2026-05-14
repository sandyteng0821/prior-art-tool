# configs/acetaminophen_formulation_evidence.py

TARGET_PRODUCT = "Acetaminophen 配方佐證搜尋"

DRUG_ALIASES = [
    "Acetaminophen",
    "Paracetamol",
    "Tylenol",
    "APAP",
]

MECHANISMS = [
    "analgesic",
    "antipyretic",
    "COX inhibitor",
    "cyclooxygenase",
]

FORMULATIONS = [
    "tablet",
    "capsule",
    "oral",
    "formulation",
    "suspension",
    "granule",
    "extended release",
    "modified release",
]

INDICATIONS = []  # 不限適應症，專注配方

SCREENING_MODEL = "gpt-4o-mini"
ANALYSIS_MODEL  = "gpt-4o"
MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2
FETCH_SIZE = 100  # 先抓 100 筆看品質
CLAIMS_MAX_CHARS = 3000
USE_LLM = False  # 先 rule mode 確認搜到的東西對不對

SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2024"

TARGET_DRUG       = "Acetaminophen / Paracetamol（解熱鎮痛藥）"
TARGET_ROUTE      = "口服（tablet / capsule / suspension）"
TARGET_INDICATION = "配方佐證（不限適應症）"

SCREENING_IRRELEVANT_EXAMPLES = "純合成路徑、代謝毒性研究、非配方相關"

RULE_DRUG_KEYWORDS = ["acetaminophen", "paracetamol", "tylenol", "apap"]
RULE_ROUTE_KEYWORDS = ["tablet", "capsule", "oral", "formulation", "suspension", "granule"]
RULE_INDICATION_KEYWORDS = []
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "excipient", "diluent", "binder", "disintegrant",
    "microcrystalline cellulose", "polyethylene glycol",
    "starch", "coating", "modified release", "extended release",
]

CUSTOM_QUERIES = [
    'ta=Acetaminophen AND ta=formulation',
    'ta=Acetaminophen AND ta=tablet',
    'ta=Acetaminophen AND ta=capsule',
    'ta=Paracetamol AND ta=formulation',
    'ta=Paracetamol AND ta=tablet',
]
