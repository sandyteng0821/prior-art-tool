# config.py  →  configs/doxapram_hs_v1.py
# ── 換專案時只改這個檔案 ────────────────────────────────────────────────────
# [2026-10] HS drug-repurposing patentability screen — Task 組 1 / 3（優先序 1）
# Indication: Hidradenitis Suppurativa (HS) × Doxapram
#
# ⚠ SOP 定位：此為 SOP1「初步構想階段」快速篩選（drug + indication + 假說 MoA，
#   尚無證據包）。本工具只覆蓋 SOP1 §3.3 的「專利資料庫」面；NPL（PubMed /
#   ClinicalTrials.gov）與 Orange Book 面需另行手動檢索才算完成 SOP1 memo。
# ⚠ 檢索性質：patentability（非 FTO）。前案含已過期/已放棄/申請中，
#   故 SEARCH_ONLY_GRANTED 必須為 False（granted-only 會漏前案）。
# ⚠ 已知限制：EPO OPS `ta=` 只搜 title+abstract。把藥物埋在 claims/description
#   的 drug-list / Markush HS 專利，以及 description-only 提及，`ta=` 抓不到
#   （見 architecture.md Bug Y / bromocriptine×SMA case）。→ 近零命中 ≠ 綠燈，
#   需 Google Patents fulltext（L2/L3）或專家 ID import（Task L）補強後才可下結論。
# ⚠ 本檔未經 EPO probe 驗證，屬 v1 draft。先跑 rule-mode 驗證命中合理性再開 LLM。
#
# 藥物識別（供查核，非搜尋字串）：
#   Doxapram  CAS 309-29-5（base）/ 7081-53-0（monohydrate）/ 113-07-5（HCl）
#   ATC R07AB01 · 1965 approved · 呼吸興奮劑 / analeptic
#   target: TASK/KCNK 鉀離子通道阻斷（KCNK3 / TASK-1、KCNK9 / TASK-3）、頸動脈體

# 目標產品描述（給 LLM 的 system prompt 用）
TARGET_PRODUCT = "Doxapram 用於治療化膿性汗腺炎 (Hidradenitis Suppurativa, HS / Acne Inversa) — 藥物再利用第二醫藥用途構想"

# 藥物
DRUG_ALIASES = [
    "Doxapram",
    "Doxapram hydrochloride",
    "Dopram",
    "Dopram-V",       # 獸醫商品名
    "Docatone",
    "Stimulexin",
]

# 作用機制（藥物本身的藥理身分；供 LLM 理解 + 機制交集查詢）
MECHANISMS = [
    "respiratory stimulant",
    "analeptic",
    "carotid body",
    "potassium channel blocker",
    "TASK channel",
    "KCNK3",
    "KCNK9",
]

# 劑型 / 給藥途徑（HS 治療常見途徑；repurposing 途徑未定，先放寬）
FORMULATIONS = [
    "topical",
    "oral",
    "systemic",
    "subcutaneous",
    "intralesional",
    "cream",
    "gel",
]

# 適應症（HS 同義詞；"HS" 縮寫太雜訊，不放進搜尋字串）
INDICATIONS = [
    "hidradenitis suppurativa",
    "hidradenitis",
    "acne inversa",
    "Verneuil disease",
    "suppurative hidradenitis",
    "apocrine acne",
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
# SOP1 第一輪：先用規則模式驗證搜尋命中是否合理，確認後再開 LLM
USE_LLM = False

# ── 搜尋過濾條件 ──────────────────────────────────────────────────────────────
# patentability 檢索必須含過期/申請中/已放棄前案 → 不可只收 granted
SEARCH_ONLY_GRANTED = False
# HS 第二用途專利多為近年；如要抓更早的化合物/用途前案可往前放寬（如 "1985 2030"）
SEARCH_YEAR_RANGE = "2000 2030"

# ── 目標產品三要素（給 LLM prompt 用）────────────────────────────────────────
TARGET_DRUG       = "Doxapram（呼吸興奮劑 / TASK-KCNK 鉀離子通道阻斷劑）"
TARGET_ROUTE      = "外用 / 口服 / 全身（HS 途徑未定，先不設限）"
TARGET_INDICATION = "化膿性汗腺炎（Hidradenitis Suppurativa, HS / Acne Inversa）"

# ── 初篩排除範例（告訴 LLM 什麼是完全無關）──────────────────────────────────
# Doxapram 原核准適應症是呼吸興奮，要明確排除
SCREENING_IRRELEVANT_EXAMPLES = (
    "呼吸興奮原適應症（呼吸抑制 / 呼吸暫停 / 早產兒窒息 / 麻醉後甦醒 / "
    "藥物過量呼吸抑制 / COPD 高碳酸血症），且與皮膚、毛囊或發炎無關者"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）────────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "doxapram",
    "dopram",
    "docatone",
    "stimulexin",
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
    # HS 藥物地景（與藥物共現 → 可能是 HS 相關專利，值得注意）
    "adalimumab",
    "secukinumab",
    "bimekizumab",
    "TNF",
    "IL-17",
    "IL-36",
    "JAK inhibitor",
]

# ── 自定義搜尋字串（對應 Strategy F/G；EPO CQL，ta= = title+abstract）─────────
# 設計原則：直接交集（破壞新穎性風險）→ 廣義發炎性皮膚病 → 機制交集 → HS 地景全掃
CUSTOM_QUERIES = [
    # ── (重要，選用) 全管轄藥物單搜 → 把整個 doxapram corpus 撈進 DB，
    #    讓 Phase 4 LLM 讀 claims（可補 ta=drug AND ta=HS 漏掉的 claims-only 命中，
    #    見 architecture.md US9415051B1 case）。Strategy A 註為 EP/US-only，WO 要靠這條。
    #    啟用前先 probe doxapram 單搜筆數（舊藥，應 <200；FETCH_SIZE 已設 200）。
    # 'ta=doxapram',

    # ── 直接交集：Doxapram × HS 同義詞（命中即可能破壞新穎性，最高優先）──
    'ta=doxapram AND ta=hidradenitis',
    'ta=doxapram AND ta="acne inversa"',

    # ── Doxapram × 廣義發炎性皮膚病（抓未點名 HS 但涵蓋的用途 claim）──
    'ta=doxapram AND ta="inflammatory skin"',
    'ta=doxapram AND ta=dermatitis',
    'ta=doxapram AND ta=abscess',

    # ── 機制 × HS（TASK/KCNK 鉀離子通道；命中機率低但成本低）──
    'ta="potassium channel" AND ta=hidradenitis',

    # ── HS 疾病面全掃（不限藥物，建立 HS 專利地景 + 進步性風險判斷）──
    'ta="hidradenitis suppurativa"',
    'ta="acne inversa"',

    # ── (選用) 皮膚科分類交集：A61P17 = dermatologicals（SOP1 §3.3 分類號技法）──
    # 註：啟用前先 probe query_builder 是否支援 cpc=／ipc= 傳遞
    # 'ta=doxapram AND cpc=A61P17',
]
