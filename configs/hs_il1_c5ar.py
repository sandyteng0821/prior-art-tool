# configs/hs_il1_c5ar.py
# ── HS repurposing: Anakinra / Canakinumab / Avacopan ──────────────────────
# Copy over config.py to run. See docs/spec/task_P_hs_prior_art.md for the
# probe evidence behind every query below, and for what this run cannot answer.
#
# NOTE: this config stretches the single-product assumption. TARGET_DRUG /
# TARGET_ROUTE are single strings meant for one molecule; here they carry
# three drugs across two routes (SC injection for the biologics, oral for
# avacopan). Screening precision will be lower than a single-drug project.
# Accepted deliberately — see task_P §"Config limits".

TARGET_PRODUCT = "HS_IL1_C5aR repurposing (anakinra, canakinumab, avacopan)"

# 三個藥的 alias 聯集。必須是聯集而非分三次跑：_get_or_fetch 在 DB hit 時
# 直接 return，不重跑 snippet extraction，而 _collect_snippets 讀 module-level
# DRUG_ALIASES。分三次跑會讓第二、三個藥的 snippets 永遠算不出來。
DRUG_ALIASES = [
    # anakinra（IL-1 receptor antagonist, SOBI）
    "anakinra", "Kineret", "IL-1ra", "IL-1 receptor antagonist",
    "interleukin-1 receptor antagonist", "r-metHuIL-1ra",
    # canakinumab（anti-IL-1beta mAb, Novartis）
    "canakinumab", "Ilaris", "ACZ885",
    # avacopan（C5aR antagonist, ChemoCentryx → Amgen）
    "avacopan", "Tavneos", "CCX168",
]

MECHANISMS = [
    "IL-1", "interleukin-1", "IL-1beta", "IL-1 beta", "IL-1alpha",
    "IL-1 receptor", "IL-1R1", "inflammasome", "NLRP3",
    "C5a", "C5aR", "C5a receptor", "complement",
]

# 給藥途徑 / 劑型。注意：FORMULATIONS 只被 tools/debug_scoring.py 的 keyword
# probe 使用，不影響 snippet extraction —— 後者的 KEYWORDS 硬寫在
# patent_fetcher._extract_formulation_snippets 裡，且是口服固體劑型詞彙
# （tablet / capsule / excipient / carrier），對注射劑基本空轉。見 task_P。
FORMULATIONS = [
    "subcutaneous", "injection", "prefilled syringe", "autoinjector",
    "lyophilized", "oral", "tablet",
]

INDICATIONS = [
    "hidradenitis suppurativa",
    "acne inversa",            # HS 舊稱
    "Verneuil",                # Verneuil's disease
    "HS",
    "follicular occlusion",
]

SCREENING_MODEL = "gpt-4o-mini"  # 初篩（全部摘要）
ANALYSIS_MODEL  = "gpt-4o"       # 精讀（Medium / High 專利）

MAX_WORKERS = 1
LLM_MAX_RETRIES = 6
LLM_RETRY_BASE_SECONDS = 2

FETCH_SIZE = 200
CLAIMS_MAX_CHARS = 3000
USE_LLM = True

SEARCH_ONLY_GRANTED = False
SEARCH_YEAR_RANGE = "2000 2030"

TARGET_DRUG       = "Anakinra（IL-1 受體拮抗劑）/ Canakinumab（抗 IL-1β 單株抗體）/ Avacopan（C5aR 拮抗劑）"
TARGET_ROUTE      = "皮下注射（生物藥）或口服（avacopan）"
TARGET_INDICATION = "化膿性汗腺炎（Hidradenitis Suppurativa, HS；舊稱 acne inversa）"

