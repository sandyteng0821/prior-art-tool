# HS Prior Art — Repurposing Search Operation Playbook

> Operational runbook for the HS drug-repurposing patentability screen
> (Pass 1 EPO → Pass 2 GPSS → analyze). Use this at execution time, not
> at design time.
>
> **Per-drug aliases, exact filenames and counts are NOT hardcoded here** —
> look them up at runtime (`grep -A10 DRUG_ALIASES configs/<drug>_hs_v1.py`,
> `ls -t output/gap_analysis_*.csv`). Config and run artifacts evolve; this
> playbook does not. The dated snapshot in §5 is reference, not truth —
> re-measure each run.
>
> Candidates this run: Doxapram / Hydroflumethiazide / Thiothixene.

---

## 0 · Pre-flight

```bash
cp config.py config.py.bak                 # backup active config (local-only, never commit)
ls configs/*_hs_v1.py                       # confirm the drug configs exist
git check-ignore gpss-probe/.env.code2      # confirm TIPO credential is git-ignored
```

`config.py` is swapped per drug below and is **local-only** — never commit
it; commit `configs/*_hs_v1.py` snapshots instead. `.env*` (except
`.env-example`), `cache/`, `data/`, `output/`, `logs/` must be git-ignored.

---

## 1 · Pass 1 — EPO (per drug: rule-validate → LLM)

One round per drug. Below is doxapram; repeat with each config filename.
**Each `nohup` must finish before the next** — they share `config.py`.

### 1.a · Swap config + rule-mode validate (free)

```bash
cp configs/doxapram_hs_v1.py config.py      # USE_LLM=False in config → rule mode
mkdir -p logs
LOG=logs/doxapram_hs_$(date +%Y%m%d_%H%M).log
nohup python3 -u main.py > "$LOG" 2>&1 & echo "PID $! → $LOG"
```

Expected output (`tail -f "$LOG"`):
- Strategy list printed; `[EPO fetch]` / `[family member]` lines
- `[_fetch_claims] <US/CN/CA>...: 404` — **expected** (licensing, not a bug);
  EP-B1 / WO claims DO fetch
- Report → `output/gap_analysis_*.csv` (+ `.xlsx`)

### 1.b · Flip to LLM, re-run (uses DB cache, only LLM cost)

```bash
sed -i 's/^USE_LLM = False/USE_LLM = True/' config.py
LOG=logs/doxapram_hs_llm_$(date +%Y%m%d_%H%M).log
nohup python3 -u main.py > "$LOG" 2>&1 & echo "PID $! → $LOG"
```

Repeat 1.a–1.b for `hydroflumethiazide_hs_v1.py` and `thiothixene_hs_v1.py`.

> Models come from config (`gpt-4o-mini` / `gpt-4o`). **Do not run two
> `main.py` concurrently** — they fight over `config.py`.

---

## 2 · Pass 1 → Pass 2 handoff (CSV → patent IDs)

Look up each drug's latest CSV at runtime (`ls -t output/gap_analysis_*.csv`),
then:

```bash
python3 tools/csv2ids.py output/gap_analysis_<ts_doxapram>.csv data/doxapram_hs_ids.txt
python3 tools/csv2ids.py output/gap_analysis_<ts_hydro>.csv    data/hydroflumethiazide_hs_ids.txt
python3 tools/csv2ids.py output/gap_analysis_<ts_thio>.csv     data/thiothixene_hs_ids.txt
```

Expected output per call:
- `id 欄 = 'patent_id'(自動偵測,命中 N/N 列)` — if detection looks wrong,
  pass `--id-col patent_id`
- `專利號 = M 筆(去重) → data/<drug>_hs_ids.txt`

---

## 3 · Pass 2 — GPSS fetch (merge 3 drugs into one JSONL)

The three drugs share one HS sweep → `--resume` into a single `--out` so the
~700 overlapping landscape patents are fetched **once**.

### 3.a · First drug (creates the shared file)

```bash
LOG=logs/doxapram_hs_gpss_$(date +%Y%m%d_%H%M).log
nohup python3 -u gpss-probe/fetch_by_pn.py --ids data/doxapram_hs_ids.txt \
  --out data/hs_gpss.jsonl --env-file ./gpss-probe/.env.code2 --pause 12 > "$LOG" 2>&1 & echo "PID $!"
```

Expected output:
- `[canary] PN=US09415051B1 ... ✅ 參數正常` — params + credential OK
- per-ID `✓` lines; `·` (empty) for misses (miss = 0 quota)
- Summary: 管轄覆蓋率 table, 配額消耗

### 3.b · Remaining drugs (serial, --resume appends only new IDs)

```bash
LOG=logs/hs_gpss_hydro_thio_$(date +%Y%m%d_%H%M).log
nohup bash -c '
python3 -u gpss-probe/fetch_by_pn.py --ids data/hydroflumethiazide_hs_ids.txt --out data/hs_gpss.jsonl --env-file ./gpss-probe/.env.code2 --pause 12 --resume &&
python3 -u gpss-probe/fetch_by_pn.py --ids data/thiothixene_hs_ids.txt        --out data/hs_gpss.jsonl --env-file ./gpss-probe/.env.code2 --pause 12 --resume
' > "$LOG" 2>&1 & echo "PID $!"
```

