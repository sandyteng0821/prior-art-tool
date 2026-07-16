# Coverage Analysis: GPP + IPF Expert Patent Lists

**Date:** 2026-07-16
**Context:** Will provided 584 patents (GPP=147, IPF=437) for scoring alignment. Prior to DB import, assess data source coverage to plan import strategy.

---

## Background

Pipeline DB (EPO-only) has known claims coverage gap for non-EP
jurisdictions. Prior validation runs demonstrated this:

- **Orphenadrine (EPO-only):** 97/576 claims = 16.8%
  (`probe_coverage_v2 --csv output/gap_analysis_20260707_1454.csv`)
- **Pemirolast (EPO + Google Patents):** 234/293 claims = 79.9%
  (`probe_coverage_v2 --csv output/gap_analysis_20260508_1551.csv`)

This analysis extends that pattern to expert-provided ID lists that
are NOT yet in DB — evaluating GP vs EPO coverage *before* import
to confirm GP as primary data source for non-EP jurisdictions.

---

## Method

1. `check_db --file` confirmed 580/584 patents NOT IN DB
2. Google Patents scraped via Kaggle notebook (JSONL output)
3. EPO probed via `batch_epo_probe` (inspect API, sandbox fallback)
4. `compare_coverage --mode all` compared both sources per-patent

---

## Results

### GPP (147 patents — CN/TW/HK/MO jurisdictions)

| Metric | Count | Coverage |
|--------|-------|----------|
| GP Claim coverage | 139/147 | 94.6% |
| EPO Abstract coverage (`abstract_chars > 0`) | 99/147 | 67.3% |
| EPO Claim coverage | 0/147 | 0.0% |
| **Union Claim coverage (GP ∪ EPO)** | **139/147** | **94.6%** |
| Union Any content | 141/147 | 95.9% |
| No data from either | 6/147 | 4.1% |

- EPO: zero claims for CN/TW/HK/MO (abstract only)
- GP: near-complete coverage except 6 MO patents (Macau)
- GP is sole source for claims in this jurisdiction set

### IPF (437 patents — 28 jurisdictions)

| Metric | Count | Coverage |
|--------|-------|----------|
| GP Claim coverage | 304/437 | 69.6% |
| EPO Abstract coverage (`abstract_chars > 0`) | 217/437 | 49.7% |
| EPO Claim coverage | 92/437 | 21.1% |
| **Union Claim coverage (GP ∪ EPO)** | **305/437** | **69.8%** |
| Union Any content | 333/437 | 76.2% |
| No data from either | 104/437 | 23.8% |

Key jurisdiction patterns
(from `compare_coverage --mode all` jurisdiction table, see `scratch/coverage_compare_ipf.txt`):

- **US (169):** GP 167 claims, EPO 1 claim
- **WO (87):** GP 76 claims, EPO 74 claims
- **EP (35):** GP 20 claims, EPO 7 claims
- **AT (47):** both sources empty
- **IN/PH (25):** both sources empty

### No-data patents (104/437 IPF, 6/147 GPP)

Concentrated in translation/validation jurisdictions
(from `neithr` column in jurisdiction table):
AT=47, IN=14, PH=11, MO=6, MT=3, IL=3.

---

## Conclusions

1. Google Patents confirmed as primary import source (claims coverage)
2. EPO supplements abstract for GP-empty patents
3. ~18% of IPF patents unreachable from either source (translation patents)
4. Import strategy: GP JSONL with `--allow-insert`, EPO abstract gap-fill
5. Consistent with Pemirolast Task I finding: EPO-only 16.8% → EPO+GP 79.9% claims
   (see `probe_coverage_v2` runs in Background)

---

## Reproduction

See `docs/validation/coverage_analysis_gpp_ipf_20260716_runbook.md`

---

## Raw output

Full compare_coverage output preserved in:

- `scratch/coverage_compare_gpp.txt`
- `scratch/coverage_compare_ipf.txt`
