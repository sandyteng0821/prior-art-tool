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

# 每次搜尋最多抓幾筆（Strategy G 有 200 筆，設 200 確保全部撈到）
FETCH_SIZE = 200

# Claims 截斷字元數（避免 token 爆炸）
CLAIMS_MAX_CHARS = 3000

# LLM 開關：False = 免費規則評分，True = LLM 分析
USE_LLM = False

# ── 搜尋過濾條件（新增） ──────────────────────────────────────────────────────
# True = 只撈 granted patents（B1/B2），過濾掉 A1 申請案
SEARCH_ONLY_GRANTED = True

# EPO CQL 年份範圍，太新的 A1 資料不完整
SEARCH_YEAR_RANGE = "2000 2024"