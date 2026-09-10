#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
藥名 → InChIKey 解析器（probe_surechembl_bulk.py phase 2 的前置）。

為什麼需要這個
--------------
SureChEMBL bulk 的 `compounds` 表只有 id / smiles / inchi / inchi_key /
mol_weight，**沒有名稱欄位**。所以「藥名 → compound_id」在 bulk data 裡走不
通，必須先在外部解析成 InChIKey 再 join。

比對策略
--------
InChIKey 是三段式 `AAAAAAAAAAAAAA-BBBBBBBBFV-P`，共 27 字元：
  第 1 段 14 碼  連接性骨架（立體異構共用）
  第 2 段 10 碼  立體化學 / 同位素 + 版本旗標
  第 3 段  1 碼  質子化狀態

輸出同時給精確 key 和 14 碼骨架前綴。phase 2 用前綴比對可以一次抓到所有
立體異構，這對專利很重要 —— 專利常只畫平面結構或寫消旋體。

**鹽型不共用骨架段**（連接性不同），所以要靠同義字分別查。本 script 會把
PubChem 回的所有相關 CID 都收進來，包含鹽型。

已知會失敗的類型
----------------
  生物製劑（單株抗體、胜肽藥）  無 InChIKey，SureChEMBL 化學抽取抓不到
  藥物類別名（如 Dihydropyridines）不是化合物，需人工展開為成員
兩者都會標在 report 裡，跟「查得到但 SureChEMBL 沒收」區分開。

Usage
-----
  python resolve_inchikey.py --drugs drugs.txt --out drug_inchikey.tsv
  python resolve_inchikey.py --drugs drugs.txt --out drug_inchikey.tsv \\
      --report resolve_report.md --cache .inchi_cache.json

Deps: 只用標準函式庫。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"

UA = "prior-art-tool/inchikey-resolver (contact: see project README)"

# 已知的生物製劑 / 非小分子。標記出來，跟「小分子但沒查到」分開歸因。
KNOWN_BIOLOGIC = {
    "spesolimab",       # anti-IL36R mAb
    "imsidolimab", "adalimumab", "secukinumab", "ixekizumab",
    "ustekinumab", "guselkumab", "risankizumab", "bimekizumab",
    "etanercept", "infliximab", "brodalumab", "tildrakizumab",
}

# 已知的藥物類別名（不是單一化合物）。專利不會用這個字面。
KNOWN_CLASS_TERM = {
    "dihydropyridines", "dihydropyridine",
    "corticosteroids", "retinoids", "statins", "beta-blockers",
    "calcium channel blockers", "antihistamines",
}

# 手動同義字。PubChem 對 INN / 商品名 / 鹽型的覆蓋不一致，補幾個已知的。
EXTRA_SYNONYMS = {
    "cromolyn": ["cromoglicic acid", "cromolyn sodium", "sodium cromoglycate"],
    "cyclosporine": ["ciclosporin", "cyclosporin A"],
    "nedocromil": ["nedocromil sodium"],
    "olopatadine": ["olopatadine hydrochloride"],
    "azelastine": ["azelastine hydrochloride"],
    "ketotifen": ["ketotifen fumarate"],
    "trospium": ["trospium chloride"],
    "oxybutynin": ["oxybutynin chloride", "oxybutynin hydrochloride"],
    "solifenacin": ["solifenacin succinate"],
    "darifenacin": ["darifenacin hydrobromide"],
    "pemirolast": ["pemirolast potassium"],
    "nerandomilast": ["BI 1015550", "BI-1015550"],
    "maxacalcitol": ["22-oxacalcitriol"],
}


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════════════════

