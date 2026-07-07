# config.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-07] Orphenadrine × Prurigo Nodularis
# Previous: Pemirolast × IPF

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Orphenadrine 口服／外用製劑治療結節性癢疹 (Prurigo Nodularis)"

# ──────────────────────────────────────────────────────────────────────────────
# 藥物
# ──────────────────────────────────────────────────────────────────────────────
# Orphenadrine 是 diphenhydramine 的 monomethyl 衍生物，dirty drug：
#   H1 antihistamine + muscarinic antagonist + Nav1.7/1.8/1.9 blocker
#   + NMDA antagonist + NE reuptake inhibitor + HERG K+ channel blocker
# 原核准適應症：肌肉痙攣 / 帕金森輔助治療
DRUG_ALIASES = [
    "Orphenadrine",
    "Orphenadrine citrate",
    "Orphenadrine hydrochloride",
    "Norflex",            # 主要品牌名（US）
    "Disipal",            # UK 品牌
    "Banflex",
    "Flexon",
    "Mio-Rel",
    "Antiflex",
    "Myolin",
]

# ──────────────────────────────────────────────────────────────────────────────
# 作用機制
# ──────────────────────────────────────────────────────────────────────────────
# 多重機制都可能命中 PN 的 itch-scratch neuroimmune pathway：
#   - H1 antihistamine → 阻斷 histamine-dependent itch
#   - Muscarinic antagonist → 調節 cholinergic itch signaling
#   - Nav1.7/1.8/1.9 blocker → 抑制 pruriceptor action potential propagation
#   - NMDA antagonist → 打斷 central sensitization of itch
#   - NE reuptake inhibitor → 與 doxepin/amitriptyline 等抗癢抗鬱藥同路徑
MECHANISMS = [
    "antihistamine",
    "H1 antagonist",
    "histamine H1 receptor antagonist",
    "anticholinergic",
    "muscarinic antagonist",
    "sodium channel blocker",
    "Nav1.7",
    "NMDA antagonist",
    "norepinephrine reuptake inhibitor",
    "muscle relaxant",
]

# ──────────────────────────────────────────────────────────────────────────────
# 劑型 / 給藥途徑
# ──────────────────────────────────────────────────────────────────────────────
# 現有劑型：oral tablet (extended release)、IM/IV injection
# Repurposing 可能探索 topical（外用抗癢）或維持 oral
FORMULATIONS = [
    "oral",
    "tablet",
    "topical",
    "cream",
    "ointment",
    "injection",
    "extended release",
]

# ──────────────────────────────────────────────────────────────────────────────
# 適應症
# ──────────────────────────────────────────────────────────────────────────────
# PN = Prurigo Nodularis，chronic prurigo 的一個亞型
# 搜尋策略：疾病名 + 上位概念（chronic pruritus）+ 相關病理（neurogenic itch）
INDICATIONS = [
    "prurigo nodularis",
    "prurigo",
    "chronic prurigo",
    "nodular prurigo",
    "chronic pruritus",
    "pruritus",
    "itch",
    "antipruritic",
    "neurodermatitis",
    "itch-scratch cycle",
]

