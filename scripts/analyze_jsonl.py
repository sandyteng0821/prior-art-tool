"""
analyze_jsonl — JSONL → Phase 4 → Phase 5（不經 DB）

讀 Google Patents scraper 產出的 JSONL，直接餵進 LLM/Rule analyzer，
輸出 gap_analysis CSV + Excel。完全不碰 SQLite DB。

Data flow:
    scraper.jsonl → [parse] → patent dict list → Phase 4 → Phase 5 → output/

用途：
- 新委託快速出報告（不等 EPO pipeline 跑完）
- 避免汙染現有 DB
- BigQuery / Google Patents 手動搜結果的快速分析

Usage:
    # Rule mode（免費，用 config 裡的 RULE_*_KEYWORDS）
    python3 scripts/analyze_jsonl.py \
        --input data/tiagabine_eb_scrape.jsonl \
        --config configs/tiagabine_eb.py

    # LLM mode（花錢）
    python3 scripts/analyze_jsonl.py \
        --input data/tiagabine_eb_scrape.jsonl \
        --config configs/tiagabine_eb.py \
        --use-llm

    # Dry-run（只看轉換結果，不跑分析）
    python3 scripts/analyze_jsonl.py \
        --input data/tiagabine_eb_scrape.jsonl \
        --config configs/tiagabine_eb.py \
        --dry-run

    # 自訂 output prefix
    python3 scripts/analyze_jsonl.py \
        --input data/tiagabine_eb_scrape.jsonl \
        --config configs/tiagabine_eb.py \
        --prefix gp_tiagabine_eb

Requires:
    config.py must be the target project config (or use --config to
    specify). The script copies the specified config to config.py
    before importing llm_analyzer (which reads config at module level).
"""

from __future__ import annotations

# Load .env BEFORE any module that needs API keys
from dotenv import load_dotenv
load_dotenv()

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# JSONL → patent dict conversion
# ═══════════════════════════════════════════════════════════════════════════════

# Scraper JSONL 的 dirty markers（跟 import_google_patents_jsonl.py 一致）
DIRTY_TITLE_PREFIXES = ("Not Found", "Error")
SENTINEL_VALUES = {"N/A", "n/a", "N/a", ""}


def _is_dirty(record: dict) -> bool:
    """Skip dirty rows: 404s, errors, CSV pollution."""
    title = record.get("title", "")
    if any(title.startswith(p) for p in DIRTY_TITLE_PREFIXES):
        return True
    return False


def _clean(val: str | None) -> str:
    """Convert sentinel values to empty string."""
    if val is None or val in SENTINEL_VALUES:
        return ""
    return val.strip()


def _extract_year(record: dict) -> str:
    """Extract year from publication_date or formatted_id."""
    pub_date = record.get("publication_date", "")
    if pub_date and pub_date != "N/A":
        # Try YYYY-MM-DD or YYYYMMDD
        match = re.match(r"(\d{4})", pub_date)
        if match:
            return match.group(1)
    # Fallback: extract from patent ID (e.g. US20070225293A1 → 2007)
    fid = record.get("formatted_id", "")
    match = re.search(r"(19|20)\d{2}", fid)
    return match.group(0) if match else ""


def jsonl_to_patent_dicts(jsonl_path: str) -> list[dict]:
    """
    Read scraper JSONL, convert each record to the patent dict format
    expected by llm_analyzer.rule_based_analyze() / analyze_patent().

    Scraper fields → patent dict mapping:
        requested_id    → patent_id
        title           → title
        abstract        → abstract
        claims          → claims
        full_text       → examples_extracted (description)
        publication_date → year (extracted)
        expiration_date → expiry_date
        assignee        → (ignored, not in output schema)

    Returns list of patent dicts, skipping dirty rows.
    """
    patents = []
    skipped_dirty = 0
    skipped_empty = 0

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"  [WARN] Line {line_num}: invalid JSON, skipped")
                continue

            if _is_dirty(record):
                skipped_dirty += 1
                continue

            title = _clean(record.get("title"))
            abstract = _clean(record.get("abstract"))
            claims = _clean(record.get("claims"))
            full_text = _clean(record.get("full_text"))

            # Skip rows with no useful content
            if not title and not abstract and not claims:
                skipped_empty += 1
                continue

            patent_id = record.get("requested_id", record.get("formatted_id", f"LINE_{line_num}"))
            expiry_raw = _clean(record.get("expiration_date"))

            patent = {
                "patent_id":          patent_id,
                "title":              title,
                "abstract":           abstract,
                "claims":             claims,
                "examples_extracted": full_text,
                "year":               _extract_year(record),
                "status":             "Unknown",
                "source":             "google_patents_jsonl",
                "expiry_date":        expiry_raw if expiry_raw else "",
                "expiry_source":      "google_patents" if expiry_raw else "",
            }
            patents.append(patent)

    print(f"  Loaded:  {len(patents)} patents")
    print(f"  Skipped: {skipped_dirty} dirty, {skipped_empty} no content")
    return patents


