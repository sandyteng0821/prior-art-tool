# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-05] txGNN repurposing validation: Apremilast × Psoriasis
# txGNN link prediction rank: No. 6448 (score: 5.06824259646237e-05)
# Hypothesis: high rank ≠ high score; prior art should be DENSE here
# because apremilast is ALREADY approved for psoriasis/PsA.
# Expected outcome: many High / Medium FTO risk patents → confirms low txGNN rank is rational.

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Apremilast 口服治療銀屑病 (Psoriasis) / 銀屑病關節炎 (PsA)"

# 藥物
DRUG_ALIASES = [
    "Apremilast",
    "CC-10004",
    "Otezla",
]

# 作用機制
MECHANISMS = [
    "PDE4 inhibitor",
    "phosphodiesterase 4 inhibitor",
    "cAMP elevation",
    "TNF-alpha inhibitor",
    "anti-inflammatory",
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "oral",
    "tablet",
    "film-coated tablet",
    "oral administration",
]

# 適應症
INDICATIONS = [
    "psoriasis",
    "plaque psoriasis",
    "pustular psoriasis",
    "generalized pustular psoriasis",
    "psoriatic arthritis",
    "PsA",
    "skin inflammation",
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
# 建議先 False 驗證搜尋結果量是否符合預期（應該很多），再開 LLM 精分析
USE_LLM = False

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Apremilast（PDE4 抑制劑，已核准藥物）"
TARGET_ROUTE      = "口服（Oral tablet）"
TARGET_INDICATION = "銀屑病（Psoriasis）/ 銀屑病關節炎（Psoriatic Arthritis）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Apremilast 也用於 Behçet's disease / 口腔潰瘍，但不是本次驗證重點
SCREENING_IRRELEVANT_EXAMPLES = (
    "Behçet's disease、口腔潰瘍（aphthous ulcer）、"
    "完全無 apremilast / CC-10004 / PDE4 inhibitor 相關的 general 抗炎專利"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "apremilast",
    "cc-10004",
    "otezla",
    "pde4 inhibitor",
    "phosphodiesterase 4 inhibitor",
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "oral administration",
    "film-coated",
    "oral dosage form",
]
RULE_INDICATION_KEYWORDS = [
    "psoriasis",
    "plaque psoriasis",
    "psoriatic arthritis",
    "psa",
    "pustular psoriasis",
    "generalized pustular psoriasis",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "skin inflammation",
    "keratinocyte",
    "tnf-alpha",
    "il-17",
    "il-23",
    "camp",
    "methotrexate",      # 競爭/比較藥物
    "cyclosporine",      # 競爭/比較藥物
    "biologic",
    "anti-tnf",
]

# ── 自定義搜尋字串（indication sweep + mechanism sweep）──────────────────────
CUSTOM_QUERIES = [
    # 直打藥名
    'ta=apremilast AND ta=psoriasis',
    # 機制角度掃 psoriasis
    'ta="PDE4 inhibitor" AND ta=psoriasis',
    'ta="phosphodiesterase 4" AND ta=psoriasis',
    # 推廣到 PsA
    'ta=apremilast AND ta="psoriatic arthritis"',
    # 膿皰型銀屑病（適應症擴充）
    'ta=apremilast AND ta="pustular psoriasis"',
    # Otezla 商標名
    'ta=otezla',
    # CC-10004（研發代號）
    'ta="CC-10004"',
    # 競爭格局：同適應症不同藥，看有無 formulation / method claim 包到 apremilast
    'ta="plaque psoriasis" AND ta="oral"',
]