# ──────────────────────────────────────────────────────────────────────────────
# LLM 模型設定
# ──────────────────────────────────────────────────────────────────────────────
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
USE_LLM = True  # 先用規則模式驗證搜尋結果正確性，確認後再開 LLM

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Orphenadrine（H1 抗組織胺 / 抗膽鹼 / Nav1.7 鈉通道阻斷劑 — 多靶點 dirty drug）"
TARGET_ROUTE      = "口服錠劑（Oral tablet）或外用製劑（Topical cream/ointment）"
TARGET_INDICATION = "結節性癢疹（Prurigo Nodularis, PN）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Orphenadrine 原核准適應症是肌肉痙攣和帕金森，要明確排除
# 同時排除純眼科和不涉及搔癢的皮膚科專利
SCREENING_IRRELEVANT_EXAMPLES = (
    "單純肌肉痙攣／骨骼肌鬆弛（muscle spasm / skeletal muscle relaxation）、"
    "帕金森病運動功能治療（Parkinson's motor control）、"
    "術後鎮痛（perioperative analgesia）、"
    "心律不整（cardiac arrhythmia — HERG blocker 副作用角度）、"
    "不涉及搔癢的皮膚科疾病"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────

# Drug keywords: 藥物名 + 結構母核 + 核心機制
RULE_DRUG_KEYWORDS = [
    "orphenadrine",
    "norflex",
    "disipal",
    "banflex",
    "diphenhydramine",       # 結構類似物，PN antipruritic 前案常見
    "ethanolamine",          # 藥物化學母核分類
    "anticholinergic",
    "muscarinic antagonist",
    "sodium channel blocker",
]

# Route keywords: 給藥途徑
RULE_ROUTE_KEYWORDS = [
    "oral",
    "tablet",
    "topical",
    "cream",
    "ointment",
    "dermal",
    "cutaneous",
    "transdermal",
    "lotion",
]

# Indication keywords: 疾病核心
RULE_INDICATION_KEYWORDS = [
    "prurigo nodularis",
    "prurigo",
    "chronic pruritus",
    "pruritus",
    "antipruritic",
    "itch",
    "nodular prurigo",
]

# Additional indication keywords: 疾病機制 + 競爭藥物 + pathway
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "itch-scratch",
    "scratching",
    "neurogenic itch",
    "neuropathic itch",
    "chronic itch",
    "pruritogen",
    "pruriceptor",
    # 病理機制
    "IL-31",               # "itchy cytokine"，PN 核心
    "IL-4",                # type 2 inflammation
    "IL-13",               # type 2 inflammation
    "substance P",         # neuropeptide，PN 增高
    "CGRP",               # calcitonin gene-related peptide
    "nerve growth factor",
    "NGF",
    "mast cell",           # PN lesion 浸潤
    "eosinophil",          # PN lesion 浸潤
    "histamine",
    # 相關通道/受體
    "Nav1.7",
    "Nav1.8",
    "sodium channel",
    "NMDA",
    "TRPV1",
    "NK1",                 # neurokinin-1 receptor（serlopitant 靶點）
    "NK-1",
    # 競爭藥物（同適應症，有共現就值得注意）
    "dupilumab",           # FDA 2022 approved for PN
    "nemolizumab",         # FDA 2024 approved for PN (anti-IL-31)
    "serlopitant",         # NK1R antagonist, PN clinical trials
    "aprepitant",          # NK1R antagonist, PN case reports
    "nalbuphine",          # opioid modulator, PN trials
    "vixarelimab",         # anti-OSMRβ, PN trials
    "barzolvolimab",       # anti-KIT, PN trials
    "doxepin",             # TCA with H1/H2 + antipruritic（機制最接近的已用藥）
    "amitriptyline",       # TCA antipruritic
]

# ── 自定義搜尋字串（對應 Strategy F/G）───────────────────────────────────────
CUSTOM_QUERIES = [
    # ── Layer 1：Drug × Disease 直接交叉 ──────────────────────
    'ta=orphenadrine AND ta="prurigo"',
    'ta=orphenadrine AND ta="pruritus"',
    'ta=orphenadrine AND ta="itch"',
    'ta=orphenadrine AND ta="antipruritic"',

    # ── Layer 2：Drug × Mechanism pathway ─────────────────────
    # Orphenadrine 的 antihistamine 母核（diphenhydramine 衍生物）
    'ta=orphenadrine AND ta="antihistamine"',
    'ta=orphenadrine AND ta="sodium channel"',
    'ta=orphenadrine AND ta="topical"',

    # ── Layer 3：Disease 角度全掃（不限藥物）──────────────────
    'ta="prurigo nodularis"',
    'ta="prurigo nodularis" AND ta="treatment"',
    'ta="chronic pruritus" AND ta="antihistamine"',
    'ta="chronic pruritus" AND ta="sodium channel"',
    'ta="chronic pruritus" AND ta="anticholinergic"',
    'ta="prurigo" AND ta="topical"',

    # ── Layer 4：同機制不同藥名（結構類似物 + 同靶點）─────────
    # Diphenhydramine = 結構最近的 antipruritic（Benadryl）
    'ta=diphenhydramine AND ta="prurigo"',
    'ta=diphenhydramine AND ta="pruritus"',
    'ta=diphenhydramine AND ta="itch"',
    # Doxepin = TCA + H1/H2 antihistamine，PN 外用前案豐富
    'ta=doxepin AND ta="pruritus"',
    'ta=doxepin AND ta="prurigo"',
    # Sodium channel × itch（orphenadrine 的差異化機制角度）
    'ta="Nav1.7" AND ta="itch"',
    'ta="Nav1.7" AND ta="pruritus"',
    'ta="sodium channel" AND ta="pruritus"',
    'ta="sodium channel" AND ta="antipruritic"',

    # ── Layer 5：競爭藥物 × PN（了解 approved/pipeline 前案地貌）─
    'ta=dupilumab AND ta="prurigo nodularis"',
    'ta=nemolizumab AND ta="prurigo"',
    'ta=serlopitant AND ta="pruritus"',
    'ta=nalbuphine AND ta="pruritus"',

    # ── Layer 6：NMDA × itch（orphenadrine 獨特機制角度）─────
    'ta="NMDA" AND ta="pruritus"',
    'ta="NMDA" AND ta="itch"',
    'ta="glutamate" AND ta="pruritus"',

    # ── Layer 7：Broader neuroimmune itch pathway ─────────────
    'ta="IL-31" AND ta="prurigo"',
    'ta="substance P" AND ta="prurigo nodularis"',
    'ta="mast cell" AND ta="prurigo"',
    'ta="nerve growth factor" AND ta="pruritus"',
]
