# Task R — Config 注入去磁碟改寫 + 分析層 drift tripwire

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 承「Config / Prompt refactoring」討論串。本 task = 該串的 **(1)** + **(3)**；
> **(2)** 收 prompt 島 blocked on `rubric_v2` 拍板，不在本 task（見 Non-Goals）。
> 前置條件：無。完成後請更新 `docs/architecture.md`（line 110 analyze_jsonl、line 111 API）＋ `design_config_map.md`（「載入來源」節）。

---

## Context

工具目前有**兩種「載 config」的方式**並存，風險不一樣：

- **main.py / analyze_jsonl** 共用一個固定位置的 `config.py`：`llm_analyzer` 一被 import
  就從這個 `config.py` 讀設定。麻煩在於 `analyze_jsonl` 想換 project 或開關 LLM 時，沒有別條路
  能叫 `llm_analyzer` 改讀別的 config，只好**先去改磁碟上那個 `config.py`**（改完再 import、
  跑完再還原）。
- **debug_scoring / llm_bridge** 走另一條：直接用檔名把 `configs/` 底下指定的檔案各自載成
  一份獨立 config，**從頭到尾不碰 `config.py`**，所以天生互不干擾。

```
main.py / analyze_jsonl     ──讀──▶  config.py（單一、全域）      ← 只有 analyze_jsonl 會「改寫」它
debug_scoring / llm_bridge  ──讀──▶  configs/xxx.py（各自一份、隔離）
```

換句話說：**全案裡唯一會用程式去改寫 `config.py` 的，就是 `analyze_jsonl`。** 反倒是那兩座
「島」（debug_scoring / llm_bridge），雖然各自抄了一份 prompt，在 config 這件事上是最乾淨的。

還有一件相關的事：Task Q 已經讓 production 的分析 prompt 收斂成只有一份（單一來源），
但 debug_scoring 和 llm_bridge 為了保持獨立，各自留了一份自己的 prompt 和 schema。
三份現在剛好一樣——只是因為還沒人去改。一旦把 `rubric_v2` 併進 production，這兩座島就會
悄悄跟 production 不一致。

三個判斷（對應討論裡的 1–3）：

1. **(1) 拿掉 config 改寫。** analyze_jsonl 去改磁碟這招，只要跑到一半掛掉、被強制中斷、
   或兩個一起跑，就可能把 `config.py` 留在錯的狀態——**下一次跑 production（main.py）就吃到髒的**。
   這是這張 ticket 的主菜。

2. **(3) 幫 prompt 島放一條絆線。** 在 rubric 拍板前，先加一個測試盯著「三份 prompt / schema
   一不一致」。這樣哪天改了 production 卻忘了同步島，**CI 會直接叫，而不是靜靜漂走**。

3. **(2) 收島先不做。** 那兩座島的獨立是刻意的——debug_scoring 正是你拿來 A/B 決定 rubric
   的工具，收掉就沒得比；llm_bridge 是對外 API，要等 production 穩定再說。本 task 不碰它們，
   只用 (3) 的絆線看著。

**為什麼 (1) 和 (3) 放同一張：** (1) 的做法是把「改磁碟 `config.py`」換成「在記憶體裡把選好的
config 塞給 `llm_analyzer`」——效果一樣、但磁碟那份永遠不動。而 (3) 的測試也需要同一個「把某個
config 塞進去」的小工具（才能拿到 production 對那個 config 的 prompt 去比對）。既然共用同一支
小工具，就併成一張（跟 Task Q 把共用機制綁一起的做法一致）。**這支小工具、以及 analyze_jsonl 的
改動，都不需要動到 `llm_analyzer` 本身。**

---

## Goal

1. `analyze_jsonl` 改用 **in-memory 注入 `sys.modules['config']`** 取代磁碟改寫；
   磁碟 `config.py` 全程不被程式碰。**改動只在 `analyze_jsonl`（+ 一支共用 helper），
   不牽動 `llm_analyzer`、不牽動島。**
2. 加一條 **drift tripwire 測試**：同一 config 下，production /
   `debug_scoring` / `llm_bridge` 的 default analysis system prompt + schema 欄位須逐字一致
   ——讓「等 `rubric_v2` 拍板」這段期間的漂移**變成 CI 紅燈，而非靜默**。

---

## Files to Modify

- 新增 `modules/config_inject.py` —— 共用 helper：載入 `configs/{name}.py` → 覆寫 `USE_LLM`
  → 注入 `sys.modules['config']`。（#1 與 #3 都用它）
- `scripts/analyze_jsonl.py` —— 移除 `_ensure_config`（`:173`）/ `_restore_config`（`:206`）/
  `USE_LLM` 的 `write_text` patch（`:343-350`）/ `del sys.modules` 迴圈（`:360`）/ 包住它們的
  try/finally，改呼叫 helper。
