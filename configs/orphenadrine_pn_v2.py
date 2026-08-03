# config_orphenadrine_PN.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-07] Orphenadrine × Prurigo Nodularis (PN)

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Orphenadrine 治療結節性癢疹 (Prurigo Nodularis, PN)"

# 藥物
DRUG_ALIASES = [
    "Orphenadrine",
    "Norflex",
    "Disipal",
    "Banflex",
    "Flexon",
    "Mephenamine",
    "Methyldiphenylhydramine",
]

# 作用機制
# Orphenadrine 是非選擇性 muscarinic antagonist，同時有 NMDA 拮抗、
# H1 antihistamine、NE reuptake inhibition — 多重機制都可能跟 PN 相關
MECHANISMS = [
    "muscarinic receptor antagonist",
    "muscarinic antagonist",
    "antimuscarinic",
    "anticholinergic",
    "NMDA receptor antagonist",
    "histamine H1 antagonist",
    "antihistamine",
    "norepinephrine reuptake inhibitor",
]

# 劑型 / 給藥途徑
# 原藥有 oral tablet 和 injection；PN repurposing 可能也考慮 topical
FORMULATIONS = [
    "oral",
    "tablet",
    "injection",
    "intramuscular",
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
TARGET_DRUG       = "Orphenadrine（非選擇性蕈毒鹼受體拮抗劑 / NMDA 拮抗劑 / H1 抗組織胺）"
TARGET_ROUTE      = "口服（Oral）或外用（Topical）"
TARGET_INDICATION = "結節性癢疹（Prurigo Nodularis, PN）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Orphenadrine 原核准適應症是骨骼肌鬆弛 / 肌肉痙攣 / 帕金森，要明確排除
SCREENING_IRRELEVANT_EXAMPLES = (
    "骨骼肌鬆弛（skeletal muscle relaxant / muscle spasm）、"
    "帕金森症（Parkinson's disease / extrapyramidal symptoms）、"
    "單純肌肉骨骼疼痛（musculoskeletal pain without itch/skin context）、"
    "單純 COPD 吸入型抗膽鹼（tiotropium / ipratropium）"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "orphenadrine",
    "norflex",
    "disipal",
    "banflex",
    "mephenamine",
    "methyldiphenylhydramine",
    "muscarinic antagonist",
    "antimuscarinic",
    "NMDA antagonist",
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "injection",
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
    "keratinocyte",            # muscarinic receptor 在角質細胞上的作用
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
    "NMDA",                    # Orphenadrine 獨有機制
    "glutamate",               # NMDA pathway
    "diphenhydramine",         # Orphenadrine 的母體化合物，同為 antihistamine
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # ── 核心：Orphenadrine × PN / pruritus ───────────────────
    'ta=orphenadrine AND ta="prurigo nodularis"',
    'ta=orphenadrine AND ta=pruritus',
    'ta=orphenadrine AND ta=prurigo',
    'ta=orphenadrine AND ta=itch',
    'ta=orphenadrine AND ta=skin',
    'ta=orphenadrine AND ta=dermatitis',

    # ── 機制角度：muscarinic / anticholinergic × skin / itch ─
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

    # ── Orphenadrine 獨有機制：NMDA × itch / PN ─────────────
    'ta="NMDA antagonist" AND ta=pruritus',
    'ta="NMDA antagonist" AND ta="prurigo nodularis"',
    'ta="NMDA receptor" AND ta=pruritus',
    'ta="NMDA receptor" AND ta=itch',
    'ta=glutamate AND ta=pruritus AND ta=skin',

    # ── Orphenadrine 獨有機制：antihistamine × PN ────────────
    'ta=antihistamine AND ta="prurigo nodularis"',
    'ta="histamine receptor" AND ta="prurigo nodularis"',
    'ta=diphenhydramine AND ta=pruritus',
    'ta=diphenhydramine AND ta="prurigo nodularis"',

    # ── Sodium channel × itch（orphenadrine 的差異化機制角度）──
    'ta="Nav1.7" AND ta="itch"',
    'ta="Nav1.7" AND ta="pruritus"',
    'ta="sodium channel" AND ta="pruritus"',
    'ta="sodium channel" AND ta="antipruritic"',

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
    'ta=darifenacin AND ta=pruritus',

    # ── 同類 antimuscarinic 藥物 × skin ──────────────────────
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
