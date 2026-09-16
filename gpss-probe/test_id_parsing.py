#!/usr/bin/env python3
"""
test_id_parsing.py — fetch_by_pn.py ID parsing 的離線回歸測試

放置位置：scratch/test_id_parsing.py（gitignored；純本地回歸用）

用途
────
每次改 LOOKS_LIKE_ID / ID_RE / parse_id / variants 之前後跑一次，確認：
  1. must-pass    — TWI/MOJ/TWM/TWD（含真清單號碼）能 parse
  2. must-still   — 既有格式（US/JP/CN/EP/WO/KR/TW-無I）parse 結果不變、能 parse
  3. must-reject  — garbage（藥名 / A12 / ABC123）仍被拒
  4. no-leak      — TWI/MOJ 的 variants 不產生 TW…/MO… 裸號（不跨 namespace）

不碰 GPSS、不花配額、不需網路。

    python scratch/test_id_parsing.py          # 跑全部，印結果
    python scratch/test_id_parsing.py -q       # 只印失敗與總結

取得被測函式的方式（fallback 設計）
────────────────────────────────────
優先直接 import fetch_by_pn（測本尊，零 drift）。
若 import 失敗（probe_gpss_quota 未套 patch → 頂層 sys.exit guard 觸發，
或缺網路相依），落到 AST 抽取：只取 parsing 相關的 top-level 定義，
丟掉 import G / guard，exec 進乾淨 namespace。
—— 兩條路都是讀「同一個 fetch_by_pn.py 檔案」，不是抄副本。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# fetch_by_pn.py 與本檔的相對位置：本檔在 scratch/，fetcher 在 gpss-probe/
_HERE = Path(__file__).resolve().parent
_CANDIDATES = [
    _HERE.parent / "gpss-probe" / "fetch_by_pn.py",   # scratch/ 與 gpss-probe/ 同層
    _HERE.parent / "fetch_by_pn.py",                   # 兩者同層的情況
    _HERE / "fetch_by_pn.py",
]

_WANTED = ("_SERIES", "ID_RE", "LOOKS_LIKE_ID", "parse_id", "variants", "variant_rule")


def _find_source() -> Path:
    for p in _CANDIDATES:
        if p.exists():
            return p
    sys.exit(f"找不到 fetch_by_pn.py，找過：{[str(p) for p in _CANDIDATES]}")


def _load_direct():
    """路徑 1：直接 import 真 module。最嚴謹，測到本尊全部依賴。"""
    src_dir = _find_source().parent
    sys.path.insert(0, str(src_dir))
    import fetch_by_pn as m  # 若頂層 guard 觸發，這行會 SystemExit → 由呼叫端接
    return {name: getattr(m, name) for name in _WANTED if hasattr(m, name)}, "import"


def _load_ast():
    """路徑 2（fallback）：AST 抽 top-level parsing 定義，繞過 guard / 網路相依。

    ⚠ 只保留 _WANTED 裡的 top-level 名字。若未來 variants 依賴新的 top-level
      helper，必須把它加進 _WANTED，否則這裡會漏抓 —— 測試會在 collect 階段
      就抓到 NameError（下方 self-check），不會靜默過。
    """
    src_path = _find_source()
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    keep: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & set(_WANTED):
                keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            keep.append(node)

    header = ast.parse("import re\nfrom collections import Counter, defaultdict")
    mod = ast.Module(body=[*header.body, *keep], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(compile(mod, f"<extracted:{src_path.name}>", "exec"), ns)
    return {name: ns[name] for name in _WANTED if name in ns}, "ast-fallback"


def load_functions() -> tuple[dict, str]:
    try:
        fns, how = _load_direct()
        # import 成功但符號不齊（理論上不會，保險）
        if all(k in fns for k in ("parse_id", "variants", "LOOKS_LIKE_ID")):
            return fns, how
    except SystemExit:
        pass  # 頂層 guard 觸發 → 落 fallback
    except Exception as e:
        print(f"  (直接 import 失敗：{type(e).__name__}: {e} → 落 AST fallback)")
    return _load_ast()


# ══════════════════════════════════════════════════════════════════════
# 測試資料
# ══════════════════════════════════════════════════════════════════════

# 真清單裡的號碼（來自 GPP_idlist_20260709.txt 實跑）——這是回歸的錨點
REAL_TWI = ["TWI818938B", "TWI487533B", "TWI606049B", "TWI907452B",
            "TWI920103B", "TWI618535B", "TWI659030B", "TWI888665B",
            "TWI657076B", "TWI646091B", "TWI728957B", "TWI380812B",
            "TWI414517B", "TWI726916B", "TWI822754B", "TWI762542B",
            "TWI433839B", "TWI694986B", "TWI373465B", "TWI520945B",
            "TWI723158B", "TWI527806B"]
REAL_MOJ = ["MOJ001158C", "MOJ002704C", "MOJ001108C",
            "MOJ006086C", "MOJ002598C", "MOJ003309C"]

# 額外形態（設計 / 新型 / 澳門其它 kind），確認 series 群組不只認 I/J
MUST_PASS = REAL_TWI + REAL_MOJ + [
    "TWM604140U", "TWD214823S", "USD801033S",  # 新型 / 設計（含美國設計）
    "TWI221587", "MOJ001158",                  # 無 kind code
]

# 既有格式：改動後必須 (a) 仍能 parse (b) parse 結果與「無 series 群組」時相同。
# (b) 用「series 必須為空」來斷言——既有格式不該誤觸新群組。
MUST_STILL_PASS = [
    "US9415051B1", "US09415051B1", "US2017112837A1",
    "JPH04368330A", "JPS5725786B2", "JP2003055224A",
    "TW201800397A", "TW200836724A", "TW591076",
    "EP1818058A3", "WO2016190847A1", "CN101587106A", "CN117062607A",
    "KR20180012345A", "HK1139652A1", "MO12345",
]

MUST_REJECT = [
    "PIOGLITAZONE", "drug composition", "see note 3",
    "A12", "ABC123", "US", "TWI", "1234567",
    "COMPOSITION", "", "   ",
]

# variants 不得把 TWI…/MOJ… 變成裸 TW…/MO…（跨 namespace = N1 已驗證的不同專利）
NO_LEAK_INPUTS = REAL_TWI[:5] + REAL_MOJ + ["TWM604140U", "TWD214823S", "TWI221587"]


# ══════════════════════════════════════════════════════════════════════
# 測試
# ══════════════════════════════════════════════════════════════════════

def run(quiet: bool = False) -> int:
    fns, how = load_functions()
    parse_id = fns["parse_id"]
    variants = fns["variants"]
    LOOKS = fns["LOOKS_LIKE_ID"]

    fails: list[str] = []
    npass = 0

    def check(cond: bool, label: str) -> None:
        nonlocal npass
        if cond:
            npass += 1
            if not quiet:
                print(f"  ✓ {label}")
        else:
            fails.append(label)
            print(f"  ✗ {label}")

    print(f"[load] 被測函式來源：{how}（{_find_source()}）")
    print(f"[load] 抓到符號：{sorted(k for k in _WANTED if k in fns)}\n")

    print("── must-pass：TWI/MOJ/TWM/TWD 能 parse 且 LOOKS 接受 ──")
    for pid in MUST_PASS:
        p = parse_id(pid)
        check(bool(p) and bool(LOOKS.match(pid.upper())),
              f"{pid:<14} parse={'ok' if p else 'None'} looks={bool(LOOKS.match(pid.upper()))}")

    print("\n── must-still-pass：既有格式仍能 parse 且未誤觸 series ──")
    for pid in MUST_STILL_PASS:
        p = parse_id(pid)
        ok = bool(p) and bool(LOOKS.match(pid.upper()))
        # 既有格式 series 應為空（沒有 I/M/D/J 型別字母）
        series_clean = (p or {}).get("series", "") == ""
        check(ok and series_clean,
              f"{pid:<14} parse={'ok' if p else 'None'} series="
              f"{(p or {}).get('series','?')!r}（應空）")

    print("\n── must-reject：garbage 仍被拒 ──")
    for pid in MUST_REJECT:
        p = parse_id(pid)
        looks = bool(LOOKS.match(pid.upper()))
        check((p is None) and (not looks),
              f"{pid!r:<18} parse={'None' if p is None else p} looks={looks}")

    print("\n── no-leak：TWI/MOJ variants 不產生裸 TW…/MO… ──")
    _bad_prefix = tuple(f"{cc}{d}" for cc in ("TW", "MO") for d in "0123456789")
    for pid in NO_LEAK_INPUTS:
        allv = set(variants(pid)) | set(variants(pid, try_no_kind=True))
        leaks = [v for v in allv if v.startswith(_bad_prefix)]
        check(not leaks, f"{pid:<14} variants={sorted(allv)} leak={leaks or 'none'}")

    print("\n" + "═" * 60)
    total = npass + len(fails)
    if fails:
        print(f"  ❌ {len(fails)}/{total} 失敗：")
        for f in fails:
            print(f"       {f}")
        return 1
    print(f"  ✅ 全部 {total} 項通過（來源：{how}）")
    return 0


if __name__ == "__main__":
    sys.exit(run(quiet="-q" in sys.argv))
