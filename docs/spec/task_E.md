# Excipient Pipeline Evaluation — V0 Spec

**Version:** 0.1 (lightweight validation)
**Status:** Ready to implement
**Scope:** Single drug × single target excipient run

> This is a weak-label evaluation using prior art evidence.
> It measures whether recommended excipients appear in real patent disclosures,
> not whether they are optimal formulations.

---

## Overview

```
INPUT: drug + target_excipient + candidate_patents
    ↓
STEP 1: read patent text from DB (sqlite) → combined text per patent
    ↓
STEP 2: keyword match → ground truth set → output to json (with metadata)
    ↓
STEP 3: recommend API → top 10
    ↓
STEP 4: overlap (normalize-then-exact) → precision@k
    ↓
STEP 5: print report
```

---

## V0 Evaluation Limitations

The 6 candidate patents in this V0 run are all from jurisdictions without EPO fulltext access (US/CN/EA/NZ — see Task C licensing limitation in `docs/architecture.md`). Their DB rows have `claims=0` and `examples_extracted=0`; only `abstract` (~500 chars each) contains searchable text.

Ground truth is therefore extracted from **abstract only**, which will significantly under-count excipient occurrences. Expected ground truth size: 3–5 keywords. Expected P@5 / P@10 will be biased low.

This is **acceptable for V0** because:

- Goal is to validate the pipeline mechanics (DB read, API call, scoring logic), not to measure recommendation accuracy
- The recommendation API is being tested for "does it suggest something that appears in known prior art" — abstract-level overlap is a weak but non-zero signal
- V1 will extend the candidate set to include EP granted patents (with full claims/examples) for stronger evaluation

> ⚠️ **Do not interpret V0 P@k scores as recommendation quality.** Use them only to confirm the pipeline runs end-to-end.

---

## Input (hardcode for V0)

```python
drug             = "Ampicillin"
target_excipient = "Lactose, Anhydrous"

candidate_patents = [
    "US7108864B1",
    "US2009062404A1",
    "US2013029965A1",
    "CN103830190A",
    "EA004311B1",
    "NZ575435A",
]

k_values          = [5, 10]
recommend_api_url = "http://localhost:8000/excipients/recommend"
```

---

## Keyword List

Derived from `POST /excipients/recommend` output for this run + target excipient.
**Re-generate per drug × target_excipient — do not hardcode permanently.**
Keep canonical form only; use **singular** form so substring match covers plurals. Add common abbreviations manually.

```python
excipient_keywords = [
    # target excipient
    "lactose",

    # top 10 recommended (canonical singular + abbreviation where needed)
    "polymethacrylate",          # singular; covers "polymethacrylates" too
    "microcrystalline cellulose",
    "mcc",
    "powdered cellulose",
    "polyethylene glycol",
    "peg",
    "sorbitol",
    "calcium phosphate",
    "carboxymethylcellulose",
    "cmc",
    "erythritol",
    "fumaric acid",
]
```

> **Why singular:** `"polymethacrylate" in "polymethacrylates"` returns `True`
> (singular IS substring of plural). But `"polymethacrylates" in "polymethacrylate"`
> returns `False` (plural is not substring of singular). Using singular form means
> substring match catches both.

---

## STEP 1 — Fetch Patent Text from DB

Read directly from `cache/patents.db`. **No subprocess, no EPO API call** — the data is already in DB from prior `main.py` runs.

```python
import sqlite3

DB_PATH = "cache/patents.db"

def fetch_patent_text(patent_id):
    """
    Read patent text directly from DB. Combines title + abstract +
    claims + examples_extracted into a single searchable string.

    Returns None if patent not in DB. Returns combined text otherwise
    (may be short if only abstract is populated, e.g. US/CN/KR patents).
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT title, abstract, claims, examples_extracted
               FROM patents WHERE patent_id = ?""",
            (patent_id,)
        ).fetchone()
    if not row:
        print(f"[!] Not in DB: {patent_id}")
        return None
    return " ".join([
        row["title"] or "",
        row["abstract"] or "",
        row["claims"] or "",
        row["examples_extracted"] or "",
    ])

patent_texts = {}
for pid in candidate_patents:
    text = fetch_patent_text(pid)
    if text:
        patent_texts[pid] = text
```

**Why direct DB read instead of subprocess + inspect_patent:**

- 5–10x faster (no Python startup per patent)
- Pure data, no UI text contamination (headers, table separators, Espacenet URL etc.)
- DB row is single source of truth, decoupled from inspect_patent UI
- Debug-friendly: can print per-field text length to diagnose coverage issues