class Fetcher:
    def __init__(self, pause: float = 0.25, retries: int = 3,
                 timeout: int = 30, cache: dict | None = None):
        self.pause, self.retries, self.timeout = pause, retries, timeout
        self.cache = cache if cache is not None else {}
        self.calls = 0
        self.errors = 0          # 抓取失敗次數，與「查無」嚴格區分

    def get_json(self, url: str) -> dict | None:
        """回傳 JSON，404 視為「查無」回 None，其他錯誤重試後回 None。"""
        if url in self.cache:
            return self.cache[url]
        last = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                self.cache[url] = data
                self.calls += 1
                time.sleep(self.pause)   # PubChem 建議 <= 5 req/s
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:        # 查無此名，不是錯誤
                    self.cache[url] = None
                    time.sleep(self.pause)
                    return None
                last = f"HTTP {e.code}"
                # 503 = PubChem 限流，退避
                time.sleep(self.pause * (3 ** attempt) if e.code == 503
                           else self.pause)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(self.pause * (2 ** attempt))
        # 不 silent-except，但也不能把「抓取失敗」誤記成「查無」——
        # 兩者在歸因上完全不同：前者是我方網路問題，後者是資料庫真的沒有。
        self.errors += 1
        log(f"      ! 取得失敗（重試 {self.retries} 次）: {last}")
        log(f"        {url[:110]}")
        return "FETCH_ERROR"


# ══════════════════════════════════════════════════════════════════════════════
# 解析
# ══════════════════════════════════════════════════════════════════════════════

# InChIKey 是 27 字元 14-10-1：`AAAAAAAAAAAAAA-BBBBBBBBFV-P`
# ⚠ 第二段是 10 碼不是 8 碼（如 UHFFFAOYSA）。寫成 8 會把所有 key 判為無效，
#   而且失敗方式很陰險：不報錯，只是全部靜默略過，看起來像「查無」。
_IK_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


def valid_ik(k: str) -> bool:
    return bool(_IK_RE.match(str(k).strip().upper()))


def norm_name(s: str) -> str:
    """比對用正規化：小寫、只留英數。"""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def name_matches(pref: str, drug: str, via: str) -> bool:
    """
    ChEMBL 回傳的 pref_name 是否真的是我們要的藥。

    ⚠ ChEMBL `molecule/search?q=` 是**全文檢索**不是名稱查詢。實測查
      "cromolyn sodium" 會回氯化鈉 / 氰化鉀 / 疊氮化鈉 / 碳酸氫鈉；查
      "olopatadine hydrochloride" 會回一堆別人的鹽酸鹽，造成 Olopatadine /
      Azelastine / Oxybutynin 共用七個 key。不驗名稱就會把 NaCl 送進
      phase 2，命中全庫。

    接受條件：pref_name 與「原始藥名」或「本次查詢名」任一方向含括。
    用原始藥名是為了 code name 同義字（BI 1015550 → pref_name
    NERANDOMILAST，兩者字面無關，但藥名對得上）。
    """
    p = norm_name(pref)
    if not p:
        return False          # pref_name 為空的多是研究用化合物，不是我們的藥
    for cand in (norm_name(drug), norm_name(via)):
        if cand and (cand in p or p in cand):
            return True
    return False


# 反離子 / 無機小分子的後備過濾。名稱驗證是主防線，這是第二道。
_FORMULA_C = re.compile(r"C(?![a-z])(\d*)")


def is_counterion(formula: str, mw) -> bool:
    """MW 小且碳數少 → 反離子或無機鹽，不可能是本專案的藥。"""
    try:
        mw = float(mw)
    except (TypeError, ValueError):
        return False
    m = _FORMULA_C.search(str(formula or ""))
    n_c = int(m.group(1) or 1) if m else 0
    return mw < 200 and n_c < 6


def pubchem_lookup(f: Fetcher, name: str) -> list[dict]:
    """名稱 → CID → InChIKey。回傳 [{cid, inchikey, formula, mw, name}]。"""
    q = urllib.parse.quote(name)
    url = (f"{PUBCHEM}/compound/name/{q}/property/"
           f"InChIKey,MolecularFormula,MolecularWeight,Title/JSON")
    data = f.get_json(url)
    if data == "FETCH_ERROR":
        return "FETCH_ERROR"
    if not data:
        return []
    out = []
    for p in data.get("PropertyTable", {}).get("Properties", []):
        ik = str(p.get("InChIKey", "")).upper()
        if not valid_ik(ik):
            continue
        out.append({"cid": p.get("CID"), "inchikey": ik,
                    "formula": p.get("MolecularFormula"),
                    "mw": p.get("MolecularWeight"),
                    "name": p.get("Title") or name, "src": "pubchem"})
    return out


