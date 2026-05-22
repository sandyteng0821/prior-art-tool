"""
eval_v1.py — Excipient Pipeline Evaluation V1 (CSV-driven)

Extends eval_v0.py:
  - Patent list from --csv (gap_analysis_*.xlsx/csv) instead of hardcoded
  - Keyword list derived dynamically from recommend API top 10
  - Typo guard between --target-excipient and API's matched_as
  - CLI args for drug / target excipient / api_groups / k / force

Step 3-5 logic identical to V0 (ground truth extraction, normalize+is_hit
evaluation, report+JSON output). See task_F.md for design rationale.

Usage:
    python -m tools.eval_v1 \
      --csv output/gap_analysis_20260508_1645.xlsx \
      --drug Ampicillin \
      --target-excipient "Lactose, Anhydrous"

API URL is read from EXCIPIENT_API_URL env var.
Default: http://192.168.66.188:8026
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

import pandas as pd
import requests


# ── Config ────────────────────────────────────────────────────────────────────

DB_PATH = "cache/patents.db"

RECOMMEND_API_URL = os.getenv(
    "EXCIPIENT_API_URL",
    "http://192.168.66.188:8026",
).rstrip("/") + "/excipients/recommend"

# Abbreviation map for keyword derivation. If any derived keyword matches a key
# here, the value is also added to the keyword list. Per task_F.md spec.
ABBREVIATIONS = {
    "microcrystalline cellulose":    "mcc",
    "polyethylene glycol":           "peg",
    "carboxymethylcellulose":        "cmc",
    "hydroxypropyl methylcellulose": "hpmc",
}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Excipient pipeline eval V1 (CSV-driven).",
    )
    ap.add_argument(
        "--csv", required=True,
        help="Path to gap analysis CSV/Excel. Must have 'patent_id' column.",
    )
    ap.add_argument(
        "--drug", required=True,
        help="Drug name (API name for recommend endpoint).",
    )
    ap.add_argument(
        "--target-excipient", required=True,
        help="Target excipient name (fuzzy matched by API).",
    )
    ap.add_argument(
        "--api-groups", nargs="+", default=None,
        help='Manual functional group override (one or more). '
             'If omitted, API queries PubChem. '
             'Example: --api-groups "Primary Amine" "Amide"',
    )
    ap.add_argument(
        "--k", default="5,10",
        help="Comma-separated k values for P@k. Default: 5,10",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Skip the typo guard for target_excipient vs matched_as.",
    )
    return ap.parse_args()


# ── Shared helpers (V0 verbatim) ──────────────────────────────────────────────

def normalize(x: str) -> str:
    """
    Lowercase, strip, split on commas, sort words.
    Makes comparison order-independent.

    "Cellulose, Microcrystalline" → "cellulose microcrystalline"
    "microcrystalline cellulose"  → "cellulose microcrystalline"

    Identical to V0 — copied verbatim. Used by STEP 4 is_hit() and
    by STEP 2 _share_token() typo guard.
    """
    parts = [p.strip() for p in x.lower().split(",")]
    return " ".join(sorted(" ".join(parts).split()))


def is_hit(rec_name: str, gt: set[str]) -> bool:
    """Identical to V0 — copied verbatim."""
    rec_norm = normalize(rec_name)
    return any(rec_norm == normalize(kw) for kw in gt)


def get_git_commit() -> str:
    """Identical to V0 — copied verbatim."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


# ── STEP 1a: Load patent IDs from CSV ─────────────────────────────────────────