---

## STEP 2 — Ground Truth Extraction

Simple keyword match against combined text. Substring, lowercased. Track which patents contributed each keyword for debugging.

```python
ground_truth = set()
keyword_to_patents = {kw: [] for kw in excipient_keywords}

for pid, text in patent_texts.items():
    text_lower = text.lower()
    for kw in excipient_keywords:
        if kw in text_lower:
            ground_truth.add(kw)
            keyword_to_patents[kw].append(pid)

# Drop keywords with no patent support
keyword_to_patents = {kw: pids for kw, pids in keyword_to_patents.items() if pids}
```

Output ground truth + metadata to a separate file:

```python
import json, os, subprocess
from datetime import datetime

def get_git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"

os.makedirs("outputs/ground_truth", exist_ok=True)

# Filename-safe target excipient
target_safe = target_excipient.replace(",", "").replace(" ", "_")
out_path = f"outputs/ground_truth/{drug}_{target_safe}_v0.json"

with open(out_path, "w") as f:
    json.dump({
        "drug":                  drug,
        "target_excipient":      target_excipient,
        "evaluated_at":          datetime.now().isoformat(),
        "git_commit":            get_git_commit(),
        "candidate_patents":     list(patent_texts.keys()),
        "keyword_list_used":     excipient_keywords,
        "ground_truth_keywords": sorted(ground_truth),
        "keyword_to_patents":    keyword_to_patents,
    }, f, indent=2, ensure_ascii=False)

print(f"Ground truth written to: {out_path}")
```

> The `keyword_to_patents` field shows **how many patents back each keyword**. A keyword supported by 1 patent is a weaker signal than one supported by 5. Useful for V1 to consider weight or thresholds.

---

## STEP 3 — Recommendation API

```python
import requests

payload = {
    "target_excipient": target_excipient,
    "api_name":         drug,
    "api_groups":       ["Primary Amine"],   # override if needed
}

resp = requests.post(recommend_api_url, json=payload)
if resp.status_code != 200:
    print(f"API error: {resp.status_code}")
    exit(1)

recommendations = resp.json()["recommendations"][:10]
```

---

## STEP 4 — Evaluation

Exact match after normalize. Normalize handles word order differences (e.g. `"Cellulose, Microcrystalline"` vs `"microcrystalline cellulose"`).

```python
def normalize(x):
    """
    Lowercase, strip, split on commas, sort words.

    Examples:
        "Cellulose, Microcrystalline" → "cellulose microcrystalline"
        "microcrystalline cellulose"  → "cellulose microcrystalline"

    Both normalize to the same string, so they match.
    """
    parts = [p.strip() for p in x.lower().split(",")]
    return " ".join(sorted(" ".join(parts).split()))

def is_hit(rec_name, ground_truth):
    rec_norm = normalize(rec_name)
    # Normalize ground_truth entries the same way for comparison
    return any(rec_norm == normalize(gt) for gt in ground_truth)

results = {}
for k in k_values:
    hits = sum(is_hit(r["name"], ground_truth) for r in recommendations[:k])
    results[k] = hits / k
```

**Why normalize-then-exact instead of plain substring:**

- Recommendation API returns `"Cellulose, Microcrystalline"` (canonical form with comma)
- Ground truth keyword is `"microcrystalline cellulose"` (natural order)
- Plain substring match misses this (the comma-form is not a substring of the natural-order keyword)
- Normalization makes the comparison order-independent

---

## STEP 5 — Report

```python
print("=== Excipient Pipeline Evaluation — V0 ===\n")
print(f"Drug:             {drug}")
print(f"Target Excipient: {target_excipient}")
print(f"Patents:          {len(patent_texts)} evaluated\n")

print("Ground Truth Keywords Found:")
for kw in sorted(ground_truth):
    n_patents = len(keyword_to_patents.get(kw, []))
    print(f"  {kw:<35} (supported by {n_patents} patent{'s' if n_patents != 1 else ''})")
print()

print("Top 10 Recommendations:")
for i, r in enumerate(recommendations, 1):
    hit = "✅" if is_hit(r["name"], ground_truth) else "❌"
    print(f"  {i:>2}. {r['name']:<35} score={r['total_score']}  {hit}")

print()
for k, p in results.items():
    print(f"P@{k:<3} = {p:.2f}")

print(f"\nGround truth file: {out_path}")
print("\n⚠️  V0 limitation: ground truth from abstract only.")
print("   Low P@k does not imply poor recommendations — see spec for details.")
```

**Example output:**

