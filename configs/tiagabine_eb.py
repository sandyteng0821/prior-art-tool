# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-07] New commission: Tiagabine × Epidermolysis Bullosa
# Tiagabine = selective GABA reuptake inhibitor (GAT-1), 原核准適應症 epilepsy

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Tiagabine_口服治療表皮溶解水疱症_(EB)"

# 藥物
DRUG_ALIASES = [
    "Tiagabine",
    "NO-328",           # Novo Nordisk 開發代號（主要）
    "NO-050328",        # 開發代號長格式
    "NO050328",         # 無連字號格式
    "NNC-05-0328",      # Novo Nordisk 內部編號
    "ABT-569",          # Abbott 代號
    "A-70569",          # Abbott 代號（另一格式）
    "CEP-6671",         # Cephalon 代號
    "Gabitril",         # Brand name
    "TGB",              # 常見縮寫
    "GABA reuptake inhibitor",  # 藥理類名
]

# 作用機制
MECHANISMS = [
    "GABA reuptake inhibitor",
    "GAT-1 inhibitor",
    "GABA transporter 1",
    "GABA transporter",
    "GABAergic",
    "gamma-aminobutyric acid",
    "GABA uptake",
    "nipecotic acid",       # Tiagabine 的化學母核
    "anticonvulsant",
    "anti-inflammatory",    # GABA 系統的皮膚抗發炎角色
]

# 劑型 / 給藥途徑
FORMULATIONS = [
    "oral",
    "tablet",
    "topical",          # EB 外用可能性（藥物再利用角度）
    "cream",
    "ointment",
    "transdermal",
]

