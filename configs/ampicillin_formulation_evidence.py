# configs/ampicillin_formulation_evidence.py

TARGET_PRODUCT = "Ampicillin 配方佐證搜尋"

DRUG_ALIASES = [
    "Ampicillin",
    "Principen",
    "Omnipen",
]

MECHANISMS = [
    "beta-lactam",
    "penicillin",
    "cell wall synthesis inhibitor",
]

FORMULATIONS = [
    "tablet",
    "capsule",
    "oral",
    "formulation",
    "powder",
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

TARGET_DRUG       = "Ampicillin（beta-lactam 抗生素）"
TARGET_ROUTE      = "口服（tablet / capsule）"
TARGET_INDICATION = "配方佐證（不限適應症）"

SCREENING_IRRELEVANT_EXAMPLES = "純合成路徑、抗藥性機轉研究、非配方相關"

RULE_DRUG_KEYWORDS = ["ampicillin", "principen", "omnipen"]
RULE_ROUTE_KEYWORDS = ["tablet", "capsule", "oral", "formulation", "powder"]
RULE_INDICATION_KEYWORDS = []
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "excipient", "diluent", "binder", "disintegrant",
    "microcrystalline cellulose", "polyethylene glycol",
]

CUSTOM_QUERIES = [
    'ta=Ampicillin AND ta=formulation',
    'ta=Ampicillin AND ta=tablet',
    'ta=Ampicillin AND ta=capsule',
]