# ── 初篩排除範例 ──────────────────────────────────────────────────────────
# 三個藥的既有核准適應症會主導搜尋結果（CAPS / FMF / Still's / gout / AAV /
# RA）。但不能一律排除：IL-1 專利很常寫成 indication laundry list，
# 「CAPS、FMF、TRAPS、…、hidradenitis suppurativa」把 HS 埋在列表裡，那類
# claim 才是真正會擋人的 prior art。所以排除條件是「只談既有適應症、且全文
# 未提及 HS 或皮膚病灶」，不是「提到既有適應症」。
SCREENING_IRRELEVANT_EXAMPLES = (
    "只談既有核准適應症（RA / CAPS / FMF / TRAPS / Still's disease / "
    "gouty arthritis / ANCA-associated vasculitis）且完全未提及 hidradenitis "
    "suppurativa、acne inversa 或皮膚化膿性病灶者；純診斷試劑；抗體製程專利"
    "（cell line / purification）與適應症無關者"
)

# ── 規則評分關鍵字（USE_LLM=False 時使用）─────────────────────────────────
RULE_DRUG_KEYWORDS = [
    "anakinra", "kineret", "il-1ra", "interleukin-1 receptor antagonist",
    "canakinumab", "ilaris", "acz885", "anti-il-1beta",
    "avacopan", "tavneos", "ccx168",
    "il-1 inhibitor", "c5ar antagonist",
]
RULE_ROUTE_KEYWORDS = [
    "subcutaneous", "injection", "intradermal", "topical",
    "prefilled syringe", "oral",
]
RULE_INDICATION_KEYWORDS = [
    "hidradenitis suppurativa", "acne inversa", "verneuil",
]
RULE_ADDITIONAL_INDICATION_KEYWORDS = [
    "inflammasome", "nlrp3", "interleukin-1", "il-1beta",
    "c5a", "complement",
    "adalimumab",        # HS 唯一長期核准藥，共現值得注意
    "secukinumab",       # HS 已核准 IL-17
    "bimekizumab",       # HS IL-17
    "lutikizumab",       # AbbVie anti-IL-1alpha/beta，HS 臨床
    "vilobelimab",       # InflaRx anti-C5a，HS 臨床
    "hurley",            # Hurley staging
    "hiscr",             # HS Clinical Response endpoint
    "abscess", "fistula", "sinus tract",
]

# ── 自定義搜尋字串 ────────────────────────────────────────────────────────
# 每條後面的數字是 2026-08-03 probe 實測 @total-result-count。
# 0-hit 的保留在此當作「搜過了」的紀錄，對 quota 無影響。
CUSTOM_QUERIES = [
    # ── 單藥全掃（對稱 query_builder 只取 DRUG_ALIASES[0] 的行為）──
    'ta=canakinumab',
    'ta=avacopan',
    
    # ── 主力：indication 全掃（sample 66% 為 WO，fulltext 可取）──
    'ta="hidradenitis suppurativa"',                    # 150
    'ta="acne inversa"',                                # 2

    # ── mechanism × HS：優先精讀 ──
    'ta="IL-1" AND ta="hidradenitis suppurativa"',      # 5
    'ta="C5a" AND ta="hidradenitis"',                   # 0

    # ── drug × HS ──
    # 全部 0-1 hit。這證明「無專利在 title/abstract 同時提到藥名與 HS」，
    # 不證明 white space —— genus claim（anti-IL-1beta antibody）不點名藥物。
    # 該問題要靠掃那 150 筆的 description 回答，不是靠這些 query。
    'ta=avacopan AND ta="hidradenitis"',                # 1
    'ta=anakinra AND ta="hidradenitis"',                # 0
    'ta=canakinumab AND ta="hidradenitis"',             # 0

    # ── 既有適應症當入口（laundry-list 路徑）──
    # 合計僅 30 筆，量體遠低於預期。保留但不為它做任何額外設計。
    'ta="cryopyrin-associated periodic syndrome"',      # 6
    'ta="familial Mediterranean fever"',                # 20
    'ta="ANCA-associated vasculitis" AND ta="C5a"',     # 3
    'ta="Still\'s disease" AND ta="interleukin-1"',     # 1
]