def load_patent_ids(csv_path: str) -> list[str]:
    """
    Read patent IDs from xlsx/csv. Dispatches on extension.
    Requires 'patent_id' column. Returns deduplicated list, order preserved.
    Exits on missing column.
    """
    ext = os.path.splitext(csv_path)[1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            df = pd.read_excel(csv_path)
        elif ext == ".csv":
            df = pd.read_csv(csv_path)
        else:
            print(f"[!] Unsupported file extension: {ext}")
            print(f"    Expected .xlsx, .xls, or .csv")
            sys.exit(1)
    except FileNotFoundError:
        print(f"[!] File not found: {csv_path}")
        sys.exit(1)

    if "patent_id" not in df.columns:
        print(f"[!] CSV missing 'patent_id' column.")
        print(f"    Available columns: {df.columns.tolist()}")
        sys.exit(1)

    seen = set()
    ids = []
    for raw in df["patent_id"].dropna().astype(str):
        pid = raw.strip()
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


# ── STEP 1b: Fetch patent text from DB ────────────────────────────────────────

def fetch_patent_text(patent_id: str) -> str | None:
    """
    Read patent from local DB. Combines title + abstract + claims +
    examples_extracted into one searchable string.

    Returns None if patent not in DB. Text may be short if only abstract
    is populated (US/CN/EA/KR patents — EPO fulltext licensing limit).

    Identical to V0 — copied verbatim.
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


# ── STEP 2a: Recommendation API call ──────────────────────────────────────────

def call_recommend_api(
    drug: str,
    target_excipient: str,
    api_groups: list[str] | None,
) -> tuple[list[dict], str, str]:
    """
    Call POST /excipients/recommend. Exits on connection error or non-200.

    Per excipient pipeline API docs:
      - api_groups absent  → API queries PubChem automatically
      - api_groups present → used directly (offline / controlled override)

    Returns (recommendations[:10], matched_as, api_context).
    """
    payload: dict = {
        "target_excipient": target_excipient,
        "api_name":         drug,
    }
    if api_groups:
        payload["api_groups"] = api_groups

    try:
        resp = requests.post(RECOMMEND_API_URL, json=payload, timeout=30)
    except requests.exceptions.ConnectionError as e:
        print(f"[!] Cannot connect to API: {e}")
        print(f"    Is the excipient pipeline service running?")
        print(f"    Set EXCIPIENT_API_URL env var to override default URL.")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"[!] API error: {resp.status_code}")
        print(f"    Response: {resp.text[:200]}")
        sys.exit(1)

    data = resp.json()
    return (
        data["recommendations"][:10],
        data.get("matched_as", "?"),
        data.get("api_context", "?"),
    )


# ── STEP 2b: Typo guard ───────────────────────────────────────────────────────

def _share_token(a: str, b: str) -> bool:
    """True if normalized a and b share at least one token."""
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    return bool(ta & tb)


def check_typo_guard(target_excipient: str, matched_as: str, force: bool) -> None:
    """
    Compare user input against API's matched_as. Exit if they share no tokens
    (likely typo, since rapidfuzz silently returns nearest neighbour even for
    garbage input). --force bypasses.

    Probe 3 finding: "latose" → "Ammonium Alginate" with no API warning.
    """
    if _share_token(target_excipient, matched_as):
        return

    print(f"[!] target_excipient '{target_excipient}' fuzzy-matched to "
          f"'{matched_as}' but they share no tokens.")
    print(f"    This is likely a typo. Re-run with --force to proceed anyway.")
    if not force:
        sys.exit(1)
    print(f"    [!] --force given; continuing.")


# ── STEP 2c: Dynamic keyword derivation ───────────────────────────────────────

def derive_keywords_from_name(name: str) -> list[str]:
    """
    Expand a canonical excipient name to all forms that may appear in
    patent text.

    "Cellulose, Microcrystalline" → ["cellulose, microcrystalline",
                                      "microcrystalline cellulose"]
    "Lactose, Anhydrous"          → ["lactose, anhydrous",
                                      "anhydrous lactose"]
    "Polyethylene Glycol"         → ["polyethylene glycol"]
    """
    name = name.lower().strip()
    out = [name]
    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        natural = " ".join(reversed(parts))  # reverse, not sort
        if natural != name:
            out.append(natural)
    return out


def build_keyword_list(matched_as: str, recommendations: list[dict]) -> list[str]:
    """
    Build the keyword list used for ground truth extraction.

    1. Anchor on matched_as (not user input) — output is consistent with what
       was actually evaluated.
    2. Expand each canonical name via derive_keywords_from_name() to produce
       both comma-form and natural-order phrasing.
    3. Apply ABBREVIATIONS map (exact-match on derived keyword).
    4. Deduplicate while preserving order.
    """
    keywords: list[str] = []

    # 1. target excipient (matched, not user input)
    keywords.extend(derive_keywords_from_name(matched_as))

    # 2. top 10 recommendations
    for rec in recommendations:
        keywords.extend(derive_keywords_from_name(rec["name"]))

    # 3. abbreviations
    extras: list[str] = []
    for kw in keywords:
        if kw in ABBREVIATIONS:
            extras.append(ABBREVIATIONS[kw])
    keywords.extend(extras)

    # 4. dedupe, preserve order
    seen = set()
    out = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


# ── STEP 3: Ground truth extraction ───────────────────────────────────────────

def extract_ground_truth(
    patent_texts: dict[str, str],
    keywords: list[str],
) -> tuple[set[str], dict[str, list[str]]]:
    """
    Substring keyword match against combined patent text, lowercased.
    Identical logic to V0 (STEP 2), only the keyword list source differs.

    Returns:
      ground_truth: set of keywords that matched ≥1 patent
      keyword_to_patents: dict mapping each matched keyword to its supporting
                          patent IDs (for evidence strength debugging)
    """
    ground_truth: set[str] = set()
    keyword_to_patents: dict[str, list[str]] = {kw: [] for kw in keywords}

    for pid, text in patent_texts.items():
        text_lower = text.lower()
        for kw in keywords:
            if kw in text_lower:
                ground_truth.add(kw)
                keyword_to_patents[kw].append(pid)

    # Drop keywords with no patent support
    keyword_to_patents = {
        kw: pids for kw, pids in keyword_to_patents.items() if pids
    }
    return ground_truth, keyword_to_patents


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    k_values = [int(k.strip()) for k in args.k.split(",") if k.strip()]

    print("=" * 60)
    print("=== Excipient Pipeline Evaluation — V1 ===")
    print("=" * 60)
    print(f"CSV:              {args.csv}")
    print(f"Drug:             {args.drug}")
    print(f"Target Excipient: {args.target_excipient}")
    print(f"API Groups:       {args.api_groups or '(auto from PubChem)'}")
    print(f"k values:         {k_values}")
    print(f"Force:            {args.force}")
    print()

    # ── STEP 1a: Load patent IDs from CSV ─────────────────────────────────────
    print("=== STEP 1a: Loading patent IDs from CSV ===")
    patent_ids = load_patent_ids(args.csv)
    print(f"  {len(patent_ids)} unique patent IDs loaded.")
    print(f"  Sample: {patent_ids[:5]}")
    print()

    # ── STEP 1b: Fetch patent text from DB ────────────────────────────────────
    print("=== STEP 1b: Reading patent text from DB ===")
    patent_texts: dict[str, str] = {}
    missing: list[str] = []
    for pid in patent_ids:
        text = fetch_patent_text(pid)
        if text:
            patent_texts[pid] = text
        else:
            missing.append(pid)

    print(f"  {len(patent_texts)}/{len(patent_ids)} patents loaded.")
    if missing:
        print(f"  Missing: {len(missing)} (first 5: {missing[:5]})")

    if not patent_texts:
        print("[!] No patents loaded — check DB path and CSV contents.")
        sys.exit(1)

    total_chars = sum(len(t) for t in patent_texts.values())
    avg_chars = total_chars / len(patent_texts)
    print(f"  Text coverage: total={total_chars:,} chars, avg={avg_chars:,.0f} chars/patent")
    print()

    # ── STEP 2a: Recommendation API call ──────────────────────────────────────
    print("=== STEP 2a: Calling recommendation API ===")
    print(f"  URL: {RECOMMEND_API_URL}")
    recommendations, matched_as, api_context = call_recommend_api(
        args.drug, args.target_excipient, args.api_groups,
    )
    print(f"  matched_as:  {matched_as}")
    print(f"  api_context: {api_context}")
    print(f"  {len(recommendations)} recommendations received.")
    print()

    # ── STEP 2b: Typo guard ───────────────────────────────────────────────────
    print("=== STEP 2b: Typo guard check ===")
    check_typo_guard(args.target_excipient, matched_as, args.force)
    print()

    # ── STEP 2c: Dynamic keyword derivation ───────────────────────────────────
    print("=== STEP 2c: Building keyword list ===")
    excipient_keywords = build_keyword_list(matched_as, recommendations)
    print(f"  {len(excipient_keywords)} keywords derived from "
          f"matched_as + top {len(recommendations)} recommendations + ABBREVIATIONS")
    for kw in excipient_keywords:
        print(f"    - {kw}")
    print()

    # ── STEP 3: Ground truth extraction ───────────────────────────────────────
    print("=== STEP 3: Extracting ground truth via keyword match ===")
    ground_truth, keyword_to_patents = extract_ground_truth(
        patent_texts, excipient_keywords,
    )
    print(f"  Ground truth keywords found: {len(ground_truth)}")
    for kw in sorted(ground_truth):
        n = len(keyword_to_patents.get(kw, []))
        print(f"    {kw:<35} ({n} patent{'s' if n != 1 else ''})")
    print()

    # Write ground truth JSON
    os.makedirs("outputs/ground_truth", exist_ok=True)
    # target_safe from matched_as, not from user input — reflects what was
    # actually evaluated. Per task_F spec §Output.
    target_safe = matched_as.replace(",", "").replace(" ", "_")
    out_path = f"outputs/ground_truth/{args.drug}_{target_safe}_v1.json"

    notes = (
        "Pre-Task-D backfill state; 16 parents (out of 187 patents) were "
        "fetched before May 2026 family expansion filter widening and may "
        "have missing TW/KR/AU/JP A-series sibling patents. "
        "See architecture.md Gap Analysis 3f."
    )

    with open(out_path, "w") as f:
        json.dump(
            {
                "version":              "v1",
                "csv_source":           args.csv,
                "drug":                 args.drug,
                "user_input_target":    args.target_excipient,
                "matched_as":           matched_as,
                "api_context":          api_context,
                "keyword_source":       "dynamic (recommend API top 10 + ABBREVIATIONS map)",
                "evaluated_at":         datetime.now().isoformat(),
                "git_commit":           get_git_commit(),
                "candidate_patents":    list(patent_texts.keys()),
                "keyword_list_used":    excipient_keywords,
                "ground_truth_keywords": sorted(ground_truth),
                "keyword_to_patents":   keyword_to_patents,
                "notes":                notes,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Ground truth written to: {out_path}")
    print()

    # ── STEP 4: Evaluation ────────────────────────────────────────────────────
    print("=== STEP 4: Computing P@k ===")
    results: dict[int, float] = {}
    for k in k_values:
        hits = sum(is_hit(r["name"], ground_truth) for r in recommendations[:k])
        results[k] = hits / k
    print(f"  Computed P@k for k={k_values}")
    print()

    # ── STEP 5: Report ────────────────────────────────────────────────────────
    print("=" * 60)
    print("=== Excipient Pipeline Evaluation — V1 Report ===")
    print("=" * 60)
    print()
    print(f"CSV source:           {args.csv}")
    print(f"Drug:                 {args.drug}")
    print(f"User input target:    {args.target_excipient}")
    print(f"API matched as:       {matched_as}")
    print(f"API context:          {api_context}")
    print(f"Patents evaluated:    {len(patent_texts)}")
    print(f"Keywords derived:     {len(excipient_keywords)}")
    print()

    print("Ground Truth Keywords Found:")
    if ground_truth:
        for kw in sorted(ground_truth):
            n_patents = len(keyword_to_patents.get(kw, []))
            print(f"  {kw:<35} (supported by {n_patents} patent{'s' if n_patents != 1 else ''})")
    else:
        print("  (none — check keyword derivation and patent text coverage)")
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
    print("⚠️  V1 caveats:")
    print("   - 16 parents pre-Task-D backfill: TW/KR/AU/JP A-series siblings may be missing")
    print("   - CN/US patents are still abstract-only (EPO fulltext licensing limit)")
    print("   See architecture.md Gap Analysis 3f and task_F.md for details.")


if __name__ == "__main__":
    main()