def chembl_lookup(f: Fetcher, name: str) -> list[dict]:
    """ChEMBL molecule search。對已上市藥的 INN 覆蓋比 PubChem 好。"""
    q = urllib.parse.quote(name)
    data = f.get_json(f"{CHEMBL}/molecule/search?q={q}&format=json&limit=10")
    if data == "FETCH_ERROR":
        return "FETCH_ERROR"
    if not data:
        return []
    out = []
    for m in data.get("molecules", []):
        st = m.get("molecule_structures") or {}
        ik = str(st.get("standard_inchi_key") or "").upper()
        if not valid_ik(ik):
            continue
        props = m.get("molecule_properties") or {}
        out.append({"cid": m.get("molecule_chembl_id"), "inchikey": ik,
                    "formula": props.get("full_molformula"),
                    "mw": props.get("full_mwt"),
                    "name": m.get("pref_name"),
                    "pref_name": m.get("pref_name"),
                    "type": m.get("molecule_type"), "src": "chembl"})
    return out


def resolve(f: Fetcher, drug: str) -> dict:
    low = drug.strip().lower()
    rec = {"drug": drug, "hits": [], "flags": [], "queried": []}

    if low in KNOWN_CLASS_TERM:
        rec["flags"].append("CLASS_TERM")
        log(f"    ⚠ {drug}: 藥物類別名，不是化合物。需人工展開為成員藥物")
        return rec
    if low in KNOWN_BIOLOGIC:
        rec["flags"].append("BIOLOGIC")
        log(f"    ⚠ {drug}: 生物製劑，無 InChIKey。SureChEMBL 結構抽取涵蓋不到")
        return rec

    names = [drug] + EXTRA_SYNONYMS.get(low, [])
    seen: dict[str, dict] = {}
    rejected: list[dict] = []
    n_err = 0
    for nm in names:
        rec["queried"].append(nm)
        for fn in (pubchem_lookup, chembl_lookup):
            res = fn(f, nm)
            if res == "FETCH_ERROR":
                n_err += 1
                continue
            for h in res:
                h["matched_via"] = nm
                # PubChem 的 compound/name/ 是精確名稱查詢，直接採信。
                # ChEMBL 的 molecule/search 是全文檢索，必須驗名稱。
                if h["src"] == "chembl" and not name_matches(
                        h.get("pref_name"), drug, nm):
                    h["reject"] = f"pref_name={h.get('pref_name')!r} 名稱不符"
                    rejected.append(h)
                    continue
                if is_counterion(h.get("formula"), h.get("mw")):
                    h["reject"] = "反離子／無機小分子"
                    rejected.append(h)
                    continue
                if h["inchikey"] not in seen:
                    seen[h["inchikey"]] = h
    rec["hits"] = list(seen.values())
    rec["rejected"] = rejected
    rec["fetch_errors"] = n_err
    if rejected:
        log(f"        （擋掉 {len(rejected)} 筆污染，見 report）")

    if not rec["hits"]:
        if n_err:
            # 關鍵區分：這一格在 phase 2 顯示為未解析，但原因是我方抓取失敗，
            # 不是資料庫沒有。誤記成 NOT_FOUND 會把網路問題寫進結論。
            rec["flags"].append("FETCH_ERROR")
            log(f"    ⚠ {drug}: {n_err} 次抓取失敗，無法判定是否存在。重跑")
        else:
            rec["flags"].append("NOT_FOUND")
            log(f"    ✗ {drug}: PubChem 與 ChEMBL 都查無")
    elif n_err:
        rec["flags"].append("PARTIAL")
        log(f"        ⚠ 另有 {n_err} 次抓取失敗，結果可能不完整")
    else:
        skels = {h["inchikey"][:14] for h in rec["hits"]}
        log(f"    ✓ {drug}: {len(rec['hits'])} keys / {len(skels)} 骨架"
            f"  ({', '.join(sorted(h['src'] for h in rec['hits'])[:3])}...)")
        if len(skels) > 3:
            rec["flags"].append("MANY_SKELETONS")
            log(f"        ⚠ {len(skels)} 個不同骨架，同義字可能撈到不相關的，"
                f"請看 report 逐筆確認")
    return rec


