# findings — Spesolimab × GPP 可專利性 rubric 調校（收斂於 v3b）

- 日期：2026-10-15
- 性質：**positive-control scoring 驗證 + 專利工程師標準對齊**，非 FTO memo。spesolimab 是已上市 GPP 原研藥，當已知陽性用。
- 凍結輸入：`gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl`（104 篇，0911 存檔即凍結，本線全程未重抓）。
- 最終狀態：config `spesolimab_gpp_v2.py`（CLAIMS_MAX_CHARS=15000）＋ rubric `rubric_patentability_v3b.txt` ＋ `_make_llm` 加 `seed=42, top_p=0`，skip-screening，gpt-4o。
- 一句話結論：rubric 已逼近 **claims-only 的天花板（對齊工程師 8/11）**；剩下 3 個 miss 沒有一個是再改 prompt 能解的（2 個 plumbing、1 個資料源）。

> 全文 risk 計數一律用 **Low/Med/High** 順序（與 appendix H 的實驗紀錄一致）；`compare_runs` 畫面是 High 先，注意別看反。

---

## 1. 調整目標

**起點**：跟專利工程師 align 時，他認為工具的 `fto_risk` 其實比較接近「可專利性（新穎性）」而非 FTO。

**探查後修正的前提**（這決定了整條線的方向）：
- 工具從頭到尾只產出**一個**風險維度 `fto_risk`，欄位名是 FTO，但 prompt 實際做的是「claims 的 drug+indication 是否重疊」＝新穎性邏輯。**不是「兩個 risk 混在一起」，是一個被貼錯標籤的維度；侵權維度根本不存在**（工程師那欄是人工補的，需逐一 claim element 比對，工具沒做）。
- 因此目標不是「把兩個分數拆開」，是：做一份**可專利性（新穎性）框架的 rubric**，讓 High/Medium/Low 逐筆貼近工程師在 11 篇標註集上的「patentability (novelty)」判斷。

**成功標準**：對工程師標註**逐筆** match（不是看總 High 數）。理由見第 3 段的「positive-control 陷阱」。

**明確不做**：FTO/侵權；不改 scoring 的本質；不為衝高分而用鈍規則一律判 High。

---

## 2. 調整流程

### 2a. config / pipeline 調整

| 動作 | 檔案 | 改動 | 為什麼 | 證據 / 效果 |
|---|---|---|---|---|
| baseline | `spesolimab_gpp_v1.py` | CLAIMS_MAX_CHARS=3000、skip-screening | 照抄 darifenacin v2 | — |
| 修 truncation | → `spesolimab_gpp_v2.py` | CLAIMS_MAX_CHARS **3000→15000** | 97/104 篇 claims > 3000（median 8796）；治療 claim 排在冗長 biomarker 基因清單後被切（US20220290239A1 的「method of treating GPP」在 @4312） | cap 後治療 claim 進得了 model |
| 壓抖動 | `llm_analyzer._make_llm`（:153 附近） | + `model_kwargs={"seed":42,"top_p":0}` | gpt-4o 在 temperature=0 下仍有殘差，長 claims / 低資訊邊界案會跳（US12552870B2 Low↔Medium） | 加 seed 後同條件 3 次 **0 變動** |

### 2b. rubric 迭代（全部 config v2 / cap15k / skip-screening / gpt-4o）

| rubric | 基於 | 相對前版動了什麼 | 目標 | 全批 L/M/H | 關鍵結果 | 判定 |
|---|---|---|---|---|---|---|
| `rubric_v2.txt`（既有） | — | §5 同藥同適應症→High；讀所有 claim | （對照組） | 77/13/14 | 鈍觸發：US12712081B2（純診斷，該 Low）、US20220073628A1（該 Med）都判 High | 過度觸發 |
| `rubric_patentability_v1.txt` | 重寫 | novelty 框架；法律狀態不降分、claim 類型不降分、teaching vs 提及、fto_risk 重貼標籤 | 新穎性框架初版 | 90/7/7 | **7/11**。US12624397B2 / US20220290239A1 仍 Med；US20220119538A1 誤 High | 暫最佳（當時） |
| `rubric_patentability_v2.txt` | pv1 | **一次改 5 樣**（High gate 措辭／揭露／特異性／spec→Medium／能力邊界） | 一步到位 | 78/24/2 | High 掉到 2、Low→Medium 暴增，無法歸因是哪條造成 | **作廢**（違反 one-variable） |
| `rubric_patentability_v3a.txt` | pv1 | **只改原則 2**：看揭露、不看 claim 主旨（monitoring/biomarker claim 內的 administering 算治療揭露） | 修 biomarker | 91/5/8 | US12624397B2 翻 High ✓；但 US20220073628A1 誤抬 High（無特異性守門）→ 證明「放寬揭露」與「特異性守門」耦合 | 不完整 |
| **`rubric_patentability_v3b.txt`** | pv3a | **只加原則 5**：適應症特異性守門（GPP 為優先；僅 plaque psoriasis／neutrophilic／泛稱、或 GPP 附帶 → 封頂 Medium） | 修特異性 | 90/8/6 | US20220073628A1 / US20220119538A1 降 Medium ✓；**8/11** | **定版** |

