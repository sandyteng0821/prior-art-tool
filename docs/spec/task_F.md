# Task F — Excipient Pipeline Evaluation, CSV-driven

> 存檔備查。實作過程中的微調紀錄在對應的 chat 對話裡。
> 前置條件：Task E 完成，`tools/eval_v0.py` 已驗證 pipeline 機制可運作。
> 完成後請更新 `docs/architecture.md`。

**Post-implementation note (added after completion):**
Implementation followed spec closely. Four points worth recording:

1. `--api-groups` changed from single string to `nargs='+'` (list of
  strings) to align with the recommend API's `list[str]` schema.
  One-line diff, didn't warrant a separate spec. See excipient
  pipeline API docs in project knowledge.

2. STEP 2b's `OK: ... share tokens` line was removed after testing
  showed it printed misleadingly on the `--force` bypass path
  (claimed tokens were shared when guard had been bypassed).

3. V1 P@5/P@10 (0.40 / 0.20) numerically equals V0's, but the hits
  are completely different keywords:
  - V0 hits in top 5: polymethacrylate, MCC
  - V1 hits in top 5: MCC, polyethylene glycol
  The matching numbers are coincidence, not evidence of equivalent
  evaluation quality.

4. Three compounding causes for V1 ground truth not expanding despite
  31× larger candidate set (187 vs V0's 6):

  a. **Narrower keywords (spec-predicted, see §V0→V1 Differences)**:
      V0's hardcoded `"lactose"` caught all variants. V1's
      `"lactose, anhydrous"` / `"anhydrous lactose"` misses bare
      `"lactose"` mentions. Probe confirmed: 187-patent CSV has 6
      patents containing `"lactose"` substring, but V1 matched 0 of
      them.

  b. **Candidate-set curation difference (NOT in spec)**: V0's 6
      hardcoded patents = 3 auto-found + 3 manually-curated from
      `ampicillin_formulation.md`. The patent that contributed V0's
      polymethacrylate evidence (`US2013029965A1`) was manually added,
      not from auto search, and is **NOT** in V1's CSV. V1 evaluates
      a purely auto-derived candidate set, V0 evaluated a partially
      human-curated one. The two are different universes.

  c. **Abstract-only majority (architecture.md §EPO licensing)**:
      Most of V1's 187 candidates are CN/US (no claims/examples
      available). New candidates contribute thin evidence by volume.

Per spec §Verification: "The verification does not claim P@k will
improve over V0." V1 ships as pipeline-mechanics validation. The
implicit thesis "candidate-set expansion → stronger ground truth" is
partially refuted, but reasons (a) and (b) suggest the issue is in
V1's design tradeoffs rather than ground truth being fundamentally
unobtainable. Future tasks could:
    - Add bare-noun fallback to keyword derivation (offsets cause a)
    - Use `ampicillin_formulation.md` as a curated seed set (task_G,
      addresses cause b)
    - Wait for Google Patents fulltext integration (offsets cause c)

本 spec 保留原樣以記錄 spec/implementation/reality 落差，作為 LLM
協作範本資料集的一部分。

---

## Context

`tools/eval_v0.py` (Task E) 驗證了 pipeline 機制：DB read → keyword match → recommend API → P@k。
input 是 hardcoded 的 6 個 patent + hardcoded keyword list。

實際使用時希望能：

1. 換不同的 prior art run（每次 `main.py` 輸出的 `gap_analysis_*.xlsx`）
2. 換不同的 drug × target excipient（Acetaminophen + MCC、Acetaminophen + Suspension 等）
3. 不要每次都手動維護 keyword list

Task F 把 V0 的 hardcode 部分換成 CLI args + CSV input + 動態 keyword list。
**Step 2–5 邏輯完全沿用 V0，不重寫。**

---

## V0 → V1 Differences