# ══════════════════════════════════════════════════════════════════════════════

def load_drugs(path: Path) -> list[str]:
    """支援 # 註解與空行（使用者清單就是這個格式）。"""
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#")[0].strip()
        if ln:
            out.append(ln)
    return out


def write_tsv(recs: list[dict], path: Path, use_skeleton: bool) -> None:
    lines = ["# drug_name\tinchikey[,inchikey...]",
             "# 14 碼無連字號者 = InChIKey 骨架前綴（涵蓋所有立體異構）",
             f"# generated {datetime.now(timezone.utc).isoformat()}"]
    for r in recs:
        keys: list[str] = []
        if use_skeleton:
            keys = sorted({h["inchikey"][:14] for h in r["hits"]})
        else:
            keys = sorted({h["inchikey"] for h in r["hits"]})
        # 沒命中也要留一行，phase 2 才能把「查不了」跟「0 命中」分開顯示
        lines.append(f"{r['drug']}\t{','.join(keys)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(recs: list[dict], path: Path) -> None:
    L = [f"# InChIKey 解析報告", "",
         f"生成 {datetime.now(timezone.utc).isoformat()}", ""]
    ok = [r for r in recs if r["hits"]]
    L += [f"- 解析成功 {len(ok)} / {len(recs)}", ""]

    fails = [r for r in recs if not r["hits"]]
    if fails:
        L += ["## 未解析（phase 2 會顯示為 `·` 而非 0）", ""]
        for r in fails:
            why = {"CLASS_TERM": "藥物類別名，需人工展開",
                   "BIOLOGIC": "生物製劑，無 InChIKey",
                   "NOT_FOUND": "PubChem/ChEMBL 查無（資料庫真的沒有）",
                   "FETCH_ERROR": "**抓取失敗，非查無** — 請重跑後再判定"}
            reason = "；".join(why.get(f, f) for f in r["flags"]) or "未知"
            L.append(f"- **{r['drug']}** — {reason}")
        L.append("")

    L += ["## 逐筆結果", "",
          "| 藥物 | InChIKey | 骨架 | 分子式 | MW | 來源 | 比對名稱 |",
          "|---|---|---|---|---|---|---|"]
    for r in recs:
        if not r["hits"]:
            L.append(f"| {r['drug']} | — | — | — | — | — | "
                     f"{'/'.join(r['flags'])} |")
            continue
        for h in sorted(r["hits"], key=lambda x: x["inchikey"]):
            mw = h.get("mw")
            mw = f"{float(mw):.1f}" if mw not in (None, "") else "?"
            L.append(f"| {r['drug']} | `{h['inchikey']}` | "
                     f"`{h['inchikey'][:14]}` | {h.get('formula') or '?'} | "
                     f"{mw} | {h['src']} | {h.get('matched_via', '')} |")
    allrej = [(r["drug"], h) for r in recs for h in r.get("rejected", [])]
    if allrej:
        L += ["", f"## 已自動擋掉的污染（{len(allrej)} 筆）", "",
              "ChEMBL `molecule/search` 是全文檢索，查「X sodium」會回所有含",
              "sodium 的分子。以下已被名稱驗證或反離子過濾擋下，僅供稽核。", "",
              "| 藥物 | InChIKey | 分子式 | MW | 查詢名 | 擋下原因 |",
              "|---|---|---|---|---|---|"]
        for d, h in allrej[:80]:
            mw = h.get("mw")
            mw = f"{float(mw):.1f}" if mw not in (None, "") else "?"
            L.append(f"| {d} | `{h['inchikey']}` | {h.get('formula') or '?'} | "
                     f"{mw} | {h.get('matched_via','')} | {h.get('reject','')} |")
        if len(allrej) > 80:
            L.append(f"| … | 另 {len(allrej)-80} 筆 | | | | |")

    L += ["", "## 人工確認重點", "",
          "1. 同一藥物仍出現多個**骨架**時，先看 MW 是否落在該藥合理範圍。",
          "   鹽型骨架不同但 MW 接近母體，要留；MW 差一個數量級的要刪。",
          "2. 交叉污染的徵兆：同一個 InChIKey 出現在兩個不相關的藥物底下。",
          "   grep 一下重複的 key 就看得到。",
          "3. 未解析的藥物要區分歸因：類別名 / 生物製劑 / 抓取失敗 / 真的查無，",
          "   四者在 phase 2 的意義完全不同。"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="藥名 → InChIKey")
    ap.add_argument("--drugs", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("drug_inchikey.tsv"))
    ap.add_argument("--report", type=Path, default=Path("resolve_report.md"))
    ap.add_argument("--cache", type=Path, default=Path(".inchi_cache.json"))
    ap.add_argument("--pause", type=float, default=0.25,
                    help="請求間隔秒數。PubChem 建議 <= 5 req/s")
    ap.add_argument("--exact", action="store_true",
                    help="輸出完整 InChIKey 而非 14 碼骨架前綴。"
                         "預設用骨架以涵蓋立體異構")
    args = ap.parse_args()

    drugs = load_drugs(args.drugs)
    log("=" * 70)
    log(f"  InChIKey 解析 — {len(drugs)} 個藥物")
    log("=" * 70)

    cache = {}
    if args.cache.exists():
        try:
            cache = json.loads(args.cache.read_text(encoding="utf-8"))
            log(f"  cache: {len(cache)} 筆")
        except Exception as e:
            log(f"  ! cache 讀取失敗，忽略: {e}")

    f = Fetcher(pause=args.pause, cache=cache)
    recs = []
    try:
        for d in drugs:
            recs.append(resolve(f, d))
    finally:
        try:
            args.cache.write_text(json.dumps(cache, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception as e:
            log(f"  ! cache 寫入失敗: {e}")

    write_tsv(recs, args.out, use_skeleton=not args.exact)
    write_report(recs, args.report)

    # 交叉污染偵測：同一個 key 出現在多個藥物底下
    from collections import defaultdict
    key_owner = defaultdict(set)
    for r in recs:
        for h in r["hits"]:
            key_owner[h["inchikey"]].add(r["drug"])
    shared = {k: v for k, v in key_owner.items() if len(v) > 1}
    if shared:
        log(f"\n  ⚠ {len(shared)} 個 InChIKey 同時屬於多個藥物，可能仍有污染：")
        for k, v in list(shared.items())[:10]:
            log(f"      {k}  →  {', '.join(sorted(v))}")

    ok = sum(1 for r in recs if r["hits"])
    ferr = [r["drug"] for r in recs if "FETCH_ERROR" in r["flags"]]
    partial = [r["drug"] for r in recs if "PARTIAL" in r["flags"]]
    log(f"\n  解析成功 {ok}/{len(recs)}  ·  HTTP {f.calls} 次"
        f"  ·  失敗 {f.errors} 次")
    if ferr:
        log(f"  ⚠ 抓取失敗導致無結果（非查無），重跑即可: {', '.join(ferr)}")
    if partial:
        log(f"  ⚠ 有結果但不完整: {', '.join(partial)}")
    log(f"  → {args.out}")
    log(f"  → {args.report}   ← 送 phase 2 前請人工看過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