> 注意：**全批 High 數不是指標**。pv3a 有 8 個 High、v3b 只有 6 個，但 v3b 逐筆對齊更準（8/11 vs pv3a 約 6/11）。High 多 ≠ 判得準（見 rubric_v2 的 14 High）。

### 2c. 方法紀律（非表格但關鍵）

- **一次只動一軸**：pv2 一次改 5 樣就無法歸因，退回 pv1 逐條加（pv3a 加原則 2 → pv3b 加原則 5）。
- **先 probe 再改**：truncation、loader、抖動都先量再動。
- **重複跑量抖動**：single-run 的 compare 不可信到逐筆；seed 前後各跑 3 次確認穩定，才敢把 v3b 定版。
- **已排除的死路（勿重追）**：
  1. 「claims 缺失／loader 漏抽／整批 abstract-only」→ **錯**。GPSS claims 巢狀在 `records[0].claims.claim[].claim-text`，`_gpss_text` 遞迴抽得到（實測 7718 字）；真因是 truncation。
  2. 旗標靜默被 `\` 吞掉（實跑預設 rubric 卻不自知）→ 鐵證是終端 `Mode:` / `Rubric:` 行，每次必看。

---

## 3. 結果比較（11 篇，穩定 v3b + seed vs 工程師）

| 專利號 | 類型 | 證據在 | 工程師 | v3b 穩定 | match | miss 槓桿 |
|---|---|---|---|---|---|---|
| US20220281987A1 | 治療 GPP | claims | High | High | ✓ | — |
| US20240352137A1 | 治療 GPP | claims | High | High | ✓ | — |
| US20240409646A1 | 治療 GPP | claims | High | High | ✓ | — |
| US20220073628A1 | 抗體・嗜中性皮膚病（未列 GPP） | claims | Medium | Medium | ✓ | —（特異性守門修對） |
| US20220119538A1 | 慢性疼痛（GPP 附帶） | claims | Medium | Medium | ✓ | —（特異性守門修對） |
| US12624397B2 | biomarker（granted） | claims | High | Medium | ✗ | plumbing（治療 claim 包在 monitoring 主旨） |
| US20220290239A1 | biomarker | claims | High | Medium | ✗ | plumbing（治療 claim 埋 >4000 字） |
| US11267893B2 | 抗體（組成項） | spec | High | High | ✓\* | —（\*矇對：abstract 命中，非讀 claim） |
| US20200017592A1 | 抗體（組成項） | spec | High | High | ✓\* | —（\*矇對） |
| US20250215098A1 | 抗體（組成/用途） | spec | High | High | ✓\* | —（\*矇對） |
| US12552870B2 | 抗體（CDR≠spesolimab） | spec | High | Medium | ✗ | 資料源（High 依據在 spec [0039]；claims 讀到不同 CDR→誠實 Medium） |

**合計 8/11。** 3 個 miss 全是 Medium-vs-High 的**安全 under-call**（仍進人工複審，非被漏掉的 Low）。
（對照：pv1 為 7/11，v3b 多修對 US20220119538A1；故 appendix H 中標在 pv1 的「\*\*\* closest」已被 v3b 取代。）

**分兩組看（不要看總分）**：
- **claims 組（7 筆）：5/7**。miss 的 US12624397B2 / US20220290239A1 是 rubric 寫對、model 注意力沒讀到 → plumbing，非 rubric。
- **spec 組（4 筆）：3/4，但 3 個「對」是矇的**（abstract 命中 High，非讀懂 claim）；唯一「miss」的 US12552870B2 反而是 model 誠實讀 claims（讀到不同 CDR）的結果。這組依據都在說明書，claims-only 搆不到。

**三個 miss 的槓桿（都不是 rubric）**：
- US12624397B2 / US20220290239A1 → **plumbing（claim prioritization）**。已評估：blast radius 過大（動全 104 篇餵入內容）、換取的是工程師說「可接受」的 polish → **判定不划算，不做**，接受 Medium。
- US12552870B2（＋那 3 筆矇對的組成項）→ **資料源**。要嘛補 full text、要嘛接受「組成項一律誠實 under-call 成 Medium + 旗標」。獨立決策，非 bug。

**⚠ positive-control 陷阱（務必隨結論保留）**：8/11 是在**偏 High 的集合**上跑的，**證明不了假陽性率**。那 3 筆矇對的組成項 High 是「abstract 命中」的鈍觸發——**正是會在真 repurposing target 上過度觸發的那種**。故 **v3b 上線到真實 repurposing 篩選（darifenacin / HS / keloid）前，必須在那邊重驗**，別拿 spesolimab 的 8/11 當保證。

---

## Appendix（回追用）

### A. 最終落地狀態
- config：`configs/spesolimab_gpp_v2.py`，唯一偏離 darifenacin v2 的一行＝`CLAIMS_MAX_CHARS = 15000`。
- rubric：`configs/rubrics/rubric_patentability_v3b.txt`（定版，勿再逐版微調）。
- 穩定性：`_make_llm` 加 `model_kwargs={"seed": 42, "top_p": 0}`。**這是重現性的一部分，像 temperature=0 一樣要固化、勿被誤改。** best-effort：OpenAI repoint / `system_fingerprint` 變則不保證。
- 模型：gpt-4o（浮動別名）。temperature=0 已確認生效（gpt-4o 不在 `NO_TEMPERATURE_MODELS`）。

### B. claims 長度分佈（本批 104 篇，決定 cap 選值）
`min 1922 / median 8796 / p90 37707 / max 227217`，**97/104 > 3000**。
- 15000 蓋得過 median 與本輪要驗的兩筆；但 p90/max 仍被切 → cap 只為解鎖偵錯，**完整批次對照需 claim prioritization，非更大 cap**（227217 會把基因清單／sequence listing 整包餵入，昂貴且雜訊）。

### C. rubric 版本語意 diff
- v1 → **v3a**：只改原則 2（「看揭露、不看 claim 主旨」：monitoring/biomarker claim 內的 `administering/treating` 算治療揭露；反向排除收窄成「完全沒有投藥步驟」才成立）。
- v3a → **v3b**：只加原則 5（「適應症特異性守門」：投藥揭露要成立 High，治療的必須是優先適應症 GPP；僅 plaque psoriasis / neutrophilic / 泛稱、或 GPP 附帶/關聯 → 封頂 Medium）。
- **v2 作廢**（一次改太多，無法歸因）。

### D. 抖動證據（對照 appendix H 的 run）
- seed 前 v3b：`v3b_rep1`(91/7/6) vs `rep2/rep3`(90/8/6) → US12552870B2 在 Low/Medium 跳；`pv3b initial`(90/7/7, 7 High) vs reps(6 High) → US12712081B2 曾 jitter 上去。
- seed 後 v3b（`seed=42,top_p=0`）：`rep1/2/3` 皆 **90/8/6，完全相同**；關鍵 11 筆 6× unanimous。

### E. 誤判更正紀錄（省下重走死路）
1. 「兩個 risk 混在一起」→ 實為一個被貼錯標籤的新穎性維度，侵權維度不存在。
2. 「claims 缺失 / loader 漏抽 / 整批 abstract-only」→ **錯**。GPSS claims 巢狀，`_gpss_text` 遞迴抽得到；真根因是 truncation。
3. 「flowchart High 可放寬成『監測 claim 投藥揭露→High』」一度被收回成「biomarker→Medium」→ **那次收回是錯的**；biomarker 若 claim 揭露投予該藥治 GPP，照 flowchart 該 High（US12624397B2 / US20220290239A1），只是 model 注意力沒讀到。
4. 把 US12552870B2 的 patentability 依據看成 infringement 欄的 CDR 內容 → 實際 patentability High 依據在 spec `[0039]`。
5. v3b 穩定表一度把 US11267893B2 / US20200017592A1 / US20250215098A1 誤記成 Medium → 實為 High（矇對）。最終數字是 **8/11**。

### F. 待辦（收尾後的獨立項目）
- [ ] US12624397B2 / US20220290239A1：接受 Medium；可選輕量後處理旗標（reasoning 命中 monitoring/biomarker + claims 含 drug alias + GPP → 標「⚠ 可能漏治療 claim」）。不改餵入、不動 model。
- [ ] spec 組：做「要不要補 full text」的資料源決策。
- [ ] schema/rubric 清理（綁下次改 llm_analyzer 一起）：`reasoning`、`gap_opportunity` 的輸出格式搬回 schema `Field(description=...)`（gap 的限定軸是搬家不是刪除）；`fto_risk` description 改 novelty 框架或 rename `patentability_risk`；之後 rubric 的「fto_risk 一律解讀為新穎性」補丁行可移除。
- [ ] `CLAIMS_MAX_CHARS` 考慮抽成 llm_analyzer 單一全域來源（現 per-config，跨藥對照時 darifenacin 要同步調，否則不公平）。
- [ ] gpt-6 luna：當**獨立變數**測（rubric 固定 v3b、有穩定基準後，換模型前後乾淨對比）。
- [ ] **v3b 在真 repurposing target 重驗假陽性**（見第 3 段警告）。

### G. 重現指令
```bash
INPUT=gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl
python3 -u scripts/analyze_jsonl.py --input "$INPUT" \
  --config configs/spesolimab_gpp_v2.py --skip-screening \
  --rubric-override configs/rubrics/rubric_patentability_v3b.txt \
  --prefix spesolimab_pv3b > logs/spesolimab_$(date +%Y%m%d_%H%M).log 2>&1
