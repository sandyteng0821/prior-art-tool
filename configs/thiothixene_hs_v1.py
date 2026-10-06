# config.py  →  configs/thiothixene_hs_v1.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-10] HS drug-repurposing patentability screen — Task 組 3 / 3（優先序 3）
# Indication: Hidradenitis Suppurativa (HS) × Thiothixene
#
# ⚠ SOP 定位：SOP1「初步構想階段」快速篩選。本工具只覆蓋 SOP1 §3.3「專利資料庫」面；
#   NPL（PubMed / ClinicalTrials.gov）與 Orange Book 面需另行手動檢索。
# ⚠ 檢索性質：patentability（非 FTO）；前案含過期/放棄/申請中 → SEARCH_ONLY_GRANTED=False。
# ⚠ 已知限制：`ta=` 只搜 title+abstract；drug-list/Markush、description-only 提及抓不到
#   （Bug Y）。近零命中 ≠ 綠燈，需 Google Patents fulltext / 專家 ID import 補強。
# ⚠ 拼法陷阱：INN/JAN 作 "Tiotixene"（單 h），US/USAN/USP 作 "Thiothixene"。
#   EP/WO 前案常用 INN，兩種拼法都必須搜（見 DRUG_ALIASES / CUSTOM_QUERIES）。
# ⚠ 本檔未經 EPO probe 驗證，v1 draft。先跑 rule-mode 再開 LLM。
#
# 藥物識別（供查核，非搜尋字串）：
#   Thiothixene / Tiotixene  CAS 5591-45-7（cis/活性）· ATC N05AF04 · 典型抗精神病
#   brand: Navane / Orbinamon · salt: hydrochloride · 活性為 cis-(Z)-isomer
#   dev code: CP-12,252-1 · NSC-108165 · P-4657B
#   target: thioxanthene；dopamine D2 antagonist（亦 5-HT2A / H1 / α1）

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Thiothixene (Tiotixene) 用於治療化膿性汗腺炎 (Hidradenitis Suppurativa, HS / Acne Inversa) — 藥物再利用第二醫藥用途構想"

# 藥物（兩種拼法都放：Thiothixene = US；Tiotixene = INN/JAN）
DRUG_ALIASES = [
    "Thiothixene",
    "Tiotixene",              # INN / JAN — EP/WO 常用
    "Navane",
    "Orbinamon",
    "Thiothixene hydrochloride",
    "cis-Thiothixene",
    "NSC-108165",
]

# 作用機制
MECHANISMS = [
    "antipsychotic",
    "neuroleptic",
    "thioxanthene",
    "dopamine antagonist",
    "dopamine D2 antagonist",
    "D2 receptor antagonist",
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
TARGET_DRUG       = "Thiothixene / Tiotixene（thioxanthene 典型抗精神病 / D2 拮抗劑）"
TARGET_ROUTE      = "外用 / 口服 / 全身（HS 途徑未定，先不設限）"
TARGET_INDICATION = "化膿性汗腺炎（Hidradenitis Suppurativa, HS / Acne Inversa）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Thiothixene 原核准適應症是抗精神病，要明確排除
SCREENING_IRRELEVANT_EXAMPLES = (
    "抗精神病原適應症（思覺失調症 / 精神病 / 躁症 / 中樞神經精神疾病之一般"
    "調配與劑型），且與皮膚、毛囊或發炎無關者"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "thiothixene",
    "tiotixene",
    "navane",
    "orbinamon",
    "thioxanthene",
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
    # HS 藥物地景
    "adalimumab",
    "secukinumab",
    "bimekizumab",
    "TNF",
    "IL-17",
    "IL-36",
]

# ── 自定義搜尋字串（EPO CQL，ta= = title+abstract）───────────────────────────
CUSTOM_QUERIES = [
    # ── (重要，選用) 全管轄藥物單搜（兩種拼法）→ 把整個 corpus 撈進 DB，
    #    讓 Phase 4 LLM 讀 claims（補 claims-only 命中，見 architecture.md US9415051B1 case）。
    #    Strategy A 註為 EP/US-only，WO 要靠這條。
    # 'ta=thiothixene',
    # 'ta=tiotixene',

    # ── 直接交集：Thiothixene / Tiotixene × HS（兩種拼法都搜）──
    'ta=thiothixene AND ta=hidradenitis',
    'ta=tiotixene AND ta=hidradenitis',
    'ta=thiothixene AND ta="acne inversa"',
    'ta=tiotixene AND ta="acne inversa"',

    # ── 藥物 × 廣義發炎性皮膚病 ──
    'ta=thiothixene AND ta="inflammatory skin"',
    'ta=tiotixene AND ta="inflammatory skin"',

    # ── 機制 / 同類藥 × HS（thioxanthene / 抗精神病 / D2）──
    'ta=thioxanthene AND ta=hidradenitis',
    'ta="dopamine antagonist" AND ta="inflammatory skin"',

    # ── HS 疾病面全掃 ──
    'ta="hidradenitis suppurativa"',
    'ta="acne inversa"',

    # ── (選用) 皮膚科分類交集 A61P17（啟用前先 probe cpc= 支援）──
    # 'ta=thiothixene AND cpc=A61P17',
]
