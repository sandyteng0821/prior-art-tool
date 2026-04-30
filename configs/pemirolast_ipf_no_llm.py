# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2025-04] Validation run: Pemirolast × IPF
# Previous: Roflumilast × SCA

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Pemirolast 吸入劑治療特發性肺纖維化 (IPF)"

# 藥物
DRUG_ALIASES = [
    "Pemirolast",
    "BMY-26517",
    "TBX",
    "Alegysal",
]

# 作用機制
MECHANISMS = [
    "mast cell stabilizer",
    "TGF-beta inhibitor",
    "histamine H1 antagonist",
    "eosinophil inhibitor",
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "inhaled",
    "inhalation",
    "nebulizer",
    "oral",
    "pulmonary",
]

# 適應症
INDICATIONS = [
    "idiopathic pulmonary fibrosis",
    "IPF",
    "pulmonary fibrosis",
    "interstitial lung disease",
    "ILD",
]

# LLM 模型設定
SCREENING_MODEL = "gpt-4o-mini"   # 初篩（全部摘要）
ANALYSIS_MODEL  = "gpt-4o"        # 精讀（Medium / High 專利）

# 限流保守設定
MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

# 每次搜尋最多抓幾筆
FETCH_SIZE = 200

# Claims 截斷字元數（避免 token 爆炸）
CLAIMS_MAX_CHARS = 3000

# LLM 開關：False = 免費規則評分，True = LLM 分析
USE_LLM = False  # 先用規則模式驗證搜尋結果正確性，確認後再開 LLM

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2024"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Pemirolast（肥大細胞穩定劑 / TGF-beta 抑制劑）"
TARGET_ROUTE      = "吸入劑（Inhalation / Nebulizer）"
TARGET_INDICATION = "特發性肺纖維化（Idiopathic Pulmonary Fibrosis, IPF）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Pemirolast 原本核准適應症是眼科和氣喘，要明確排除
SCREENING_IRRELEVANT_EXAMPLES = "眼科過敏（ophthalmic / allergic conjunctivitis）、單純氣喘無肺纖維化、皮膚科"

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "pemirolast",
    "bmy-26517",
    "tbx",
    "alegysal",
    "mast cell stabilizer",
    "tgf-beta inhibitor",
]
RULE_ROUTE_KEYWORDS = [
    "inhaled",
    "inhalation",
    "nebulizer",
    "pulmonary delivery",
    "intratracheal",
]
RULE_INDICATION_KEYWORDS = [
    "pulmonary fibrosis",
    "ipf",
    "idiopathic pulmonary fibrosis",
    "interstitial lung disease",
    "ild",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "fibrosis",
    "collagen",
    "tgf-beta",
    "bleomycin",       # 動物模型關鍵字
    "nintedanib",      # 競爭藥物，有共現就值得注意
    "pirfenidone",     # 競爭藥物
    "eosinophil",
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # 同機制不同藥名（Cromolyn 是最近的競爭者）
    'ta="mast cell stabilizer" AND ta="pulmonary fibrosis"',
    # 疾病角度全掃，不限藥物
    'ta="idiopathic pulmonary fibrosis"',
    # 同事討論裡點名的競爭專利
    'ta=cromolyn AND ta="pulmonary fibrosis"',
]