- 新增 `tests/test_analysis_drift.py` —— tripwire。
- （完成後）更新 docs：`docs/architecture.md`（line 110/111：analyze_jsonl 載入改注入）
  ＋ `design_config_map.md`（「載入來源」節：analyze_jsonl 移入隔離組、`config.py` 無程式改寫者）。

---

## Required Changes

### 1. `modules/config_inject.py` — 共用 config 注入 helper

```python
"""
modules/config_inject.py — 用 in-memory module 注入取代 config.py 磁碟改寫。

analyze_jsonl 與 drift tripwire 共用。載入 configs/{name}.py 成獨立 module、在記憶體上
覆寫 USE_LLM，再放進 sys.modules['config']。之後任何 `from config import ...`
(含 llm_analyzer.py:13) 都吃到這份；磁碟 config.py 全程不動。

必須在 llm_analyzer 首次 import 之前呼叫（注入在前、import 在後）。
"""
import importlib.util
import sys
from pathlib import Path

# 對齊 llm_analyzer.py:13-19 的 `from config import (...)` 完整清單（post-Task-Q 已核對，15 欄）。
# 缺任一欄，注入的 module 會在 llm_analyzer `from config import` 當下 ImportError。
_REQUIRED = [
    "SCREENING_MODEL", "ANALYSIS_MODEL", "TARGET_PRODUCT", "USE_LLM",
    "TARGET_DRUG", "TARGET_ROUTE", "TARGET_INDICATION",
    "SCREENING_IRRELEVANT_EXAMPLES",
    "RULE_DRUG_KEYWORDS", "RULE_ROUTE_KEYWORDS",
    "RULE_INDICATION_KEYWORDS", "RULE_ADDITIONAL_INDICATION_KEYWORDS",
    "CLAIMS_MAX_CHARS", "LLM_MAX_RETRIES", "LLM_RETRY_BASE_SECONDS",
]


def inject_config(config_path: str, *, use_llm: bool | None = None):
    """載入 config → (可選)覆寫 USE_LLM → 注入 sys.modules['config']。回傳該 module。"""
    p = Path(config_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    spec = importlib.util.spec_from_file_location("config", str(p))  # 模組名就叫 config
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if use_llm is not None:
        mod.USE_LLM = use_llm

    missing = [f for f in _REQUIRED if not hasattr(mod, f)]
    if missing:
        raise ValueError(f"Config '{p.name}' 缺欄位: {', '.join(missing)}")

    # 清掉可能已快取的 config / llm_analyzer，確保吃到這份注入
    # （取代 analyze_jsonl 原本散在 main() 的 del-sys.modules 舞步）
    for name in [n for n in sys.modules
                 if n == "config" or n.startswith("modules.llm_analyzer")]:
        del sys.modules[name]
    sys.modules["config"] = mod
    return mod
```

### 2. `scripts/analyze_jsonl.py` — 去磁碟改寫

移除 `_ensure_config` / `_restore_config` / `USE_LLM` 的 `write_text` patch / `del sys.modules`
迴圈 / 包住它們的 try/finally（已無東西需要還原）。改成：

```python
from modules.config_inject import inject_config

# 注入在前、import 在後 —— 磁碟 config.py 不動
inject_config(args.config, use_llm=args.use_llm)

from modules.llm_analyzer import analyze_patent
import modules.llm_analyzer as _analyzer_mod
# 其餘不變：rubric override 仍走 load_rubric → rebuild_analysis_chain（Task Q）；_run_batch 等
```

> `inject_config` 內已處理「清快取確保吃新 config」，原本 main() 裡的 `del sys.modules`
> 迴圈可一併刪。`--use-llm` / `--rubric-override` / `--compare` / `--skip-screening` 旗標語意不變。

### 3. `tests/test_analysis_drift.py` — drift tripwire

非侵入：只呼叫各自的 builder，**不建 LLM chain、不需 API key**（注入時 `use_llm=False`，
llm_analyzer import 就跳過 `if USE_LLM:` 的 chain 初始化，`llm_analyzer.py:149`）。