Expected: each `--resume` logs `既有 N hit / M miss，略過 X，剩 Y` where
**Y is small** (only each drug's new IDs, ~tens). If Y is still ~700,
`--resume` isn't reading the same `--out` — check the path.

> `--pause 12` is conservative (avoid TIPO rate-block / IP ban). Lower to
> 3–5 if you've run GPSS before without a block; `--resume` covers interrupts.
> **Never run two fetches concurrently on the same `--out`** — interleaved
> appends corrupt the JSONL.

---

## 4 · Pass 2 — analyze (rubric_v2 + skip-screening)

This is the **memo basis** (Pass 1's LLM output used the default rubric and
was only for ID harvesting).

```bash
LOG=logs/hs_llm_3drug_$(date +%Y%m%d_%H%M).log
nohup bash -c '
for d in doxapram hydroflumethiazide thiothixene; do
  echo "=== $d ==="
  python3 scripts/analyze_jsonl.py --input data/hs_gpss.jsonl \
    --config configs/${d}_hs_v1.py \
    --rubric-override configs/rubrics/rubric_v2.txt \
    --skip-screening --prefix gp_hs_${d}
done
' > "$LOG" 2>&1 & echo "PID $! → $LOG"
```

Expected output per drug:
- `Mode: LLM (rubric override) + skip-screening`, `Rubric: ...rubric_v2.txt`
- `Loaded: N patents / Skipped: K no content` — **N is the analyzed count**
  (lines − no-content), NOT the ok count and NOT any Low count
- Output `output/gp_hs_<drug>_*.csv` + summary High/Medium/Low

> **`analyze_jsonl` config-swaps internally — never run two at once.**

---

## 5 · Verified snapshot (2026-10-02 — reference only, re-measure each run)

| Stage | Value |
|---|---|
| Pass 1 CSV rows → distinct IDs (doxa / hydro / thio) | 714→710 / 721→718 / 735→732 |
| `hs_gpss.jsonl` lines | 776 (= 760 ok + 16 miss) |
| analyze `Loaded` (per drug) | 760 (776 − 16 no-content) |
| Risk per drug (skip-screening + rubric_v2) | doxa 16 M / 744 L · hydro 39 / 721 · thio 6 / 754 · **all High = 0** |

Three numbers that got confused before — keep them straight:
**776 lines ≠ 760 ok ≠ 744** (744 is only doxapram's Low count). Re-measure:
```bash
wc -l < data/hs_gpss.jsonl
python3 -c "import json,collections;print(collections.Counter(json.loads(l).get('verdict') for l in open('data/hs_gpss.jsonl') if l.strip()))"
grep -i 'Loaded\|Skipped' logs/hs_llm_3drug_*.log
```

---

## 6 · Automation boundary (read before scripting this end-to-end)

The **plumbing** (config → report → GPSS → analyze → candidate list +
landscape numbers) can be chained to near-one-command. These steps **must
stay human** — automation only prepares the material, it does not conclude:

- **False-positive judgement** — keyword scans surface candidates; a human
  decides real vs noise. (This run: `abscess` ×3, `IL-1`=66, `d2`=11 were
  all false positives.) Any scan tool MUST use word-boundary (`\b`) regex,
  never bare substring — else it automates the wrong numbers.
- **Go/No-Go call** — human.
- **NPL (PubMed / ClinicalTrials.gov)** — structurally outside this pipeline
  (patents only), and the biggest novelty risk for repurposing. A pipeline
  "green" without NPL is a false green.

---

## 7 · Recovery / rerun

- **Pass 1** — re-running `main.py` re-uses the DB cache (fetched patents
  aren't re-pulled); safe to re-run.
- **GPSS fetch** — `--resume` skips IDs already in `--out`; interrupt-safe,
  miss costs no quota. `--retry-misses --try-no-kind` retries misses.
- **analyze** — **no resume**: writes the CSV only at the end. If killed
  mid-run, re-run that drug from scratch. config-swap restores via
  try/finally; if hard-killed (SIGKILL / power loss), check `config.py`
  isn't left mid-swap and `config.py.bak.analyze_jsonl` for the original.

---

## Appendix · Quick reference

```bash
# Pass 1 (per drug): rule-validate, then flip LLM
cp configs/<drug>_hs_v1.py config.py && nohup python3 -u main.py > logs/<drug>.log 2>&1 &
sed -i 's/^USE_LLM = False/USE_LLM = True/' config.py && nohup python3 -u main.py > logs/<drug>_llm.log 2>&1 &

# handoff
python3 tools/csv2ids.py output/gap_analysis_<ts>.csv data/<drug>_hs_ids.txt   # --id-col patent_id if detection off

# Pass 2 fetch (first drug plain; rest with --resume, same --out)
nohup python3 -u gpss-probe/fetch_by_pn.py --ids data/<drug>_hs_ids.txt \
  --out data/hs_gpss.jsonl --env-file ./gpss-probe/.env.code2 --pause 12 [--resume] &

# Pass 2 analyze (memo basis)
python3 scripts/analyze_jsonl.py --input data/hs_gpss.jsonl \
  --config configs/<drug>_hs_v1.py --rubric-override configs/rubrics/rubric_v2.txt \
  --skip-screening --prefix gp_hs_<drug>
```
