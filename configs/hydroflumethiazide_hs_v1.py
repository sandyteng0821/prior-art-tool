# config.py  →  configs/hydroflumethiazide_hs_v1.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-10] HS drug-repurposing patentability screen — Task 組 2 / 3（優先序 2）
# Indication: Hidradenitis Suppurativa (HS) × Hydroflumethiazide
#
# ⚠ SOP 定位：SOP1「初步構想階段」快速篩選。本工具只覆蓋 SOP1 §3.3「專利資料庫」面；
#   NPL（PubMed / ClinicalTrials.gov）與 Orange Book 面需另行手動檢索。
# ⚠ 檢索性質：patentability（非 FTO）；前案含過期/放棄/申請中 → SEARCH_ONLY_GRANTED=False。
# ⚠ 已知限制：`ta=` 只搜 title+abstract；drug-list/Markush、description-only 提及抓不到
#   （Bug Y）。近零命中 ≠ 綠燈，需 Google Patents fulltext / 專家 ID import 補強。
# ⚠ 本檔未經 EPO probe 驗證，v1 draft。先跑 rule-mode 再開 LLM。
#
# 藥物識別（供查核，非搜尋字串）：
#   Hydroflumethiazide  CAS 135-09-1 · DrugBank DB00774 · ATC C03AA02 · 1959 approved
#   status: approved + withdrawn（已下市）· thiazide 利尿劑
#   target: thiazide-sensitive Na-Cl cotransporter (SLC12A3) inhibitor；兼弱碳酸酐酶抑制
#   NSC-44627 · 化學同義：dihydroflumethiazide / trifluoromethylhydrothiazide
#   註：勿與 bendroflumethiazide（不同藥）混淆
#   HS 脈絡：spironolactone（利尿劑/抗雄）與 metformin 已為 HS off-label →
#            「利尿劑/thiazide × HS」有脈絡，機制交集查詢命中具進步性意義

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Hydroflumethiazide 用於治療化膿性汗腺炎 (Hidradenitis Suppurativa, HS / Acne Inversa) — 藥物再利用第二醫藥用途構想"

# 藥物
DRUG_ALIASES = [
    "Hydroflumethiazide",
    "Dihydroflumethiazide",   # 化學同義詞
    "Saluron",
    "Diucardin",
    "Hydrenox",
    "NSC-44627",
]

# 作用機制
MECHANISMS = [
    "thiazide diuretic",
    "diuretic",
    "sodium-chloride cotransporter",
    "sodium chloride symporter",
    "SLC12A3",
    "carbonic anhydrase inhibitor",
]

# 劑型 / 給藥途徑（HS 治療常見途徑；repurposing 途徑未定）
FORMULATIONS = [
    "topical",
    "oral",
    "systemic",
    "subcutaneous",
    "intralesional",
    "cream",
    "gel",
]

# 適應症（HS 同義詞）
INDICATIONS = [
    "hidradenitis suppurativa",
    "hidradenitis",
    "acne inversa",
    "Verneuil disease",
    "suppurative hidradenitis",
    "apocrine acne",
]

# LLM 模型設定
SCREENING_MODEL = "gpt-4o-mini"
ANALYSIS_MODEL  = "gpt-4o"

# 限流保守設定
MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

# 每次搜尋最多抓幾筆
FETCH_SIZE = 200

# Claims 截斷字元數
CLAIMS_MAX_CHARS = 3000

# LLM 開關：SOP1 第一輪先用規則模式驗證命中合理性，確認後再開 LLM
USE_LLM = False

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Hydroflumethiazide（thiazide 利尿劑 / Na-Cl cotransporter 抑制劑）"
TARGET_ROUTE      = "外用 / 口服 / 全身（HS 途徑未定，先不設限）"
TARGET_INDICATION = "化膿性汗腺炎（Hidradenitis Suppurativa, HS / Acne Inversa）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Hydroflumethiazide 原核准適應症是利尿/降血壓，要明確排除
SCREENING_IRRELEVANT_EXAMPLES = (
    "利尿／降血壓原適應症（高血壓 / 水腫 / 充血性心衰竭 / 肝硬化腹水 / "
    "腎功能不全 / 電解質調節），且與皮膚、毛囊或發炎無關者"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "hydroflumethiazide",
    "dihydroflumethiazide",
    "saluron",
    "diucardin",
    "hydrenox",
]
RULE_ROUTE_KEYWORDS = [
    "topical",
    "oral",
    "cream",
    "gel",
    "subcutaneous",
    "intralesional",
    "skin",
]
RULE_INDICATION_KEYWORDS = [
    "hidradenitis suppurativa",
    "hidradenitis",
    "acne inversa",
    "verneuil",
    "suppurative hidradenitis",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    # HS 病理 / 表徵
    "follicular occlusion",
    "apocrine",
    "sinus tract",
    "abscess",
    "inflammatory skin",
    "autoinflammatory",
    # 同類藥 / HS off-label 脈絡（共現值得注意）
    "spironolactone",
    "metformin",
    "anti-androgen",
    # HS 藥物地景
    "adalimumab",
    "secukinumab",
    "bimekizumab",
    "TNF",
    "IL-17",
]

# ── 自定義搜尋字串（EPO CQL，ta= = title+abstract）───────────────────────────
CUSTOM_QUERIES = [
    # ── (重要，選用) 全管轄藥物單搜 → 把整個 hydroflumethiazide corpus 撈進 DB，
    #    讓 Phase 4 LLM 讀 claims（補 claims-only 命中，見 architecture.md US9415051B1 case）。
    #    Strategy A 註為 EP/US-only，WO 要靠這條。此藥已下市且舊，單搜筆數應很少。
    # 'ta=hydroflumethiazide',

    # ── 直接交集：Hydroflumethiazide × HS 同義詞 ──
    'ta=hydroflumethiazide AND ta=hidradenitis',
    'ta=hydroflumethiazide AND ta="acne inversa"',
    'ta=dihydroflumethiazide AND ta=hidradenitis',

    # ── 藥物 × 廣義發炎性皮膚病 ──
    'ta=hydroflumethiazide AND ta="inflammatory skin"',
    'ta=hydroflumethiazide AND ta=abscess',

    # ── 機制 / 同類藥 × HS（thiazide / diuretic；利尿劑×HS 有 off-label 脈絡）──
    'ta=thiazide AND ta=hidradenitis',
    'ta=diuretic AND ta="acne inversa"',

    # ── HS 疾病面全掃 ──
    'ta="hidradenitis suppurativa"',
    'ta="acne inversa"',

    # ── (選用) 皮膚科分類交集 A61P17（啟用前先 probe cpc= 支援）──
    # 'ta=hydroflumethiazide AND cpc=A61P17',
]