| Dimension | V0 | V1 |
|-----------|----|----|
| Patent list | Hardcoded 6 IDs in source | `--csv` from gap_analysis Excel/CSV |
| Keyword list | Hardcoded 13 keywords curated by hand | Derived dynamically from recommend API + ABBREVIATIONS map |
| Drug / target | Hardcoded `Ampicillin` / `Lactose, Anhydrous` | `--drug` / `--target-excipient` CLI args |
| api_groups | Hardcoded (later removed) | `--api-groups` optional, default to PubChem auto-detect |
| Typo guard | None — silent failure on garbage input | `_share_token` check between input and `matched_as`; `--force` bypass |
| Output filename | `{drug}_{target_safe}_v0.json` from CLI literals | `{drug}_{target_safe}_v1.json` from `matched_as` (reflects what was actually evaluated) |
| JSON provenance | drug, target, candidate_patents, keywords | + version, csv_source, user_input_target, matched_as, keyword_source |
| Step 2–5 logic | — | Identical to V0 |
| Match logic (`normalize` + `is_hit`) | — | Identical to V0 |
| File location | `tools/eval_v0.py` | `tools/eval_v1.py` (coexists with V0) |

### Keyword list — closer inspection

V0 keyword list was hand-curated to substring-match patent text. It mixed singulars
(`polymethacrylate`), abbreviations (`mcc`, `peg`, `cmc`), and base nouns
(`calcium phosphate`, `carboxymethylcellulose`).

V1 derives keywords mechanically from the recommend API's top 10 names. API names
use canonical comma-form (`"Cellulose, Microcrystalline"`), but patent text uses
natural word order (`"microcrystalline cellulose"`). The derivation must produce
**both forms** to substring-match patent text correctly. See Change 2 for the
reverse-order rule.

V1's keyword list is mechanically reproducible but **slightly less aggressive** than
V0:
- V0 had `"cellulose"`-like short tokens that could substring-match many variants
- V1 keeps full two-word phrases (`"microcrystalline cellulose"`, `"powdered cellulose"`)
  to avoid polluting ground truth with unrelated cellulose derivatives

This is an intentional precision-over-recall tradeoff for V1.

---

## Pre-implementation Probes

> PROJECT_SKILL §3.1: Don't trust spec assumptions — probe first.
> 本 spec 不預測 P@k 變化，只描述 input layer 怎麼改。實際 ground truth
> 規模、P@k 結果都看 probe 數據和實跑結果再說。

### Probe 1：CSV 結構
跑一次 `pd.read_excel('output/gap_analysis_20260508_1645.xlsx').columns` 確認
`patent_id` 欄位名稱跟假設一致。其他 prior art run 的 CSV 也應該檢查（schema
可能微妙不同）。

### Probe 2：188 篇的內容覆蓋分布
跑一次 SQL 統計 187 筆在 DB 裡的 `abstract / claims / examples_extracted`
長度分布——多少是 EP granted（有 claims）、多少是 US/CN/EA/KR（只有 abstract）。
這個結果決定了「擴大 candidate set 後 ground truth 會不會明顯改善」，
不要在 spec 階段假設答案。

### Probe 3：Fuzzy match 穩定性（2026-05 已完成）

**結果摘要：**

| Property | Result |
|---|---|
| Deterministic（同 input 跑 5 次）| ✅ 一致 |
| Case-insensitive | ✅ `lactose` / `Lactose` / `LACTOSE ANHYDROUS` 都對到 `Lactose, Anhydrous` |
| Partial input | ⚠️ `"lactose"` → `Lactose, Anhydrous`（取 prefix match 第一筆；換 DB 順序可能變） |
| Typo handling | ❌ `"latose"` → `Ammonium Alginate`（silent failure，rapidfuzz 強制 fallback） |

**結論：** Fuzzy match 對「拼對的不完整輸入」可用，對「拼錯」沒有任何保護。
V1 必須處理 typo silent failure。

---

## Goal

新增 `tools/eval_v1.py`，接受 CLI args，從 CSV/Excel 讀 patent list，
自動產生 keyword list。**不動 `tools/eval_v0.py`。**

---

## Files to Create

- `tools/eval_v1.py`（新檔，與 `tools/eval_v0.py` 並存）

---

## CLI Interface

```bash
# Basic usage
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "Lactose, Anhydrous"

# Manual api_groups override (skip PubChem)
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "Lactose, Anhydrous" \
  --api-groups "Primary Amine"

# Force run even when fuzzy match looks suspect
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "latose" \
  --force
```

### Arguments

| Arg | Required | Description |
|-----|----------|-------------|
| `--csv` | ✅ | Path to gap analysis CSV/Excel. Must have `patent_id` column. |
| `--drug` | ✅ | Drug name (API name for recommend endpoint) |
| `--target-excipient` | ✅ | Target excipient name (fuzzy matched by API) |
| `--api-groups` | — | Manual functional group override. If omitted, API queries PubChem. |
| `--k` | — | Comma-separated k values for P@k. Default: `5,10` |
| `--force` | — | Skip the typo guard (see Pipeline → Change 3). |

