"""
test_debug_tools.py — Validation tests for tools/debug_scoring.py and tools/check_db.py

Two tiers:
  - Dry-run tests (default): zero cost, pure DB + CLI validation
  - Live tests (--live flag): calls LLM API, costs money

Usage:
    # Dry-run tests only (zero cost, safe to run anytime)
    python3 -m tests.test_debug_tools

    # Include live LLM tests (costs API calls)
    python3 -m tests.test_debug_tools --live

Validation cases from: docs/spec/spec_debug_scoring.md
Origin: US9415051B1 risk underscoring investigation (2026-06-17)
"""

import argparse
import subprocess
import sys

# ═══════════════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════════════

PATENT_ID = "US9415051B1"
CONFIG = "configs/pemirolast_ipf_v3.py"
RUBRIC_V2 = "configs/rubrics/rubric_v2.txt"

# Expected values (from spec + findings doc)
EXPECTED_CLAIMS_CHARS = 1229
EXPECTED_IPF_IN_CLAIMS = True
EXPECTED_PEMIROLAST_IN_CLAIMS = True


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

passed = 0
failed = 0
skipped = 0


def run_cmd(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=120,
    )


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}")
        if detail:
            print(f"    {detail}")


def skip(name: str, reason: str = ""):
    global skipped
    skipped += 1
    print(f"  ○ {name} (skipped{': ' + reason if reason else ''})")


def section(title: str):
    print()
    print(f"── {title} {'─' * max(1, 60 - len(title))}")


# ═══════════════════════════════════════════════════════════════════════════════
# check_db tests (all zero cost)
# ═══════════════════════════════════════════════════════════════════════════════

def test_check_db():
    section("check_db: basic lookup")

    # Known patent should be found
    r = run_cmd(["python3", "-m", "tools.check_db", PATENT_ID])
    check("exits 0", r.returncode == 0)
    check("shows patent ID", PATENT_ID in r.stdout)
    check("shows ✓ for known patent", "✓" in r.stdout)
    check("reports In DB: 1", "In DB: 1" in r.stdout)

    # Unknown patent should show missing
    r = run_cmd(["python3", "-m", "tools.check_db", "FAKE_PATENT_999"])
    check("unknown patent shows ✗", "✗" in r.stdout)
    check("reports Missing: 1", "Missing: 1" in r.stdout)

    section("check_db: semicolon-separated input")
    r = run_cmd(["python3", "-m", "tools.check_db",
                 f"{PATENT_ID};FAKE_PATENT_999"])
    check("splits semicolons", "Checking 2" in r.stdout)
    check("one found one missing", "In DB: 1" in r.stdout and "Missing: 1" in r.stdout)

    section("check_db: --detail mode")
    r = run_cmd(["python3", "-m", "tools.check_db", PATENT_ID, "--detail"])
    check("shows title", "pemirolast" in r.stdout.lower())
    check("shows source", "google_patents" in r.stdout or "epo" in r.stdout)
    check("shows fetched_at", "fetched_at" in r.stdout)

    section("check_db: --family mode")
    r = run_cmd(["python3", "-m", "tools.check_db", PATENT_ID, "--family"])
    check("--family runs without error", r.returncode == 0)
    check("shows family section", "Family members" in r.stdout or "family_of" in r.stdout)


# ═══════════════════════════════════════════════════════════════════════════════
# debug_scoring dry-run tests (zero cost)
# ═══════════════════════════════════════════════════════════════════════════════

