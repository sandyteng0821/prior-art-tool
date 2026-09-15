#!/usr/bin/env python3
"""
fetch_by_pn.py — 用專利號清單向 GPSS 撈紀錄，落 JSONL

放置位置：gpss-probe/fetch_by_pn.py（與 probe_gpss_quota.py 同層）

設計立場
────────
1. 只負責「撈」，不負責「入庫」。輸出是逐行 JSONL，raw record 原樣保存，
   任何欄位對應／schema 轉換都留給獨立的 import step。
   （沿用 import_google_patents_jsonl.py 的分工；fetch 與 import 風險輪廓不同）

2. 號碼格式未知 → 內建變體重試。miss 回 0 筆記錄 = 0 配額，所以換格式重試
   在配額上免費，只花請求數。跑完的 summary 會告訴你哪一種形態實際有效，
   之後就能關掉變體搜尋改用確定的規則。

3. 配額算的是實際輸出筆數，不是請求數，也與 expFld 無關。
   → 欄位一律拉滿（免費），一個 PN 命中就是 1 筆。

用法
────
    python fetch_by_pn.py --ids pemirolast.txt --out gpss_pn.jsonl
    python fetch_by_pn.py --ids list.txt --limit 10 --dry-run
    python fetch_by_pn.py --ids list.txt --out gpss_pn.jsonl --resume
    python fetch_by_pn.py --ids list.txt --out gpss_pn.jsonl --resume \
                          --retry-misses --try-no-kind

輸入格式
────────
每行一個專利號，或 TSV（自動抓出看起來像專利號的那一欄，其餘欄位存進
source_meta）。`#` 開頭與空行忽略。重複的 ID 只查一次。

安全性
──────
userCode 只出現在實際送出的 request。所有 print / JSONL / 例外訊息都經過
G._redact()。新增任何輸出路徑都必須套。
驗證碼類錯誤 fail-fast，絕不重試（TIPO 會封 IP）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_gpss_quota as G

if not hasattr(G, "GpssResponseError"):
    sys.exit("probe_gpss_quota.py 尚未套用 patch（缺 GpssResponseError / "
             "loads_lenient）。未套用時 JP 案的 JSON bug 會讓整批中止。")


# ══════════════════════════════════════════════════════════════════════════════
# 查詢參數
# ══════════════════════════════════════════════════════════════════════════════

PAT_DB = ("TWA,TWB,TWD,JPA,JPB,JPD,CNA,CNB,CND,KPA,KPB,KPD,"
          "USA,USB,USD,SEAA,SEAB,WO,EPA,EPB,EUIPO,OTA,OTB")
PAT_AG = "A,B"
PAT_TY = "I,M,D"

# expFld —— 官方全集，20 個 token。
# 依據：API_code.pdf「表2 檢索參數、輸出欄位對照表」，輸出欄位欄位共 20 個，
#       與 API_instructions.pdf 的 expFld 範例逐字相同。這是窮盡的，不是範例。
#
#   PN,ID  → publication-reference (doc-number, date)
#   AN,AD  → application-reference (doc-number, date)
#   PA,IN  → parties.applicants / parties.inventors
#   LX     → parties.agents          ★ 巢狀，不是頂層 key
#   EX     → examiners
#   PR     → priority-claims
#   IC/CS  → classifications-ipc / classifications-cpc
#   UC/FI/FT/IR → classifications-national (uspc / fi / f-term / d-term)
#   IQ     → LOC 洛迦諾分類（設計專利專用）
#   TI/AB/CL/CI → patent-title / abstract / claims / references-cited
#
# ⚠ 沒有「說明書」token。GPSS 在 API 層級就不提供全文說明書，
#   所以 examples_extracted / backfill_snippets 那條線 GPSS 永遠補不上，
#   這是規格不是覆蓋率問題。
#
# ⚠ 2026-08-25 曾誤砍 LX/IQ/IR，理由是「13 筆零出現」。那個推論是錯的：
#     LX 的輸出在 parties.agents（巢狀），探針只比對頂層 key 所以看不見。
#        實測：拉 LX 時 WO2007093184A2 有 agents=PLOUGMANN & VINGTOFT，
#        不拉時同一篇就沒有 —— 真的會丟資料。
#     IQ/IR 是設計專利專用，發明案永遠為空。零出現是「不適用」不是「無效」。
#   expFld 確實有作用（expFld=PN 只回 1 欄，全集回 11 欄），
#   所以少拉就是少資料，而多拉不計配額。一律用全集。
EXP_FLD = "PN,AN,ID,AD,TI,PA,IN,LX,EX,PR,AB,IC,IQ,CS,UC,FI,FT,IR,CI,CL"

# 自檢用：已知存在、已知有 claims 的專利。參數打錯時 GPSS 會靜默丟棄未知欄位
# 並給錯的結果集，所以每次跑之前先確認這筆撈得到。
CANARY_PN = "US09415051B1"


# ══════════════════════════════════════════════════════════════════════════════
# 專利號解析與變體
# ══════════════════════════════════════════════════════════════════════════════

ID_RE = re.compile(r"^(?P<cc>[A-Z]{2})(?P<era>[HS])?(?P<num>\d+)(?P<kind>[A-Z]\d?)?$")

# 看起來像專利號的 token（用來從 TSV 裡挑欄位）
LOOKS_LIKE_ID = re.compile(r"^[A-Z]{2}[HS]?\d{4,}[A-Z]?\d?$")


def parse_id(pid: str) -> dict | None:
    m = ID_RE.match(pid.strip().upper())
    if not m:
        return None
    return {k: (m.group(k) or "") for k in ("cc", "era", "num", "kind")}


def variants(pid: str, *, try_no_kind: bool = False) -> list[str]:
    """產生候選格式，依信心排序。miss 免費，所以多試幾種沒有配額代價。

    規則來源是實際觀察到的差異：
      US9415051B1   vs  US09415051B1     ← 公告號補零到 8 碼
      US2017112837A1 vs US20170112837A1  ← 公開號流水號補零到 7 碼
      JPH04368330A  vs  JP04368330A      ← 平成年號的 H 前綴
    去掉 kind code 放最後，且預設關閉：它可能一次回多筆（同號多 kind），
    配額變成 N 筆，且拿回來的未必是你要的那一篇。
    """
    pid = pid.strip().upper()
    p = parse_id(pid)
    if not p:
        return [pid]

    cc, era, num, kind = p["cc"], p["era"], p["num"], p["kind"]
    out: list[str] = [pid]

    def add(num_: str, era_: str = era, kind_: str = kind) -> None:
        v = f"{cc}{era_}{num_}{kind_}"
        if v not in out:
            out.append(v)

    # ⚠ 補零規則只對 US 套用。唯一的證據是 US9415051B1 vs US09415051B1，
    #   那是美國公告號的慣例。WO/EP/AU/JP/CN 的號碼結構不同（WO 是
    #   年+6 碼流水號、EP 是 7 碼），對它們硬造變體只會讓 miss 清單變髒。
    #   非美管轄一律原樣送，讓第一輪的 miss 自己指出問題在哪。
    if cc == "US":
        is_pregrant = (len(num) == 10 and 1900 <= int(num[:4]) <= 2100)
        if is_pregrant:
            # YYYY + 6 碼 → YYYY + 7 碼（USPTO 標準公開號）
            add(num[:4] + num[4:].zfill(7))
        elif len(num) < 8:
            # 公告號補零到 8 碼
            add(num.zfill(8))

    if era:
        # 平成／昭和年號前綴可能不被收錄。只脫前綴，不加補零——
        # JP 號碼要不要補零沒有任何證據。
        add(num, era_="")

    if try_no_kind:
        for v in list(out):
            vp = parse_id(v)
            if vp and vp["kind"]:
                stripped = v[: -len(vp["kind"])]
                if stripped not in out:
                    out.append(stripped)

    return out


def variant_rule(requested: str, matched: str) -> str:
    """把「哪一種變體中了」歸類，summary 用。"""
    if requested == matched:
        return "as-is"
    rp, mp = parse_id(requested), parse_id(matched)
    if not rp or not mp:
        return "other"
    if rp["kind"] and not mp["kind"]:
        return "kind-code-stripped"
    if rp["era"] and not mp["era"]:
        return "era-prefix-dropped"
    rn, mn = rp["num"], mp["num"]
    if len(mn) > len(rn):
        if rn.lstrip("0") == mn.lstrip("0"):
            return "zero-padded"
        # 內插補零：US 公開號 YYYY + 6 碼 → YYYY + 7 碼
        if len(rn) == 10 and len(mn) == 11 and rn[:4] == mn[:4] \
                and rn[4:].lstrip("0") == mn[4:].lstrip("0"):
            return "serial-zero-padded"
    return "other"


# ══════════════════════════════════════════════════════════════════════════════
# 輸入
# ══════════════════════════════════════════════════════════════════════════════

def read_ids(path: Path) -> list[tuple[str, dict]]:
    """回傳 [(patent_id, source_meta), ...]，保序去重。

    支援純清單與 TSV。TSV 會挑出第一個長得像專利號的欄位當 ID，
    其餘欄位存進 source_meta（保留原始清單的 drug / title 等脈絡）。
    """
    seen: set[str] = set()
    out: list[tuple[str, dict]] = []
    skipped = 0

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        cols = [c.strip() for c in re.split(r"\t+|\s{2,}", line) if c.strip()]
        if len(cols) == 1:
            cols = line.split()

        pid, idx = None, None
        for i, c in enumerate(cols):
            if LOOKS_LIKE_ID.match(c.upper()):
                pid, idx = c.upper(), i
                break

        if pid is None:
            skipped += 1
            G.log(f"  ⚠ line {lineno}: 找不到專利號，略過 — {line[:70]!r}")
            continue

        meta = {"line": lineno,
                "other_cols": [c for i, c in enumerate(cols) if i != idx]}

        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, meta))

    if skipped:
        G.log(f"  ⚠ 共 {skipped} 行沒解析出專利號")
    return out


def load_done(path: Path) -> tuple[set[str], set[str]]:
    """讀既有 JSONL，回傳 (已命中的 id, 已 miss 的 id)。"""
    hits: set[str] = set()
    misses: set[str] = set()
    if not path.exists():
        return hits, misses
    # ⚠ 逐行迭代，不用 read_text()。47k 筆的 JSONL 約 230 MB
    #   （平均 5 KB/筆，41 條請求項那種上看 15 KB），一次讀進來會讓
    #   每次 --resume 啟動都吃掉 230 MB 字串。
    #   仍需 json.loads 每行 —— 用字串比對抓 requested_id 太脆 ——
    #   但解析完就丟，只留兩個 set。47k 行約 20-30 秒。
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                G.log(f"  ⚠ {path.name} line {lineno} 不是合法 JSON，忽略")
                continue
            rid = rec.get("requested_id")
            if not rid:
                continue
            (hits if rec.get("verdict") == "ok" else misses).add(rid)
            del rec
    return hits, misses


# ══════════════════════════════════════════════════════════════════════════════
# 輸入完整度與語言
# ══════════════════════════════════════════════════════════════════════════════
#
# 為什麼 fetcher 要管這個：
#   rule_based_analyze() 把 title+abstract+claims 串起來對英文關鍵字做子字串
#   比對。2026-08-25 實測出兩種會讓它靜默評成 Low risk 的輸入：
#     JP2012224629A — 摘要與請求項是日文（英文標題來自 PAJ）→ 上限 1/4
#     AU2003246097A1 — 根本沒有 claims 欄位 → 只剩標題+摘要
#   兩者都不是「無風險」，是「讀不到內容」。分析器目前無法區分。
#
#   修分析器是 task_O 的事（動 analyzer，不動 fetcher）。fetcher 的職責是
#   把判斷所需的事實記進 JSONL —— 它是唯一看得到 raw record 的地方。

def _flatten(node, out: list[str], budget: int = 4000) -> None:
    """把巢狀 dict/list 裡的字串攤平（語言偵測用，不做語意解析）。"""
    if sum(map(len, out)) > budget:
        return
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for x in node:
            _flatten(x, out, budget)
    elif isinstance(node, dict):
        for k, v in node.items():
            if not k.startswith("@"):
                _flatten(v, out, budget)


def _segments(node, budget: int = 4000) -> list[str]:
    """攤平成非空字串 list。語言判定必須逐段做 —— GPSS 的 abstract.p
    常常是 [原文, 官方英譯] 兩段，串起來會互相稀釋。"""
    buf: list[str] = []
    _flatten(node, buf, budget)
    return [s for s in buf if s.strip()]


def _lang(s: str) -> str:
    """單一字串的粗略語言判定。假名是 ja/zh 的判別特徵（中文沒有假名）。

    ⚠ 只對單一語言的字串可靠。中英混合時 han 比例會落在 0.15 門檻附近：
        CN101587106A 中英合併 → 0.164 → zh
        CN106361709A 中英合併 → 0.158 → zh
        CN105106147A 中英合併 → 0.150 → en   ← 同樣是中文摘要，判成英文
    三筆都在門檻 ±0.014 內，差別只來自機翻產出多長、中文段裡有多少全形
    標點和數字（都不算 CJK）。所以一律用 _lang_profile() 逐段判，不要
    直接把多段串起來丟進來。
    （JP 沒這問題 —— 假名門檻只有 2%，英文段稀釋不掉。）
    """
    if not s.strip():
        return "none"
    n = len(s)
    kana = sum(1 for c in s if "\u3040" <= c <= "\u30ff")
    han = sum(1 for c in s if "\u4e00" <= c <= "\u9fff")
    hang = sum(1 for c in s if "\uac00" <= c <= "\ud7af")
    if hang / n > 0.10:
        return "ko"
    if kana / n > 0.02:
        return "ja"
    if han / n > 0.15:
        return "zh"
    return "en"


def _lang_profile(node) -> dict:
    """逐段語言判定。

    primary   — 有任何非英文段就回該語言，全英文才回 "en"
                （語意是「這個欄位含有非英文內容」）
    has_english — 是否有任何一段是英文。這欄比 primary 重要：
                JP 公開案的 abstract 是 [日文, PAJ 英譯]，
                CN 公開案是 [中文, SIPO 英譯] —— 對英文關鍵字比對
                來說並非全盲，失去的只有請求項。
    """
    segs = _segments(node)
    if not segs:
        return {"langs": [], "primary": "none", "has_english": False, "n_segments": 0}
    langs = [_lang(s) for s in segs]
    non_en = [l for l in langs if l != "en"]
    return {
        "langs": sorted(set(langs)),
        "primary": Counter(non_en).most_common(1)[0][0] if non_en else "en",
        "has_english": "en" in langs,
        "n_segments": len(segs),
    }


def assess(rec: dict) -> dict:
    """單筆 raw record 的完整度／語言體檢。"""
    claims = (rec.get("claims") or {}).get("claim")
    claim_blocks = claims if isinstance(claims, list) else ([claims] if claims else [])

    title_obj = rec.get("patent-title") or {}

    # english-title 可能是 str | list（handoff 資料結構地雷 #4/#5）
    _raw_title = title_obj.get("english-title") or ""
    if isinstance(_raw_title, list):
        _raw_title = next((s for s in _raw_title if isinstance(s, str) and s.strip()), "")
    en_title = _raw_title if isinstance(_raw_title, str) else ""
    
    abstract = _lang_profile(rec.get("abstract"))
    claims_lang = _lang_profile(claims)

    # 回傳的號碼未必等於請求的號碼。GPSS 會正規化，而且格式不一致：
    #   US9492454B2    → US09492454B2      補零
    #   JP2003055224A  → JP2003-55224A     加連字號、去補零
    #   EP1818058A3    → EP1818058A2       kind code 被改掉
    # 而 JP 的年號前綴在比對時被忽略，會跨世代誤中（見 _ambiguity_cause）。
    # 所以下游一律以這個欄位為準，不要信 requested_id。
    pub = (rec.get("publication-reference") or {}).get("doc-number")

    return {
        "fields": sorted(k for k in rec if not k.startswith("@")),
        "database": rec.get("@database"),
        "doc_number": pub,
        "has_claims": bool(claim_blocks),
        # ⚠ 不是請求項條數。US 案的第一個 block 是「The invention claimed is:」
        #   前言，JP 案沒有前言 —— 偏移量隨管轄而變。真號碼要從文字前綴剖。
        "n_claim_blocks": len(claim_blocks),
        "has_english_title": bool(en_title),
        "title_keys": sorted(k for k in title_obj if not k.startswith("@")),
        "lang": {
            "title": _lang(en_title) if en_title else "none",
            "abstract": abstract["primary"],
            "claims": claims_lang["primary"],
        },
        "lang_detail": {"abstract": abstract, "claims": claims_lang},

        # 嚴格條件：請求項讀得到英文。claim scope 是 FTO 判斷的核心，
        # 這一項失敗就沒得補。
        "rule_analyzable": bool(claim_blocks) and claims_lang["primary"] == "en",

        # 寬鬆條件：標題或摘要有英文可比對。JP/CN 公開案的 abstract 是
        # [原文, 官方英譯]，drug / indication 關鍵字仍能命中 —— 並非全盲。
        # 公告(B)案通常兩者皆缺，那才是真正的盲區。
        "english_text_available": bool(en_title) or abstract["has_english"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 抓取
# ══════════════════════════════════════════════════════════════════════════════

def _params(pn: str, fields: str) -> list[tuple[str, str]]:
    return [
        ("patDB", PAT_DB),
        ("patAG", PAT_AG),
        ("patTY", PAT_TY),
        ("PN", pn),
        ("expFld", fields),
        ("expFmt", "json"),
        ("expQty", "30"),
    ]


def _row(pid: str, meta: dict, verdict: str, attempts: list[dict],
         **extra) -> dict:
    """組出一筆 JSONL 行。

    ⚠ 所有分支都必須經過這裡。原本 ok / empty / response_error / quota
      四個 return 各寫各的，只有 ok 帶 json_fixes 和 quality —— 47k 若有
      20% miss 就是 9,400 行 schema 不一致，每次都得 recompute 才統一。
      2026-08-25 的回歸測試就是被這個 gap 抓出來的（IL152571A）。
    """
    row = {
        "requested_id": pid,
        "verdict": verdict,
        "attempts": attempts,
        "json_fixes": [],
        "quality": [],
        "records": [],
        "cost": 0,
        "source_meta": meta,
        "fetched_at": _now(),
    }
    row.update(extra)
    return row


def fetch_one(pid: str, meta: dict, *, fields: str, pause: float,
              try_no_kind: bool) -> dict:
    """試各種格式直到命中。回傳一筆 JSONL 用的 dict。"""
    attempts: list[dict] = []
    cands = variants(pid, try_no_kind=try_no_kind)

    for pn in cands:
        # 錯誤分流：
        #   GpssResponseError（傳輸／解析）→ 記錄該筆，繼續跑下一個
        #   GpssFatal（驗證碼類）        → 不接，往上冒，整批停
        #                                  TIPO 會封 IP，絕不重試
        try:
            r = G.call(_params(pn, fields), pause=pause)
        except G.GpssResponseError as e:
            G.log(f"      ⚠ {pn}: {e}")
            return _row(pid, meta, "response_error", attempts,
                        error=G._redact(str(e)))

        attempts.append({"pn": pn, "verdict": r["verdict"],
                         "total_rec": r["total_rec"], "returned": r["returned"]})

        if r["verdict"] == "quota":
            return _row(pid, meta, "quota", attempts)

        if r["verdict"] == "ok" and r["returned"]:
            return _row(pid, meta, "ok", attempts,
                        matched_form=pn,
                        variant_rule=variant_rule(pid, pn),
                        ambiguous=r["returned"] > 1,
                        total_rec=r["total_rec"],
                        cost=r["returned"],
                        json_fixes=r.get("json_fixes") or [],
                        quality=[assess(rec) for rec in r["records"]],
                        records=r["records"])

        if r["verdict"] not in ("ok", "empty"):
            # truncate / length / unknown_field / other —— 不是「查無此號」，
            # 換格式再試沒有意義，直接記下來。
            return _row(pid, meta, r["verdict"], attempts,
                        message=r["message"])

    return _row(pid, meta, "empty", attempts)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def canary(fields: str, pause: float) -> bool:
    """參數自檢。未知欄位靜默丟棄，打錯不會報錯只會給錯的結果集。"""
    G.log(f"[canary] PN={CANARY_PN}")
    r = G.call(_params(CANARY_PN, fields), pause=pause)
    rec = r["records"][0] if r["records"] else {}
    has_claims = bool((rec.get("claims") or {}).get("claim"))
    has_title = bool((rec.get("patent-title") or {}).get("english-title"))
    ok = r["verdict"] == "ok" and r["returned"] == 1 and has_claims and has_title
    G.log(f"          verdict={r['verdict']} returned={r['returned']} "
          f"claims={'✓' if has_claims else '✗'} title={'✓' if has_title else '✗'} "
          f"→ {'✅ 參數正常' if ok else '❌ 參數可能有問題'}")
    return ok


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════

# summarise() 實際會讀的欄位。records / source_meta 不在其中，
# 而它們正是體積的來源。
_SUMMARY_FIELDS = ("requested_id", "matched_form", "variant_rule", "verdict",
                   "ambiguous", "total_rec", "cost", "quality", "json_fixes",
                   "error", "message")


def _slim(row: dict) -> dict:
    """丟掉 records / source_meta，只留 summary 用得到的欄位。

    attempts 只需要筆數（summarise 拿它算請求總數），所以壓成一個整數。
    47k 筆下記憶體從 ~1.5 GB 降到 ~30 MB。
    """
    out = {k: row[k] for k in _SUMMARY_FIELDS if k in row}
    out["_n_attempts"] = len(row.get("attempts", []))
    return out


def _ambiguity_cause(row: dict) -> str:
    """一號多筆的實際原因，從資料推斷而非假設。

    2026-08-25 實測到兩種，成因完全不同：

      era-collision  JP5725786B2 → JP5725786B2(2015, 眼科組成物)
                                 + JPS5725786B2(1982, 雷達)
                     GPSS 在 PN 比對時忽略 JP 的年號前綴，兩篇差 33 年、
                     毫無關係。這比 kind code 歧義嚴重 —— 是兩件不同的
                     專利被合併回傳，且其中一筆常是殘缺紀錄。

      kind-stripped  --try-no-kind 造成，同一件專利的不同公開階段。
    """
    docs = [q.get("doc_number") or "" for q in row.get("quality", [])]
    req = row.get("requested_id", "")
    if row.get("variant_rule") == "kind-code-stripped":
        return "kind-code-stripped"
    # 去掉年號前綴後相同 → 跨世代碰撞
    def _strip_era(s: str) -> str:
        return s[:2] + s[3:] if len(s) > 2 and s[2] in "HS" else s
    if len({_strip_era(d) for d in docs if d}) < len([d for d in docs if d]):
        return "era-collision（JP 年號前綴被忽略）"
    if any(d and d != req for d in docs):
        return f"doc-number 與請求號不同：{', '.join(d for d in docs if d)}"
    return "原因不明"


def summarise(rows: list[dict]) -> None:
    G.log("\n" + "═" * 72)
    G.log("  Summary")
    G.log("═" * 72)

    verdicts = Counter(r["verdict"] for r in rows)
    cost = sum(r.get("cost", 0) for r in rows)
    reqs = sum(r.get("_n_attempts", len(r.get("attempts", []))) for r in rows)

    G.log(f"  輸入 {len(rows)} 筆   請求 {reqs} 次   配額消耗 {cost} 筆 "
          f"（一組時段 10,000 → {cost / 100:.1f}%）")
    for v, n in verdicts.most_common():
        G.log(f"    {v:<10} {n}")

    amb = [r for r in rows if r.get("ambiguous")]
    if amb:
        G.log(f"\n  ⚠ 一號多筆 {len(amb)} 筆（需人工確認）：")
        for r in amb[:10]:
            G.log(f"      {r['requested_id']} → 回 {r['cost']} 筆"
                  f"  [{_ambiguity_cause(r)}]")
            for q in r.get("quality", []):
                G.log(f"          {q.get('doc_number')}  {q.get('database')}"
                      f"  欄位 {len(q.get('fields', []))}")

    G.log("\n  ── 哪一種號碼格式有效 " + "─" * 42)
    rules = Counter(r["variant_rule"] for r in rows if r["verdict"] == "ok")
    if not rules:
        G.log("      （沒有命中，無法判定）")
    for rule, n in rules.most_common():
        G.log(f"      {rule:<22} {n}")
        ex = [r for r in rows if r.get("variant_rule") == rule][:3]
        for r in ex:
            arrow = "" if rule == "as-is" else f" → {r['matched_form']}"
            G.log(f"          {r['requested_id']}{arrow}")

    G.log("\n  ── 管轄覆蓋率 " + "─" * 50)
    by_cc: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_cc[r["requested_id"][:2]][r["verdict"]] += 1
    for cc in sorted(by_cc):
        c = by_cc[cc]
        total = sum(c.values())
        hit = c.get("ok", 0)
        G.log(f"      {cc}  {hit:>3}/{total:<3} ({hit / total * 100:>5.1f}%)"
              f"{'  ⚠ 全 miss' if hit == 0 else ''}")

    # ── 輸入完整度：rule_based_analyze() 讀不讀得到內容 ────────────────
    #
    # ⚠ 計數單位是「紀錄」不是「輸入 ID」。一號多筆時（見 _ambiguity_cause）
    #   同一個 requested_id 會貢獻多筆紀錄，且它們的完整度可能不同 ——
    #   JP5725786B2 的真紀錄是「有請求項、日文」，年號碰撞來的幽靈紀錄是
    #   「無請求項、無英文」，分別落在不同桶。所以總數 ≥ 輸入數。
    #
    # 三個桶互斥且窮盡，依「英文關鍵字比對能拿到什麼」分：
    #   請求項可讀 → claim scope 判斷得了，這是唯一真正夠用的
    #   僅標題摘要 → JP/CN 公開案，abstract 是 [原文, 官方英譯]，
    #                drug / indication 關鍵字仍能命中，claim scope 不行
    #   完全無英文 → 公告(B)案居多，標題只有 native、摘要常缺
    qrows = [(r, q) for r in rows for q in r.get("quality", [])]
    if qrows:
        G.log(f"\n  ── 輸入完整度：{len(qrows)} 筆紀錄 " + "─" * 32)

        buckets: dict[str, list[tuple[str, dict]]] = {
            "usable": [], "partial": [], "blind": []}
        for r, q in qrows:
            key = ("usable" if q["rule_analyzable"]
                   else "partial" if q.get("english_text_available")
                   else "blind")
            buckets[key].append((r["requested_id"], q))

        def _why(q: dict) -> str:
            if not q["has_claims"]:
                return "無請求項"
            return f"請求項 {q['lang']['claims']}"

        G.log(f"      請求項可讀  {len(buckets['usable']):>5}"
              f"   ← claim scope 判斷得了")
        for label, note in (("partial", "有官方英譯，drug/indication 仍可比對"),
                            ("blind", "真正的盲區")):
            rows_b = buckets[label]
            name = "僅標題摘要" if label == "partial" else "完全無英文"
            G.log(f"      {name}  {len(rows_b):>5}   ← {note}")
            if rows_b:
                byw = Counter(_why(q) for _, q in rows_b)
                G.log(f"                     "
                      f"{', '.join(f'{w}×{n}' for w, n in byw.most_common())}")
                G.log(f"                     "
                      f"{', '.join(rid for rid, _ in rows_b[:10])}"
                      f"{' …' if len(rows_b) > 10 else ''}")

        if buckets["partial"] or buckets["blind"]:
            G.log("      ⚠ 後兩桶目前會被靜默評成 Low risk"
                  "（讀不到內容 ≠ 無風險）。見 task_O。")

    fixed = [r for r in rows if r.get("json_fixes")]
    if fixed:
        keys = Counter(f["missing_before"] for r in fixed for f in r["json_fixes"])
        G.log(f"\n  ── GPSS JSON 缺逗號修補 {len(fixed)} 筆 " + "─" * 30)
        for k, n in keys.most_common():
            G.log(f"      缺在 \"{k}\" 前  ×{n}")

    dead = [r["requested_id"] for r in rows if r["verdict"] == "empty"]
    if dead:
        G.log(f"\n  ── miss 清單（{len(dead)} 筆）" + "─" * 44)
        G.log("      " + ", ".join(dead[:40]) + (" …" if len(dead) > 40 else ""))
        G.log("      miss 不消耗配額。加 --try-no-kind --retry-misses 可再試一輪。")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="用專利號清單向 GPSS 撈紀錄，落 JSONL",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", type=Path, required=True,
                    help="專利號清單（每行一個，或 TSV）")
    ap.add_argument("--out", type=Path, default=Path("gpss_pn.jsonl"),
                    help="輸出 JSONL（預設 gpss_pn.jsonl）")
    ap.add_argument("--limit", type=int, default=0,
                    help="只跑前 N 筆（試水溫用）")
    ap.add_argument("--resume", action="store_true",
                    help="略過 --out 裡已有的 ID")
    ap.add_argument("--retry-misses", action="store_true",
                    help="搭配 --resume：miss 過的重試（miss 不耗配額）")
    ap.add_argument("--try-no-kind", action="store_true",
                    help="變體包含「去掉 kind code」。可能一號回多筆，預設關閉")
    ap.add_argument("--fields", default=EXP_FLD,
                    help="expFld（預設全集；配額計筆數不計欄位，拉滿免費）")
    ap.add_argument("--pause", type=float, default=1.0,
                    help="每次請求後的禮貌性間隔秒數（預設 1.0）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只印出會送的 URL（已 redact），不實際請求")
    ap.add_argument("--no-canary", action="store_true",
                    help="跳過參數自檢（省 1 筆配額）")
    ap.add_argument("--env-file", type=Path, default=None)
    args = ap.parse_args()

    G.load_user_code(args.env_file)

    if not args.ids.exists():
        sys.exit(f"找不到 {args.ids}")

    todo = read_ids(args.ids)
    G.log(f"\n讀入 {len(todo)} 個專利號（已去重）")

    if args.resume:
        hits, misses = load_done(args.out)
        skip = hits if args.retry_misses else (hits | misses)
        before = len(todo)
        todo = [(p, m) for p, m in todo if p not in skip]
        G.log(f"  --resume：既有 {len(hits)} hit / {len(misses)} miss，"
              f"略過 {before - len(todo)}，剩 {len(todo)}")

    if args.limit:
        todo = todo[: args.limit]
        G.log(f"  --limit {args.limit} → 本次跑 {len(todo)}")

    if not todo:
        G.log("沒有要跑的項目。")
        return

    if args.dry_run:
        G.log("\n[dry-run] 前 5 個 ID 的候選格式與 URL：")
        for pid, _ in todo[:5]:
            cands = variants(pid, try_no_kind=args.try_no_kind)
            G.log(f"\n  {pid}  候選 {cands}")
            G.log("    " + G._redact(G.build_url(_params(cands[0], args.fields))))
        G.log(f"\n  預估：命中 ≈ {len(todo)} 筆配額（miss 為 0），"
              f"上限請求數 {sum(len(variants(p, try_no_kind=args.try_no_kind)) for p, _ in todo)}")
        return

    if not args.no_canary and not canary(args.fields, args.pause):
        sys.exit("canary 失敗——先確認參數，別讓整批跑在錯的設定上。")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # ⚠ 只保留 summarise() 需要的欄位。原本 rows.append(row) 會連完整的
    #   records 一起留著，47k × ~5 KB 在 Python 物件裡約 1-2 GB。
    #   records 已經逐筆寫進 JSONL 了，記憶體裡不需要第二份。
    rows: list[dict] = []
    cost = 0

    G.log(f"\n開始（輸出 → {args.out}）")
    try:
        with args.out.open("a", encoding="utf-8") as fh:
            for i, (pid, meta) in enumerate(todo, 1):
                row = fetch_one(pid, meta, fields=args.fields, pause=args.pause,
                                try_no_kind=args.try_no_kind)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()          # 逐筆落地，中斷不掉資料
                rows.append(_slim(row))
                cost += row.get("cost", 0)

                mark = {"ok": "✓", "empty": "·", "quota": "⛔"}.get(row["verdict"], "⚠")
                extra = ""
                if row["verdict"] == "ok" and row["variant_rule"] != "as-is":
                    extra = f"  [{row['variant_rule']} → {row['matched_form']}]"
                if row.get("ambiguous"):
                    extra += f"  ⚠ {row['cost']} 筆"
                G.log(f"  {mark} {i:>4}/{len(todo)}  {pid:<18}{extra}")

                if row["verdict"] == "quota":
                    G.log("\n  ⛔ 配額耗盡，停止。--resume 可續跑。")
                    break

    except KeyboardInterrupt:
        G.log("\n  [中斷] 已寫入的資料完整，--resume 可續跑。")
    except G.GpssFatal as e:
        G.log(f"\n  [FATAL] {e}")
        G.log("  驗證碼類錯誤不重試（TIPO 會封 IP）。")

    if rows:
        summarise(rows)
    G.log(f"\n[jsonl] {args.out}  （本次 +{len(rows)} 行，配額 {cost} 筆）")


if __name__ == "__main__":
    main()
