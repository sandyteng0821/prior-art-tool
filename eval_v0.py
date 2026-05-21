"""
eval_v0.py — Excipient Pipeline Evaluation V0

Weak-label evaluation using prior art patent evidence.
Measures whether recommended excipients appear in real patent disclosures.

V0 limitation: candidate patents are US/CN/EA/NZ (no EPO fulltext).
Ground truth is extracted from abstract only — P@k scores will be biased
low. Use only to confirm the pipeline runs end-to-end.

Usage:
    python eval_v0.py

API URL is read from EXCIPIENT_API_URL env var.
Default: http://192.168.66.188:8026
"""

import json
import os
import sqlite3
import subprocess
from datetime import datetime

import requests

# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = "cache/patents.db"

RECOMMEND_API_URL = os.getenv(
    "EXCIPIENT_API_URL",
    "http://192.168.66.188:8026",
).rstrip("/") + "/excipients/recommend"

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

k_values = [5, 10]

# Keyword list: target excipient + top 10 from recommend API for this run.
# Re-generate per drug × target_excipient — do not reuse across runs.
# Singular form so substring match covers plurals
# (e.g. "polymethacrylate" in "polymethacrylates" → True).
excipient_keywords = [
    # target excipient
    "lactose",

    # top 10 recommended (canonical singular + manual abbreviations)
    "polymethacrylate",
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


# ── STEP 1: Fetch Patent Text from DB ────────────────────────────────────────

def fetch_patent_text(patent_id: str) -> str | None:
    """
    Read patent from local DB. Combines title + abstract + claims +
    examples_extracted into one searchable string.

    Returns None if patent not in DB. Text may be short if only abstract
    is populated (US/CN/EA/KR patents — EPO fulltext licensing limit).
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT title, abstract, claims, examples_extracted
                   FROM patents WHERE patent_id = ?""",
                (patent_id,),
            ).fetchone()
    except Exception as e:
        print(f"[!] DB error for {patent_id}: {e}")
        return None

    if not row:
        print(f"[!] Not in DB: {patent_id}")
        return None

    return " ".join([
        row["title"] or "",
        row["abstract"] or "",
        row["claims"] or "",
        row["examples_extracted"] or "",
    ])


print("=== STEP 1: Reading patents from DB ===")
patent_texts: dict[str, str] = {}
for pid in candidate_patents:
    text = fetch_patent_text(pid)
    if text:
        patent_texts[pid] = text
        text_len = len(text)
        print(f"  {pid}: {text_len} chars")

print(f"\n{len(patent_texts)}/{len(candidate_patents)} patents loaded.\n")

if not patent_texts:
    print("[!] No patents loaded — check DB path and that main.py has been run.")
    exit(1)


# ── STEP 2: Ground Truth Extraction ──────────────────────────────────────────

print("=== STEP 2: Extracting ground truth via keyword match ===")

ground_truth: set[str] = set()
keyword_to_patents: dict[str, list[str]] = {kw: [] for kw in excipient_keywords}

for pid, text in patent_texts.items():
    text_lower = text.lower()
    for kw in excipient_keywords:
        if kw in text_lower:
            ground_truth.add(kw)
            keyword_to_patents[kw].append(pid)

# Drop keywords with no patent support
keyword_to_patents = {kw: pids for kw, pids in keyword_to_patents.items() if pids}

print(f"  Ground truth keywords found: {len(ground_truth)}")
for kw in sorted(ground_truth):
    n = len(keyword_to_patents.get(kw, []))
    print(f"    {kw:<35} ({n} patent{'s' if n != 1 else ''})")


def get_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


os.makedirs("outputs/ground_truth", exist_ok=True)
target_safe = target_excipient.replace(",", "").replace(" ", "_")
out_path = f"outputs/ground_truth/{drug}_{target_safe}_v0.json"

with open(out_path, "w") as f:
    json.dump(
        {
            "drug":                  drug,
            "target_excipient":      target_excipient,
            "evaluated_at":          datetime.now().isoformat(),
            "git_commit":            get_git_commit(),
            "candidate_patents":     list(patent_texts.keys()),
            "keyword_list_used":     excipient_keywords,
            "ground_truth_keywords": sorted(ground_truth),
            "keyword_to_patents":    keyword_to_patents,
        },
        f,
        indent=2,
        ensure_ascii=False,
    )

print(f"\nGround truth written to: {out_path}\n")


# ── STEP 3: Recommendation API ────────────────────────────────────────────────

print("=== STEP 3: Calling recommendation API ===")
print(f"  URL: {RECOMMEND_API_URL}")

payload = {
    "target_excipient": target_excipient,
    "api_name":         drug,
    # "api_groups":       ["Primary Amine"],
}

try:
    resp = requests.post(RECOMMEND_API_URL, json=payload, timeout=30)
except requests.exceptions.ConnectionError as e:
    print(f"[!] Cannot connect to API: {e}")
    print("    Is the excipient pipeline service running?")
    print(f"    Set EXCIPIENT_API_URL env var to override default URL.")
    exit(1)

if resp.status_code != 200:
    print(f"[!] API error: {resp.status_code}")
    print(f"    Response: {resp.text[:200]}")
    exit(1)

resp_data      = resp.json()
recommendations = resp_data["recommendations"][:10]
matched_as     = resp_data.get("matched_as", "?")
api_context    = resp_data.get("api_context", "?")

print(f"  matched_as:  {matched_as}")
print(f"  api_context: {api_context}")
print(f"  {len(recommendations)} recommendations received.\n")


# ── STEP 4: Evaluation ────────────────────────────────────────────────────────

def normalize(x: str) -> str:
    """
    Lowercase, strip, split on commas, sort words.
    Makes comparison order-independent.

    "Cellulose, Microcrystalline" → "cellulose microcrystalline"
    "microcrystalline cellulose"  → "cellulose microcrystalline"
    """
    parts = [p.strip() for p in x.lower().split(",")]
    return " ".join(sorted(" ".join(parts).split()))


def is_hit(rec_name: str, gt: set[str]) -> bool:
    rec_norm = normalize(rec_name)
    return any(rec_norm == normalize(kw) for kw in gt)


results: dict[int, float] = {}
for k in k_values:
    hits = sum(is_hit(r["name"], ground_truth) for r in recommendations[:k])
    results[k] = hits / k


# ── STEP 5: Report ────────────────────────────────────────────────────────────

print("=" * 55)
print("=== Excipient Pipeline Evaluation — V0 ===")
print("=" * 55)
print()
print(f"Drug:             {drug}")
print(f"Target Excipient: {target_excipient}")
print(f"API matched as:   {matched_as}")
print(f"Patents:          {len(patent_texts)} evaluated")
print()

print("Ground Truth Keywords Found:")
if ground_truth:
    for kw in sorted(ground_truth):
        n_patents = len(keyword_to_patents.get(kw, []))
        print(f"  {kw:<35} (supported by {n_patents} patent{'s' if n_patents != 1 else ''})")
else:
    print("  (none — abstracts may not contain excipient keywords)")
print()

print("Top 10 Recommendations:")
for i, r in enumerate(recommendations, 1):
    hit    = "✅" if is_hit(r["name"], ground_truth) else "❌"
    safety = r.get("api_safety", "")
    print(f"  {i:>2}. {r['name']:<35} score={r['total_score']}  {hit}  {safety}")

print()
for k, p in results.items():
    print(f"P@{k:<3} = {p:.2f}")

print(f"\nGround truth file: {out_path}")
print()
print("⚠️  V0 limitation: ground truth from abstract only.")
print("   Low P@k does not imply poor recommendations — see task_E.md for details.")