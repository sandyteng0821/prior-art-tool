# design_config_map — config.py 欄位 × Phase 1–5 實際使用對照

> 用途：後續 refactor `config.py` 的參考。**只列「實際被 import 或 called」的**，不看欄位名猜。
> 來源：本次直接 grep 生產管線五個 module（query_builder / patent_fetcher / patent_store /
> llm_analyzer / output_writer）+ 工具（debug_scoring / analyze_jsonl / backfill_snippets）。
> 核對狀態：已對 **live post-Task-Q** `llm_analyzer.py` 核對 —— config 使用面與本表一致
> （`ANALYSIS_HUMAN_TEMPLATE` / `rebuild_analysis_chain` 已在；`ANALYSIS_SYSTEM` 仍為舊 rubric，
> rubric_v2 尚未併入）。`analyze_patent` 定義兩次是刻意的 save-and-wrap dispatcher（非 bug）。

## 管線 Phase ↔ module

| Phase | module | 讀 config? |
|---|---|---|
| 1 | `query_builder.py`   | ✅ |
| 2 | `patent_fetcher.py`  | ✅ |
| 3 | `patent_store.py`    | ❌ 完全不讀 |
| 4 | `llm_analyzer.py`    | ✅（rule / LLM 兩條，參數不同）|
| 5 | `output_writer.py`   | ❌ 完全不讀 |

「rule/LLM」欄只對 Phase 4 有意義；Phase 1/2 是 **mode-agnostic**（搜尋/抓取不看 `USE_LLM`）。

---

## config 載入來源：全域 `config.py` vs 隔離 `configs/`

分界**不是**「modules/ vs 不是」，是**載入機制**：`from config import`（讀全域 `config.py`）
vs `importlib` 載 `configs/{name}.py`（每次獨立 module、不碰全域）。這條線橫跨資料夾
（`backfill_snippets` 是 tool 卻讀全域；`debug_scoring` 是 tool 卻隔離）。

**讀全域 `config.py`（`from config import`）：**

| 誰 | 角色 |
|---|---|
| `main.py` | production entry（驅動下面三個 module）|
| `modules/query_builder.py` | Phase 1 |
| `modules/patent_fetcher.py` | Phase 2 |
| `modules/llm_analyzer.py` | Phase 4 |
| `backfill_snippets.py` | 獨立工具（snippet 回填）|

**隔離讀 `configs/{name}.py`（importlib，不碰全域）：**

| 誰 | 角色 |
|---|---|
| `scripts/analyze_jsonl.py` | ← **Task R 之後**才進這組（之前是「改寫全域 `config.py`」）|
| `tools/debug_scoring.py` | 獨立島 |
| `api/core/llm_bridge.py` | REST（D1：不 import `modules/`）|

**完全不讀 config：** `modules/patent_store.py`、`modules/output_writer.py`、`modules/rubric.py`
（rubric 的 `load_rubric(path, cfg)` 用**傳入的** cfg，不自己 import config）。

**Task R 對這條線的效果：**
- 把 `analyze_jsonl` 從「全案唯一會**改寫**全域 `config.py`」搬到「隔離讀 `configs/{name}.py`」。
- 之後全域 `config.py` **沒有任何程式會改寫它**（只剩手動編輯）；讀者就是上表「讀全域」那群。
- 機制：analyze_jsonl 用 `sys.modules['config'] = <configs/{name}.py 載入的 module>`，讓
  `llm_analyzer` 的 `from config import` 吃到注入那份、而非磁碟 `config.py`。

> 註：`analyze_jsonl` 現在（Task R 前）同時「用 importlib 讀 cfg 供顯示」＋「改寫磁碟 `config.py`」，
> 兩邊都沾；Task R 後只剩隔離這半。

---

## 主表（欄位 → 生產 Phase → rule/LLM → 用途 / 註記）

