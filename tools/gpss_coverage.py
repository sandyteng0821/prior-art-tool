#!/usr/bin/env python3
"""
gpss_coverage.py — fetch_by_pn.py 產出的 JSONL 的欄位覆蓋率報告

放置位置：gpss-probe/gpss_coverage.py（與 fetch_by_pn.py 同層）

設計立場
────────
1. 對齊既有的 Google Patents 覆蓋率報告（tools/inspect_jsonl.py →
   jsonl_qc_summary_*.xlsx），讓 GPSS 與 GP 能並排比較。但兩者的「拿不到」
   語意不同，欄位定義必須分開，見下。

2. 不重新解析 raw record。fetch_by_pn.py 的 assess() 已經把每筆 ok 紀錄的
   title / abstract / claims 判定寫進 `quality[]`（含語言、空 block 處理、
   rule_analyzable）。這支只讀 quality，跟 fetcher 的判定對齊——重新數 raw
   會產生兩套不一致的數字。

3. 三層流失分開算。GP 報告只有「clean vs dirty + 欄位覆蓋」一層；GPSS 有
   輸入端就流失的號碼（TW/MOJ 等不被 LOOKS_LIKE_ID 接受），那是 GP 沒有的
   類別，混進覆蓋率會誤導。

GPSS vs GP 的關鍵差異
─────────────────────
  full_text  GP 有 full_text 欄；GPSS 在 API 層級就沒有說明書（handoff：
             無說明書 token）。所以 full_text 對 GPSS 恆為 N/A，不是覆蓋率
             低，是規格。報告會標成「n/a」而非 0%，避免被讀成缺漏。

  分母       GP 的覆蓋率分母是 clean rows。GPSS 的欄位覆蓋率分母是 ok 紀錄
             （成功抓到內容的），不含 empty/response_error/quota——那些沒有
             內容可談。三層流失（輸入解析→抓取→欄位）各自獨立呈現。

三層流失
────────
  ① 輸入解析：清單行數 → 合法專利號數（read_ids 的 LOOKS_LIKE_ID）
              ⚠ 這層算不到——JSONL 裡沒有被跳過的行的紀錄。需要原始清單。
              用 --idlist 傳入才能補上這層；不傳就只報 ②③。
  ② 抓取成功：JSONL 行數 → verdict=ok 的數（扣 empty/response_error/quota）
  ③ 欄位覆蓋：ok 紀錄裡 title / abstract / claims 各有幾個非空

用法
────
    # 單檔彙總
    python gpss_coverage.py data/gpss_IPF_idlist_20260709.jsonl

    # 多檔一次彙總（對齊 xlsx 那張表，一檔一列）
    python gpss_coverage.py data/gpss_*.jsonl

    # 補上輸入解析層（需原始清單）
    python gpss_coverage.py data/gpss_IPF_idlist_20260709.jsonl \
        --idlist ~/dev-workplace/prior_art_tool/data/plainid/IPF_idlist_20260709.txt

    # 逐行明細（title/abstract/claims 的 * / NA / miss）
    python gpss_coverage.py data/gpss_IPF_idlist_20260709.jsonl --detail

    # 匯出彙總成 CSV
    python gpss_coverage.py data/gpss_*.jsonl --csv coverage_gpss.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


# read_ids 用的同一條規則。要跟 fetch_by_pn.py 一致，改那邊這邊也要改。
LOOKS_LIKE_ID = re.compile(r"^[A-Z]{2}[HS]?\d{4,}[A-Z]?\d?$")


def count_idlist(path: Path) -> tuple[int, int]:
    """回傳 (清單非空行數, 解析出合法號碼數)。對齊 read_ids 的解析邏輯。

    這是流失層 ①。JSONL 本身看不到被跳過的行，所以要原始清單才算得出。
    """
    lines = 0
    ids = 0
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines += 1
        cols = [c.strip() for c in re.split(r"\t+|\s{2,}", line) if c.strip()]
        if len(cols) == 1:
            cols = line.split()
        pid = next((c.upper() for c in cols if LOOKS_LIKE_ID.match(c.upper())), None)
        if pid and pid not in seen:
            seen.add(pid)
            ids += 1
        elif pid is None:
            pass  # 解析不出號碼的行
    return lines, ids


def _field_status(q: dict) -> dict:
    """從一筆 quality dict 讀出 title / abstract / claims 是否有內容。

    直接用 assess() 已算好的判定，不重新解析 raw record。
      title    — has_english_title
      abstract — lang.abstract 不是 'none'（assess 對空摘要記 'none'）
      claims   — has_claims
    另外帶出 rule_analyzable / english_text_available 給彙總分桶用。
    """
    lang = q.get("lang") or {}
    return {
        "title": bool(q.get("has_english_title")),
        "abstract": (lang.get("abstract") not in (None, "none")),
        "claims": bool(q.get("has_claims")),
        "rule_analyzable": bool(q.get("rule_analyzable")),
        "english_text_available": bool(q.get("english_text_available")),
        "doc_number": q.get("doc_number") or "",
    }


def analyze_file(path: Path) -> dict:
    """讀一個 JSONL，回傳該檔的覆蓋率統計。

    計數單位說明（關鍵）：
      ok_rows     = verdict=ok 的「行數」= 唯一 requested_id 數。覆蓋率分母用這個。
      ok_records  = quality 紀錄總數。一號多筆時 > ok_rows。
                    多出來的是「同一件專利的多個公開形式」（實測全是這種）：
                      CO6140030A1 → [CO6140030A1, CO6140030A2]  同號多 kind
                      JP2009057382A → [JP…A, JPWO…A1]           PCT 進入國家階段
                      TW381079B → [TW…B, TWI…B]                 TW 發明專利前綴
                    都是同一件發明，不是不同專利，所以覆蓋率以「件」計，一號算一次。
      field_hits  = 以「件」為單位：該 id 只要有任一紀錄含該欄位，就算 1。
    """
    verdicts: dict[str, int] = {}
    total_lines = 0
    ok_records = 0          # quality 紀錄總數（一號多筆時 > ok 行數）
    ok_rows = 0             # verdict=ok 的行數 = 唯一 requested_id 數（覆蓋率分母）
    field_hits = {"title": 0, "abstract": 0, "claims": 0}   # 以「件」計
    bucket = {"usable": 0, "partial": 0, "blind": 0}        # 以「件」計
    multi_form = 0         # 一號多筆的件數（同一專利多公開形式）
    per_line: list[dict] = []   # 明細用

    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                verdicts["_bad_json_line"] = verdicts.get("_bad_json_line", 0) + 1
                continue

            v = rec.get("verdict", "?")
            verdicts[v] = verdicts.get(v, 0) + 1
            rid = rec.get("requested_id", "?")

            if v != "ok":
                per_line.append({"id": rid, "verdict": v,
                                 "title": None, "abstract": None, "claims": None})
                continue

            ok_rows += 1
            quals = rec.get("quality") or []
            if len(quals) > 1:
                multi_form += 1
            if not quals:
                # ok 但沒有 quality（理論上不該發生，防禦）。以「件」計為全缺。
                per_line.append({"id": rid, "verdict": "ok(no-quality)",
                                 "title": False, "abstract": False, "claims": False})
                continue

            # 以「件」為單位彙整：該 id 只要有任一紀錄含該欄位就算有。
            # 一號多筆是同一專利的多公開形式，取跨形式的聯集才不會低估
            # （例：A1 形式沒 claims、A2 形式有，這件應算「有 claims」）。
            id_has = {"title": False, "abstract": False, "claims": False}
            id_usable = id_partial = False
            for q in quals:
                ok_records += 1
                st = _field_status(q)
                for f in ("title", "abstract", "claims"):
                    id_has[f] = id_has[f] or st[f]
                id_usable = id_usable or st["rule_analyzable"]
                id_partial = id_partial or st["english_text_available"]

            for f in ("title", "abstract", "claims"):
                if id_has[f]:
                    field_hits[f] += 1
            key = ("usable" if id_usable
                   else "partial" if id_partial
                   else "blind")
            bucket[key] += 1

            per_line.append({
                "id": rid,
                "verdict": "ok" + (f"×{len(quals)}" if len(quals) > 1 else ""),
                "title": id_has["title"],
                "abstract": id_has["abstract"],
                "claims": id_has["claims"],
            })

    return {
        "path": path,
        "total_lines": total_lines,
        "verdicts": verdicts,
        "ok_rows": ok_rows,
        "ok_records": ok_records,
        "multi_form": multi_form,
        "field_hits": field_hits,
        "bucket": bucket,
        "per_line": per_line,
    }


def _pct(n: int, d: int) -> str:
    return f"{n / d * 100:5.1f}%" if d else "  n/a"


def print_summary(stats: list[dict], idlist_map: dict[str, tuple[int, int]]) -> None:
    print("\n" + "═" * 78)
    print("  GPSS 欄位覆蓋率彙總")
    print("═" * 78)
    print("  full_text 欄：GPSS 無說明書（API 規格），恆為 n/a — 不是覆蓋率低。")
    print("  欄位覆蓋率分母 = 成功抓到的專利「件數」（唯一號碼，一號多筆算一件）。")

    for s in stats:
        name = s["path"].name
        v = s["verdicts"]
        ok = s["ok_rows"]          # 分母 = 件數（唯一 requested_id）
        rec = s["ok_records"]      # 紀錄總數（含多公開形式）
        fh = s["field_hits"]
        b = s["bucket"]

        print("\n" + "─" * 78)
        print(f"  {name}")
        print("─" * 78)

        # 流失層 ①（若有原始清單）
        key = s["path"].name
        if key in idlist_map:
            lines, ids = idlist_map[key]
            print(f"  ① 輸入解析   清單 {lines} 行 → 合法號碼 {ids} "
                  f"（{_pct(ids, lines)}）  流失 {lines - ids} 行"
                  f"（號碼格式不被接受，如 TWI/MOJ）")

        # 流失層 ②：抓取
        vparts = "  ".join(f"{k}={v}" for k, v in sorted(v.items())
                           if not k.startswith("_"))
        print(f"  ② 抓取結果   {s['total_lines']} 行：{vparts}")
        mf = s.get("multi_form", 0)
        print(f"               ok {ok} 件"
              f"{f'（{mf} 件含多公開形式 → {rec} 筆紀錄）' if mf else ''}"
              f"  抓取成功率 {_pct(ok, s['total_lines'])}")

        # 流失層 ③：欄位覆蓋（分母 = 件數）
        print(f"  ③ 欄位覆蓋   （分母 {ok} 件；一號多筆取跨形式聯集）")
        print(f"       title       {fh['title']:>5} / {ok}   {_pct(fh['title'], ok)}")
        print(f"       abstract    {fh['abstract']:>5} / {ok}   {_pct(fh['abstract'], ok)}")
        print(f"       claims      {fh['claims']:>5} / {ok}   {_pct(fh['claims'], ok)}")
        print(f"       full_text     n/a          （GPSS 無說明書）")

        # 可分析度分桶（以件計）
        print(f"  ── 可分析度   usable {b['usable']}"
              f"（claim scope 判得了）· partial {b['partial']}"
              f"（僅英文標題/摘要）· blind {b['blind']}（無英文）")

    print("\n" + "═" * 78)


def print_detail(s: dict) -> None:
    print("\n" + "─" * 78)
    print(f"  明細：{s['path'].name}")
    print(f"  {'ID':<20} {'verdict':<14} {'title':<6} {'abs':<6} {'claims':<6}")
    print("─" * 78)

    def mark(b) -> str:
        return "  *  " if b is True else ("  ·  " if b is False else " NA  ")

    for r in s["per_line"]:
        print(f"  {r['id']:<20} {r['verdict']:<14} "
              f"{mark(r['title'])} {mark(r['abstract'])} {mark(r['claims'])}")


def write_csv(stats: list[dict], idlist_map: dict, out: Path) -> None:
    """對齊 jsonl_qc_summary xlsx 的欄位排列（GPSS 版）。"""
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["File", "JSONL lines", "ok rows", "ok records",
                    "fetch success %", "title (n)", "abstract (n)", "claims (n)",
                    "title %", "abstract %", "claims %", "full_text %",
                    "usable", "partial", "blind"])
        for s in stats:
            ok = s["ok_rows"]          # 分母 = 件數
            rec = s["ok_records"]
            fhd = s["field_hits"]
            b = s["bucket"]
            def p(n, d): return round(n / d, 6) if d else ""
            w.writerow([
                s["path"].name, s["total_lines"], s["ok_rows"], rec,
                p(s["ok_rows"], s["total_lines"]),
                fhd["title"], fhd["abstract"], fhd["claims"],
                p(fhd["title"], ok), p(fhd["abstract"], ok), p(fhd["claims"], ok),
                "n/a",
                b["usable"], b["partial"], b["blind"],
            ])
    print(f"[csv] {out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="fetch_by_pn.py JSONL 的欄位覆蓋率報告",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jsonl", type=Path, nargs="+", help="一或多個 JSONL 檔")
    ap.add_argument("--idlist", type=Path, default=None,
                    help="對應的原始清單（補上輸入解析流失層；單檔時有效）")
    ap.add_argument("--detail", action="store_true",
                    help="逐行印 title/abstract/claims 狀態（* / · / NA）")
    ap.add_argument("--csv", type=Path, default=None, help="彙總匯出 CSV")
    args = ap.parse_args()

    # idlist 對應：單檔 --idlist 直接綁；多檔時靠檔名猜（gpss_<清單名>.jsonl）
    idlist_map: dict[str, tuple[int, int]] = {}
    if args.idlist:
        if len(args.jsonl) == 1:
            idlist_map[args.jsonl[0].name] = count_idlist(args.idlist)
        else:
            print("[warn] --idlist 只在單檔時綁定；多檔請逐檔跑。", file=sys.stderr)

    stats = []
    for p in args.jsonl:
        if not p.exists():
            print(f"[skip] 找不到 {p}", file=sys.stderr)
            continue
        stats.append(analyze_file(p))

    if not stats:
        sys.exit("沒有可分析的檔案。")

    print_summary(stats, idlist_map)

    if args.detail:
        for s in stats:
            print_detail(s)

    if args.csv:
        write_csv(stats, idlist_map, args.csv)


if __name__ == "__main__":
    main()
