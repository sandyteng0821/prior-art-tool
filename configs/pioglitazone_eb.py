# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-07] New commission: Pioglitazone × Epidermolysis Bullosa

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Pioglitazone_口服治療表皮溶解水疱症_(EB)"

# 藥物
DRUG_ALIASES = [
    "Pioglitazone",
    "AD-4833",
    "U-72107",
    "Actos",
    "thiazolidinedione",   # 藥理類名，專利常以類名出現
]

# 作用機制
MECHANISMS = [
    "PPARgamma agonist",
    "PPAR gamma",
    "PPARγ",
    "peroxisome proliferator-activated receptor gamma",
    "thiazolidinedione",
    "TGF-beta inhibitor",
    "TGF-β",
    "anti-fibrotic",
    "antifibrotic",
    "collagen regulation",
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "oral",
    "tablet",
    "topical",         # EB 外用可能性
    "cream",
    "ointment",
    "transdermal",
]

# 適應症
INDICATIONS = [
    "epidermolysis bullosa",
    "EB",
    "dystrophic epidermolysis bullosa",
    "DEB",
    "RDEB",
    "DDEB",
    "recessive dystrophic epidermolysis bullosa",
    "junctional epidermolysis bullosa",
    "JEB",
    "epidermolysis bullosa simplex",
    "EBS",
    "skin fragility",
    "skin blistering",
    "COL7A1",
    "collagen VII",
    "collagen type VII",
    "anchoring fibril",
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
USE_LLM = False  # 先用規則模式驗證搜尋結果正確性，確認後再開 LLM

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Pioglitazone（PPARγ 促效劑 / 抗纖維化）"
TARGET_ROUTE      = "口服（Oral tablet）；外用（Topical）亦列入監測"
TARGET_INDICATION = "表皮溶解水疱症（Epidermolysis Bullosa, EB）— 特別關注 dystrophic EB (DEB/RDEB)"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Pioglitazone 原核准適應症是糖尿病，要明確排除純糖尿病專利
SCREENING_IRRELEVANT_EXAMPLES = (
    "單純第二型糖尿病治療（type 2 diabetes without fibrosis/skin context）、"
    "單純 NASH/NAFLD 無皮膚纖維化關聯、"
    "單純心血管疾病、"
    "單純膀胱癌風險評估、"
    "眼科糖尿病視網膜病變"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "pioglitazone",
    "ad-4833",
    "u-72107",
    "actos",
    "thiazolidinedione",
    "ppargamma",
    "ppar gamma",
    "pparγ",
    "glitazone",
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "topical",
    "cream",
    "ointment",
    "transdermal",
    "dermal",
    "cutaneous",
    "skin delivery",
]
RULE_INDICATION_KEYWORDS = [
    "epidermolysis bullosa",
    "dystrophic epidermolysis bullosa",
    "rdeb",
    "ddeb",
    "junctional epidermolysis bullosa",
    "col7a1",
    "collagen vii",
    "collagen type vii",
    "anchoring fibril",
    "skin blistering",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "fibrosis",
    "skin fibrosis",
    "dermal fibrosis",
    "wound healing",
    "scarring",
    "tgf-beta",
    "tgf-β",
    "collagen",
    "keratinocyte",
    "basement membrane",
    "squamous cell carcinoma",  # RDEB 晚期併發症，出現即高度相關
    "mitten deformity",         # RDEB 特有手指融合
    "pseudosyndactyly",         # 同上
    "losartan",                 # 競爭藥物——EB 纖維化研究
    "rigosertib",               # EB 相關臨床試驗藥物
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # ── 藥物 × 適應症（核心交叉）─────────────────────────────
    'ta=pioglitazone AND ta="epidermolysis bullosa"',
    'ta=pioglitazone AND ta="skin fibrosis"',
    'ta=pioglitazone AND ta="dermal fibrosis"',
    'ta=pioglitazone AND ta="wound healing"',
    'ta=pioglitazone AND ta="skin blistering"',

    # ── 機制 × 適應症（PPARγ 在 EB 的角色）──────────────────
    'ta="PPARgamma" AND ta="epidermolysis bullosa"',
    'ta="PPAR gamma" AND ta="epidermolysis bullosa"',
    'ta="peroxisome proliferator" AND ta="epidermolysis bullosa"',
    'ta=thiazolidinedione AND ta="epidermolysis bullosa"',
    'ta="PPARgamma" AND ta="skin fibrosis"',
    'ta="PPAR gamma" AND ta="skin fibrosis"',
    'ta=thiazolidinedione AND ta="skin fibrosis"',
    'ta=thiazolidinedione AND ta="wound healing"',

    # ── 適應症全掃（不限藥物）────────────────────────────────
    'ta="epidermolysis bullosa"',
    'ta="dystrophic epidermolysis bullosa"',
    'ta="COL7A1"',
    'ta="collagen VII" AND ta="skin"',

    # ── 藥物 × 機制交叉（抗纖維化角度）──────────────────────
    'ta=pioglitazone AND ta="anti-fibrotic"',
    'ta=pioglitazone AND ta="antifibrotic"',
    'ta=pioglitazone AND ta="TGF-beta"',
    'ta=pioglitazone AND ta="collagen"',

    # ── 競爭藥物（EB 纖維化領域）────────────────────────────
    'ta=losartan AND ta="epidermolysis bullosa"',
    'ta=losartan AND ta="skin fibrosis"',

    # ── 同類藥物 × EB（其他 glitazone 是否已被探索）─────────
    'ta=rosiglitazone AND ta="epidermolysis bullosa"',
    'ta=rosiglitazone AND ta="skin fibrosis"',
    'ta=troglitazone AND ta="skin fibrosis"',

    # ── EB × 特定治療策略（監測競爭 landscape）──────────────
    'ta="epidermolysis bullosa" AND ta="gene therapy"',
    'ta="epidermolysis bullosa" AND ta="collagen VII" AND ta="treatment"',
]