def test_debug_scoring_dryrun():
    section("debug_scoring: --dry-run (spec validation case 1)")

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--dry-run"])
    check("exits 0", r.returncode == 0)
    out = r.stdout

    # DB state
    check("claims present (non-empty)",
          "claims" in out and "0 chars" not in out.split("claims")[1][:20]
          if "claims" in out else False)
    check("shows DB state section", "DB State" in out)

    # Stage 1 section present
    check("Stage 1 section shown", "Stage 1: Screening" in out)
    check("shows screening model", "gpt-5-mini" in out or "SCREENING" in out)

    # Stage 2 section present
    check("Stage 2 section shown", "Stage 2: Analysis" in out)
    check("DRY-RUN label shown", "DRY-RUN" in out)

    # Keyword probe (core validation: IPF visible in claims)
    check("keyword probe: drug ✓", "drug" in out and "Pemirolast" in out)
    check("keyword probe: indication ✓",
          "idiopathic pulmonary fibrosis" in out.lower())
    check("keyword probe: route ✓", "route" in out)

    section("debug_scoring: --dry-run --stage 1")
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--dry-run", "--stage", "1"])
    check("stage 1 only: exits 0", r.returncode == 0)
    check("stage 1 shown", "Stage 1" in r.stdout)
    check("stage 2 NOT shown", "Stage 2" not in r.stdout)

    section("debug_scoring: --dry-run --stage 2")
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--dry-run", "--stage", "2"])
    check("stage 2 only: exits 0", r.returncode == 0)
    check("stage 1 NOT shown", "Stage 1" not in r.stdout)
    check("stage 2 shown", "Stage 2" in r.stdout)


def test_debug_scoring_rubric_dryrun():
    section("debug_scoring: --rubric-override --dry-run")

    import os
    if not os.path.exists(RUBRIC_V2):
        skip("rubric override dry-run", f"{RUBRIC_V2} not found")
        return

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--stage", "2",
                 "--rubric-override", RUBRIC_V2, "--dry-run"])
    check("exits 0", r.returncode == 0)
    check("shows 'override' label", "override" in r.stdout.lower())
    check("shows OVERRIDE prompt", "OVERRIDE" in r.stdout)
    # rubric_v2 key change: "分析所有 claims" instead of "重點分析 independent"
    check("rubric v2 interpolated (分析所有 claims)",
          "分析所有 claims" in r.stdout)


def test_debug_scoring_model_override_dryrun():
    section("debug_scoring: --screening-model / --analysis-model --dry-run")

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--dry-run",
                 "--screening-model", "gpt-4o-mini",
                 "--analysis-model", "gpt-4o"])
    check("exits 0", r.returncode == 0)
    check("screening model = gpt-4o-mini", "gpt-4o-mini" in r.stdout)
    check("analysis model = gpt-4o",
          r.stdout.count("gpt-4o") >= 2)  # appears in both stages