```
=== Excipient Pipeline Evaluation — V0 ===

Drug:             Ampicillin
Target Excipient: Lactose, Anhydrous
Patents:          6 evaluated

Ground Truth Keywords Found:
  lactose                             (supported by 4 patents)
  microcrystalline cellulose          (supported by 2 patents)
  polymethacrylate                    (supported by 1 patent)

Top 10 Recommendations:
   1. Polymethacrylates                  score=8   ✅
   2. Cellulose, Microcrystalline        score=7   ✅
   3. Cellulose, Powdered                score=7   ❌
   4. Polyethylene Glycol                score=7   ❌
   5. Sorbitol                           score=7   ❌
   6. Calcium Phosphate, Tribasic        score=6   ❌
   7. Carboxymethylcellulose Calcium     score=6   ❌
   8. Carboxymethylcellulose Sodium      score=6   ❌
   9. Erythritol                         score=6   ❌
  10. Fumaric Acid                       score=6   ❌

P@5  = 0.40
P@10 = 0.20

Ground truth file: outputs/ground_truth/Ampicillin_Lactose_Anhydrous_v0.json

⚠️  V0 limitation: ground truth from abstract only.
   Low P@k does not imply poor recommendations — see spec for details.
```

---

## Code Constraints

| Item | Decision |
|------|----------|
| Single file | Yes — no multiple modules |
| Libraries | `sqlite3`, `requests`, `json`, `os`, `subprocess` (only for `git rev-parse`), `datetime` |
| Classes / dataclasses | No |
| CLI args | No — hardcode input for V0 |
| Async | No |
| Logging frameworks | No |
| Error handling | DB miss → skip patent + print warning; API failure → print + exit |
| Match logic | Normalize-then-exact (lowercase + comma-split + sort words) |
| Data source | DB direct read (`cache/patents.db`), no subprocess to inspect_patent |

---

## Constraints on Ground Truth

| Item | Decision |
|------|----------|
| Keyword list source | Top 10 from recommend API + target excipient — re-generate per run |
| Keyword form | Singular canonical (substring covers plurals) |
| Abbreviations | Add manually (MCC, PEG, CMC) |
| Fuzzy resolve | Not in V0 |
| Claim / disclosure tagging | Not in V0 |
| Evidence strength grading | Tracked via `keyword_to_patents` count, not used for scoring |

---

## Verification

Before considering V0 complete:

```bash
# 1. Spec runs end-to-end without error
python eval_v0.py

# 2. Ground truth file is created and well-formed
cat outputs/ground_truth/Ampicillin_Lactose_Anhydrous_v0.json | python -m json.tool

# 3. Patent text length sanity check — verify the abstract-only limitation
python -c "
import sqlite3
with sqlite3.connect('cache/patents.db') as conn:
    for pid in ['US7108864B1', 'US2009062404A1', 'CN103830190A', 'EA004311B1']:
        row = conn.execute(
            'SELECT length(abstract), length(claims), length(examples_extracted) '
            'FROM patents WHERE patent_id = ?', (pid,)
        ).fetchone()
        print(f'{pid}: abstract={row[0]}, claims={row[1]}, examples={row[2]}')
"
# Expected: most rows show abstract>0 but claims=0 and examples=0

# 4. Sanity check ground truth output
python -c "
import json
with open('outputs/ground_truth/Ampicillin_Lactose_Anhydrous_v0.json') as f:
    data = json.load(f)
print('Keywords found:', data['ground_truth_keywords'])
print('Keyword sources:')
for kw, pids in data['keyword_to_patents'].items():
    print(f'  {kw}: {pids}')
"
```

---

## Future Work (V1+)

### Expanding ground truth coverage

| Item | Description |
|------|-------------|
| Add EP/WO patents to candidate set | Strong ground truth from claims + examples_extracted |
| Multi-drug benchmark | Macro average across drugs |

### Improving keyword/matching

| Item | Description |
|------|-------------|
| Fuzzy resolve | Map keywords to canonical DB names via `/excipients/{name}/search` |
| Full name mapping | Use `/excipients/names` full list instead of top-10-derived keywords |
| Abbreviation map | Systematic alias expansion (MCC → Cellulose Microcrystalline) |

### Richer evaluation signal

| Item | Description |
|------|-------------|
| Evidence strength | Strong / Moderate / Not supported grading per excipient |
| Claim / disclosure tagging | Record `found_in` field per excipient (which DB column) |
| Weight by patent support | Give higher score for excipients backed by more patents |
| LLM extraction | Replace rule-based STEP 2 with LLM-based excipient name extraction |