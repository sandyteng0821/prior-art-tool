# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Roflumilast 鼻噴劑治療小腦萎縮症 (SCA)"

# 藥物
DRUG_ALIASES = [
    "Roflumilast",
    "Daliresp",
    "Daxas",
    "B9302-107",
]

# 作用機制
MECHANISMS = [
    "PDE4 inhibitor",
    "phosphodiesterase 4",
    "cAMP enhancer",
    "neuroprotective",
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "nasal",
    "intranasal",
    "nose-to-brain",
    "nasal spray",
    "transmucosal",
]

# 適應症
INDICATIONS = [
    "spinocerebellar ataxia",
    "SCA",
    "cerebellar ataxia",
    "cerebellar degeneration",
    "Machado-Joseph disease",
]

# LLM 模型設定
SCREENING_MODEL = "gpt-4o-mini"   # 初篩（全部摘要）
ANALYSIS_MODEL  = "gpt-4o"        # 精讀（Medium / High 專利）

# 限流保守設定
MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

# 每次搜尋最多抓幾筆（Strategy G 有 200 筆，設 200 確保全部撈到）
FETCH_SIZE = 200

# Claims 截斷字元數（避免 token 爆炸）
CLAIMS_MAX_CHARS = 3000

# LLM 開關：False = 免費規則評分，True = LLM 分析
USE_LLM = True

# ── 搜尋過濾條件（新增） ──────────────────────────────────────────────────────
# True = 只撈 granted patents（B1/B2），過濾掉 A1 申請案
SEARCH_ONLY_GRANTED = False

# EPO CQL 年份範圍，太新的 A1 資料不完整
SEARCH_YEAR_RANGE = "2000 2024"

# ── 目標產品三要素（給 LLM prompt 和 Pydantic description 動態產生用）──────
TARGET_DRUG       = "Roflumilast（PDE4 抑制劑）"
TARGET_ROUTE      = "鼻噴劑（Nasal spray / Nose-to-brain）"
TARGET_INDICATION = "小腦萎縮症（Spinocerebellar Ataxia, SCA）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）─────────────────────────────────
SCREENING_IRRELEVANT_EXAMPLES = "純 COPD 口服、皮膚科外用、眼科"

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS       = ["roflumilast", "pde4", "phosphodiesterase 4", "pde-4"]
RULE_ROUTE_KEYWORDS      = ["nasal", "intranasal", "nose-to-brain", "nasal spray"]
RULE_INDICATION_KEYWORDS = ["spinocerebellar", "ataxia", "cerebellar", "sca", "machado-joseph"]
RULE_ADDITIONAL_INDICATION_KEYWORDS = ["neurodegenerat", "cerebellum", "purkinje", "cognitive", "neuroprotect"]

# ── 自定義搜尋字串（疾病特定，對應原本的 Strategy F/G）────────────────────────
CUSTOM_QUERIES = [
    'ta="cognitive impairment" AND ta="PDE4"',
    'ta=spinocerebellar',
]