| config 欄位 | 生產 Phase | rule/LLM | 用途 / 註記 |
|---|---|---|---|
| `TARGET_PRODUCT` | P2, P4 | LLM（P4）| P2：寫 `search_log.project`（⚠ `[:30].replace` 截斷源，`patent_fetcher.py:37`）；P4：FTO system prompt |
| `DRUG_ALIASES` | P1, P2 | — | P1 搜尋**只用 `[:1]`**（第一個藥名）；P2 formulation snippet 抽取用**全清單**。→ 別名 `[1:]` 對搜尋無效，只在 P2 有用 |
| `MECHANISMS` | —（reserved）| — | 🟡 **reserved**：query_builder import 未用（Strategy F 未實作的輸入）；保留給未來 mechanism 查詢。見 Task S |
| `FORMULATIONS` | —（reserved）| — | 🟡 **reserved + debug**：生產不用；`debug_scoring` keyword probe 讀；保留（見 Task S）|
| `INDICATIONS` | —（reserved）| — | 🟡 **reserved + debug**：生產不用；`debug_scoring` keyword probe 讀；保留（見 Task S）|
| `SCREENING_MODEL` | P4 | **LLM** | 初篩模型（`llm_analyzer.py:150`）|
| `ANALYSIS_MODEL` | P4 | **LLM** | 精讀模型（`:151`）|
| `MAX_WORKERS` | — | — | 🔴 **死**：全案無任何 module import/讀 |
| `LLM_MAX_RETRIES` | P4 | **LLM** | retry 次數（`:157-166`）|
| `LLM_RETRY_BASE_SECONDS` | P4 | **LLM** | retry 指數退避（`:166`）|
| `FETCH_SIZE` | P2 | — | 每次 EPO 搜尋抓幾筆（`patent_fetcher.py:40`）|
| `CLAIMS_MAX_CHARS` | P2, P4 | LLM（P4）| ⚠ **一欄兩用**：P2 存 DB 時截斷 claims（`:213/242/333`）；P4 截斷 LLM 輸入（`:203`）。調它會同時影響兩處 |
| `USE_LLM` | P4 | **switch** | rule/LLM dispatcher（`:149` init、`:268` 分派）|
| `SEARCH_ONLY_GRANTED` | P1 | — | granted 過濾（`_add_filters`）。**現在 = False → 目前無作用** |
| `SEARCH_YEAR_RANGE` | P1 | — | 年份過濾（`_add_filters` / `_add_filters_epb`）|
| `TARGET_DRUG` | P4 | **LLM** | screening + analysis prompt + schema description |
| `TARGET_ROUTE` | P4 | **both** | LLM：prompt/schema；rule：輸出標籤（`[TARGET_ROUTE] if route_match`，`:254`）|
| `TARGET_INDICATION` | P4 | **both** | LLM：prompt/schema；rule：輸出標籤（`:255`）|
| `SCREENING_IRRELEVANT_EXAMPLES` | P4 | **LLM** | 初篩排除範例 prompt（`:102`）|
| `RULE_DRUG_KEYWORDS` | P4 | **rule** | rule 計分關鍵字（`:228`）|
| `RULE_ROUTE_KEYWORDS` | P4 | **rule** | rule 計分（`:229`）|
| `RULE_INDICATION_KEYWORDS` | P4 | **rule** | rule 計分（`:230`）|
| `RULE_ADDITIONAL_INDICATION_KEYWORDS` | P4 | **rule** | rule 計分（`:231`）|
| `CUSTOM_QUERIES` | P1 | — | 自訂搜尋字串（`build_queries` 逐筆加 filter）|

---

## Phase 4 的 rule / LLM 參數切分（你要的 True/False）

**`USE_LLM = False`（rule mode，`rule_based_analyze`）只讀：**
- `RULE_DRUG_KEYWORDS`, `RULE_ROUTE_KEYWORDS`, `RULE_INDICATION_KEYWORDS`, `RULE_ADDITIONAL_INDICATION_KEYWORDS`
- （+ `TARGET_ROUTE` / `TARGET_INDICATION` 僅拿來當輸出標籤，不參與比對）

