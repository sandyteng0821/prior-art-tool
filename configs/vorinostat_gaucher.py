# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Vorinostat 治療 Gaucher disease"
# 藥物
DRUG_ALIASES = [
    "Vorinostat",
    "SAHA",
    "Zolinza",
    "suberoylanilide hydroxamic acid",
]
# 作用機制
MECHANISMS = [
    "HDAC inhibitor",
    "histone deacetylase inhibitor",
    "epigenetic modulation",
    "lysosomal function enhancement",
    "protein folding correction",
]
# 劑型 / 給藥途徑
FORMULATIONS = [
    "oral",
    "capsule",
    "systemic",
]
# 適應症
INDICATIONS = [
    "Gaucher disease",
    "Gaucher",
    "lysosomal storage disorder",
    "glucocerebrosidase deficiency",
    "GBA deficiency",
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
SEARCH_YEAR_RANGE = "2000 2024"
# ── 目標產品三要素 ───────────────────────────────────────────────────
TARGET_DRUG       = "Vorinostat（HDAC 抑制劑）"
TARGET_ROUTE      = "口服（oral capsule, systemic）"
TARGET_INDICATION = "Gaucher disease（溶酶體儲積症）"
# ── 初篩排除範例 ─────────────────────────────────────────────────────
SCREENING_IRRELEVANT_EXAMPLES = "皮膚T細胞淋巴瘤、癌症單純抗腫瘤用途、非溶酶體疾病"
# ── 規則評分關鍵字 ───────────────────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "vorinostat",
    "saha",
    "hdac inhibitor",
    "histone deacetylase",
]
RULE_ROUTE_KEYWORDS = [
    "oral",
    "capsule",
    "systemic",
]
RULE_INDICATION_KEYWORDS = [
    "gaucher",
    "gaucher disease",
    "glucocerebrosidase",
    "gba",
    "lysosomal storage",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "lysosomal",
    "protein misfolding",
    "enzyme deficiency",
    "lipid storage",
    "macrophage",
]
# ── 自定義搜尋字串（針對 Gaucher / LSD）───────────────────────────────
CUSTOM_QUERIES = [
    'ta="Gaucher disease" AND ta="HDAC inhibitor"',
    'ta="glucocerebrosidase" AND ta=vorinostat',
    'ta="lysosomal storage disorder" AND ta="histone deacetylase"',
]