# Spesolimab × GPP — Scoring Experiment Playbook

> Operational runbook for the spesolimab **positive-control scoring experiment**
> (frozen GPSS JSONL → analyze under varying rubric / screening axes → compare).
> Use this at execution time, not at design time. Purpose is **validating &
> tuning the scoring logic**, NOT producing an FTO memo (spesolimab is a known
> marketed GPP drug — it is the positive control).
>
> **Timestamps, row counts and output filenames are NOT hardcoded here** —
> look them up at runtime (`ls -t output/spesolimab_*.csv`). Run artifacts
> evolve every time you re-tune; this playbook does not. The snapshot in §5 is
> reference, not truth — re-measure each run.
>
> Target this run: Spesolimab (Spevigo, anti-IL-36R mAb) · input frozen from 0911.

---

## 0 · Pre-flight

```bash
cd ~/dev-workplace/prior_art_tool
source venv/bin/activate                         # prompt shows (venv)
echo "${OPENAI_API_KEY:+set}"                    # must print "set"

cp config.py config.py.bak                       # backup active config (local-only, never commit)
ls configs/spesolimab_gpp_v1.py                  # committed drug config exists
ls configs/rubrics/rubric_v2.txt                 # committed rubric exists
wc -l < gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl   # frozen input: expect 104
```

- **Input is frozen, not fetched.** This experiment reuses the 0911 GPSS JSONL
  (`gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl`). There is **no Pass 1 EPO /
  Pass 2 fetch here** — the 104-patent input is a saved 0911 snapshot. GPSS
  backend drift cannot touch it. Guard this file; it is the one input to keep.
- `config.py` is swapped internally by `analyze_jsonl` (try/finally restore, see
  §7) and is **local-only** — never commit it. Commit `configs/spesolimab_gpp_v1.py`
  and `configs/rubrics/rubric_v2.txt` instead (already done).

---

## 1 · The scoring-experiment axes (read before running)

This is a **one-variable-at-a-time** experiment. Two axes are being tuned:

| Axis | Values | Flag |
|------|--------|------|
| **rubric** | default · v2 | `--rubric-override configs/rubrics/rubric_v2.txt` |
| **screening** | on (2-stage) · skip (all → analysis) | `--skip-screening` |