---

## Pipeline

5-step pipeline same as V0. Three changes:

### Change 1: Patent list source (Step 1)

V0 hardcodes `candidate_patents = [...]`. V1 reads from `--csv`:

- Detect `.xlsx`/`.xls` vs `.csv` from extension, dispatch to `pd.read_excel` / `pd.read_csv`
- Require `patent_id` column; missing column → print available columns + `exit(1)`
- Strip whitespace, drop nulls
- Remaining DB read logic identical to V0 (`fetch_patent_text`, skip + warning on miss)

### Change 2: Keyword list source (between Step 1 and Step 2)

V0 hardcodes `excipient_keywords = [...]`. V1 derives it from the recommend API:

- **Call recommend API once at start of run** (before Step 2). Store `recommendations`,
  `matched_as`, `api_context` as locals.
- Step 2 uses these to build keyword list.
- Step 3 reuses the same `recommendations` variable — **no second API call**.

#### Keyword derivation logic

For each canonical name (target excipient + top 10 recommendation names), produce
all forms that may appear in patent text:

```python
def derive_keywords_from_name(name: str) -> list[str]:
    """
    "Cellulose, Microcrystalline" → ["cellulose, microcrystalline",
                                      "microcrystalline cellulose"]
    "Lactose, Anhydrous"          → ["lactose, anhydrous",
                                      "anhydrous lactose"]
    "Polyethylene Glycol"         → ["polyethylene glycol"]
    "Carboxymethylcellulose Calcium" → ["carboxymethylcellulose calcium"]
    """
    name = name.lower().strip()
    out = [name]
    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        natural = " ".join(reversed(parts))   # reverse order, not sort
        if natural != name:
            out.append(natural)
    return out
```

**Why reverse, not sort:**

Patent text uses natural English word order
(`"microcrystalline cellulose"`, `"anhydrous lactose"`, `"tribasic calcium phosphate"`).
API canonical form is `"<noun>, <modifier>"`. Reversing the comma-split parts
produces the natural-language phrasing patents actually use. Sorting would produce
nonsense word orders that don't appear in any patent text.

**Why not strip to the bare noun (e.g. `"cellulose"`):**

Bare nouns substring-match too much. `"cellulose"` would falsely hit
`"cellulose acetate"`, `"hydroxypropyl cellulose"`, `"cellulose ether"`, etc.,
polluting ground truth. Two-word phrases retain enough specificity to avoid
false positives. This is a deliberate precision-over-recall choice — see
"V0 → V1 Differences > Keyword list — closer inspection".

#### Building the full keyword list

1. Start with `matched_as` (lowercased), expand via `derive_keywords_from_name`
2. For each name in top 10 recommendations, expand via `derive_keywords_from_name`
3. Apply abbreviation map:

```python
ABBREVIATIONS = {
    "microcrystalline cellulose":     "mcc",
    "polyethylene glycol":            "peg",
    "carboxymethylcellulose":         "cmc",
    "hydroxypropyl methylcellulose":  "hpmc",
}
# If any derived keyword matches a key in ABBREVIATIONS, also add the value.
```

4. Deduplicate (preserve order)

> **Use `matched_as`, not `--target-excipient`.** V0 used the literal CLI input,
> which can disagree with what the API actually matched. V1 anchors everything
> on `matched_as` so output files, keyword list, and report are all consistent
> with what was actually evaluated.

### Change 3: Typo guard (right after API call, before keyword derivation)

Probe 3 showed `"latose"` → `Ammonium Alginate` with no warning from the API.
A typo in `--target-excipient` would silently produce a nonsense eval.

Compare normalized `--target-excipient` against normalized `matched_as`:

```python
def _share_token(a: str, b: str) -> bool:
    """True if normalized a and b share at least one token."""
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    return bool(ta & tb)

if not _share_token(args.target_excipient, matched_as):
    print(f"[!] target_excipient '{args.target_excipient}' fuzzy-matched to "
          f"'{matched_as}' but they share no tokens.")
    print(f"    This is likely a typo. Re-run with --force to proceed anyway.")
    if not args.force:
        exit(1)
```

Rationale: cheap typo trap. False positives (legitimate aliases that share no
tokens) are rare for excipient names and `--force` covers them.

