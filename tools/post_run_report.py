"""
post_run_report — Pipeline 跑完後的標準健檢報告

讀取 gap_analysis Excel/CSV，產出結構化的 post-run 摘要：
  1. 基本統計（總筆數、風險分布、is_target_drug 比例）
  2. High risk 清單（patent_id + title + reasoning）
  3. Medium 抽樣（前 10 筆，看 reasoning 品質）
  4. 資料品質檢查（claims missing、year 空值、expiry 覆蓋）
  5. DB 交叉比對（search_log project 筆數、jurisdiction 分布）
  6. 可選：匯出 High+Medium 子集到獨立 Excel（方便人工精讀）

純讀取，零成本，不改 DB、不改 output。

Usage:
    # 基本用法：指定 Excel 路徑
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.xlsx

    # 指定 CSV
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.csv

    # 加 DB 交叉比對（需指定 project name）
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.xlsx \
        --project 'Pioglitazone_口服治療表皮溶解水疱症_(EB)'

    # 匯出 High+Medium 子集
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.xlsx \
        --export-review

    # 只看 High（快速模式）
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.xlsx \
        --high-only

    # 顯示 Medium 前 N 筆
    python3 -m tools.post_run_report output/gap_analysis_20260724_1530.xlsx \
        --medium-sample 20
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from collections import Counter

import pandas as pd

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

DB_PATH = Path(_project_root) / "cache" / "patents.db"

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        sys.exit(1)
    if p.suffix in (".xlsx", ".xls"):
        return pd.read_excel(p)
    elif p.suffix == ".csv":
        return pd.read_csv(p)
    else:
        print(f"ERROR: unsupported format: {p.suffix}", file=sys.stderr)
        sys.exit(1)


def _jurisdiction(patent_id: str) -> str:
    """Extract jurisdiction prefix from patent ID (e.g. EP, US, CN, WO)."""
    prefix = ""
    for ch in str(patent_id):
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix.upper() or "??"


def _separator(title: str = "") -> str:
    width = 60
    if title:
        return f"\n{'═' * width}\n  {title}\n{'═' * width}"
    return f"\n{'─' * width}"


# ═══════════════════════════════════════════════════════════════════════════════
# Report sections
# ═══════════════════════════════════════════════════════════════════════════════

def section_overview(df: pd.DataFrame) -> None:
    """§1 — 基本統計"""
    print(_separator("§1  Overview"))
    print(f"  Total patents:  {len(df)}")
    print()

    # Risk distribution
    risk_counts = df["fto_risk"].value_counts()
    risk_map = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
    for risk in ["High", "Medium", "Low"]:
        count = risk_counts.get(risk, 0)
        pct = count / len(df) * 100 if len(df) > 0 else 0
        icon = risk_map.get(risk, "  ")
        print(f"  {icon} {risk:8s}: {count:4d}  ({pct:5.1f}%)")

    other = len(df) - sum(risk_counts.get(r, 0) for r in ["High", "Medium", "Low"])
    if other > 0:
        print(f"     Other:    {other:4d}  (scoring error?)")

    print()

    # is_target_drug
    if "is_target_drug" in df.columns:
        target_count = df["is_target_drug"].sum()
        pct = target_count / len(df) * 100 if len(df) > 0 else 0
        print(f"  is_target_drug = True:  {int(target_count)} / {len(df)}  ({pct:.1f}%)")


def section_high_risk(df: pd.DataFrame) -> None:
    """§2 — High risk 逐筆清單"""
    high = df[df["fto_risk"] == "High"].copy()
    print(_separator(f"§2  High Risk Detail  ({len(high)} patents)"))

    if high.empty:
        print("  (none)")
        return

    for i, (_, r) in enumerate(high.iterrows(), 1):
        pid = r.get("patent_id", "?")
        title = str(r.get("title", ""))[:70]
        year = r.get("year", "")
        is_td = r.get("is_target_drug", "")
        routes = r.get("delivery_routes", "")
        reasoning = str(r.get("reasoning", ""))

        print(f"\n  [{i}] {pid}  ({year})")
        print(f"      Title:    {title}")
        print(f"      Target?   {is_td}    Routes: {routes}")
        print(f"      Reason:   {reasoning[:120]}")


def section_medium_sample(df: pd.DataFrame, n: int = 10) -> None:
    """§3 — Medium 抽樣（看 reasoning 品質）"""
    med = df[df["fto_risk"] == "Medium"].copy()
    shown = min(n, len(med))
    print(_separator(f"§3  Medium Sample  (showing {shown} / {len(med)})"))

    if med.empty:
        print("  (none)")
        return

    for i, (_, r) in enumerate(med.head(n).iterrows(), 1):
        pid = r.get("patent_id", "?")
        title = str(r.get("title", ""))[:60]
        reasoning = str(r.get("reasoning", ""))[:100]
        print(f"  [{i}] {pid}  {title}")
        print(f"      {reasoning}")
        print()


def section_data_quality(df: pd.DataFrame) -> None:
    """§4 — 資料品質檢查"""
    print(_separator("§4  Data Quality"))

    total = len(df)

    # Year missing
    year_missing = df["year"].isna().sum() if "year" in df.columns else 0
    print(f"  year empty/NaN:       {year_missing} / {total}")

    # Reasoning empty
    if "reasoning" in df.columns:
        reason_empty = df["reasoning"].isna().sum() + (df["reasoning"] == "").sum()
        print(f"  reasoning empty:      {reason_empty} / {total}")

        # Claims missing signal (reasoning 裡提到 claims missing)
        claims_missing_kw = df["reasoning"].fillna("").str.lower()
        claims_missing = claims_missing_kw.str.contains("claims missing").sum()
        claims_missing += claims_missing_kw.str.contains("未進行.*精讀").sum()
        print(f"  'claims missing' in reasoning:  {claims_missing} / {total}")

    # Expiry date coverage
    if "expiry_date" in df.columns:
        expiry_filled = df["expiry_date"].notna() & (df["expiry_date"] != "")
        print(f"  expiry_date filled:   {expiry_filled.sum()} / {total}")

    # Status distribution
    if "status" in df.columns:
        status_counts = df["status"].value_counts()
        print(f"\n  Status distribution:")
        for st, cnt in status_counts.items():
            print(f"    {st:20s}: {cnt}")

    # Jurisdiction distribution
    if "patent_id" in df.columns:
        jurisdictions = df["patent_id"].apply(_jurisdiction)
        jur_counts = jurisdictions.value_counts()
        print(f"\n  Jurisdiction distribution:")
        for jur, cnt in jur_counts.items():
            print(f"    {jur:6s}: {cnt}")


def section_db_crosscheck(df: pd.DataFrame, project: str) -> None:
    """§5 — DB 交叉比對"""
    print(_separator(f"§5  DB Cross-check  (project: {project})"))

    if not DB_PATH.exists():
        print(f"  DB not found at {DB_PATH}, skipping.")
        return

    conn = sqlite3.connect(str(DB_PATH))

    # search_log count for this project
    row = conn.execute(
        "SELECT COUNT(DISTINCT patent_id) FROM search_log WHERE project = ?",
        (project,),
    ).fetchone()
    print(f"  search_log distinct patent_ids: {row[0]}")

    # Total patents in DB
    row = conn.execute("SELECT COUNT(*) FROM patents").fetchone()
    print(f"  DB total patents:               {row[0]}")

    # Check how many from this output are in DB
    output_ids = set(df["patent_id"].dropna().astype(str).tolist())
    placeholders = ",".join("?" for _ in output_ids)
    if output_ids:
        row = conn.execute(
            f"SELECT COUNT(*) FROM patents WHERE patent_id IN ({placeholders})",
            list(output_ids),
        ).fetchone()
        print(f"  Output patents in DB:           {row[0]} / {len(output_ids)}")

        # Claims coverage in DB for this batch
        rows = conn.execute(
            f"""SELECT patent_id, 
                       CASE WHEN claims IS NOT NULL AND claims != '' THEN 1 ELSE 0 END as has_claims
                FROM patents 
                WHERE patent_id IN ({placeholders})""",
            list(output_ids),
        ).fetchall()
        has_claims = sum(r[1] for r in rows)
        print(f"  DB claims non-empty:            {has_claims} / {len(rows)}")

    conn.close()


def section_export(df: pd.DataFrame, source_path: str) -> None:
    """§6 — 匯出 High + Medium 子集"""
    subset = df[df["fto_risk"].isin(["High", "Medium"])].copy()
    if subset.empty:
        print("  No High/Medium patents to export.")
        return

    out_path = Path(source_path).with_name(
        Path(source_path).stem + "_review_subset.xlsx"
    )
    subset.to_excel(str(out_path), index=False, sheet_name="Review")
    print(f"\n  Exported {len(subset)} patents → {out_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Post-run analysis report for gap_analysis output.",
    )
    ap.add_argument(
        "input",
        help="Path to gap_analysis .xlsx or .csv",
    )
    ap.add_argument(
        "--project",
        default=None,
        help="Project name for DB cross-check (e.g. 'Pioglitazone_口服治療表皮溶解水疱症_(EB)')",
    )
    ap.add_argument(
        "--export-review",
        action="store_true",
        help="Export High+Medium subset to a separate Excel file",
    )
    ap.add_argument(
        "--high-only",
        action="store_true",
        help="Only show §1 overview + §2 High detail (quick mode)",
    )
    ap.add_argument(
        "--medium-sample",
        type=int,
        default=10,
        help="Number of Medium patents to sample in §3 (default: 10)",
    )
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    df = _load(args.input)

    print(f"\n  Input: {args.input}")
    print(f"  Rows:  {len(df)}")

    # §1 always
    section_overview(df)

    # §2 always
    section_high_risk(df)

    if not args.high_only:
        # §3
        section_medium_sample(df, n=args.medium_sample)

        # §4
        section_data_quality(df)

        # §5 if --project
        if args.project:
            section_db_crosscheck(df, args.project)

    # §6 if --export-review
    if args.export_review:
        section_export(df, args.input)

    print()


if __name__ == "__main__":
    main()
