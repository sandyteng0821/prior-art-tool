# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-05] txGNN repurposing validation: Maxacalcitol × Psoriasis
# txGNN link prediction rank: No. 22 (high score)
# Hypothesis: prior art should be SPARSE for psoriasis use case,
# because maxacalcitol is primarily approved for renal osteodystrophy,
# not psoriasis. Low FTO density → confirms high txGNN rank is rational.

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Maxacalcitol 外用治療銀屑病 (Psoriasis)"

# 藥物
DRUG_ALIASES = [
    "Maxacalcitol",
    "22-oxacalcitriol",
    "22-oxa-1,25-dihydroxyvitamin D3",
    "OCT",
    "Oxarol",
]

# 作用機制
MECHANISMS = [
    "vitamin D3 analogue",
    "vitamin D receptor agonist",
    "VDR agonist",
    "calcipotriol analogue",
    "keratinocyte differentiation",
    "anti-proliferative",
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "topical",
    "ointment",
    "cream",
    "cutaneous",
    "dermal",
    "transdermal",
    "topical application",
]

# 適應症
INDICATIONS = [
    "psoriasis",
    "plaque psoriasis",
    "skin disorder",
    "hyperproliferative skin",
    "dermatosis",
    "psoriatic lesion",
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
# 建議先 False 確認 prior art 量少（符合假設），再開 LLM 精分析
USE_LLM = False

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "1990 2030"   # maxacalcitol 合成早，往前拉

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Maxacalcitol（22-oxacalcitriol，Vitamin D3 類似物）"
TARGET_ROUTE      = "外用（Topical ointment / cream）"
TARGET_INDICATION = "銀屑病（Psoriasis）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Maxacalcitol 原本核准適應症是腎性骨病（renal osteodystrophy），要明確排除
SCREENING_IRRELEVANT_EXAMPLES = (
    "腎性骨病（renal osteodystrophy）、次級副甲狀腺機能亢進（secondary hyperparathyroidism）、"
    "骨質疏鬆（osteoporosis）、沒有 psoriasis 相關的 general vitamin D 代謝專利"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "maxacalcitol",
    "22-oxacalcitriol",
    "22-oxa-1,25-dihydroxyvitamin",
    "oxarol",
    "oct",
    "vitamin d3 analogue",
    "vdr agonist",
]
RULE_ROUTE_KEYWORDS = [
    "topical",
    "ointment",
    "cream",
    "cutaneous",
    "dermal",
    "transdermal",
    "topical application",
]
RULE_INDICATION_KEYWORDS = [
    "psoriasis",
    "plaque psoriasis",
    "psoriatic",
    "skin disorder",
    "hyperproliferative",
    "dermatosis",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "keratinocyte",
    "differentiation",
    "proliferation",
    "calcipotriol",      # 最接近的競爭類似物（Daivonex）
    "calcitriol",        # 上游母化合物
    "tacalcitol",        # 同族競爭物
    "il-17",
    "il-23",
    "tnf-alpha",
    "t-cell",
]

# ── 自定義搜尋字串（indication sweep + analogue sweep）───────────────────────
CUSTOM_QUERIES = [
    # 直打藥名
    'ta=maxacalcitol AND ta=psoriasis',
    # 化學名
    'ta="22-oxacalcitriol" AND ta=psoriasis',
    # 機制角度掃 psoriasis（同族競爭物也掃，看有無涵蓋性 claim）
    'ta="vitamin D" AND ta=psoriasis AND ta=topical',
    'ta=calcipotriol AND ta=psoriasis',          # 最強競爭對手
    'ta=calcitriol AND ta=psoriasis AND ta=topical',
    'ta=tacalcitol AND ta=psoriasis',
    # 不限藥名，掃 VDR agonist × psoriasis
    'ta="vitamin D receptor" AND ta=psoriasis',
    # 外用劑型角度
    'ta=maxacalcitol AND ta=ointment',
    # Oxarol 商標名（日本核准品牌）
    'ta=oxarol',
]