# ═══════════════════════════════════════════════════════════════════════════════
# Config handling
# ═══════════════════════════════════════════════════════════════════════════════

def _ensure_config(config_path: str) -> None:
    """
    llm_analyzer.py does `from config import ...` at module level.
    To make it read the right config, we must ensure config.py in the
    project root matches the specified config file.

    Strategy: backup current config.py, copy target config, restore on exit.
    """
    project_root = Path(__file__).resolve().parent.parent
    active_config = project_root / "config.py"
    backup_config = project_root / "config.py.bak.analyze_jsonl"

    target = Path(config_path).resolve()
    if not target.exists():
        print(f"[ERROR] Config not found: {config_path}")
        sys.exit(1)

    # Check if already the right config (avoid unnecessary backup/restore)
    if active_config.exists():
        if active_config.read_text() == target.read_text():
            return  # Already correct

    # Backup
    if active_config.exists():
        shutil.copy2(active_config, backup_config)
        print(f"  [config] Backed up config.py → config.py.bak.analyze_jsonl")

    # Copy target config
    shutil.copy2(target, active_config)
    print(f"  [config] Activated: {config_path}")


def _restore_config() -> None:
    """Restore original config.py if backup exists."""
    project_root = Path(__file__).resolve().parent.parent
    active_config = project_root / "config.py"
    backup_config = project_root / "config.py.bak.analyze_jsonl"

    if backup_config.exists():
        shutil.copy2(backup_config, active_config)
        backup_config.unlink()
        print(f"  [config] Restored original config.py")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Analyze Google Patents JSONL without touching DB.",
        prog="python3 scripts/analyze_jsonl.py",
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to scraper JSONL file",
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to project config file (e.g. configs/tiagabine_eb.py)",
    )
    parser.add_argument(
        "--use-llm", action="store_true",
        help="Use LLM mode (costs money). Default: rule mode (free).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse JSONL and show stats, don't run analysis.",
    )
    parser.add_argument(
        "--prefix", type=str, default="gp_analysis",
        help="Output filename prefix (default: gp_analysis)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N patents (for testing)",
    )
    parser.add_argument(
        "--rubric-override", type=str, metavar="FILE",
        help="Replace ANALYSIS_SYSTEM prompt with content from this file. "
             "Implies --use-llm. Supports {TARGET_DRUG}, {TARGET_ROUTE}, "
             "{TARGET_INDICATION} placeholders.",
    )
    parser.add_argument(
        "--compare", type=str, metavar="FILE",
        help="A/B test: run each patent twice (default rubric vs FILE), "
             "output two Excel files side by side. Implies --use-llm.",
    )
    parser.add_argument(
        "--skip-screening", action="store_true",
        help="Skip Stage 1 screening, send ALL patents directly to Stage 2. "
             "Mimics /api/v1/analysis/score behavior. Implies --use-llm.",
    )
    args = parser.parse_args()

    # --rubric-override, --compare, --skip-screening imply --use-llm
    if args.rubric_override or args.compare or args.skip_screening:
        args.use_llm = True

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print("═" * 70)
    print("  analyze_jsonl: JSONL → Phase 4 → Phase 5 (no DB)")
    print("═" * 70)
    print(f"  Input:   {args.input}")
    print(f"  Config:  {args.config}")
    mode_str = "LLM" if args.use_llm else "Rule"
    if args.compare:
        mode_str = "LLM (A/B compare)"
    elif args.rubric_override:
        mode_str = "LLM (rubric override)"
    if args.skip_screening:
        mode_str += " + skip-screening"
    print(f"  Mode:    {mode_str}")
    print(f"  Prefix:  {args.prefix}")
    if args.rubric_override:
        print(f"  Rubric:  {args.rubric_override}")
    if args.compare:
        print(f"  Compare: default vs {args.compare}")
    if args.limit:
        print(f"  Limit:   {args.limit}")
    print()

    # ── Step 1: Parse JSONL ───────────────────────────────────────────────────
    print("── Step 1: Parse JSONL ─────────────────────────────────────────")
    if not os.path.exists(args.input):
        print(f"[ERROR] Input file not found: {args.input}")
        sys.exit(1)

    patents = jsonl_to_patent_dicts(args.input)

    if not patents:
        print("[ERROR] No valid patents in JSONL. Aborting.")
        sys.exit(1)

    if args.limit:
        patents = patents[:args.limit]
        print(f"  Limited to first {args.limit} patents.")

    if args.dry_run:
        print()
        print("── Dry-run: Sample patents ─────────────────────────────────────")
        for p in patents[:5]:
            print(f"  {p['patent_id']:<25} title={len(p['title']):>5} chars  "
                  f"abstract={len(p['abstract']):>5}  claims={len(p['claims']):>6}  "
                  f"year={p['year']}")
        print(f"\n  Total: {len(patents)} patents ready for analysis.")
        print("  (dry-run complete, no analysis performed)")
        return

    # ── Step 2: Activate config ───────────────────────────────────────────────
    print()
    print("── Step 2: Activate config ─────────────────────────────────────")

    # Load config to validate and get settings
    cfg_spec = importlib.util.spec_from_file_location("_cfg", str(Path(args.config).resolve()))
    cfg = importlib.util.module_from_spec(cfg_spec)
    cfg_spec.loader.exec_module(cfg)
    print(f"  Target: {cfg.TARGET_PRODUCT}")

    # Override USE_LLM based on CLI flag
    # We need to patch config.py before importing llm_analyzer
    _ensure_config(args.config)

    try:
        # Patch USE_LLM in the active config if needed
        project_root = Path(__file__).resolve().parent.parent
        active_config = project_root / "config.py"
        config_text = active_config.read_text()

        if args.use_llm and "USE_LLM = False" in config_text:
            config_text = config_text.replace("USE_LLM = False", "USE_LLM = True")
            active_config.write_text(config_text)
            print(f"  [config] Patched USE_LLM = True")
        elif not args.use_llm and "USE_LLM = True" in config_text:
            config_text = config_text.replace("USE_LLM = True", "USE_LLM = False")
            active_config.write_text(config_text)
            print(f"  [config] Patched USE_LLM = False")

        # Now import llm_analyzer (reads config at module level)
        # Add project root to path if needed
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        # Force reimport in case config was cached
        for mod_name in list(sys.modules.keys()):
            if mod_name == "config" or mod_name.startswith("modules."):
                del sys.modules[mod_name]

        from modules.output_writer import save_results, print_summary

        # ── Rubric loading helper ─────────────────────────────────────────
        def _load_rubric(rubric_path: str) -> str:
            """Load rubric file and expand {TARGET_*} placeholders."""
            text = Path(rubric_path).read_text(encoding="utf-8")
            text = text.replace("{TARGET_DRUG}", cfg.TARGET_DRUG)
            text = text.replace("{TARGET_ROUTE}", cfg.TARGET_ROUTE)
            text = text.replace("{TARGET_INDICATION}", cfg.TARGET_INDICATION)
            return text

        def _patch_analysis_chain(rubric_text: str):
            """
            Monkey-patch llm_analyzer's analysis_chain with a custom rubric.
            Must be called AFTER importing llm_analyzer.
            """
            import modules.llm_analyzer as _analyzer
            from langchain_core.prompts import ChatPromptTemplate

            patched_prompt = ChatPromptTemplate.from_messages([
                ("system", rubric_text),
                ("human", "標題：{title}\n\n摘要：{abstract}\n\n請求項：{claims}\n\n法律狀態：{status}"),
            ])
            _analyzer.analysis_chain = patched_prompt | _analyzer.analysis_llm.with_structured_output(
                _analyzer.PatentAnalysis
            )

        # ── Import analyzer ───────────────────────────────────────────────
        from modules.llm_analyzer import analyze_patent
        import modules.llm_analyzer as _analyzer_mod

        # ── Skip-screening: bypass Stage 1, all patents go to Stage 2 ────
        if args.skip_screening:
            def _stage2_only(patent: dict) -> dict:
                """Directly run Stage 2 analysis, skipping Stage 1 screening."""
                raw_claims = patent.get("claims") or ""
                if not raw_claims.strip():
                    claims_input = (
                        f"(Claims missing, analysis based on Abstract): "
                        f"{patent.get('abstract', '')}"
                    )
                else:
                    claims_input = raw_claims[:_analyzer_mod.CLAIMS_MAX_CHARS]

                analysis = _analyzer_mod.invoke_with_retry(
                    _analyzer_mod.analysis_chain, {
                        "title":    patent.get("title", ""),
                        "abstract": patent.get("abstract", ""),
                        "claims":   claims_input,
                        "status":   patent.get("status", "Unknown"),
                    }
                )
                return {**patent, **analysis.model_dump()}

            # Replace the function used in _run_batch
            analyze_patent = _stage2_only
            print(f"  [skip-screening] All patents go directly to Stage 2")

        # ── Helper: run one batch ─────────────────────────────────────────
        def _run_batch(label: str) -> list[dict]:
            batch_results = []
            for i, patent in enumerate(patents, 1):
                if i % 50 == 0 or i == len(patents):
                    print(f"  [{label}] [{i}/{len(patents)}] ...", flush=True)
                try:
                    result = analyze_patent(patent)
                    batch_results.append(result)
                except Exception as e:
                    print(f"  [{label}] [ERROR] {patent['patent_id']}: {e}")
                    batch_results.append({
                        **patent,
                        "is_target_drug": False,
                        "delivery_routes": ["Error"],
                        "indications": [],
                        "fto_risk": "Low",
                        "gap_opportunity": f"Analysis error: {str(e)[:100]}",
                        "reasoning": f"Error during analysis: {str(e)[:200]}",
                    })
            return batch_results

        # ── Step 3: Run analysis ──────────────────────────────────────────
        print()

        if args.compare:
            # ── A/B compare mode ──────────────────────────────────────────
            print("── Step 3: A/B Compare ─────────────────────────────────────")
            print(f"  [A] Default rubric (from llm_analyzer.py)")
            print(f"  [B] Override: {args.compare}")
            print(f"  Processing {len(patents)} patents × 2 runs...")

            # Run A: default rubric
            print()
            print("  ── Run [A]: Default rubric ────────────────────────────────")
            results_a = _run_batch("A")

            # Run B: override rubric
            print()
            print("  ── Run [B]: Override rubric ───────────────────────────────")
            rubric_b = _load_rubric(args.compare)
            _patch_analysis_chain(rubric_b)
            results_b = _run_batch("B")

            # ── Step 4: Output both ───────────────────────────────────────
            print()
            print("── Step 4: Output (A/B) ────────────────────────────────────")
            csv_a = save_results(results_a, prefix=f"{args.prefix}_A_default")
            print_summary(results_a)

            csv_b = save_results(results_b, prefix=f"{args.prefix}_B_override")
            print_summary(results_b)

            # ── Step 5: Diff summary ──────────────────────────────────────
            print()
            print("── Step 5: A/B Diff ────────────────────────────────────────")
            diff_count = 0
            for ra, rb in zip(results_a, results_b):
                pid = ra.get("patent_id", "?")
                risk_a = ra.get("fto_risk", "?")
                risk_b = rb.get("fto_risk", "?")
                if risk_a != risk_b:
                    diff_count += 1
                    print(f"  {pid:<25} {risk_a:>6} → {risk_b:<6}  "
                          f"A: {ra.get('reasoning', '')[:60]}")
                    print(f"  {'':25} {'':>6}   {'':6}  "
                          f"B: {rb.get('reasoning', '')[:60]}")
            if diff_count == 0:
                print("  No risk level differences between A and B.")
            else:
                print(f"\n  {diff_count}/{len(results_a)} patents changed risk level.")
            print(f"\n  Done. Compare both Excel files to review.")

        else:
            # ── Normal or rubric-override mode ────────────────────────────
            mode_label = "LLM" if args.use_llm else "Rule"
            if args.rubric_override:
                mode_label = "LLM (rubric override)"
                rubric_text = _load_rubric(args.rubric_override)
                _patch_analysis_chain(rubric_text)
                print(f"── Step 3: Analyze ({mode_label}) ─────────────────────────")
                print(f"  Rubric: {args.rubric_override} ({len(rubric_text)} chars)")
            else:
                print(f"── Step 3: Analyze ({mode_label} mode) ─────────────────────────────")
            print(f"  Processing {len(patents)} patents...")

            results = _run_batch("run")

            # ── Step 4: Output ────────────────────────────────────────────
            print()
            print("── Step 4: Output ──────────────────────────────────────────────")
            csv_path = save_results(results, prefix=args.prefix)
            print_summary(results)

            print(f"\n  Done. {len(results)} patents analyzed.")

    finally:
        # Always restore original config
        _restore_config()


if __name__ == "__main__":
    main()
    