# 適應症
INDICATIONS = [
    "epidermolysis bullosa",
    "EB",
    "epidermolysis bullosa simplex",
    "EBS",
    "junctional epidermolysis bullosa",
    "JEB",
    "dystrophic epidermolysis bullosa",
    "DEB",
    "recessive dystrophic epidermolysis bullosa",
    "RDEB",
    "dominant dystrophic epidermolysis bullosa",
    "DDEB",
    "Kindler syndrome",
    "skin fragility",
    "skin blistering",
    "COL7A1",
    "collagen VII",
    "collagen type VII",
    "anchoring fibril",
    "laminin 332",          # JEB 相關
    "keratin 5",            # EBS 相關
    "keratin 14",           # EBS 相關
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
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Tiagabine（選擇性 GABA 再攝取抑制劑 / GAT-1 inhibitor）"
TARGET_ROUTE      = "口服（Oral tablet）"
TARGET_INDICATION = "表皮溶解水疱症（Epidermolysis Bullosa, EB）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Tiagabine 原本核准適應症是 epilepsy，也有 off-label 用於焦慮和神經痛
SCREENING_IRRELEVANT_EXAMPLES = (
    "純癲癇治療無皮膚適應症（epilepsy / seizure / anticonvulsant without skin indication）、"
    "焦慮症（anxiety / GAD / panic disorder）、"
    "神經痛無 EB 相關（neuropathic pain without EB context）、"
    "睡眠障礙（insomnia / sleep）"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "tiagabine",
    "no-328",
    "no-050328",
    "no050328",
    "nnc-05-0328",
    "abt-569",
    "a-70569",
    "cep-6671",
    "gabitril",
    "gaba reuptake inhibitor",
    "gat-1 inhibitor",
    "gaba transporter 1",
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
    "skin application",
]
RULE_INDICATION_KEYWORDS = [
    "epidermolysis bullosa",
    "eb simplex",
    "ebs",
    "junctional epidermolysis",
    "jeb",
    "dystrophic epidermolysis",
    "deb",
    "rdeb",
    "ddeb",
    "col7a1",
    "collagen vii",
    "anchoring fibril",
    "skin blistering",
    "skin fragility",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "blister",
    "wound healing",
    "skin fibrosis",
    "dermal fibrosis",
    "collagen",
    "keratin",
    "basement membrane",
    "gaba",                 # GABA 在皮膚的角色
    "gabaergic",
    "gabaa receptor",
    "gabab receptor",
    "beremagene",           # 競爭藥物（B-VEC gene therapy）
    "oleogel",              # 競爭藥物（Filsuvez / birch triterpenes）
    "diacerein",            # 競爭藥物（EBS 臨床試驗中）
    "gentamicin",           # 競爭藥物（read-through therapy for JEB）
    "losartan",             # 競爭藥物（anti-TGF-β for RDEB fibrosis）
    "squamous cell carcinoma",  # RDEB 晚期併發症
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # ══════════════════════════════════════════════════════════════════════
    # 1. 藥物 × 適應症（核心交叉）
    # ══════════════════════════════════════════════════════════════════════
    'ta=tiagabine AND ta="epidermolysis bullosa"',
    'ta=tiagabine AND ta="skin blistering"',
    'ta=tiagabine AND ta="skin fibrosis"',
    'ta=tiagabine AND ta="wound healing"',
    'ta=tiagabine AND ta="dermal"',
    'ta=tiagabine AND ta="skin disease"',
    'ta=tiagabine AND ta="skin disorder"',
    'ta=tiagabine AND ta="skin inflammation"',

    # ══════════════════════════════════════════════════════════════════════
    # 2. 機制 × 適應症（GABA 系統在 EB / 皮膚的角色）
    # ══════════════════════════════════════════════════════════════════════
    'ta="GABA reuptake" AND ta="epidermolysis bullosa"',
    'ta="GABA reuptake" AND ta="skin"',
    'ta="GABA reuptake" AND ta="wound healing"',
    'ta="GABA reuptake" AND ta="dermal"',
    'ta="GAT-1" AND ta="skin"',
    'ta="GAT-1" AND ta="epidermolysis"',
    'ta="GABA transporter" AND ta="skin"',
    'ta="GABA transporter" AND ta="wound"',
    # GABAergic × skin（寬搜，抓 GABA 系統皮膚作用專利）
    'ta="gamma-aminobutyric acid" AND ta="skin"',
    'ta="gamma-aminobutyric acid" AND ta="wound healing"',
    'ta="gamma-aminobutyric acid" AND ta="epidermolysis"',
    'ta="GABA" AND ta="epidermolysis bullosa"',
    'ta="GABA" AND ta="skin fibrosis"',
    'ta="GABA" AND ta="skin blistering"',
    # GABA receptor × skin inflammation（baclofen 文獻暗示 GABA-R 有抗發炎角色）
    'ta="GABA receptor" AND ta="skin inflammation"',
    'ta="GABA receptor" AND ta="wound healing"',

    # ══════════════════════════════════════════════════════════════════════
    # 3. 適應症全掃（不限藥物，建立 landscape）
    # ══════════════════════════════════════════════════════════════════════
    'ta="epidermolysis bullosa"',
    'ta="dystrophic epidermolysis bullosa"',
    'ta="junctional epidermolysis bullosa"',
    'ta="epidermolysis bullosa simplex"',
    'ta="COL7A1"',
    'ta="collagen VII" AND ta="skin"',
    'ta="anchoring fibril"',
    'ta="laminin 332" AND ta="skin"',

    # ══════════════════════════════════════════════════════════════════════
    # 4. 競爭藥物（EB 領域核心競品）
    # ══════════════════════════════════════════════════════════════════════
    # Gene therapy（最大競爭路徑）
    'ta="beremagene" AND ta="epidermolysis"',
    'ta="COL7A1" AND ta="gene therapy"',
    'ta="epidermolysis bullosa" AND ta="gene therapy"',
    # Small molecule competitors
    'ta=losartan AND ta="epidermolysis bullosa"',
    'ta=losartan AND ta="skin fibrosis"',
    'ta=diacerein AND ta="epidermolysis bullosa"',
    'ta=gentamicin AND ta="epidermolysis bullosa"',
    # Birch triterpenes / Oleogel（Filsuvez）
    'ta="birch" AND ta="epidermolysis bullosa"',
    'ta="oleogel" AND ta="epidermolysis"',

    # ══════════════════════════════════════════════════════════════════════
    # 5. 同類藥物（其他 GABA 相關藥物 × 皮膚 / EB）
    # ══════════════════════════════════════════════════════════════════════
    'ta=gabapentin AND ta="epidermolysis bullosa"',
    'ta=pregabalin AND ta="epidermolysis bullosa"',
    'ta=vigabatrin AND ta="skin"',
    'ta=baclofen AND ta="skin inflammation"',
    'ta=baclofen AND ta="wound healing"',
    # Nipecotic acid derivatives（Tiagabine 母核）
    'ta="nipecotic acid" AND ta="skin"',

    # ══════════════════════════════════════════════════════════════════════
    # 6. EB × 治療策略交叉（監測 landscape）
    # ══════════════════════════════════════════════════════════════════════
    'ta="epidermolysis bullosa" AND ta="anti-inflammatory"',
    'ta="epidermolysis bullosa" AND ta="anti-fibrotic"',
    'ta="epidermolysis bullosa" AND ta="wound healing"',
    'ta="epidermolysis bullosa" AND ta="pruritus"',
    'ta="epidermolysis bullosa" AND ta="pain"',
]
