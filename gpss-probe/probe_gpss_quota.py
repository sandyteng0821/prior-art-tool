#!/usr/bin/env python3
"""
probe_gpss_quota.py — GPSS API 配額與限制探測

放置位置：scratch/probe_gpss_quota.py（gitignored）
Probe-before-code artifact。不寫 DB、不 import modules/、不碰 cache。

四個階段，預設只跑 0 和 1（便宜）；2 和 3 要顯式開啟（會燒配額 / 長時間）。

  Phase 0  preflight    — 驗證 userCode、CL 欄位、expQty 下限、回傳語意
  Phase 1  limits       — 檢索式長度上限、布林欄位數上限、切截/字距錯誤
  Phase 2  burn         — 燒配額直到 Over download quantity，量出「時段」總額
  Phase 3  watch        — 輪詢偵測配額恢復，量出「時段」長度

用法：
    python probe_gpss_quota.py                      # Phase 0 + 1（安全）
    python probe_gpss_quota.py --burn               # 加跑 Phase 2（會用掉配額）
    python probe_gpss_quota.py --burn --watch 26    # 再加 Phase 3，最多守 26 小時

相依：requests, python-dotenv
    pip install requests python-dotenv

.env（自動由 cwd 或本檔位置往上層尋找，也可用 --env-file 指定）：
    GPSS_USER_CODE=你的驗證碼

    優先序：--env-file > 環境變數 > script 同層 .env > cwd 往上（load_dotenv 預設不覆蓋 os.environ，
    所以臨時測試可用 `GPSS_USER_CODE=xxx python probe_gpss_quota.py` 覆寫）

安全性：
    userCode 只出現在實際送出的 request 中。所有 print / artifact / exception
    訊息都經過 _redact()。若你要改這支 script，任何新增的輸出路徑都必須套。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("需要 requests：pip install requests")

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:
    sys.exit("需要 python-dotenv：pip install python-dotenv")


# ══════════════════════════════════════════════════════════════════════════════
# 常數
# ══════════════════════════════════════════════════════════════════════════════

API_URL = "https://tiponet.tipo.gov.tw/gpss1/gpsskmc/gpss_api"

# 官方文件的回傳訊息（肆、回傳結果）。全部小寫比對——文件表格與截圖 JSON
# 的大小寫不一致（"No record found" vs "no record found"），不能區分大小寫。
MSG_FATAL = {
    "usercode not exist": "驗證碼無效",
    "usercode expired": "驗證碼過期（365 日效期，需展期）",
    "ip blocked": "IP 已被封鎖（無效驗證碼連線次數超限）",
    "no search command": "未包含檢索條件——這是 script 的 bug，不是 API 問題",
}
MSG_QUOTA = "over download quantity"
MSG_EMPTY = "no record found"
MSG_SKIP_OVER = "expskip too much"
MSG_TRUNCATE = "cannot use truncated and kerning"
MSG_LENGTH = "exceeded search condition length"
MSG_UNKNOWN_FIELD = "unknow field"          # sic — 官方文件就是拼成 "unknow"

# Phase 0 金絲雀：claim 5 字面寫 pemirolast 治 IPF，abstract 只提
# airway hyperresponsiveness。命中即證明 CL 欄位確實被索引。
CANARY_DB = "USA,USB"
CANARY_DRUG = "pemirolast"
CANARY_INDICATION = "idiopathic pulmonary fibrosis"
CANARY_EXPECT_PN = "US09415051B1"


class GpssFatal(RuntimeError):
    """不可重試的錯誤。驗證碼類錯誤絕對不能進 retry loop——
    會把 IP 打進黑名單（見 MSG_FATAL['ip blocked']）。"""


class GpssResponseError(GpssFatal):
    """傳輸／解析失敗 —— 與驗證碼錯誤不同，可以跳過該筆繼續跑。

    刻意繼承 GpssFatal：既有 script 的 `except GpssFatal` 仍然接得到，
    行為完全不變。新 code 想分流時再明確接這個子類。

    驗證碼類錯誤（MSG_FATAL）維持丟 GpssFatal 本身 —— TIPO 會封 IP，
    永遠 fail-fast。
    """


# ══════════════════════════════════════════════════════════════════════════════
# userCode 載入與遮蔽
# ══════════════════════════════════════════════════════════════════════════════

_USER_CODE: str | None = None


def load_user_code(env_file: Path | None = None) -> str:
    """讀 GPSS_USER_CODE。

    尋找順序（先找到含該 key 的就停）：
      1. --env-file 明確指定      ← 顯式優先於環境變數，避免 shell 裡
                                     export 過舊的 code 而靜默用錯組
      2. 既有環境變數 GPSS_USER_CODE
      3. 與本 script 同層的 .env      ← standalone 用法
      4. 從 cwd 往上層找的 .env       ← 放在專案 scratch/ 的用法

    刻意不用裸的 find_dotenv()：它靠 stack frame 推算位置，在 `python -c`
    或 REPL 下會被判定為互動模式而改用 cwd，導致 script 同層的 .env 找不到。
    第 3 條用 __file__ 直接算，行為可預測。
    """
    global _USER_CODE

    def _fp(c: str) -> str:
        """不可逆指紋——手上有多組 code 時用來確認載到哪一組。"""
        return hashlib.sha256(c.encode()).hexdigest()[:8]

    # 1. --env-file 顯式指定，優先於環境變數
    if env_file:
        path = Path(env_file).expanduser().resolve()
        if not path.is_file():
            sys.exit(f"--env-file 指定的檔案不存在：{path}")
        load_dotenv(path, override=True)
        code = os.environ.get("GPSS_USER_CODE", "").strip()
        if not code:
            sys.exit(f"{path} 裡沒有 GPSS_USER_CODE")
        _USER_CODE = code
        log(f"[env] userCode 載入自 {path}（指紋 {_fp(code)}）")
        return code

    # 2. 環境變數
    code = os.environ.get("GPSS_USER_CODE", "").strip()
    if code:
        _USER_CODE = code
        log(f"[env] userCode 來自環境變數（指紋 {_fp(code)}）")
        return code

    # 3/4. 自動尋找 .env
    candidates: list[Path] = [Path(__file__).resolve().parent / ".env"]
    found = find_dotenv(usecwd=True)
    if found:
        candidates.append(Path(found).resolve())

    # 去重但保序
    seen: set[Path] = set()
    ordered = [p for p in candidates if not (p in seen or seen.add(p))]

    tried: list[str] = []
    loaded_from: str | None = None
    for path in ordered:
        if not path.is_file():
            continue
        tried.append(str(path))
        load_dotenv(path)
        code = os.environ.get("GPSS_USER_CODE", "").strip()
        if code:
            loaded_from = str(path)
            break

    if not code:
        detail = "\n".join(f"    - {p}（無此 key）" for p in tried) or "    （沒有找到任何 .env）"
        sys.exit(
            "找不到 GPSS_USER_CODE。\n"
            f"  已檢查：\n{detail}\n"
            "  建立 .env 並寫入：GPSS_USER_CODE=你的驗證碼\n"
            "  （記得把 .env 加進 .gitignore）\n"
            "  或用 --env-file 指定路徑。"
        )

    _USER_CODE = code
    log(f"[env] .env 載入自 {loaded_from}（指紋 {_fp(code)}）")
    return code


def _redact(text: str) -> str:
    """把 userCode 從任何字串中抹掉。所有輸出路徑都必須經過這裡。"""
    if not _USER_CODE:
        return text
    out = text.replace(_USER_CODE, "<USERCODE>")
    out = out.replace(quote(_USER_CODE), "<USERCODE>")
    # 保險：即使變數沒設，也不讓 userCode= 後面的東西外流
    return re.sub(r"(userCode=)[^&\s]+", r"\1<USERCODE>", out)


def log(msg: str) -> None:
    print(_redact(msg), flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP
# ══════════════════════════════════════════════════════════════════════════════

def build_url(params: list[tuple[str, str]]) -> str:
    """手動組 query string。

    不能用 urlencode：
      - 參數名含 '/'（TI/AB/CL），urlencode 會編成 %2F
      - 參數名可能有 '+' / '-' 前綴（OR / NOT），urlencode 會破壞語意
    """
    parts = [f"userCode={quote(_USER_CODE, safe='')}"]
    for key, val in params:
        # key 原樣保留 '/'（TI/AB/CL）、'+'（OR）、'-'（NOT）
        # value 保留 ',' 和 ':' —— 兩者是 RFC 3986 sub-delim，且文件範例
        # 就是原樣使用（patDB=TWA,TWB / ID=2020:2021 / AD=20191107:）。
        # 編成 %2C / %3A 理論上等價，但沒必要偏離官方格式。
        parts.append(f"{key}={quote(str(val), safe=',:')}")
    return f"{API_URL}?" + "&".join(parts)


def loads_lenient(raw: str, *, max_fixes: int = 64) -> tuple[dict, list[dict]]:
    """解析 GPSS 回應，修補已知的伺服器端序列化 bug。

    已知案例（2026-08-25，JP2012224629A，expFld 含 FT）：
        "classifications-national":{ ... ]
        "f-term":[                          ← `]` 後缺逗號
    GPSS 端序列化 bug，不是傳輸損壞。US/WO/AU 案沒有 f-term 欄位不會觸發，
    所以先前只用 expFld=PN 的探針從未撞到。

    安全前提（已驗證）：GPSS 回應不含裸控制字元，
        sum(1 for c in raw if ord(c) < 0x20 and c not in "\r\n\t") == 0
    因此每個真換行都落在 token 之間而非 JSON 字串內部，
    JSONDecodeError.pos 指向的位置可以安全插入逗號而不會改到內容。

    只修 `Expecting ',' delimiter` 且下一字元是 `"`、前一非空字元是
    `]` `}` `"` 的情形。其他 JSON 錯誤一律往上拋 —— 這是修補不是猜測。

    回傳 (data, fixes)。fixes 記錄修補點，供日後追蹤 GPSS 是否修好。
    """
    text, fixes = raw, []
    for _ in range(max_fixes):
        try:
            return json.loads(text), fixes
        except json.JSONDecodeError as e:
            if not e.msg.startswith("Expecting ',' delimiter"):
                raise GpssResponseError(
                    f"JSON 解析失敗（非已知的缺逗號型）：{e}") from None
            if e.pos >= len(text) or text[e.pos] != '"':
                raise GpssResponseError(
                    f"JSON 解析失敗（缺逗號位置不是 key 開頭）：{e}") from None
            prev = text[:e.pos].rstrip()
            if not prev or prev[-1] not in ']}"':
                raise GpssResponseError(
                    f"JSON 解析失敗（缺逗號前不是值結尾）：{e}") from None
            k = re.match(r'"([^"]{0,40})"', text[e.pos:])
            fixes.append({"pos": e.pos, "after": prev[-1],
                          "missing_before": k.group(1) if k else "?"})
            text = text[:e.pos] + "," + text[e.pos:]
    raise GpssResponseError(f"缺逗號修補超過 {max_fixes} 次仍無法解析")


def call(params: list[tuple[str, str]], *, timeout: int = 60, pause: float = 1.0) -> dict:
    """打一次 API，回傳解析後的結果 dict。

    回傳欄位：
      verdict     — ok / empty / quota / skip_over / truncate / length / unknown_field / other
      message     — API 的 message 欄位（原文）
      total_rec   — API 宣稱的總筆數（int，可能為 None）
      qty_rec     — API 的 qty-rec（注意：這是 expQty 的回音，不是實際回傳筆數）
      returned    — 實際 patentcontent 陣列長度 ← 配額計算要用這個
      records     — patentcontent list
      elapsed_s   — 耗時
    """
    url = build_url(params)
    t0 = time.time()
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise GpssResponseError(f"HTTP 失敗：{_redact(str(e))}") from None
    elapsed = time.time() - t0

    if pause:
        time.sleep(pause)

    if resp.status_code != 200:
        raise GpssResponseError(f"HTTP {resp.status_code}：{_redact(resp.text[:300])}")

    # 強制 utf-8：Content-Type 有宣告 charset=utf-8，但不倚賴 requests 猜測。
    # errors="replace" 與 requests 的 resp.text 內部行為等價（非放寬），
    # 但 requests 不會告知發生了替換 —— 非法位元組會變成 U+FFFD 混進請求項
    # 文字裡。JP/CN 全文量大，這裡明確 log 出來而不是靜默吞掉。
    raw = resp.content.decode("utf-8", errors="replace")
    n_bad = raw.count("\ufffd")
    if n_bad:
        log(f"    ⚠ utf-8 解碼出現 {n_bad} 個替換字元 U+FFFD —— 內容可能受損")
    payload, json_fixes = loads_lenient(raw)   # 失敗會丟 GpssResponseError
    if json_fixes:
        log(f"    ⚠ JSON 修補 {len(json_fixes)} 處："
            f"{', '.join(f['missing_before'] for f in json_fixes)}")

    api = payload.get("gpss-API", {}) or {}
    message = (api.get("message") or "").strip()
    m = message.lower()

    # ── fail-fast：驗證碼類錯誤絕不重試 ──────────────────────────────────
    for needle, why in MSG_FATAL.items():
        if needle in m:
            raise GpssFatal(f"{message} — {why}")

    # patentcontent 正規化：單筆時可能不是 list（防禦性）
    patent = api.get("patent") or {}
    content = patent.get("patentcontent") or []
    if isinstance(content, dict):
        content = [content]

    def _int(v):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None

    if MSG_QUOTA in m:
        verdict = "quota"
    elif MSG_EMPTY in m:
        verdict = "empty"
    elif MSG_SKIP_OVER in m:
        verdict = "skip_over"
    elif MSG_TRUNCATE in m:
        verdict = "truncate"
    elif MSG_LENGTH in m:
        verdict = "length"
    elif MSG_UNKNOWN_FIELD in m:
        verdict = "unknown_field"
    elif message:
        verdict = "other"
    else:
        verdict = "ok"

    return {
        "verdict": verdict,
        "json_fixes": json_fixes,
        "message": message,
        "status": api.get("status"),
        "total_rec": _int(api.get("total-rec")),
        "qty_rec": _int(api.get("qty-rec")),
        "returned": len(content),          # ← 配額用這個，不是 qty_rec
        "records": content,
        "elapsed_s": round(elapsed, 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 金絲雀：CL 索引驗證 + parser 地雷分析（單次呼叫）
# ══════════════════════════════════════════════════════════════════════════════

def _analyse_claims(record: dict) -> dict:
    """拆解 claims 結構，回報已知的 parser 地雷。

    三個地雷（來自先前的 sample 分析）：
      1. claim-text 型別不穩：str | list[str]
      2. @num 不是請求項號——前言（"What is claimed is:"）會佔掉 @num=1
      3. (canceled) 區段會吃掉開頭號碼，真正的 claim 1 可能從 1664 開始
    """
    claims = ((record.get("claims") or {}).get("claim")) or []
    if isinstance(claims, dict):
        claims = [claims]

    entries, types = [], {"str": 0, "list": 0, "other": 0}
    for c in claims:
        raw = c.get("claim-text")
        if isinstance(raw, str):
            types["str"] += 1
            head, segs = raw, 1
        elif isinstance(raw, list):
            types["list"] += 1
            head, segs = (raw[0] if raw else ""), len(raw)
        else:
            types["other"] += 1
            head, segs = "", 0

        head = (head or "").strip()
        m = re.match(r"^\s*(\d+)\s*[.．]", head)
        canceled = "(canceled)" in head.lower() or "（canceled）" in head.lower()
        entries.append({
            "at_num": c.get("@num"),
            "real_num": int(m.group(1)) if m else None,
            "segments": segs,
            "canceled": canceled,
            "head": head[:80],
        })

    numbered = [e for e in entries if e["real_num"] is not None and not e["canceled"]]
    first = numbered[0] if numbered else None

    return {
        "entry_count": len(entries),
        "claim_text_types": types,
        "first_entry_is_preamble": bool(entries and entries[0]["real_num"] is None),
        "first_real_claim": first,
        "at_num_offset": (
            (first["at_num"], first["real_num"]) if first else None
        ),
        "has_canceled_block": any(e["canceled"] for e in entries),
        "entries": entries,
    }


def canary_check(pause: float, verbose: bool = False) -> dict:
    """單次呼叫：drug × indication over TI/AB/CL，驗證 CL 是否被索引。

    US09415051B1 的 IPF 只出現在 claim 5，title 是 "Use of pemirolast"，
    abstract 只提 airway hyperresponsiveness。兩個詞同時成立的唯一可能
    來源就是 claims 被索引。命中 = CL 索引成立。
    """
    log(f"\n[canary] TI/AB/CL = {CANARY_DRUG} and {CANARY_INDICATION}")
    log(f"         patDB={CANARY_DB}  期望命中 {CANARY_EXPECT_PN}")

    r = call([
        ("patDB", CANARY_DB),
        ("patAG", "A,B"),
        ("patTY", "I"),
        ("TI/AB/CL", f"{CANARY_DRUG} and {CANARY_INDICATION}"),
        # 拉滿欄位——一次呼叫就把 schema 全貌看完
        ("expFld", "PN,AN,ID,AD,TI,PA,IN,PR,AB,IC,CS,UC,CI,CL"),
        ("expFmt", "json"),
        ("expQty", "30"),
    ], pause=pause)

    pns = [
        (rec.get("publication-reference") or {}).get("doc-number")
        for rec in r["records"]
    ]
    hit = CANARY_EXPECT_PN in pns

    log(f"\n  verdict   = {r['verdict']}")
    log(f"  total-rec = {r['total_rec']}   qty-rec = {r['qty_rec']}   實際筆數 = {r['returned']}")
    log(f"  PN        = {pns}")
    log(f"  結論      = {'✅ CL 索引成立' if hit else '❌ 未命中——CL 索引假設崩塌'}")
    if not hit and r["returned"]:
        log("     （有回傳但不含期望的 PN，檢查號碼格式：零填充？kind code？）")

    out = {
        "hit": hit,
        "verdict": r["verdict"],
        "total_rec": r["total_rec"],
        "qty_rec": r["qty_rec"],
        "returned": r["returned"],
        "pns": pns,
    }

    target = next(
        (rec for rec in r["records"]
         if (rec.get("publication-reference") or {}).get("doc-number") == CANARY_EXPECT_PN),
        r["records"][0] if r["records"] else None,
    )
    if not target:
        return out

    # ── 欄位到齊情況 ──────────────────────────────────────────────────────
    present = {k: bool(target.get(k)) for k in (
        "publication-reference", "application-reference", "parties",
        "priority-claims", "classifications-ipc", "classifications-cpc",
        "classifications-national", "patent-title", "abstract",
        "references-cited", "claims",
    )}
    log("\n  欄位到齊：")
    for k, v in present.items():
        log(f"    {'✓' if v else '·'} {k}")
    out["fields_present"] = present

    # ── parser 地雷 ───────────────────────────────────────────────────────
    ca = _analyse_claims(target)
    out["claims_analysis"] = ca
    log("\n  claims 結構（parser 地雷）：")
    log(f"    claim 陣列長度   : {ca['entry_count']}")
    log(f"    claim-text 型別   : {ca['claim_text_types']}"
        f"{'  ← str/list 混用，parser 兩種都要吃' if ca['claim_text_types']['list'] and ca['claim_text_types']['str'] else ''}")
    log(f"    首筆是前言        : {ca['first_entry_is_preamble']}"
        f"{'  ← @num 會偏移' if ca['first_entry_is_preamble'] else ''}")
    log(f"    有 (canceled) 區段: {ca['has_canceled_block']}")
    if ca["at_num_offset"]:
        at, real = ca["at_num_offset"]
        log(f"    首個真請求項      : @num={at} → 實際 claim {real}"
            f"{'  ← 偏移 ' + str(int(at) - real) if str(at).isdigit() and int(at) != real else ''}")

    if verbose:
        log("\n  claim 逐筆：")
        for e in ca["entries"]:
            flag = " [canceled]" if e["canceled"] else ""
            log(f"    @num={str(e['at_num']):>4}  real={str(e['real_num']):>5}  "
                f"segs={e['segments']}{flag}  {e['head']!r}")

        title = target.get("patent-title") or {}
        abstract = target.get("abstract") or {}
        log(f"\n  title(original) : {title.get('title')!r}")
        log(f"  title(english)  : {title.get('english-title')!r}")
        ap = abstract.get("p")
        log(f"  abstract 型別   : {type(ap).__name__}"
            f"{'  ← 同樣是 str|list' if isinstance(ap, list) else ''}")
        log(f"  abstract        : {str(ap)[:200]!r}")

        out["raw_record"] = target

    return out


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 — Preflight
# ══════════════════════════════════════════════════════════════════════════════

def phase0_preflight(pause: float) -> dict:
    log("\n" + "═" * 72)
    log("  Phase 0 — Preflight")
    log("═" * 72)
    out: dict = {"checks": []}

    def record(name, **kw):
        out["checks"].append({"name": name, **kw})

    # 0a. 金絲雀：CL 欄位是否被索引
    log("\n[0a] CL 索引金絲雀")
    can = canary_check(pause, verbose=False)
    out["canary"] = can
    record("cl_indexed", hit=can["hit"], total_rec=can["total_rec"], pns=can["pns"])
    if not can["hit"]:
        log("     ⚠ 未命中代表 CL 索引假設崩塌，後面所有規劃都要重估。")

    # 0b. qty-rec 語意：是 expQty 回音還是實際筆數？
    log("\n[0b] qty-rec 語意（文件範例暗示它是 expQty 回音）")
    log(f"     expQty=30 → qty-rec={can['qty_rec']}, 實際 patentcontent={can['returned']}")
    echo = (can["qty_rec"] == 30 and can["returned"] != can["qty_rec"])
    log(f"     結論：qty-rec {'是 expQty 回音（配額必須數陣列長度）' if echo else '疑似等於實際筆數——需再驗'}")
    record("qty_rec_is_echo", echo=echo, qty_rec=can["qty_rec"], returned=can["returned"])

    # 0c. expQty 下限：UI 說最少 30，API 直呼是否接受更小？
    #     這攸關 Phase 3 輪詢成本。
    log("\n[0c] expQty 下限（UI 標示最少 30，API 未必）")
    for q in ("1", "5"):
        r2 = call([
            ("patDB", CANARY_DB),
            ("patAG", "A,B"),
            ("TI/AB/CL", CANARY_DRUG),
            ("expFld", "PN"),
            ("expFmt", "json"),
            ("expQty", q),
        ], pause=pause)
        log(f"     expQty={q:>2} → verdict={r2['verdict']}  qty-rec={r2['qty_rec']}  returned={r2['returned']}")
        record("expqty_floor", requested=int(q), returned=r2["returned"], qty_rec=r2["qty_rec"])

    # 0d. 零結果查詢的行為
    log("\n[0d] 零結果查詢的行為")
    r3 = call([
        ("patDB", CANARY_DB),
        ("TI/AB/CL", "zzzznonexistentcompoundzzzz"),
        ("expFld", "PN"),
        ("expFmt", "json"),
        ("expQty", "30"),
    ], pause=pause)
    log(f"     verdict={r3['verdict']}  message={r3['message']!r}  returned={r3['returned']}")
    record("zero_result", verdict=r3["verdict"], message=r3["message"])

    # 0e. ★ 最高價值：expSkip 超過總筆數時，total-rec 還在嗎？
    #     若在 → 可用 0 筆下載換取命中數。expQty 下限是 30，所以
    #     「只要計數」的稀疏矩陣掃描成本會從 30 筆/cell 降到 0 筆/cell。
    #     890,500 個 cell 的排程完全取決於這題。
    log("\n[0e] ★ 能否 0 筆下載取得命中數（expSkip 溢位）")
    r4 = call([
        ("patDB", CANARY_DB),
        ("TI/AB/CL", CANARY_DRUG),
        ("expFld", "PN"),
        ("expFmt", "json"),
        ("expQty", "30"),
        ("expSkip", "9999999"),
    ], pause=pause)
    free_count = (r4["total_rec"] is not None and r4["returned"] == 0)
    log(f"     verdict={r4['verdict']}  message={r4['message']!r}")
    log(f"     total-rec={r4['total_rec']}   實際回傳筆數={r4['returned']}")
    log(f"     結論：{'✅ 可以——計數不耗配額，稀疏掃描成本大降' if free_count else '❌ 不行——計數也得付 30 筆/cell'}")
    record("free_count_via_skip", possible=free_count,
           total_rec=r4["total_rec"], returned=r4["returned"],
           verdict=r4["verdict"], message=r4["message"])

    # 0f. 未知欄位：報錯還是靜默丟棄？
    #     %2BAB 回的是 No record found 而不是 unknow field，暗示是靜默丟棄。
    #     若成立，欄位名打錯不會噴錯，只會安靜給錯的結果集——這會讓
    #     1b/1c 的所有「無錯誤」結果都無法採信。
    log("\n[0f] 未知欄位：報錯 or 靜默丟棄？")
    base = call([
        ("patDB", CANARY_DB), ("AB", CANARY_DRUG),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], pause=pause)
    with_bogus = call([
        ("patDB", CANARY_DB), ("AB", CANARY_DRUG),
        ("ZZZZ", "whatever"),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], pause=pause)
    silent = (with_bogus["verdict"] == base["verdict"]
              and with_bogus["total_rec"] == base["total_rec"])
    log(f"     基準（無 bogus）: verdict={base['verdict']}  total-rec={base['total_rec']}")
    log(f"     加 ZZZZ 欄位   : verdict={with_bogus['verdict']}  total-rec={with_bogus['total_rec']}  message={with_bogus['message']!r}")
    log(f"     結論：{'⚠ 靜默丟棄——欄位名打錯不會報錯' if silent else '✅ 會報錯或改變結果'}")
    record("unknown_field_silent", silent=silent,
           base_total=base["total_rec"], with_bogus_total=with_bogus["total_rec"],
           message=with_bogus["message"])

    return out


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — 語法與長度上限（幾乎不耗配額：全部設計成 0 結果）
# ══════════════════════════════════════════════════════════════════════════════

def phase1_limits(pause: float) -> dict:
    log("\n" + "═" * 72)
    log("  Phase 1 — 語法與長度上限")
    log("═" * 72)
    out: dict = {}

    # 1a. 切截 / 字距 —— 確認錯誤訊息，順便確認我們的判別邏輯對得上
    log("\n[1a] 切截運算（預期 Cannot use truncated and kerning）")
    r = call([
        ("patDB", CANARY_DB),
        ("TI/AB/CL", "pemirolast*"),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], pause=pause)
    log(f"     verdict={r['verdict']}  message={r['message']!r}")
    out["truncation"] = {"verdict": r["verdict"], "message": r["message"]}

    # 1b. 布林欄位數上限 —— 用「會歸零的 NOT」當判別器
    #     舊版全填無意義 token，欄位被忽略與被接受都回 No record found，
    #     測不出東西。新設計：
    #       欄位 1      : AB=pemirolast            → 有命中（基準 N 筆）
    #       欄位 2..N-1 : -TI=zzzz（NOT 不存在的詞）→ 不排除任何東西，維持 N
    #       欄位 N      : -AB=pemirolast           → 應排除全部，總數歸 0
    #     總數歸 0 = 第 N 欄被採用；仍是 N = 第 N 欄被靜默丟棄。
    log("\n[1b] 布林欄位數上限（用 NOT 判別，可辨識靜默丟棄）")
    base = call([
        ("patDB", CANARY_DB), ("AB", CANARY_DRUG),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], pause=pause)
    baseline = base["total_rec"]
    log(f"     基準：AB={CANARY_DRUG} → total-rec={baseline}")

    field_probe = []
    if not baseline:
        log("     ⚠ 基準為 0，無法判別。改用命中數更高的詞再測。")
    else:
        for n in range(2, 9):                      # 總欄位數 2..8
            params = [("patDB", CANARY_DB), ("AB", CANARY_DRUG)]
            params += [("-TI", f"zzzz{i}") for i in range(n - 2)]   # 中間的無害 NOT
            params += [("-AB", CANARY_DRUG)]                        # 最後一欄應歸零
            params += [("expFld", "PN"), ("expFmt", "json"), ("expQty", "30")]
            try:
                rr = call(params, pause=pause)
                honored = (rr["total_rec"] in (0, None)) or rr["verdict"] == "empty"
                log(f"     {n} 欄 → total-rec={rr['total_rec']}  verdict={rr['verdict']}  "
                    f"{'✅ 末欄生效' if honored else '⚠ 末欄被丟棄'}  {rr['message']!r}")
                field_probe.append({
                    "fields": n, "total_rec": rr["total_rec"],
                    "verdict": rr["verdict"], "message": rr["message"],
                    "last_field_honored": honored,
                })
                if not honored:
                    log(f"     ↑ 上限落在 {n - 1} 欄")
                    break
            except GpssFatal as e:
                log(f"     {n} 欄 → FATAL: {e}")
                field_probe.append({"fields": n, "fatal": str(e)})
                break
    out["boolean_fields"] = {"baseline_total_rec": baseline, "probe": field_probe}

    # 1c. 檢索條件長度上限 —— 二分逼近，並分辨單欄位 vs 全部加總
    log("\n[1c] 檢索條件長度上限（二分逼近）")

    def _expr(chars: int) -> str:
        """組出接近指定長度、且保證 0 筆命中的 OR 運算式。"""
        unit = "zzq0000 or "
        n = max(1, chars // len(unit))
        return (unit * n)[:-4]      # 去掉尾巴的 ' or '

    def _ok_at(chars: int) -> tuple[bool, str]:
        rr = call([
            ("patDB", CANARY_DB), ("TI/AB/CL", _expr(chars)),
            ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
        ], pause=pause)
        return (rr["verdict"] != "length"), rr["verdict"]

    lo, hi = 1096, 2196          # 上一輪已知：1096 過、2196 爆
    trace = []
    while hi - lo > 64:
        mid = (lo + hi) // 2
        ok, verdict = _ok_at(mid)
        trace.append({"chars": mid, "ok": ok, "verdict": verdict})
        log(f"     {mid:>5} chars → {'通過' if ok else '超限'}")
        if ok:
            lo = mid
        else:
            hi = mid
    log(f"     單欄位長度上限：{lo} ~ {hi} chars 之間")
    out["condition_length"] = {"single_field": {"lo": lo, "hi": hi, "trace": trace}}

    # 1c-2. 上限是「單欄位」還是「全部條件加總」？
    #       若是單欄位 → 拆成多欄位就能繞過長 alias 串。
    log("\n[1c-2] 上限屬性：單欄位 vs 全部加總")
    half = lo // 2
    rr = call([
        ("patDB", CANARY_DB),
        ("TI/AB/CL", _expr(half)),
        ("+AB", _expr(half)),
        ("+CL", _expr(half)),          # 三欄各 half → 加總約 1.5×lo
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], pause=pause)
    per_field = (rr["verdict"] != "length")
    log(f"     三欄各 {half} chars（加總 ~{half*3}）→ verdict={rr['verdict']}")
    log(f"     結論：{'✅ 上限是單欄位——拆欄位可繞過' if per_field else '❌ 上限是全部加總——拆欄位沒用'}")
    out["condition_length"]["per_field_limit"] = per_field

    # 1d. '+' 前綴是否被當成空白（URL 語意風險）
    #     文件寫 &+AB=...，但標準 form decoding 會把 '+' 解成空白。
    log("\n[1d] '+' 前綴 vs %2B（文件寫 &+AB=，但 '+' 在 query string 通常解為空白）")
    for label, key in (("literal +", "+AB"), ("percent-encoded", "%2BAB")):
        rr = call([
            ("patDB", CANARY_DB),
            ("TI", "zzzznonexistent"),
            (key, CANARY_DRUG),
            ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
        ], pause=pause)
        log(f"     {label:>16} → verdict={rr['verdict']}  total-rec={rr['total_rec']}  message={rr['message']!r}")
        out.setdefault("or_prefix", []).append({
            "form": label, "verdict": rr["verdict"],
            "total_rec": rr["total_rec"], "message": rr["message"],
        })
    log("     判讀：OR 生效的話 total-rec 應該 > 0（TI 那條必定 0 筆）")

    return out


# ══════════════════════════════════════════════════════════════════════════════
# 配額狀態探測 —— 找出 0 筆成本的探針
# ══════════════════════════════════════════════════════════════════════════════

# Phase 3 可用的探針種類
PROBE_KINDS = {
    # name: (params 產生器, 正常狀態下預期回傳筆數)
    "zero_result": (lambda: [
        ("patDB", CANARY_DB),
        ("TI/AB/CL", "zzzznonexistentcompoundzzzz"),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], 0),
    "skip_overflow": (lambda: [
        ("patDB", CANARY_DB),
        ("TI/AB/CL", CANARY_DRUG),
        ("expFld", "PN"), ("expFmt", "json"),
        ("expQty", "30"), ("expSkip", "9999999"),
    ], 0),
    "canary_1rec": (lambda: [
        ("patDB", CANARY_DB), ("patAG", "A,B"), ("patTY", "I"),
        ("TI/AB/CL", f"{CANARY_DRUG} and {CANARY_INDICATION}"),
        ("expFld", "PN"), ("expFmt", "json"), ("expQty", "30"),
    ], 1),
}


def quota_status(pause: float) -> dict:
    """探測目前配額狀態，並挑出成本最低的輪詢探針。

    在配額耗盡時跑最有價值：可以看出哪一種請求會回 Over download quantity。
    若 0 筆的請求（zero_result / skip_overflow）在耗盡時也會回 quota，
    那它就是免費探針——Phase 3 輪詢可以任意頻率，不吃配額。
    """
    log("\n" + "═" * 72)
    log("  配額狀態探測")
    log("═" * 72)

    results = {}
    for name, (make_params, expected) in PROBE_KINDS.items():
        r = call(make_params(), pause=pause)
        results[name] = {
            "verdict": r["verdict"], "message": r["message"],
            "returned": r["returned"], "total_rec": r["total_rec"],
            "cost": r["returned"], "expected_when_healthy": expected,
        }
        log(f"  {name:<14} verdict={r['verdict']:<10} returned={r['returned']:>3}  "
            f"total-rec={str(r['total_rec']):>7}  {r['message']!r}")

    exhausted = any(v["verdict"] == "quota" for v in results.values())
    log(f"\n  目前狀態：{'⛔ 配額耗盡' if exhausted else '✅ 配額可用'}")

    # 免費探針 = 耗盡時能反映 quota、且本身 0 筆成本
    free = [n for n, v in results.items()
            if v["cost"] == 0 and v["expected_when_healthy"] == 0]
    discriminating = [n for n in free if results[n]["verdict"] == "quota"]

    if exhausted:
        if discriminating:
            log(f"  ✅ 免費探針可用：{discriminating}")
            log("     耗盡時回 quota、恢復後會回其他訊息 → 輪詢 0 筆成本")
            best = discriminating[0]
        else:
            log("  ⚠ 0 筆請求在耗盡時不回 quota，無法區分狀態。")
            log("     Phase 3 只能用 canary_1rec（每次 1 筆）或 30 筆請求。")
            best = "canary_1rec"
    else:
        log("  （配額未耗盡，無法判定哪個探針有鑑別力——請在耗盡狀態下再跑一次）")
        best = "canary_1rec"

    log(f"  建議 Phase 3 探針：{best}")
    return {"exhausted": exhausted, "probes": results,
            "free_discriminating": discriminating, "recommended_probe": best}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — 燒配額
# ══════════════════════════════════════════════════════════════════════════════

def phase2_burn(step: int, max_requests: int, pause: float, burn_query: str,
                burn_fields: str = "PN") -> dict:
    log("\n" + "═" * 72)
    log("  Phase 2 — 燒配額（會用掉本時段額度）")
    log("═" * 72)
    log(f"  query={burn_query!r}  step={step}  fields={burn_fields}  max_requests={max_requests}")
    log("  配額計的是「輸出筆數」，與 expFld 無關，所以只拉 PN 以壓低傳輸量。")
    log("  截斷回應會剛好給出剩餘量，故 step 大小不影響精度，只影響請求數。")

    out: dict = {
        "step": step,
        "burn_query": burn_query,
        "burn_fields": burn_fields,
        "started_at": _now(),
        "calls": [],
        "cumulative": 0,
        "exhausted": False,
        "wraps": 0,
    }

    cumulative = 0
    skip = 0
    wraps = 0
    try:
        for i in range(1, max_requests + 1):
            r = call([
                ("patDB", CANARY_DB),
                ("patAG", "A,B"),
                ("TI/AB/CL", burn_query),
                ("expFld", burn_fields),
                ("expFmt", "json"),
                ("expQty", str(step)),
                ("expSkip", str(skip)),
            ], pause=pause)

            cumulative += r["returned"]
            out["calls"].append({
                "i": i, "skip": skip, "requested": step,
                "returned": r["returned"], "cumulative": cumulative,
                "verdict": r["verdict"], "message": r["message"],
                "total_rec": r["total_rec"], "elapsed_s": r["elapsed_s"],
                "at": _now(),
            })
            log(f"  #{i:>3}  skip={skip:>7}  req={step:>5}  got={r['returned']:>5}  "
                f"累計={cumulative:>8}  verdict={r['verdict']}")

            if r["verdict"] == "quota":
                out["exhausted"] = True
                out["exhausted_at"] = _now()
                out["quota_observed"] = cumulative
                log(f"\n  ★ 配額耗盡。本時段可下載總筆數 = {cumulative:,}")
                log(f"     最後一次請求 {step} 筆，實得 {r['returned']} 筆（剩餘量）")
                break

            # 結果集或分頁上限撞牆 → 回到 skip=0 重來。
            # 目的是量配額不是遍歷結果，重複下載一樣計數。
            # 這同時處理兩種情況：結果集真的用完、以及深分頁被系統設限。
            if r["verdict"] in ("empty", "skip_over") or r["returned"] == 0:
                wraps += 1
                log(f"     ↑ {r['verdict']}（skip={skip}）→ 重置 skip=0 繼續（第 {wraps} 次）")
                if wraps == 1:
                    out["pagination_ceiling"] = skip
                    log(f"     可觸及結果上限約 {skip:,} 筆——這本身是有用的數字")
                if wraps > 3:
                    log("\n  連續重置多次仍未耗盡配額，判定配額 >> 本次可量測範圍。")
                    out["note"] = "quota exceeds measurable range with this query"
                    break
                skip = 0
                continue

            skip += r["returned"]

        else:
            log(f"\n  已達 --max-requests {max_requests}，配額尚未耗盡。")
            log(f"     目前已下載 {cumulative:,} 筆 → 配額至少大於此數。")
            out["note"] = "max_requests reached before exhaustion"

    except KeyboardInterrupt:
        log("\n  [中斷] 已保存目前進度。")
        out["interrupted"] = True
    except GpssFatal as e:
        log(f"\n  [FATAL] {e}")
        out["fatal"] = str(e)

    out["cumulative"] = cumulative
    out["wraps"] = wraps
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Phase 3 — 等配額恢復
# ══════════════════════════════════════════════════════════════════════════════

def phase3_watch(max_hours: float, interval_s: int, pause: float,
                 probe_kind: str = "canary_1rec") -> dict:
    log("\n" + "═" * 72)
    log("  Phase 3 — 等配額恢復")
    log("═" * 72)
    make_params, expected = PROBE_KINDS[probe_kind]
    log(f"  探針={probe_kind}（健康時回 {expected} 筆）")
    log(f"  每 {interval_s}s 一次，最多 {max_hours} 小時。Ctrl-C 可中斷並保存。")
    if expected:
        cost_per_hour = (3600 / interval_s) * expected
        log(f"  ⚠ 此探針每次燒 {expected} 筆 → 約 {cost_per_hour:.0f} 筆/小時")

    out: dict = {
        "started_at": _now(),
        "interval_s": interval_s,
        "probe_kind": probe_kind,
        "polls": [],
        "recovered": False,
        "poll_cost_total": 0,
    }

    deadline = time.time() + max_hours * 3600
    t_start = time.time()
    cost = 0
    try:
        while time.time() < deadline:
            r = call(make_params(), pause=pause)
            cost += r["returned"]
            mins = (time.time() - t_start) / 60
            out["polls"].append({
                "at": _now(), "minutes_elapsed": round(mins, 1),
                "verdict": r["verdict"], "returned": r["returned"],
                "total_rec": r["total_rec"], "message": r["message"],
            })
            log(f"  +{mins:>7.1f} min  verdict={r['verdict']:<10} "
                f"returned={r['returned']:>3}  累計成本={cost:>5}  {r['message']!r}")

            # 恢復判定：不再回 quota
            if r["verdict"] != "quota":
                out["recovered"] = True
                out["recovered_at"] = _now()
                out["window_minutes"] = round(mins, 1)
                log(f"\n  ★ 配額已恢復。距耗盡 {mins:.1f} 分鐘（{mins/60:.2f} 小時）")
                for label, m in (("每小時", 60), ("每 6 小時", 360),
                                 ("每日", 1440), ("每月", 43200)):
                    if abs(mins - m) / m < 0.15:
                        log(f"     → 時段長度符合「{label}」（{m} min）")
                        out["window_guess"] = label
                break

            time.sleep(max(0, interval_s - pause))
        else:
            log(f"\n  已達 {max_hours} 小時仍未恢復 → 時段長度 > {max_hours} 小時。")
            out["note"] = f"not recovered within {max_hours}h"

    except KeyboardInterrupt:
        log("\n  [中斷] 已保存目前進度。")
        out["interrupted"] = True
    except GpssFatal as e:
        log(f"\n  [FATAL] {e}")
        out["fatal"] = str(e)

    out["poll_cost_total"] = cost
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def save(artifact: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(artifact, ensure_ascii=False, indent=2)
    path.write_text(_redact(text), encoding="utf-8")
    log(f"\n[artifact] {path}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="GPSS API 配額與限制探測",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--burn", action="store_true",
                    help="跑 Phase 2（會用掉本時段配額）")
    ap.add_argument("--watch", type=float, metavar="HOURS", default=0,
                    help="跑 Phase 3，守候 N 小時偵測配額恢復（需搭配 --burn）")
    ap.add_argument("--step", type=int, default=500,
                    help="Phase 2 每次請求筆數（預設 500；截斷回應自帶精度，"
                         "調大只是減少請求數）")
    ap.add_argument("--max-requests", type=int, default=400,
                    help="Phase 2 請求數安全上限（預設 400）")
    ap.add_argument("--burn-query", default="composition",
                    help="Phase 2 用的檢索詞，要挑 total-rec 夠大的（預設 composition）")
    ap.add_argument("--burn-fields", default="PN",
                    help="Phase 2 的 expFld（預設 PN）。配額計筆數不計位元組，"
                         "拉滿欄位只會拖慢傳輸。想驗證這點就跑第二個時段改成 "
                         "PN,ID,TI,AB,CL 比較兩次數字。")
    ap.add_argument("--status", action="store_true",
                    help="只探測目前配額狀態並找出免費輪詢探針（最多 1 筆成本）。"
                         "配額耗盡時跑最有價值。")
    ap.add_argument("--watch-interval", type=int, default=300,
                    help="Phase 3 輪詢間隔秒數（預設 300）")
    ap.add_argument("--watch-probe", default="auto",
                    choices=["auto", *PROBE_KINDS],
                    help="Phase 3 探針種類。auto = 先跑 --status 自動挑選")
    ap.add_argument("--pause", type=float, default=1.0,
                    help="每次請求後的禮貌性間隔秒數（預設 1.0）")
    ap.add_argument("--out", type=Path, default=None,
                    help="artifact 輸出路徑（預設 ./gpss_probe_<timestamp>.json）")
    ap.add_argument("--canary", action="store_true",
                    help="只跑金絲雀（1 次請求）：pemirolast × IPF 驗證 CL 索引，"
                         "並印出完整回傳與 parser 地雷分析。建議最先跑這個。")
    ap.add_argument("--skip-limits", action="store_true",
                    help="跳過 Phase 1")
    ap.add_argument("--env-file", type=Path, default=None,
                    help="指定 .env 路徑（預設由 cwd 與本檔位置往上層自動尋找）")
    args = ap.parse_args()

    load_user_code(args.env_file)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or Path(f"gpss_probe_{stamp}.json")

    artifact: dict = {
        "probe": "gpss_quota",
        "version": "1",
        "started_at": _now(),
        "args": {k: (str(v) if isinstance(v, Path) else v)
                 for k, v in vars(args).items()},
        "phases": {},
    }

    try:
        if args.status:
            artifact["phases"]["status"] = quota_status(args.pause)
            artifact["finished_at"] = _now()
            save(artifact, out_path)
            sys.exit(0)

        if args.canary:
            log("\n" + "═" * 72)
            log("  金絲雀模式 — 單次請求，不燒配額")
            log("═" * 72)
            artifact["phases"]["canary"] = canary_check(args.pause, verbose=True)
            artifact["finished_at"] = _now()
            save(artifact, out_path)
            hit = artifact["phases"]["canary"]["hit"]
            log("\n" + ("  → CL 索引成立，可以往下跑完整 probe："
                        "python probe_gpss_quota.py" if hit else
                        "  → 未命中。先確認號碼格式與 patDB 設定，別急著往下跑。"))
            sys.exit(0 if hit else 2)

        # --watch 可獨立跑（配額已在先前的 run 燒完的情況）
        if args.watch > 0 and not args.burn:
            probe = args.watch_probe
            if probe == "auto":
                st = quota_status(args.pause)
                artifact["phases"]["status"] = st
                probe = st["recommended_probe"]
                if not st["exhausted"]:
                    log("\n[warn] 配額未耗盡，watch 會立刻判定「已恢復」。")
                    log("       要量時段長度請先 --burn 或等真的耗盡再跑。")
            artifact["phases"]["watch"] = phase3_watch(
                args.watch, args.watch_interval, args.pause, probe)
            artifact["finished_at"] = _now()
            save(artifact, out_path)
            sys.exit(0)

        artifact["phases"]["preflight"] = phase0_preflight(args.pause)
        save(artifact, out_path)

        if not args.skip_limits:
            artifact["phases"]["limits"] = phase1_limits(args.pause)
            save(artifact, out_path)

        if args.burn:
            artifact["phases"]["burn"] = phase2_burn(
                args.step, args.max_requests, args.pause,
                args.burn_query, args.burn_fields)
            save(artifact, out_path)

            if args.watch > 0:
                if not artifact["phases"]["burn"].get("exhausted"):
                    log("\n[skip] Phase 2 未耗盡配額，Phase 3 無意義，跳過。")
                else:
                    probe = args.watch_probe
                    if probe == "auto":
                        st = quota_status(args.pause)
                        artifact["phases"]["status"] = st
                        probe = st["recommended_probe"]
                    artifact["phases"]["watch"] = phase3_watch(
                        args.watch, args.watch_interval, args.pause, probe)
                    save(artifact, out_path)
        else:
            log("\n[skip] Phase 2/3 未啟用。要量配額請加 --burn。")

    except GpssFatal as e:
        log(f"\n[FATAL] {e}")
        artifact["fatal"] = str(e)
        save(artifact, out_path)
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[中斷]")
        artifact["interrupted"] = True
        save(artifact, out_path)
        sys.exit(130)

    artifact["finished_at"] = _now()
    save(artifact, out_path)

    # ── 摘要 ──────────────────────────────────────────────────────────────
    log("\n" + "═" * 72)
    log("  摘要")
    log("═" * 72)
    burn = artifact["phases"].get("burn", {})
    watch = artifact["phases"].get("watch", {})
    if burn.get("quota_observed"):
        q = burn["quota_observed"]
        log(f"  時段配額：{q:,} 筆")
        if watch.get("window_minutes"):
            w = watch["window_minutes"]
            log(f"  時段長度：約 {w:.1f} 分鐘（{w/60:.2f} 小時）")
            log(f"  → 685 × 1300 = 890,500 cells，Phase 1 稀疏圖每 cell "
                f"最多 30 筆")
            log(f"  → 最壞情況 {890_500 * 30:,} 筆 ÷ {q:,} = "
                f"{890_500 * 30 / q:,.0f} 個時段")
            log(f"     （實際遠低於此——多數 cell 為 0 筆，不消耗配額）")
        else:
            log("  時段長度：未測（加 --watch N 量）")
    else:
        log("  配額未量到。加 --burn 執行 Phase 2。")


if __name__ == "__main__":
    main()