**`USE_LLM = True`（LLM mode，screening + analysis）只讀：**
- `SCREENING_MODEL`, `ANALYSIS_MODEL`, `LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS`
- `TARGET_PRODUCT`, `TARGET_DRUG`, `SCREENING_IRRELEVANT_EXAMPLES`, `CLAIMS_MAX_CHARS`
- `TARGET_ROUTE`, `TARGET_INDICATION`（prompt + schema）

**兩模式都用：** `TARGET_ROUTE`, `TARGET_INDICATION`（用途不同）。
**與 mode 無關（P1/P2）：** `DRUG_ALIASES`, `FETCH_SIZE`, `CLAIMS_MAX_CHARS`(P2 部分), `SEARCH_ONLY_GRANTED`, `SEARCH_YEAR_RANGE`, `CUSTOM_QUERIES`, `TARGET_PRODUCT`(P2 部分)。

---

## refactor 提示

1. **可刪：** 只有 `MAX_WORKERS`（真死、無人讀）。`MECHANISMS` / `FORMULATIONS` / `INDICATIONS`
   **不是死的** —— 是 reserved（未來 mechanism/indication 查詢，見 query_builder Strategy F/G 未實作）
   ＋ `FORMULATIONS`/`INDICATIONS` 另供 debug probe → **保留、標註即可**（詳 Task S）。
2. **命名/分區建議：** config 目前把「搜尋(P1)」「抓取(P2)」「rule 計分(P4)」「LLM(P4)」
   「目標三要素(P4 共用)」混在一起。refactor 可按**使用它的 Phase / mode** 分段，例如：
   `# ── Phase 1 搜尋 ──`（DRUG_ALIASES / SEARCH_* / CUSTOM_QUERIES）、
   `# ── Phase 2 抓取 ──`（FETCH_SIZE / CLAIMS_MAX_CHARS / DRUG_ALIASES）、
   `# ── Phase 4 目標三要素（rule+LLM 共用）──`（TARGET_*）、
   `# ── Phase 4 LLM only ──`（models / retries / IRRELEVANT_EXAMPLES）、
   `# ── Phase 4 rule only ──`（RULE_*）。
3. **`CLAIMS_MAX_CHARS` 一欄兩用**（P2 存檔 + P4 LLM 輸入）—— 若之後想「存長一點、只截 LLM 輸入」，
   要拆成兩個欄位才行；現在共用同一值。
4. **`DRUG_ALIASES` 語意分裂** —— 搜尋只吃第一個、snippet 抽取吃全部。若別名主要為了 snippet，
   命名/註解要講清楚，免得有人以為加別名能擴大搜尋（實際 P1 只用 `[:1]`）。
5. **與 Task R 無關**：本表是 refactor **內容**的參考；Task R 改的是 config **載入方式**。兩者分開。

---

## 附：非 Phase 的 config 消費者（刪欄位前的安全檢查）

| 消費者 | 讀到的 config | 性質 |
|---|---|---|
| `debug_scoring.py`（工具）| required-fields 驗證那組 + keyword probe 的 `DRUG_ALIASES`/`FORMULATIONS`/`INDICATIONS` | 獨立島，用 importlib 載 configs/ |
| `analyze_jsonl.py`（入口）| `TARGET_PRODUCT`/`TARGET_DRUG`/`TARGET_ROUTE`/`TARGET_INDICATION`/`CLAIMS_MAX_CHARS`/`USE_LLM`（多數經 llm_analyzer）| Phase 4/5 入口 |
| `backfill_snippets.py`（工具）| `DRUG_ALIASES`, `TARGET_PRODUCT` | snippet 回填 |

> 刪任何欄位前，除了看主表的生產 Phase，也要掃這張表 —— 例如 `FORMULATIONS`/`INDICATIONS`
> 生產 Phase 是「—」，但砍了會弄壞 `debug_scoring` 的 keyword probe。