---

## Output

```
outputs/ground_truth/{drug}_{target_safe}_v1.json
```

`target_safe` derived from `matched_as`, not `--target-excipient` (so output filename
reflects what was actually evaluated, not what the user typed).

Same schema as V0, plus new fields documenting provenance:

```json
{
  "version":            "v1",
  "csv_source":         "output/gap_analysis_20260508_1645.xlsx",
  "user_input_target":  "lactose anhydrous",
  "matched_as":         "Lactose, Anhydrous",
  "keyword_source":     "dynamic (recommend API top 10 + ABBREVIATIONS map)",
  ...
}
```

> Filename suffix `_v1` distinguishes from V0 output. If V1 re-runs with a
> different CSV, the file is overwritten — this is intentional for now;
> if multi-run comparison is needed, add a timestamp suffix in a future task.

---

## Code Constraints

| Item | Decision |
|------|----------|
| Single file | Yes — `tools/eval_v1.py` |
| Libraries | V0 set + `argparse`, `pandas` (pandas already in venv) |
| Classes / dataclasses | No |
| Async / logging frameworks | No |
| Error handling | CSV missing column → print + exit; DB miss → skip + warning; API failure → print + exit; typo guard → print + exit unless `--force` |
| Match logic | Identical to V0 (`normalize` + `is_hit`) |
| Modify `tools/eval_v0.py` | No |
| Modify `modules/` | No |

---

## Verification

```bash
# 1. Basic run with Ampicillin × Lactose, full 188-patent CSV
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "Lactose, Anhydrous"

# 2. Inspect ground truth JSON
cat outputs/ground_truth/Ampicillin_Lactose_Anhydrous_v1.json | python -m json.tool

# 3. Error handling — missing patent_id column
python -m tools.eval_v1 --csv <some_csv_without_patent_id> --drug X --target-excipient Y
# Expected: print available columns, exit(1)

# 4. Typo guard — known nonsense input
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "latose"
# Expected: print typo warning, exit(1) (matched_as='Ammonium Alginate' shares no token)

# 5. --force bypass
python -m tools.eval_v1 \
  --csv output/gap_analysis_20260508_1645.xlsx \
  --drug Ampicillin \
  --target-excipient "latose" \
  --force
# Expected: warning printed, run continues (eval will be against Ammonium Alginate)

# 6. Spot-check keyword derivation
#    Inspect the generated keyword list; confirm:
#      - "Cellulose, Microcrystalline" produced both comma-form AND
#        "microcrystalline cellulose" (natural order)
#      - "Polyethylene Glycol" (no comma) produced only one form
#      - "mcc" and "peg" appear (ABBREVIATIONS map applied)
#      - No bare "cellulose" or "lactose" keyword on its own

# 7. Confirm same recommend API result as a manual Swagger call with same args
#    (sanity check that the dynamic keyword path doesn't double-call or mutate response)
```

The verification does **not** claim P@k will improve over V0. That's an empirical
question to be answered after running, not a spec assumption.

---

## Known Limitations (inherited from API layer)

These are documented for users of V1, not for V1 to fix:

- **Fuzzy match for partial input is order-dependent.** `"lactose"` resolves to
  `Lactose, Anhydrous` because of DB row order; could be `Lactose, Monohydrate`
  on a different DB instance. If precision matters, pass the full canonical name.
- **No typo correction at API layer.** Rapidfuzz always returns the nearest
  neighbour, even when nonsense. V1's `--target-excipient` typo guard partially
  mitigates this for the input itself, but does not protect against subtle typos
  that happen to share a token with the wrong target.
- **Keyword recall is narrower than V0 by design.** V1 does not produce bare-noun
  keywords (`"cellulose"`, `"lactose"`), only two-word-or-more phrases. Patents
  that write only `"lactose"` without modifier may be missed. Compensated by
  the larger candidate set from `--csv`.

---

## Non-Goals

- 不重寫 Step 2–5 邏輯
- 不改 `tools/eval_v0.py`
- 不動 `modules/` 任何檔案
- 不做 human-verified ground truth（另外規劃；可能成為 task_G）
- 不做 fuzzy keyword matching（V2+）
- 不支援 batch run（多 drug × excipient 一次跑完，V2+）
- 不在 spec 預測 P@k 結果——實跑為準
- 不在 V1 修補 API 層的 fuzzy match 行為——只在 client 側加 guard