```python
"""
tests/test_analysis_drift.py — 盯 production 與兩座島 (debug_scoring / llm_bridge) 的
default 分析層是否一致。改 production rubric/schema 忘了同步島 → 這裡紅。
"""
import importlib.util
from pathlib import Path

from modules.config_inject import inject_config

CONFIG = "configs/pemirolast_ipf_v3.py"   # 任一穩定 config


def _load_cfg(path: str):
    spec = importlib.util.spec_from_file_location("_cfg_probe", str(Path(path).resolve()))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _production():
    inject_config(CONFIG, use_llm=False)     # 用同一支 helper 取得 production 視角
    import modules.llm_analyzer as a          # inject_config 已清快取 → fresh import
    return a


def test_analysis_system_prompt_no_drift():
    a = _production()
    cfg = _load_cfg(CONFIG)
    import tools.debug_scoring as dbg
    import api.core.llm_bridge as bridge

    prod = a.ANALYSIS_SYSTEM                                     # llm_analyzer.py:106
    assert dbg._analysis_system(cfg) == prod, "debug_scoring analysis prompt 漂離 production"
    assert bridge.analysis_system_prompt(cfg) == prod, "llm_bridge analysis prompt 漂離 production"


def test_analysis_schema_fields_no_drift():
    a = _production()
    cfg = _load_cfg(CONFIG)
    import tools.debug_scoring as dbg
    import api.core.llm_bridge as bridge

    prod = set(a.PatentAnalysis.model_fields)                    # llm_analyzer.py:45
    assert set(dbg._build_analysis_schema(cfg).model_fields) == prod   # debug_scoring.py:121
    assert set(bridge.build_analysis_schema(cfg).model_fields) == prod
```

---

## Expected Outcome

- 跑 `python scripts/analyze_jsonl.py --config configs/X.py ...`（任何旗標）後，
  磁碟 `config.py` bytes **不變**、且**無** `config.py.bak.analyze_jsonl` 殘留。
- `pytest tests/test_analysis_drift.py` 現在 **PASS**（三份 default 目前一致）；
  一旦改 production `ANALYSIS_SYSTEM` / `PatentAnalysis` 而沒同步兩座島 → **FAIL**。
- `main.py` 行為不變（仍讀固定 `config.py`）；`debug_scoring` / `llm_bridge` **不動**。

---

## Verification

```bash
# (1) 磁碟不再被改寫：跑前後比對 config.py 雜湊 + 確認無 .bak
md5sum config.py > /tmp/before.md5
python scripts/analyze_jsonl.py --input data/keloid_baseline.jsonl \
    --config configs/empagliflozin_keloid.py --dry-run
md5sum -c /tmp/before.md5              # 預期 OK（bytes 不變）
test ! -f config.py.bak.analyze_jsonl # 預期無殘留

# (1) 崩潰情境：跑到一半 Ctrl-C，config.py 仍應原封不動（舊版會殘留髒值 / .bak）

# (3) tripwire：現在應綠
pytest tests/test_analysis_drift.py -q

# (3) 反向驗證絆線有效：暫時改 llm_analyzer ANALYSIS_SYSTEM 一個字 → 應紅 → 還原
```

```python
# sanity：注入後 llm_analyzer 確實吃到指定 config（非磁碟 config.py）
from modules.config_inject import inject_config
inject_config("configs/pemirolast_ipf_v3.py", use_llm=False)
import modules.llm_analyzer as a
assert a.TARGET_PRODUCT  # 來自注入的 module，而非磁碟 config.py
```

---

## Non-Goals

- **不收 prompt 島**（`debug_scoring` / `llm_bridge` 各自 mirror）→ blocked on `rubric_v2` 拍板
  （architecture.md:453 仍 pending expert review）。
  - `debug_scoring` 很可能**永久隔離** —— 它是 rubric A/B 決策 + drift reference 工具，
    收斂就失去獨立 baseline。tripwire（#3）是它的替代看守。
  - `llm_bridge` 是 rubric 定案後的收斂候選（需先讓 `llm_analyzer` import-safe，另開 task）；
    在那之前 tripwire 看守其 default 漂移。**若 `/score` default 被下游依賴，漂移是真 bug。**
- **不改 `llm_analyzer` 的 import-time init**（本 task 用注入繞過，不需動它）。
- **不動 `main.py` 的 config 讀取**（讀固定 `config.py` 是刻意的 production 開關）。
- tripwire v1 **只比 system prompt + schema 欄位**；human template（島內 inline）比對需島先把
  字串 expose 成常數（**expose，非合併**），要全覆蓋再擴充。

---

## Commit plan

- `feat(config): 加 modules/config_inject 以 sys.modules 注入取代磁碟改寫`
- `fix(analyze_jsonl): 改用 config 注入，移除 config.py 備份/覆寫/還原`
  - body: 根因（llm_analyzer 模組層 from config import）/ 修法（注入在前 import 在後）/
    驗證（config.py 雜湊不變、無 .bak）/ refs: task_R.md #1 #2
- `test(analysis): 加 drift tripwire，盯 production/debug_scoring/llm_bridge default 一致`
  - body: 為何現在放（趕在 rubric_v2 併入 production 前）/ refs: task_R.md #3