> **Change ONE axis per run.** A clean diff needs a single moving part — if you
> swap rubric *and* screening together, `compare_runs` can't attribute the delta.
> Pick a distinct `--prefix` per cell so outputs never overwrite (anti-pattern #2).

Run matrix (prefix convention — pick what you need, not all four every time):

| # | rubric | screening | prefix |
|---|--------|-----------|--------|
| 1 | default | on | `spesolimab_positive_control` |
| 2 | v2 | on | `spesolimab_positive_control_rubric_v2` |
| 3 | default | skip | `spesolimab_full_analysis_skipscreen` |
| 4 | v2 | skip | `spesolimab_rubric_v2_skipscreen` |

---

## 2 · Run a cell

Baseline (#1) shown; swap flags + prefix per the matrix above. Models come from
config (`gpt-4o-mini` / `gpt-4o`). **Never run two `analyze_jsonl` at once** —
they fight over `config.py` (§7).

```bash
INPUT=gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl
LOG=logs/spesolimab_$(date +%Y%m%d_%H%M).log ; mkdir -p logs

nohup python3 -u scripts/analyze_jsonl.py \
    --input "$INPUT" \
    --config configs/spesolimab_gpp_v1.py \
    --prefix spesolimab_positive_control \
    --use-llm \
    > "$LOG" 2>&1 & echo "PID $! → $LOG"
```

Variants (change only the marked lines):
```bash
# #2 rubric_v2          add:  --rubric-override configs/rubrics/rubric_v2.txt
#                       set:  --prefix spesolimab_positive_control_rubric_v2
# #3 skip-screening     replace --use-llm with --skip-screening (implies --use-llm)
#                       set:  --prefix spesolimab_full_analysis_skipscreen
# #4 both              --skip-screening + --rubric-override …  --prefix spesolimab_rubric_v2_skipscreen
```

Expected output (`tail -f "$LOG"`):
- **`Mode:` line is the proof the flags took effect** — read it every run:
  - `Mode: LLM` (#1) · `Mode: LLM (rubric override)` + `Rubric: …rubric_v2.txt` (#2)
  - `Mode: LLM + skip-screening` (#3) · both tags (#4)
  - ⚠ if a flag you passed is missing here, a `\` broke the line and it was
    silently dropped (anti-pattern #1) — kill and re-run.
- `Loaded: N patents / Skipped: K no content` — **N is the analyzed count**
  (lines − no-content), NOT any Low count. For this frozen input lines = 104.
- Output `output/<prefix>_<ts>.csv` (+ summary High/Medium/Low).

> `--skip-screening` forces all 104 through `gpt-4o` (≈10× the part-screening
> cost). Fine for a tuning run; not a daily default.

---

## 3 · Compare two cells

Look up the two CSVs at runtime, newest-first, then diff:

```bash
ls -t output/spesolimab_positive_control_*.csv \
       output/spesolimab_positive_control_rubric_v2_*.csv | head

python3 tools/compare_runs.py \
    output/spesolimab_positive_control_<ts>.csv \
    output/spesolimab_positive_control_rubric_v2_<ts>.csv
```

Expected output:
- Risk 分佈 OLD vs NEW · Upgrades / Downgrades / Unchanged · per-ID risk moves.
- A clean one-axis diff should move **few** patents. A flood of moves (or any
  Downgrade you didn't predict) means the axis did more than intended — inspect
  before trusting.

---

## 4 · Interpret (the scoring questions this experiment answers)

Per-run, pull the patents that matter rather than eyeballing the whole CSV:

```bash
CSV=$(ls -t output/spesolimab_positive_control_*.csv | head -1)

# positive-control anchors: the 3 granted B2 should NOT all be Low
grep -E "US12552870B2|US12624397B2|US12712081B2" "$CSV"

# screening-recall probe: 2 anti-IL-36R antibodies killed in 2-stage screening
grep -E "US20220073628A1|US20220119538A1" "$CSV"

# discrimination probe: methotrexate + spesolimab co-mention
grep -E "US20260219281A1|US20260242465A1" "$CSV"
```

What each tells you (see flow report for full findings):
- **granted B2 anchors** — if composition-claim B2 lands High → core scoring logic
  works. `US12712081B2` is a *diagnostic* patent → FALSE/Low is **correct**, not a miss.
- **screening-recall** — these two only surface under `--skip-screening`. If they
  flip High/Medium there, 2-stage screening is over-filtering same-mechanism art.
- **discrimination** — does scoring tell "claimed subject" from "merely mentioned"?

---

## 5 · Verified snapshot (2026-09-14 — reference only, re-measure each run)

| Cell | Risk | Key moves |
|------|------|-----------|
| #1 default · on | High 7 / Med 1 / Low 96 | — |
| #2 v2 · on | High 9 / Low 95 | #1→#2: Up 2 (US12624397B2 M→H, US20220290239A1 L→H), Down 0 |
| #3 default · skip | *not yet run* | validates screening-recall finding |

Re-measure, don't trust the table:
```bash
for f in $(ls -t output/spesolimab_*.csv | head -4); do
  echo "== $f =="
  python3 -c "import csv,collections,sys; print(collections.Counter(r['fto_risk'] for r in csv.DictReader(open(sys.argv[1]))))" "$f"
done
grep -i 'Loaded\|Skipped\|Mode:' logs/spesolimab_*.log
```

> The only non-frozen variable across reruns is the **model snapshot**:
> config uses the floating aliases `gpt-4o` / `gpt-4o-mini` (never dated). Within
> one OpenAI snapshot window the numbers reproduce exactly (temperature is pinned
> =0, `llm_analyzer.py:147`); across a repoint they may drift. Pin a dated
> snapshot only if you need a months-later bit-identical rerun — optional, with
> the tradeoff that dated snapshots get deprecated.

---

## 6 · Judgment boundary (read before scripting this end-to-end)

The plumbing (run cells → compare → pull anchor patents) chains to near-one-command.
These stay **human** — automation prepares material, it does not conclude:

- **"Did the scoring logic pass?"** — human. The experiment surfaces moves and
  anchor verdicts; a person decides whether they mean the logic is sound or broken.
- **Diagnostic-vs-treatment calls** — a granted B2 judged FALSE can be correct
  (diagnostic method) or a miss (composition). Only reading the claim settles it.
- **Promoting rubric_v2 to default** — validated here on a *positive control*
  (marketed drug). It must be re-validated on a *repurposing* target (darifenacin)
  before becoming the default; rubric_v2 §5 "same-drug-same-indication → High" may
  over-fire in the repurposing frame.
- **Any keyword scan** — word-boundary (`\b`) regex, never bare substring, or it
  automates the wrong numbers.

---

## 7 · Recovery / rerun

- **analyze_jsonl has no resume** — the CSV is written only at the end. Killed
  mid-run → re-run that cell from scratch (input is frozen, so this is cheap in
  effort, not in tokens).
- **config-swap restore** — the tool backs up `config.py → config.py.bak.analyze_jsonl`,
  copies the target config in, and restores via try/finally on exit
  (`analyze_jsonl.py:182–213, 516–518`). On a hard kill (SIGKILL / power loss)
  the restore may not run:
  ```bash
  ls config.py.bak.analyze_jsonl        # if present, a run was interrupted mid-swap
  git status config.py                  # confirm config.py isn't left as spesolimab config
  # restore original if needed:
  cp config.py.bak.analyze_jsonl config.py && rm config.py.bak.analyze_jsonl
  ```
- **config.py never commits** — after any run, confirm it's clean (anti-pattern #3):
  ```bash
  git status config.py ; git diff config.py
  ```

---

## Appendix · Quick reference

```bash
INPUT=gpss-probe/test_0911/gpp_patent_ids_GPSS.jsonl        # frozen 0911 input (104)

# run a cell (vary flags + prefix per §1 matrix)
nohup python3 -u scripts/analyze_jsonl.py --input "$INPUT" \
  --config configs/spesolimab_gpp_v1.py --prefix <prefix> \
  --use-llm [--rubric-override configs/rubrics/rubric_v2.txt] [--skip-screening] \
  > logs/spesolimab_$(date +%Y%m%d_%H%M).log 2>&1 &
# then: confirm the printed `Mode:` line matches the flags you passed

# compare two cells (look up CSVs at runtime)
python3 tools/compare_runs.py output/<prefixA>_<ts>.csv output/<prefixB>_<ts>.csv

# anchor patents
grep -E "US12552870B2|US12624397B2|US12712081B2" output/<prefix>_<ts>.csv   # granted B2
grep -E "US20220073628A1|US20220119538A1" output/<prefix>_<ts>.csv          # screening-recall
```

- `--skip-screening` implies `--use-llm`. · Never run two `analyze_jsonl` at once.
- Snapshot numbers are reference — re-measure (§5). · Input is frozen — never rebuild for reproduction.