def test_debug_scoring_errors():
    section("debug_scoring: error handling")

    # Missing patent
    r = run_cmd(["python3", "-m", "tools.debug_scoring", "NONEXISTENT",
                 "--config", CONFIG, "--dry-run"])
    check("missing patent: nonzero exit", r.returncode != 0)
    check("missing patent: error message", "not found" in r.stdout.lower())

    # Missing config
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", "configs/nonexistent.py", "--dry-run"])
    check("missing config: nonzero exit", r.returncode != 0)
    check("missing config: error message", "not found" in r.stdout.lower())

    # Missing --config entirely
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID])
    check("no --config: nonzero exit", r.returncode != 0)

    # --compare + --dry-run conflict
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--compare", "x.txt", "--dry-run"])
    check("--compare + --dry-run rejected", r.returncode != 0)

    # --compare + --rubric-override conflict
    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG,
                 "--compare", "x.txt", "--rubric-override", "y.txt"])
    check("--compare + --rubric-override rejected", r.returncode != 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Live LLM tests (require --live flag, cost API calls)
# ═══════════════════════════════════════════════════════════════════════════════

def test_debug_scoring_live_stage1():
    """Spec validation: is_relevant=True, quick_risk=Medium"""
    section("debug_scoring LIVE: --stage 1")

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--stage", "1"])
    check("exits 0", r.returncode == 0, r.stderr[:200] if r.returncode else "")

    out = r.stdout
    check("is_relevant: True", "is_relevant: True" in out or "is_relevant:  True" in out)
    # quick_risk should be Medium (gpt-5-mini) — Low also acceptable per spec note
    has_medium = "quick_risk:  Medium" in out or "quick_risk: Medium" in out
    has_low = "quick_risk:  Low" in out or "quick_risk: Low" in out
    check("quick_risk: Medium (or Low)",
          has_medium or has_low,
          f"got neither Medium nor Low in output")


def test_debug_scoring_live_stage2_default():
    """Spec validation: fto_risk=Low or Medium with default rubric"""
    section("debug_scoring LIVE: --stage 2 (default rubric)")

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--stage", "2"])
    check("exits 0", r.returncode == 0, r.stderr[:200] if r.returncode else "")

    out = r.stdout
    check("is_target_drug = True", "True" in out.split("is_target_drug")[1][:20]
          if "is_target_drug" in out else False)

    # fto_risk should be Low (gpt-5) or Medium (gpt-4o) — not High with default rubric
    risk_line = [l for l in out.splitlines() if "fto_risk" in l]
    if risk_line:
        risk_val = risk_line[0].strip()
        check("fto_risk = Low or Medium (not High)",
              "Low" in risk_val or "Medium" in risk_val,
              f"got: {risk_val}")
    else:
        check("fto_risk found in output", False, "fto_risk line not found")

    # reasoning should mention oral / claim 1
    check("reasoning mentions oral or claim 1",
          "oral" in out.lower() or "claim 1" in out.lower() or "口服" in out)


def test_debug_scoring_live_rubric_override():
    """Spec validation: fto_risk=High with rubric_v2.txt"""
    section("debug_scoring LIVE: --stage 2 --rubric-override rubric_v2.txt")

    import os
    if not os.path.exists(RUBRIC_V2):
        skip("rubric override live", f"{RUBRIC_V2} not found")
        return

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--stage", "2",
                 "--rubric-override", RUBRIC_V2])
    check("exits 0", r.returncode == 0, r.stderr[:200] if r.returncode else "")

    out = r.stdout
    risk_line = [l for l in out.splitlines() if "fto_risk" in l]
    if risk_line:
        risk_val = risk_line[0].strip()
        check("fto_risk = High",
              "High" in risk_val,
              f"got: {risk_val}")
    else:
        check("fto_risk found in output", False, "fto_risk line not found")

    # reasoning should mention claim 5 or IPF
    check("reasoning mentions claim 5 or IPF",
          "claim 5" in out.lower() or "ipf" in out.lower())


def test_debug_scoring_live_compare():
    """Spec validation: A/B shows Low vs High"""
    section("debug_scoring LIVE: --compare rubric_v2.txt")

    import os
    if not os.path.exists(RUBRIC_V2):
        skip("compare live", f"{RUBRIC_V2} not found")
        return

    r = run_cmd(["python3", "-m", "tools.debug_scoring", PATENT_ID,
                 "--config", CONFIG, "--compare", RUBRIC_V2])
    check("exits 0", r.returncode == 0, r.stderr[:200] if r.returncode else "")

    out = r.stdout
    check("A/B Comparison section", "A/B Comparison" in out)
    check("shows ≠ marker (differences found)", "≠" in out)
    # Both Low and High should appear somewhere
    check("contains Low", "Low" in out)
    check("contains High", "High" in out)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Validation tests for debug_scoring and check_db.",
        prog="python3 -m tests.test_debug_tools",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Also run live LLM tests (costs API calls)",
    )
    args = parser.parse_args()

    print()
    print("=" * 65)
    print("  test_debug_tools — debug_scoring + check_db validation")
    print("=" * 65)

    # ── Zero-cost tests (always run) ──────────────────────────────────────
    test_check_db()
    test_debug_scoring_dryrun()
    test_debug_scoring_rubric_dryrun()
    test_debug_scoring_model_override_dryrun()
    test_debug_scoring_errors()

    # ── Live LLM tests (only with --live) ─────────────────────────────────
    if args.live:
        print()
        print("  ⚠  Running live LLM tests (API calls will be made)")
        test_debug_scoring_live_stage1()
        test_debug_scoring_live_stage2_default()
        test_debug_scoring_live_rubric_override()
        test_debug_scoring_live_compare()
    else:
        section("Live LLM tests")
        print("  (skipped — use --live to run, costs API calls)")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    total = passed + failed
    print(f"  Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("  ⚠  SOME TESTS FAILED")
    else:
        print("  ✓  ALL TESTS PASSED")
    print("=" * 65)
    print()

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()