# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Bromocriptine 治療 Spinal muscular atrophy"
# 藥物
DRUG_ALIASES = [
    "Bromocriptine",
    "Bromocriptine mesylate",
    "Parlodel",
    "2-bromo-alpha-ergocryptine",
    "2-bromoergocriptine",
]
# 作用機制
MECHANISMS = [
    "dopamine D2 receptor agonist",
    "dopamine receptor agonist",
    "prolactin inhibition",
    "motor neuron survival",
    "neuroprotective effect",
]
# 劑型 / 給藥途徑
FORMULATIONS = [
    "oral",
    "tablet",
    "capsule",
    "systemic",
]
# 適應症
INDICATIONS = [
    "Spinal muscular atrophy",
    "SMA",
    "survival motor neuron deficiency",
    "SMN deficiency",
    "SMN1 mutation",
    "motor neuron disease",
    "neuromuscular disease",
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
USE_LLM = True
# ── 搜尋過濾條件 ──────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"
# ── 目標產品三要素 ───────────────────────────────────────────────────
TARGET_DRUG       = "Bromocriptine（dopamine D2 receptor agonist）"
TARGET_ROUTE      = "口服（oral tablet / capsule, systemic）"
TARGET_INDICATION = "Spinal muscular atrophy（SMA，脊髓性肌肉萎縮症）"
# ── 初篩排除範例 ─────────────────────────────────────────────────────
SCREENING_IRRELEVANT_EXAMPLES = (
    "高泌乳素血症、帕金森氏症、肢端肥大症、糖尿病、"
    "單純多巴胺受體用途、非SMA之一般神經疾病、"
    "動物用藥（veterinary / porcine / swine）、"
    "眼科（ophthalmic / VEGF）、"
    "2-bromolysergic acid 合成（LSD precursor synthesis）"
)
# ── 規則評分關鍵字 ───────────────────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "bromocriptine",
    "bromocriptine mesylate",
    "parlodel",
    "2-bromoergocriptine",
    "2-bromo-alpha-ergocryptine",
    "dopamine d2 receptor agonist",    # 機制
    "dopamine receptor agonist",       # 機制（寬）
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "capsule",
    "systemic",
]
RULE_INDICATION_KEYWORDS = [
    "spinal muscular atrophy",
    "sma",
    "survival motor neuron",
    "smn",
    "smn1",
    "smn2",
    "motor neuron",
    "neuromuscular",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "motor neuron disease",
    "neurodegenerative disease",
    "neuromuscular disease",
    "muscle atrophy",
    "motor function",
    "neuron survival",
    "SMN protein",
]
# ── 自定義搜尋字串（針對 Bromocriptine / SMA）───────────────────────────────
CUSTOM_QUERIES = [
    'ta="Spinal muscular atrophy" AND ta=bromocriptine',
    'ta="SMA" AND ta=bromocriptine',
    'ta="survival motor neuron" AND ta=bromocriptine',
    'ta="SMN" AND ta=bromocriptine',
    'ta="SMN protein" AND ta="dopamine agonist"',
    'ta="Spinal muscular atrophy" AND ta="dopamine D2 receptor agonist"',
    'ta="motor neuron disease" AND ta=bromocriptine',

    # ── 追加：Indication 全掃（不限藥物）─────────────────────
    'ta="spinal muscular atrophy" AND ta="oral"',
    'ta="spinal muscular atrophy" AND ta="tablet"',
    'ta="spinal muscular atrophy" AND ta="neuroprotect"',
    'ta="survival motor neuron" AND ta="oral"',

    # ── 追加：機制 × Indication ──────────────────────────────
    'ta="dopamine agonist" AND ta="spinal muscular atrophy"',
    'ta="dopamine agonist" AND ta="motor neuron"',
    'ta="dopamine agonist" AND ta="neuromuscular"',

    # ── 追加：競爭藥物交叉 ───────────────────────────────────
    'ta=risdiplam AND ta="spinal muscular atrophy"',
    'ta=risdiplam AND ta="oral"',
    'ta=nusinersen AND ta="oral"',
    'ta="SMN2" AND ta="splicing"',

    # ── 追加：Bromocriptine × 機制擴展 ──────────────────────
    'ta=bromocriptine AND ta="neuroprotect"',
    'ta=bromocriptine AND ta="neurodegenerative"',
    'ta=bromocriptine AND ta="neuromuscular"',

    # ── 追加：寬 indication query（撈 description 裡提到 drug 的專利）───
    'ta="spinal muscular atrophy" AND ta="pharmaceutical composition"',
    'ta="SMN protein" AND ta="pharmaceutical"',
    'ta="spinal muscular atrophy" AND ta="combination"',
]