grep -iE 'Activated|Mode:|Rubric:' logs/spesolimab_*.log   # 確認 config v2 + rubric v3b 都生效

# 穩定性自檢（同條件重複，應 0 變動）：連跑兩次不同 --prefix 再 compare_runs
# 對齊檢視（11 筆 fto_risk + reasoning，按欄名取、不靠欄位位置）
```

### H. 使用者實驗紀錄（原始，未改；count 欄為 Low/Med/High）

| 輸出檔 | 日期 | config | scoring | L/M/H | 備註 |
|---|---|---|---|---|---|
| `spesolimab_patentability_v1_skipscreen_20261012_1208` | 10/12 | v1 | rubric_patentability_v1（skip-screening, positive control） | 94/2/8 | |
| `spesolimab_default_cap15k_skip_20261012_1442` | 10/12 | v2 | default（claim limit 15k） | 86/11/7 | |
| `spesolimab_rubricv2_cap15k_skip_20261012_1452` | 10/12 | v2 | rubric_v2（15k） | 77/13/14 | |
| `spesolimab_pv1_cap15k_skip_20261012_1455` | 10/12 | v2 | rubric_patentability_v1（15k） | 90/7/7 | \*\*\* closest one（10/12 當時；已被 v3b 取代） |
| `spesolimab_pv2_cap15k_skip_20261015_1134` | 10/15 | v2 | rubric_patentability_v2（15k） | 78/24/2 | |
| `spesolimab_pv3a_cap15k_skip_20261015_1158` | 10/15 | v2 | rubric_patentability_v3a（15k） | 91/5/8 | |
| `spesolimab_pv3b_cap15k_skip_20261015_1452` | 10/15 | v2 | rubric_patentability_v3b（15k） | 90/7/7 | # initial run |
| `spesolimab_v3b_rep1_20261015_1509` | 10/15 | v2 | v3b | 91/7/6 | # rep 1 |
| `spesolimab_v3b_rep2_20261015_1512` | 10/15 | v2 | v3b | 90/8/6 | # rep 2 |
| `spesolimab_v3b_rep3_20261015_1514` | 10/15 | v2 | v3b | 90/8/6 | # rep 3 |
| `spesolimab_v3b_rep1_20261015_1527` | 10/15 | v2 | v3b | 90/8/6 | # rep 1（seed=42, top_p=0）# stable version |
| `spesolimab_v3b_rep2_20261015_1530` | 10/15 | v2 | v3b | 90/8/6 | # rep 2（seed） |
| `spesolimab_v3b_rep3_20261015_1532` | 10/15 | v2 | v3b | 90/8/6 | # rep 3（seed） |
