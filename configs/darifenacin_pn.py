# config_darifenacin_PN.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-07] Darifenacin × Prurigo Nodularis (PN)

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Darifenacin 治療結節性癢疹 (Prurigo Nodularis, PN)"

# 藥物
DRUG_ALIASES = [
    "Darifenacin",
    "UK-88525",
    "UK88525",
    "CS-773",
    "Enablex",
    "Emselex",
]

# 作用機制
MECHANISMS = [
    "muscarinic receptor antagonist",
    "M3 receptor antagonist",
    "M3 muscarinic",
    "antimuscarinic",
    "anticholinergic",
    "muscarinic acetylcholine receptor",
]

# 劑型 / 給藥途徑
# Darifenacin 原本是口服 ER tablet；PN repurposing 可能也考慮 topical
FORMULATIONS = [
    "oral",
    "tablet",
    "extended release",
    "topical",
    "cream",
    "ointment",
    "transdermal",
]

# 適應症
INDICATIONS = [
    "prurigo nodularis",
    "prurigo",
    "nodular prurigo",
    "chronic pruritus",
    "pruritus",
    "itch",
    "antipruritic",
    "neurodermatitis",
    "lichen simplex",
    "chronic itch",
]

# LLM 模型設定
SCREENING_MODEL = "gpt-4o-mini"  # 初篩（全部摘要）
ANALYSIS_MODEL  = "gpt-4o"       # 精讀（Medium / High 專利）

# 限流保守設定
MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

# 每次搜尋最多抓幾筆
FETCH_SIZE = 200

# Claims 截斷字元數（避免 token 爆炸）
CLAIMS_MAX_CHARS = 3000

# LLM 開關：False = 免費規則評分，True = LLM 分析
USE_LLM = True  # 先用規則模式驗證搜尋結果正確性，確認後再開 LLM

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Darifenacin（選擇性 M3 蕈毒鹼受體拮抗劑）"
TARGET_ROUTE      = "口服（Oral extended-release）或外用（Topical）"
TARGET_INDICATION = "結節性癢疹（Prurigo Nodularis, PN）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Darifenacin 原核准適應症是膀胱過動症，要明確排除；
# 同時排除純眼科、純呼吸道（COPD 吸入用 antimuscarinic）等無皮膚相關性的專利
SCREENING_IRRELEVANT_EXAMPLES = (
    "膀胱過動症（overactive bladder / urinary incontinence）、"
    "單純 COPD 吸入型抗膽鹼（tiotropium / ipratropium）、"
    "青光眼（glaucoma）、"
    "純腸胃道蠕動（IBS motility without itch/skin context）"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "darifenacin",
    "uk-88525",
    "uk88525",
    "cs-773",
    "enablex",
    "emselex",
    "muscarinic antagonist",
    "m3 antagonist",
    "m3 receptor",
    "antimuscarinic",
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "extended release",
    "topical",
    "cream",
    "ointment",
    "dermal",
    "transdermal",
    "cutaneous",
    "skin",
]
RULE_INDICATION_KEYWORDS = [
    "prurigo nodularis",
    "prurigo",
    "chronic pruritus",
    "pruritus",
    "itch",
    "antipruritic",
    "nodular prurigo",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "neurodermatitis",
    "lichen simplex",
    "atopic dermatitis",       # 相關疾病，有共現值得注意
    "eczema",                  # 同上
    "keratinocyte",            # M3R 在角質細胞上的作用
    "acetylcholine",           # 膽鹼訊號與搔癢的關聯
    "substance P",             # PN 核心 neuropeptide
    "NK1R",                    # neurokinin 1 receptor
    "IL-31",                   # 搔癢核心細胞激素
    "IL-4",                    # Th2 / dupilumab 靶點
    "IL-13",                   # 同上
    "dupilumab",               # 競爭藥物（FDA approved for PN）
    "nemolizumab",             # 競爭藥物（FDA approved for PN 2024）
    "nerve growth factor",     # 神經增生與 PN
    "neuronal sensitization",  # PN 核心病理
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # ── 核心：Darifenacin × PN / pruritus ────────────────────
    'ta=darifenacin AND ta="prurigo nodularis"',
    'ta=darifenacin AND ta=pruritus',
    'ta=darifenacin AND ta=prurigo',
    'ta=darifenacin AND ta=itch',
    'ta=darifenacin AND ta=skin',
    'ta=darifenacin AND ta=dermatitis',

    # ── 機制角度：M3 muscarinic × skin / itch ────────────────
    'ta="muscarinic antagonist" AND ta=pruritus',
    'ta="muscarinic antagonist" AND ta="prurigo nodularis"',
    'ta="muscarinic receptor" AND ta=pruritus',
    'ta="muscarinic receptor" AND ta=itch',
    'ta="M3 receptor" AND ta=pruritus',
    'ta="M3 receptor" AND ta=skin',
    'ta=antimuscarinic AND ta=pruritus',
    'ta=antimuscarinic AND ta=itch',
    'ta=anticholinergic AND ta="prurigo nodularis"',
    'ta=anticholinergic AND ta=pruritus AND ta=skin',

    # ── 疾病角度全掃（不限藥物）───────────────────────────────
    'ta="prurigo nodularis"',
    'ta="prurigo nodularis" AND ta=treatment',
    'ta="chronic pruritus" AND ta="muscarinic"',
    'ta="chronic itch" AND ta="muscarinic"',

    # ── 競爭藥物 × PN（FTO 掃描）──────────────────────────────
    'ta=dupilumab AND ta="prurigo nodularis"',
    'ta=nemolizumab AND ta="prurigo nodularis"',
    'ta=nemolizumab AND ta=pruritus',
    'ta=vixarelimab AND ta="prurigo nodularis"',

    # ── 相同 antimuscarinic 類藥物 × skin ─────────────────────
    # 同類競爭者（other antimuscarinics repurposed for skin?）
    'ta=orphenadrine AND ta=pruritus',
    'ta=oxybutynin AND ta=pruritus',
    'ta=solifenacin AND ta=pruritus',
    'ta=tolterodine AND ta=pruritus',

    # ── Acetylcholine / cholinergic × PN pathway ─────────────
    'ta=acetylcholine AND ta="prurigo nodularis"',
    'ta="cholinergic" AND ta="prurigo nodularis"',
    'ta=acetylcholine AND ta=pruritus AND ta=skin',

    # ── Neuropeptide / neuroimmune × PN（機制交叉）────────────
    'ta="substance P" AND ta="prurigo nodularis"',
    'ta="NK1 receptor" AND ta="prurigo nodularis"',
    'ta="IL-31" AND ta="prurigo nodularis"',
    'ta="nerve growth factor" AND ta="prurigo nodularis"',
]
