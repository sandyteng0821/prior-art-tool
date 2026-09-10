#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SureChEMBL bulk parquet probe — phases 0-2.

Context
-------
EPO OPS 對 US/CN/KR/JP 全文回 404，GPSS 無說明書欄位（Bug Y 在該資料源無解），
粗層矩陣受 10,000 筆/8.3hr 配額限制。SureChEMBL bulk parquet 宣稱提供
patent × compound × field 與 patent × disease × field 的預算關聯，若屬實則
P1/P2/P4/P5 可在本地 join 解決，零配額。

本 script 只做驗證，不寫 patents.db，不改任何既有狀態。

Phases
------
0  FTP 目錄清單 + 檔案大小 → 決定 pandas vs DuckDB out-of-core
1  Schema 對帳（vs 官方文件宣稱）+ 列數 + field_id 分佈
2  本地重跑 23×3 矩陣，diff 既有 GPSS ground truth

★ 已知 schema 陷阱（讀官方 bulk-data 文件後標出，未實測）
  1. `compounds` 表**沒有名稱欄位**（只有 id/smiles/inchi/inchi_key/mol_weight）。
     drug name → compound_id 必須外部解析：name → InChIKey（ChEMBL/PubChem/UniChem）
     → join `compounds.inchi_key`。本 script 用 --drug-map TSV 餵入，不自己上網。
  2. `patents.patent_number` 格式為 CC-PATNO-KK（例 `WO-2011161255-A2`）。
     DB 是 `US9415051B1`，GPSS 回 `US09415051B1`（零填充）。三種格式需正規化。
  3. `patents.family_id` 是 DOCDB simple family，**不是 INPADOC**，且缺值為 -1
     （不是 NULL）。不可拿來取代 EPO family API。
  4. `fields.fieldname` 文件寫 DATA_TYPE=INT64 但語意是名稱字串，欄位型別存疑，
     phase 1 實測。對照值：1=Description 2=Claims 3=Abstract 4=Title
     5=Image 6=MOL attachment
  5. biomedical annotation 目前官方明示**只在 bulk data**，API/UI 沒有。

Usage
-----
  python probe_surechembl_bulk.py --phase 0
  python probe_surechembl_bulk.py --phase 0 --download --data-dir ./sc_bulk
  python probe_surechembl_bulk.py --phase 1 --data-dir ./sc_bulk
  python probe_surechembl_bulk.py --phase 2 --data-dir ./sc_bulk \
      --drug-map drug_inchikey.tsv \
      --indications "generalized pustular psoriasis,psoriasis,idiopathic pulmonary fibrosis" \
      --truth GPP_Psoriasis_IPF_matrix.xlsx

Deps: duckdb, pyarrow.  openpyxl 只在 --truth 為 .xlsx 時需要。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

BULK_URL = "https://ftp.ebi.ac.uk/pub/databases/chembl/SureChEMBL/bulk_data/"

# 官方 bulk-data 文件宣稱的 schema。phase 1 逐項對帳。
EXPECTED_SCHEMA = {
    "compounds": ["id", "smiles", "inchi", "inchi_key", "mol_weight"],
    "patents": ["id", "patent_number", "country", "publication_date", "family_id",
                "cpc", "ipcr", "ipc", "ecla", "assignee", "title"],
    "patent_compound_map": ["patent_id", "compound_id", "field_id"],
    # ⚠ 官方文件寫 `fieldname`，實測是 `field_name`（2026-09-01 release）
    "fields": ["id", "field_name"],
    "biomedical_entities": ["id", "type_id", "original_text", "corrected_text",
                            "resolved_form"],
    "biomedical_locations": ["entity_id", "patent_id", "field_id", "count"],
    "biomedical_types": ["id", "type_name", "description"],
}

FIELD_NAMES = {1: "Description", 2: "Claims", 3: "Abstract",
               4: "Title", 5: "Image", 6: "MOL attachment"}

# 實測 2026-09-01 release：field_name 存的是縮寫，不是文件寫的全稱。
# id → 語意的對應跟文件一致（已驗證），只是字面不同，不算假設崩塌。
FIELD_ABBREV = {1: {"desc", "description"}, 2: {"clms", "claims"},
                3: {"abst", "abstract"},    4: {"ttl", "title"},
                5: {"image", "images"},     6: {"molattachment", "mol attachment"}}

# Bug Y regression case（coverage_gaps §3b / search_coverage_gap_bromocriptine_sma.md）
BUG_Y_CASE = {"drug": "bromocriptine",
              "indication": "spinal muscular atrophy",
              "expect_patent": "WO2022028472A1"}

# GPSS 23×3 已知真陽性（GPSS_handoff §矩陣結果）
KNOWN_TRUE_POSITIVES = [
    ("pemirolast", "idiopathic pulmonary fibrosis", "US9415051B1"),
    ("olopatadine", "idiopathic pulmonary fibrosis", "US20090191183A1"),
    ("lodoxamide", "idiopathic pulmonary fibrosis", "US20150224078A1"),
]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def env_banner(data_dir: Path | None = None) -> dict:
    """
    印出 RAM / 磁碟 / 環境。Colab 與工作站的差別（12.7GB vs 更多）直接決定
    phase 1 會不會 OOM，開跑前就要知道，不是跑到一半才發現。
    """
    import shutil
    info = {}
    try:
        import os
        pages = os.sysconf("SC_PHYS_PAGES"); psize = os.sysconf("SC_PAGE_SIZE")
        info["ram_gb"] = pages * psize / 1024**3
    except Exception:
        info["ram_gb"] = None
    target = data_dir if data_dir and data_dir.exists() else Path(".")
    try:
        du = shutil.disk_usage(target)
        info["disk_free_gb"] = du.free / 1024**3
        info["disk_total_gb"] = du.total / 1024**3
    except Exception:
        info["disk_free_gb"] = info["disk_total_gb"] = None
    info["colab"] = Path("/content").is_dir() and "COLAB_RELEASE_TAG" in __import__("os").environ

    ram = f"{info['ram_gb']:.1f}GB" if info["ram_gb"] else "?"
    free = f"{info['disk_free_gb']:.1f}GB" if info["disk_free_gb"] else "?"
    log(f"  env: RAM {ram} · disk free {free} ({target})"
        + ("  [Colab]" if info["colab"] else ""))

    if info["ram_gb"] and info["ram_gb"] < 16:
        log(f"  ⚠ RAM {ram} 偏低。phase 1 的 description-only 聚合務必保持"
            f" --sample 預設值，且 --tmp-dir 指到本機 SSD（不要 Google Drive）")
    if info["disk_free_gb"] and info["disk_free_gb"] < 40:
        log(f"  ⚠ 磁碟剩 {free}。資料 17GB + DuckDB spill 可能還要數十 GB")
    if info["colab"]:
        log("  ⚠ Colab 磁碟斷線即清空。--tmp-dir 用 /content/... 不要用 "
            "/content/drive（Drive FUSE 的 I/O 會讓 DuckDB 慢到不能用）")
    return info


def section(title: str) -> None:
    log()
    log(f"── {title} " + "─" * max(1, 66 - len(title)))


# ══════════════════════════════════════════════════════════════════════════════
# ID 正規化
# ══════════════════════════════════════════════════════════════════════════════

_PN_RE = re.compile(r"^([A-Z]{2})[-\s]?(\d+)[-\s]?([A-Z]\d?)?$", re.I)

# 前導零只對這些管轄剝除。
#
# 依據：GPSS_handoff 陷阱 8 — GPSS 回 `US09415051B1`，DB 是 `US9415051B1`。
# 那是 GPSS 對「US utility grant 號碼補滿 8 位」的產物，不是 ID 的一部分。
#
# ⚠ 不可推廣到其他管轄。probe 實測：`EA004311B1`（ampicillin_formulation.md
#   實際存在的 ID）被無條件剝零會變成 `EA4311B1`，直接對不上 DB。
#   EP 同理（Solr 文件的 pnnum 範例就是 `0555555`，即 EP0555555B1）。
#   這條規則寧可保守：漏配可以在 diff 看到，錯配會靜默污染整個 join。
_STRIP_LEADING_ZERO_CTRY = {"US"}


def canon_pn(raw: str) -> str:
    """
    正規化到 DB 形式（去連字號／空白、大寫、US 剝前導零）。

      US-9415051-B1    → US9415051B1     (SureChEMBL bulk, CC-PATNO-KK)
      US09415051B1     → US9415051B1     (GPSS 零填充)
      US9415051B1      → US9415051B1     (DB)
      WO-2011161255-A2 → WO2011161255A2
      EA004311B1       → EA004311B1      ← 零保留
      EP-0555555-B1    → EP0555555B1     ← 零保留

    US pre-grant publication（`US20150224078A1`）號碼是 YYYYNNNNNNN，
    不以 0 開頭，剝零對它無影響。
    """
    if not raw:
        return ""
    s = str(raw).strip().upper().replace(" ", "")
    m = _PN_RE.match(s)
    if not m:
        return s.replace("-", "")
    ctry, num, kind = m.group(1), m.group(2), (m.group(3) or "")
    if ctry in _STRIP_LEADING_ZERO_CTRY:
        num = num.lstrip("0") or "0"
    return f"{ctry}{num}{kind}"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 — FTP 目錄清單
# ══════════════════════════════════════════════════════════════════════════════

class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for k, v in attrs:
            if k != "href" or not v:
                continue
            # 排除排序連結、上層導覽（EBI autoindex 的 parent 連結是絕對路徑
            # `/pub/databases/chembl/SureChEMBL/`，不是 `../`，probe 實測踩到）
            if v.startswith("?") or v.startswith("/") or v in ("../", "..", "#"):
                continue
            if v.startswith("http://") or v.startswith("https://"):
                continue
            self.links.append(v)


# release 目錄名形如 2026-02-01
_RELEASE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})/?$")


def list_dir(url: str) -> tuple[list[str], list[str]]:
    """回傳 (files, subdirs)。"""
    html = http_get(url).decode("utf-8", "replace")
    p = _LinkParser()
    p.feed(html)
    files = [l for l in p.links if not l.endswith("/")]
    subdirs = [l for l in p.links if l.endswith("/")]
    return files, subdirs


def resolve_release(base_url: str, want: str) -> tuple[str, list[str]]:
    """
    解析 release。SureChEMBL bulk data 每兩週一版，各版獨立完整
    （官方文件：every release is independent from the previous one），
    所以只需要一版，不是全部 36 版。

    回傳 (chosen_release, all_releases)。
    """
    _, subdirs = list_dir(base_url)
    releases = sorted(d.rstrip("/") for d in subdirs if _RELEASE_RE.match(d))
    if not releases:
        raise SystemExit(f"✗ {base_url} 底下找不到 YYYY-MM-DD 形式的 release 目錄")
    if want in ("latest", ""):
        return releases[-1], releases
    if want not in releases:
        raise SystemExit(
            f"✗ release {want!r} 不存在。可用：{releases[0]} … {releases[-1]}"
            f"（共 {len(releases)} 版）")
    return want, releases


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "prior-art-tool-probe/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def head_size(url: str, timeout: int = 60) -> int | None:
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "prior-art-tool-probe/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception as e:
        log(f"    ! HEAD failed for {url}: {e}")
        return None


def human(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def phase0(base_url: str, release: str, download: bool,
           data_dir: Path | None, skip_files: list[str] | None = None) -> dict:
    section("Phase 0 — bulk data 目錄清單")
    log(f"  {base_url}")
    env_banner(data_dir)

    # 頂層的 LICENCE / README 直接印出來。授權是 design_data_source_selection
    # 的 gating item（PROJECT_SKILL：判斷新資料源先看 LICENSE / ToU），
    # 而且 SureChEMBL 底層是 IFI Claims 商業資料，這份必須人工讀過。
    try:
        top_files, _ = list_dir(base_url)
    except Exception as e:
        log(f"  ✗ 無法取得目錄清單: {e}")
        log("    受限網路的話，用瀏覽器下載某一版到本地，再 --data-dir 指過去跑 phase 1")
        return {"ok": False, "error": str(e)}

    meta = {}
    for name in top_files:
        if name.upper().startswith(("LICEN", "README", "NOTICE", "CHANGELOG")):
            url = base_url.rstrip("/") + "/" + name
            try:
                txt = http_get(url).decode("utf-8", "replace")
            except Exception as e:
                log(f"  ! 讀不到 {name}: {e}")
                continue
            meta[name] = txt
            section(f"{name}  ({len(txt):,} chars)")
            for line in txt.splitlines():
                log(f"    {line}")

    # release 解析
    chosen, releases = resolve_release(base_url, release)
    section("Releases")
    log(f"  共 {len(releases)} 版：{releases[0]} … {releases[-1]}（每兩週一版）")
    log(f"  各版獨立完整，只需要下載一版。選用：{chosen}")

    rel_url = base_url.rstrip("/") + "/" + chosen + "/"
    log(f"\n  {rel_url}")
    files, subdirs = list_dir(rel_url)

    if subdirs:
        log(f"\n  ⚠ release 底下還有 {len(subdirs)} 個子目錄（可能是分片表）:")
        for d in subdirs:
            log(f"      {d}")

    log(f"\n  檔案 {len(files)} 個:")
    total = 0
    manifest = []
    for name in files:
        url = rel_url + name.lstrip("/")
        size = head_size(url)
        if size:
            total += size
        manifest.append({"name": name, "url": url, "bytes": size})
        log(f"    {human(size):>10}  {name}")

    # 分片表：遞迴一層
    for d in subdirs:
        sub_url = rel_url + d
        try:
            sub_files, _ = list_dir(sub_url)
        except Exception as e:
            log(f"    ! 讀不到 {d}: {e}")
            continue
        log(f"\n  {d} ({len(sub_files)} 個分片):")
        sub_total = 0
        for name in sub_files:
            url = sub_url + name.lstrip("/")
            size = head_size(url)
            if size:
                total += size
                sub_total += size
            manifest.append({"name": d.rstrip("/") + "/" + name,
                             "url": url, "bytes": size})
        log(f"    {human(sub_total):>10}  （{len(sub_files)} files）")

    log(f"\n  合計 {human(total)}")

    # 決策門檻：pandas 全載 vs DuckDB out-of-core
    section("Phase 0 判定")
    if total == 0:
        log("  ⚠ 無法取得檔案大小（HEAD 不回 Content-Length），無法判定")
    elif total < 2 * 1024**3:
        log(f"  → {human(total)} < 2GB：pandas 全載可行，但仍建議 DuckDB（SQL 較好寫）")
    elif total < 20 * 1024**3:
        log(f"  → {human(total)}：**用 DuckDB**，不要 pandas.read_parquet 全載")
    else:
        log(f"  → {human(total)}：DuckDB out-of-core，且 phase 2 要先按 "
            f"compound_id / patent_id 預過濾再落地，不要一次掃全表")

    if download and data_dir:
        data_dir.mkdir(parents=True, exist_ok=True)
        section(f"下載 {chosen} → {data_dir}")
        skipped_gb = 0.0
        for m in manifest:
            if skip_files and any(k in m["name"] for k in skip_files):
                skipped_gb += (m["bytes"] or 0) / 1024**3
                log(f"    skip (--skip-files) {m['name']} ({human(m['bytes'])})")
                continue
            # 保留相對路徑。分片表的檔名是 part-0.parquet，攤平會丟掉表名，
            # phase 1 的 _find_table 就找不到（probe 實測踩到）。
            dest = data_dir / m["name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and m["bytes"] and dest.stat().st_size == m["bytes"]:
                log(f"    skip (已完整) {m['name']}")
                continue
            log(f"    get {m['name']} ({human(m['bytes'])}) ...")
            try:
                urllib.request.urlretrieve(m["url"], dest)
            except Exception as e:
                # 不 silent-except：印出來，讓 phase 1 的缺表訊息有源頭可查
                log(f"    ✗ 下載失敗 {m['name']}: {e}")
        if skipped_gb:
            log(f"  略過 {skipped_gb:.1f}GB")
        log("  done")
        (data_dir / "_release.txt").write_text(
            f"{chosen}\n{rel_url}\n", encoding="utf-8")

    return {"ok": True, "release": chosen, "releases": releases,
            "release_url": rel_url, "total_bytes": total,
            "files": manifest, "meta_files": list(meta)}


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — schema 對帳
# ══════════════════════════════════════════════════════════════════════════════

def connect_duck(tmp_dir: Path, mem_limit: str | None, threads: int | None):
    """
    DuckDB 連線 + 大表調校。

    實測 phase 0：patent_compound_map.parquet 4.6GB 壓縮，整數欄位 parquet
    壓縮比 4-10x，推估 15-35 億列。在上面做 GROUP BY(patent_id, compound_id)
    的 hash aggregate 需要遠超一般機器記憶體。

      - temp_directory  沒設就不會 spill to disk，直接 OOM。必設。
      - preserve_insertion_order=false  大掃描/大聚合可省下可觀記憶體，
        本 probe 全部查詢都自己 ORDER BY，不依賴輸入順序。
      - memory_limit    留餘裕給 OS，預設不設讓 DuckDB 自己抓 80% RAM。
    """
    import duckdb
    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET temp_directory='{tmp_dir}'")
    con.execute("SET preserve_insertion_order=false")
    # DuckDBPyConnection 不接受自訂屬性，狀態另外回傳
    httpfs_ok, httpfs_err = True, ""
    try:
        con.execute("INSTALL httpfs; LOAD httpfs")
    except Exception as e:
        httpfs_ok, httpfs_err = False, str(e).splitlines()[0]
    if mem_limit:
        con.execute(f"SET memory_limit='{mem_limit}'")
    if threads:
        con.execute(f"SET threads={threads}")
    cfg = con.execute(
        "SELECT current_setting('memory_limit'), current_setting('threads'), "
        "current_setting('temp_directory')").fetchone()
    log(f"  duckdb: memory_limit={cfg[0]} threads={cfg[1]} temp={cfg[2]}"
        + ("" if httpfs_ok else "  [httpfs 不可用]"))
    return con, httpfs_ok, httpfs_err


def timed(con, label: str, sql: str, params=None):
    """跑查詢並回報耗時。大表上不知道跑多久最折磨人。"""
    import time
    t0 = time.time()
    log(f"    … {label}")
    try:
        rows = con.execute(sql, params or []).fetchall()
    except Exception as e:
        log(f"    ✗ {label} 失敗（{time.time()-t0:.0f}s）: {type(e).__name__}: {e}")
        return None
    log(f"    ✓ {label}  {time.time()-t0:.0f}s")
    return rows


def _find_table(data_dir: Path, table: str) -> list[Path]:
    """一張表可能是單檔或分片目錄。"""
    hits = sorted(data_dir.glob(f"**/{table}*.parquet"))
    d = data_dir / table
    if d.is_dir():
        hits += sorted(d.glob("**/*.parquet"))
    return sorted(set(hits))


def remote_src(release_url: str, table: str) -> str:
    """
    直接讀 FTP 上的 parquet，不下載。

    DuckDB httpfs 走 HTTP range request，parquet 的 column pruning 會生效：
      - count(*) 只讀 footer metadata，幾乎不傳資料
      - patents.parquet 5.5GB，但國別查詢只碰 country/publication_date/family_id
        三欄，實際傳輸遠小於 5.5GB

    ⚠ 不要期待 predicate pushdown 幫上 phase 2。compound_id 的 row-group
      statistics 只有在資料按 compound_id 排序時才有用，而它多半是按
      patent_id 排的，所以 `compound_id IN (...)` 仍會拉整欄。phase 2 用
      --remote 會很慢，建議 phase 1 用 remote 探路，phase 2 再落地。
    """
    return f"'{release_url.rstrip('/')}/{table}.parquet'"


def _src(files: list[Path]) -> str:
    """
    組出 DuckDB read_parquet() 的來源。

    ⚠ 不要用 f"{data_dir}/**/{table}*.parquet" 這種 glob：分片表的實際檔名是
      `part-0.parquet`（表名在目錄層，不在檔名層），glob 會完全對不上。
      probe 實測踩到。直接餵檔案清單最穩。
    """
    if len(files) == 1:
        return f"'{files[0]}'"
    return "[" + ",".join(f"'{f}'" for f in files) + "]"


def phase1(data_dir: Path, con, sample_pct: float,
           release_url: str | None = None, approx: bool = False) -> dict:
    section("Phase 1 — schema 對帳 vs 官方文件")
    env_banner(data_dir)
    if release_url:
        log(f"  remote 模式：{release_url}")
        log("  （不落地，走 httpfs range request）")

    def resolve(table: str):
        """回傳 (duckdb_source, n_files) 或 None。"""
        if release_url:
            return remote_src(release_url, table), 1
        f = _find_table(data_dir, table)
        return (_src(f), len(f)) if f else None

    report = {}

    for table, expected_cols in EXPECTED_SCHEMA.items():
        r_ = resolve(table)
        if not r_:
            log(f"\n  ✗ {table}: 找不到 parquet")
            report[table] = {"found": False}
            continue
        glob, n_files = r_
        files = [glob]
        try:
            cols = con.execute(
                f"SELECT * FROM read_parquet({glob}) LIMIT 0").description
            actual = [c[0] for c in cols]
            r = timed(con, f"{table} count(*)",
                      f"SELECT count(*) FROM read_parquet({glob})")
            n = r[0][0] if r else -1
        except Exception as e:
            log(f"\n  ✗ {table}: 讀取失敗 {e}")
            report[table] = {"found": True, "error": str(e)}
            continue

        missing = [c for c in expected_cols if c not in actual]
        extra = [c for c in actual if c not in expected_cols]
        status = "✓" if not missing else "⚠"
        log(f"\n  {status} {table}: {n:,} rows, {n_files} file(s)")
        if missing:
            log(f"      文件宣稱但實際沒有: {missing}   ← 假設崩塌，spec 要改")
        if extra:
            log(f"      文件沒寫但實際有:   {extra}")
        report[table] = {"found": True, "rows": n, "columns": actual,
                         "missing": missing, "extra": extra}

    # fields 表實測（陷阱 4）
    files = _find_table(data_dir, "fields")
    if files:
        section("fields 表實際內容（驗證 field_id 對照）")
        rows = con.execute(
            f"SELECT * FROM read_parquet({_src(files)}) ORDER BY 1").fetchall()
        for r in rows:
            fid = r[0]
            doc = FIELD_NAMES.get(fid, "?")
            actual = str(r[1]).strip().lower()
            ok = actual in FIELD_ABBREV.get(fid, set()) or doc.lower() in actual
            log(f"    {'✓' if ok else '✗'} id={fid}  actual={r[1]!r}"
                f"  → {doc}" + ("" if ok else "   ← 對應不上，spec 要改"))
        log("    （field_name 存縮寫，id→語意對應與文件一致）")
        report["fields_actual"] = [list(map(str, r)) for r in rows]

    # field_id 分佈 —— 這是整個評估的核心指標
    r_ = resolve("patent_compound_map")
    if r_:
        section("★ patent_compound_map field_id 分佈（P1 Bug Y 的關鍵）")
        glob = r_[0]
        # ⚠ 一度改用 approx_count_distinct 省記憶體，實測是壞交易：
        #   2026-09-01 release 上 Description 的 patents 近似值 52,682,164，
        #   比 patents 表總數 45,135,815 還大（物理上不可能），對精確值
        #   39,775,964 誤差 +32.4%。HLL 在這個基數上不堪用。
        #   精確版在同一份資料上跑得完，預設走精確。
        fn = "approx_count_distinct" if approx else "count(DISTINCT %s)"
        expr_p = (f"approx_count_distinct(patent_id)" if approx
                  else "count(DISTINCT patent_id)")
        expr_c = (f"approx_count_distinct(compound_id)" if approx
                  else "count(DISTINCT compound_id)")
        dist = timed(con, "field_id 分佈" + ("（近似）" if approx else "（精確）"), f"""
            SELECT field_id, count(*) AS n,
                   {expr_p} AS n_patents, {expr_c} AS n_compounds
            FROM read_parquet({glob}) GROUP BY field_id ORDER BY field_id
        """) or []
        suffix = "~" if approx else ""
        log(f"    {'field':<18}{'rows':>16}{'patents'+suffix:>14}"
            f"{'compounds'+suffix:>14}")
        for fid, n, np_, nc in dist:
            log(f"    {str(fid)+' '+FIELD_NAMES.get(fid,'?'):<18}{n:>16,}{np_:>14,}{nc:>14,}")
        report["field_distribution"] = [
            {"field_id": f, "rows": n, "patents": p, "compounds": c}
            for f, n, p, c in dist]

        # description-only：只出現在 description，沒出現在 claims/abstract/title
        #
        # ⚠ 這是整個 phase 1 唯一的重查詢。在 15-35 億列上做
        #   GROUP BY(patent_id, compound_id)，distinct group 可能 5 億以上，
        #   hash table 15-20GB。預設走 patent_id 雜湊抽樣，不掃全表。
        #
        #   抽樣用 hash(patent_id) % N，不是 USING SAMPLE：必須保留同一個
        #   patent 的所有列，否則「這個 pair 有沒有出現在 claims」會被抽樣
        #   本身破壞，比例會系統性高估。
        section("★ description-only 化合物-專利對（= Bug Y 可解的規模）")
        if sample_pct >= 100:
            where = ""
            log(f"    全表掃描（--sample 100）。若 OOM 改用預設抽樣")
        else:
            n = max(1, round(100 / sample_pct))
            where = f"WHERE hash(patent_id) % {n} = 0"
            log(f"    patent_id 雜湊抽樣 1/{n} ≈ {100/n:.1f}%"
                f"（--sample 100 可掃全表）")

        rows = timed(con, "description-only 聚合", f"""
            WITH m AS (SELECT patent_id, compound_id, field_id
                       FROM read_parquet({glob}) {where}),
                 g AS (
                SELECT patent_id, compound_id,
                       max(CASE WHEN field_id = 1 THEN 1 ELSE 0 END) AS in_desc,
                       max(CASE WHEN field_id IN (2,3,4) THEN 1 ELSE 0 END) AS in_other
                FROM m GROUP BY patent_id, compound_id)
            SELECT sum(CASE WHEN in_desc = 1 AND in_other = 0 THEN 1 ELSE 0 END),
                   count(*)
            FROM g
        """)
        if rows is None:
            log("    → 記憶體不足的話：--mem-limit 降低讓它更早 spill，"
                "或 --sample 1 縮小抽樣，或 --tmp-dir 指到大容量磁碟")
            report["description_only"] = {"error": "query failed"}
        else:
            only, total_pairs = rows[0]
            only, total_pairs = int(only or 0), int(total_pairs or 0)
            pct = 100 * only / total_pairs if total_pairs else 0
            scale = "" if sample_pct >= 100 else f"（抽樣 {sample_pct}%，比例可外推）"
            log(f"    description-only pairs: {only:,} / {total_pairs:,}"
                f"  ({pct:.1f}%) {scale}")
            log(f"    → 這 {pct:.1f}% 是 EPO ta= 和 GPSS TI/AB/CL 結構上搜不到的部分")
            report["description_only"] = {"pairs": only, "total": total_pairs,
                                          "pct": pct, "sample_pct": sample_pct}

        # ★ laundry-list 風險量化
        #
        # description-only 佔比高 ≠ 訊號多。專利說明書常列上百個背景化合物，
        # 那種提及不構成 prior art。新版 bulk parquet 又拿掉了舊 schema 的
        # `frequency` 欄位（只剩 patent_id/compound_id/field_id），沒法用
        # 出現次數過濾，只能看每篇標到幾個化合物。
        section("★ 每篇專利的化合物數分佈（laundry-list 風險）")
        n_ = max(1, round(100 / sample_pct)) if sample_pct < 100 else 1
        w_ = f"AND hash(patent_id) % {n_} = 0" if sample_pct < 100 else ""
        ll = timed(con, "每篇化合物數分位數", f"""
            SELECT f.field_id, count(*) AS n_patents,
                   round(avg(f.k), 1) AS mean,
                   quantile_cont(f.k, 0.5)  AS p50,
                   quantile_cont(f.k, 0.9)  AS p90,
                   quantile_cont(f.k, 0.99) AS p99,
                   max(f.k) AS max_k
            FROM (SELECT field_id, patent_id, count(DISTINCT compound_id) AS k
                  FROM read_parquet({glob})
                  WHERE field_id IN (1, 2) {w_}
                  GROUP BY field_id, patent_id) f
            GROUP BY f.field_id ORDER BY f.field_id
        """) or []
        log(f"    {'field':<14}{'專利數':>12}{'mean':>8}{'p50':>7}{'p90':>7}"
            f"{'p99':>8}{'max':>9}")
        for fid, np_, mean, p50, p90, p99, mx in ll:
            log(f"    {FIELD_NAMES.get(fid, str(fid)):<14}{int(np_):>12,}"
                f"{mean:>8}{int(p50):>7}{int(p90):>7}{int(p99):>8}{int(mx):>9}")
        report["compounds_per_patent"] = [
            {"field_id": f, "n_patents": int(n), "mean": float(m),
             "p50": float(a), "p90": float(b), "p99": float(c), "max": int(d)}
            for f, n, m, a, b, c, d in ll]
        if ll:
            log("\n    → 看中位數而非平均：p50 接近代表一般專利不是列舉，"
                "問題在右尾。")

        # ★ pair 的分桶累積分佈
        #
        # 上面的分位數是「按專利數」，但 pair 是按每篇化合物數加權的，
        # 所以少數超大列舉專利可能貢獻大部分 pair。要決定「每篇化合物數
        # 上限砍在哪」，必須看 pair 這一側，不是專利那一側。
        section("★ description pair 的分桶累積（決定 compounds-per-patent 上限）")
        buckets = timed(con, "分桶", f"""
            WITH per AS (
                SELECT patent_id, count(DISTINCT compound_id) AS k
                FROM read_parquet({glob})
                WHERE field_id = 1 {w_}
                GROUP BY patent_id)
            SELECT CASE WHEN k <=   10 THEN '1'
                        WHEN k <=   50 THEN '2'
                        WHEN k <=  200 THEN '3'
                        WHEN k <= 1000 THEN '4'
                        ELSE                '5' END AS b,
                   count(*) AS n_patents, sum(k) AS n_pairs
            FROM per GROUP BY b ORDER BY b
        """) or []
        label = {"1": "≤10", "2": "11-50", "3": "51-200",
                 "4": "201-1000", "5": ">1000"}
        tot_p = sum(int(r[1]) for r in buckets) or 1
        tot_k = sum(int(r[2]) for r in buckets) or 1
        log(f"    {'每篇化合物數':<12}{'專利數':>12}{'佔比':>8}"
            f"{'pair 數':>14}{'佔比':>8}{'累積 pair':>10}")
        cum = 0
        for b, np_, nk in buckets:
            np_, nk = int(np_), int(nk)
            cum += nk
            log(f"    {label.get(b, b):<12}{np_:>12,}{100*np_/tot_p:>7.1f}%"
                f"{nk:>14,}{100*nk/tot_k:>7.1f}%{100*cum/tot_k:>9.1f}%")
        report["desc_pair_buckets"] = [
            {"bucket": label.get(b, b), "patents": int(n), "pairs": int(k)}
            for b, n, k in buckets]
        if buckets:
            log(f"\n    合計 {tot_p:,} 專利 · {tot_k:,} pair"
                f"（抽樣 {sample_pct}%）")
            log("    → 用「累積 pair」挑上限：保留大部分專利、砍掉大部分 pair "
                "的那一格就是門檻。")
            log("      門檻不是丟掉 description，是丟掉超大列舉專利。")

    # 國別 × 年份覆蓋（驗證 US 1976~ / CN 1985~ 的宣稱）
    r_ = resolve("patents")
    if r_:
        section("國別覆蓋（驗證官方宣稱：US granted 1976~、CN 英譯 1985~）")
        glob = r_[0]
        rows = timed(con, "國別覆蓋聚合", f"""
            SELECT country, count(*) n,
                   min(publication_date) first_pub, max(publication_date) last_pub
            FROM read_parquet({glob})
            GROUP BY country ORDER BY n DESC LIMIT 15
        """) or []
        log(f"    {'ctry':<6}{'patents':>14}  {'first':<12}{'last':<12}")
        for c, n, f, l in rows:
            log(f"    {str(c):<6}{n:>14,}  {str(f):<12}{str(l):<12}")
        report["country_coverage"] = [
            {"country": c, "n": n, "first": str(f), "last": str(l)}
            for c, n, f, l in rows]

        # 陷阱 3：family_id = -1 的比例
        # ⚠ 實測 2026-09-01 release：US/EP/JP 的 min(publication_date) 是
        #   0001-01-01，那是 null 哨兵不是日期。任何吃 publication_date 的
        #   year / filing_date / expiry_date 邏輯都要先擋掉這批，否則靜默錯分。
        section("★ publication_date 哨兵值（影響 year / expiry 推導）")
        # TRY_CAST：publication_date 在不同 release 可能是 DATE 也可能是
        # VARCHAR（fixture 實測踩到型別不符）。轉不出來的也算壞值。
        sent = timed(con, "哨兵值分佈", f"""
            WITH p AS (SELECT country,
                              TRY_CAST(publication_date AS DATE) AS d
                       FROM read_parquet({glob}))
            SELECT country,
                   sum(CASE WHEN d IS NULL OR d <= DATE '1800-01-01'
                            THEN 1 ELSE 0 END) AS bad,
                   count(*) AS n,
                   min(CASE WHEN d > DATE '1800-01-01' THEN d END) AS real_first
            FROM p GROUP BY country ORDER BY n DESC LIMIT 10
        """) or []
        log(f"    {'ctry':<6}{'哨兵':>12}{'總數':>14}{'佔比':>8}  真實最早")
        tot_bad = tot_n = 0
        for c, bad, n, first in sent:
            bad, n = int(bad or 0), int(n or 0)
            tot_bad += bad; tot_n += n
            log(f"    {str(c):<6}{bad:>12,}{n:>14,}"
                f"{(100*bad/n if n else 0):>7.1f}%  {first}")
        log(f"\n    合計哨兵 {tot_bad:,} / {tot_n:,}"
            f" ({(100*tot_bad/tot_n if tot_n else 0):.1f}%)")
        if tot_bad:
            log("    → 匯入時必須把 publication_date <= 1800-01-01 視為 NULL，"
                "不可當年份用")
        report["pubdate_sentinel"] = [
            {"country": str(c), "bad": int(b or 0), "n": int(n or 0),
             "real_first": str(f)} for c, b, n, f in sent]

        _b = timed(con, "family_id = -1 比例", f"""
            SELECT sum(CASE WHEN family_id = -1 THEN 1 ELSE 0 END), count(*)
            FROM read_parquet({glob})
        """)
        bad = _b[0] if _b else (0, 1)
        log(f"\n    family_id = -1 (無 DOCDB family): {bad[0]:,} / {bad[1]:,}"
            f"  ({100*bad[0]/bad[1]:.1f}%)")
        log("    ⚠ 這是 DOCDB simple family，不是 INPADOC。EPO family API 不可替代")

    # 疾病標註可用性
    rbe, rbt = resolve("biomedical_entities"), resolve("biomedical_types")
    if rbe and rbt:
        section("biomedical 標註（P3 疾病端）")
        beg, btg = rbe[0], rbt[0]
        rows = con.execute(f"""
            SELECT t.id, t.type_name, count(e.id) n,
                   sum(CASE WHEN e.resolved_form IS NOT NULL
                             AND e.resolved_form <> '' THEN 1 ELSE 0 END) resolved
            FROM read_parquet({btg}) t
            LEFT JOIN read_parquet({beg}) e ON e.type_id = t.id
            GROUP BY t.id, t.type_name ORDER BY n DESC
        """).fetchall()
        log(f"    {'type':<28}{'entities':>12}{'resolved':>12}{'rate':>8}")
        for tid, name, n, res in rows:
            rate = f"{100*res/n:.0f}%" if n else "-"
            log(f"    {str(name)[:27]:<28}{n:>12,}{(res or 0):>12,}{rate:>8}")
        report["biomedical_types"] = [
            {"id": t, "name": str(nm), "entities": n, "resolved": r}
            for t, nm, n, r in rows]

    return report


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — 23×3 矩陣重跑 + diff
# ══════════════════════════════════════════════════════════════════════════════

def load_drug_map(path: Path) -> dict[str, list[str]]:
    """
    TSV: drug_name <TAB> inchikey[,inchikey2,...]

    compounds 表沒有名稱欄位（陷阱 1），所以藥名必須先在外部解析成 InChIKey。
    來源建議：ChEMBL molecule endpoint 或 PubChem PUG。一個藥可能對到多個
    InChIKey（鹽類、水合物、立體異構），全部列出，用逗號分隔。
    """
    out: dict[str, list[str]] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        # ⚠ 只剝換行，不要 .strip()：行尾 tab 會被吃掉，讓「有藥名但無 key」
        #   的行（生物藥／類別名／查無）變成單欄位被當格式錯誤丟掉。
        #   那會讓 phase 2 完全看不到這些藥，破壞「查不了」與「0 命中」的區分。
        ln = ln.rstrip("\r\n")
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        name = parts[0].strip().lower()
        if not name:
            log(f"  ! drug-map 略過無藥名的行: {ln[:60]!r}")
            continue
        raw = parts[1] if len(parts) > 1 else ""
        out[name] = [k.strip().upper() for k in raw.split(",") if k.strip()]
    return out


def load_truth(path: Path, cols: str | None = None,
               header: int | None = None) -> dict[tuple[str, str], set[str]]:
    """
    讀既有 GPSS ground truth，回傳 {(drug, indication): {canon_pn, ...}}。

    ⚠⚠ 這個函式壞過兩次，兩次的失敗模式相反，都記在這裡：
      v1 假設 header=0。實測使用者的 xlsx 第一列是報表標題，直接找不到欄位，
         大聲失敗。
      v2 改成掃 header=0..6 並在第一個命中就回傳。結果抓到「檢核」分頁第 4 列，
         三個欄位全是同一格長篇註解（那段文字剛好同時含「藥」「適應症」
         「專利」三個字，子字串比對全中），然後從散文裡解出 18 個假專利號。
         **安靜地給出錯答案，比大聲失敗更糟。**

    所以現在的規則：不在第一個命中就收工，而是收集所有候選、逐個結構驗證、
    取分數最高者，並把候選清單印出來讓人看得見選了哪個。
      1. 三個欄位必須是三個不同的欄
      2. 欄名長度 <= 40（真表頭不會是一整段散文）
      3. 欄名不可為 Unnamed:*
      4. 專利欄必須有足夠比例的值長得像專利號
      5. 分數 = 解出的專利號數量，取最高
    """
    import pandas as pd

    MAX_COLNAME = 40
    PN_RE = re.compile(r"^[A-Z]{2}\s?\d{4,}", re.I)

    def clean_col(c) -> str:
        return str(c).strip()

    def usable(c) -> bool:
        t = clean_col(c)
        return bool(t) and len(t) <= MAX_COLNAME and not t.startswith("Unnamed:")

    def pick(columns, *cands):
        low = {clean_col(c).lower(): c for c in columns if usable(c)}
        for cand in cands:
            for k, orig in low.items():
                if cand in k:
                    return orig
        return None

    def parse(df, dc, ic, pc):
        out: dict[tuple[str, str], set[str]] = {}
        n_pn = n_cell = 0
        for _, r in df.iterrows():
            d, i, pn = r.get(dc), r.get(ic), r.get(pc)
            if not (isinstance(d, str) and isinstance(i, str)
                    and isinstance(pn, str)):
                continue
            n_cell += 1
            for one in re.split(r"[,;\n/、\s]+", pn):
                one = one.strip()
                if len(one) >= 6 and PN_RE.match(one):
                    out.setdefault((d.strip().lower(), i.strip().lower()),
                                   set()).add(canon_pn(one))
                    n_pn += 1
        return out, n_pn, n_cell

    want = [c.strip() for c in cols.split(",")] if cols else None
    headers = [header] if header is not None else list(range(0, 7))
    cands = []

    for h in headers:
        try:
            if path.suffix.lower() in (".xlsx", ".xlsm"):
                sheets = pd.read_excel(path, sheet_name=None, dtype=str, header=h)
            else:
                sheets = {"csv": pd.read_csv(path, dtype=str, header=h)}
        except Exception:
            continue
        for name, df in sheets.items():
            if want and len(want) == 3:
                dc, ic, pc = (pick(df.columns, w.lower()) for w in want)
            else:
                dc = pick(df.columns, "drug", "compound", "藥物", "藥名", "藥")
                ic = pick(df.columns, "indication", "disease", "適應症", "疾病")
                pc = pick(df.columns, "patent no", "patent", "專利號", "專利",
                          "publication")
            if not (dc is not None and ic is not None and pc is not None):
                continue
            if len({str(dc), str(ic), str(pc)}) < 3:
                cands.append((0, h, name, dc, ic, pc, "三欄指向同一欄，排除"))
                continue
            out, n_pn, n_cell = parse(df, dc, ic, pc)
            if not out:
                cands.append((0, h, name, dc, ic, pc, "解不出專利號"))
                continue
            if n_cell and n_pn / n_cell < 0.3:
                cands.append((0, h, name, dc, ic, pc,
                              f"專利號密度過低 {n_pn}/{n_cell}"))
                continue
            cands.append((n_pn, h, name, dc, ic, pc, out))

    ok = [c for c in cands if c[0] > 0]
    if cands:
        log("  truth 候選（分數 = 解出的專利號數）：")
        for sc, h, name, dc, ic, pc, info in sorted(cands, key=lambda x: -x[0]):
            note = info if isinstance(info, str) else "採用候選"
            log(f"    {sc:>5}  sheet={name!r} header={h}  "
                f"[{str(dc)[:18]} | {str(ic)[:18]} | {str(pc)[:18]}]  {note}")
    if not ok:
        log("  ✗ 沒有通過結構驗證的候選。用 --truth-header N 與 "
            "--truth-cols 藥物,適應症,專利 明確指定")
        return {}

    best = max(ok, key=lambda x: x[0])
    sc, h, name, dc, ic, pc, out = best
    log(f"  truth 採用: sheet={name!r} header={h} "
        f"(drug={dc}, ind={ic}, pn={pc})")
    log(f"  truth: {len(out)} 個 (drug, indication) 組合，{sc} 個專利號")
    for k in sorted(out)[:5]:
        log(f"      {k}  →  {sorted(out[k])[:4]}")
    if len(out) > 5:
        log(f"      …另 {len(out) - 5} 組")
    return out


def list_entities(data_dir: Path, con, pattern: str, limit: int = 60,
                  entity_type: str | None = "Disease") -> dict:
    """
    列出 biomedical 字典裡符合關鍵詞的實體，附各自的專利數。

    為什麼需要這個工具
    ------------------
    bulk parquet **不是 fulltext index**，是實體標註索引：
      biomedical_locations = (entity_id, patent_id, field_id, count)
      整份 17GB 沒有任何說明書原文。
    所以查得到的上限是「LeadMine 字典認得的實體」，不是「專利文本」。
    字典裡沒有的病名會完全隱形，而且是靜默的 —— 回 0 命中，看起來像
    「這個資料源沒收這個病」。

    實測踩到：`generalized pustular psoriasis` 精確比對回 0，但字典裡存的是
    `generalised`（英式拼法）與全大寫 `PUSTULAR PSORIASIS`，共 10 種變型。
    不是沒收錄，是拼法不對。查任何新適應症之前先跑這個。
    """
    def g(t):
        f = _find_table(data_dir, t)
        if not f:
            raise SystemExit(f"缺少 {t} parquet")
        return _src(f)

    section(f"biomedical 字典查詢: {pattern!r}")
    kws = [w for w in re.split(r"[^A-Za-z0-9]+", pattern) if len(w) > 2]
    if not kws:
        log("    ✗ 關鍵詞太短")
        return {}
    cond = " AND ".join([f"lower(e.corrected_text) LIKE '%{w.lower()}%'"
                         for w in kws])
    # ⚠ 預設只看 Disease。實測查 'spinal muscular atrophy' 會撈到
    #   `Androgen receptor (dihydrotestosterone re…)`，type=GeneOrProtein，
    #   因為雄性素受體正是 Kennedy disease 的致病基因。不過濾 type 會把
    #   基因實體混進疾病查詢。
    if entity_type:
        cond += f" AND t.type_name = '{entity_type}'"
    log(f"    條件: corrected_text 同時含 {kws}"
        + (f" AND type = {entity_type}" if entity_type else " （不限 type）"))

    rows = timed(con, "字典查詢 + 專利數", f"""
        SELECT e.id, e.corrected_text, e.original_text, e.resolved_form,
               t.type_name,
               count(DISTINCT l.patent_id) AS n_pat,
               sum(l.count)                AS n_occ
        FROM read_parquet({g('biomedical_entities')}) e
        LEFT JOIN read_parquet({g('biomedical_types')}) t ON t.id = e.type_id
        LEFT JOIN read_parquet({g('biomedical_locations')}) l ON l.entity_id = e.id
        WHERE {cond}
        GROUP BY 1,2,3,4,5
        ORDER BY n_pat DESC NULLS LAST
        LIMIT {limit}
    """) or []
    if not rows:
        log("    ✗ 字典裡沒有符合的實體。換關鍵詞，或該病名不在 LeadMine 字典")
        return {}

    log(f"\n    {'id':>9}  {'corrected_text':<42}{'MeSH':<12}"
        f"{'type':<14}{'專利數':>9}{'總次數':>10}")
    out = []
    tot = 0
    for eid, ct, ot, rf, ty, npat, nocc in rows:
        npat = int(npat or 0); tot += npat
        log(f"    {eid:>9}  {str(ct)[:41]:<42}{str(rf or '-'):<12}"
            f"{str(ty or '-')[:13]:<14}{npat:>9,}{int(nocc or 0):>10,}")
        out.append({"id": eid, "corrected_text": ct, "original_text": ot,
                    "resolved_form": rf, "type": ty,
                    "n_patents": npat, "n_occurrences": int(nocc or 0)})
    log(f"\n    {len(rows)} 個實體 · 專利數合計 {tot:,}（可能重複計算）")

    # ★ 按 MeSH ID 彙總 —— 污染要在這一層才看得出來。
    #
    # 實測 'spinal muscular atrophy' 的 60 個實體裡，D055534
    # （Spinal and Bulbar Muscular Atrophy = Kennedy disease）佔 9,622 篇
    # 專利、15.4%。那是雄性素受體 CAG 重複擴增造成的 X 染色體疾病，
    # 跟 SMA（SMN1 缺失）是不同的病。逐筆看 60 列很難發現，按 MeSH
    # 彙總就一目了然。
    by_mesh: dict[str, dict] = {}
    for r in out:
        k = str(r["resolved_form"] or "-")
        b = by_mesh.setdefault(k, {"n_ent": 0, "n_pat": 0, "examples": []})
        b["n_ent"] += 1
        b["n_pat"] += r["n_patents"]
        if len(b["examples"]) < 2:
            b["examples"].append(str(r["corrected_text"])[:38])
    section("按 MeSH ID 彙總（★ 檢查有沒有混入不同疾病）")
    log(f"    {'MeSH':<12}{'實體數':>7}{'專利數':>10}{'佔比':>8}  代表字面")
    tot_p = sum(b["n_pat"] for b in by_mesh.values()) or 1
    for k, b in sorted(by_mesh.items(), key=lambda kv: -kv[1]["n_pat"]):
        log(f"    {k:<12}{b['n_ent']:>7}{b['n_pat']:>10,}"
            f"{100*b['n_pat']/tot_p:>7.1f}%  {' / '.join(b['examples'])}")
    if len(by_mesh) > 1:
        log(f"\n    ⚠ {len(by_mesh)} 個 MeSH ID。**逐個確認是不是同一種病**，"
            f"不是同一種的用 --exclude-mesh 排除。")
        log(f"      D 開頭 = 主標題詞，C 開頭 = 補充概念（多為罕見亞型，通常可留）")
    log("\n    → 確認後把 corrected_text 字面填進 --indications；"
        "非目標的 MeSH 用 --exclude-mesh 排除")
    return {"pattern": pattern, "entities": out,
            "by_mesh": by_mesh, "entity_type": entity_type}


def inspect_patent(data_dir: Path, con, pn: str,
                   drug_skel: str | None = None) -> dict:
    """
    檢視單篇專利在 SureChEMBL 裡的完整標註。

    為什麼需要
    ----------
    某篇專利沒被命中時，有五種完全不同的原因，結論意義各異：
      (1) 不在 patents 表          → 資料源未收錄該專利
      (2) 在表裡但無化合物標註      → 化學抽取失敗
      (3) 有化合物但沒有目標藥      → name-to-structure 沒認出該藥名
      (4) 有目標藥但無疾病標註      → 疾病字典沒認出
      (5) 兩邊都有但 join 不到      → 我方查詢或 ID 正規化問題
    猜錯會把 (5) 記成 (1)，直接產生一條假的資料源限制。

    實測情境：Bug Y 的 WO2022028472A1 在 bromocriptine × SMA 的 1,825 篇
    命中裡缺席，必須逐層確認是哪一種。
    """
    def g(t):
        f = _find_table(data_dir, t)
        if not f:
            raise SystemExit(f"缺少 {t} parquet")
        return _src(f)

    want = canon_pn(pn)
    section(f"單篇檢視: {pn}  (正規化 → {want})")

    # 步驟 1：在 patents 表裡找。用 country + 去連字號的 LIKE，
    # 因為儲存格式是 CC-PATNO-KK 而輸入可能是任何一種寫法。
    ctry = want[:2]
    num = re.sub(r"^[A-Z]{2}", "", want)
    num_core = re.sub(r"[A-Z]\d?$", "", num)
    rows = timed(con, "patents 查詢", f"""
        SELECT id, patent_number, country, publication_date, family_id, title
        FROM read_parquet({g('patents')})
        WHERE country = '{ctry}'
          AND replace(patent_number, '-', '') LIKE '{ctry}{num_core}%'
    """) or []
    if not rows:
        log(f"\n    ✗ (1) patents 表裡沒有 {ctry}{num_core}*")
        # ⚠ 先確認不是輸入錯誤才下結論。
        #   實測：US20230293730A1（2023+0293730，11 位）被誤打成
        #   US2023293730A1（10 位），工具照輸入回「不在表裡」，看起來像
        #   資料源未收錄，其實那篇在庫裡而且已被命中。
        #   專利號差一個數字仍然是合法格式，不會有任何語法錯誤提示。
        # ⚠ 前綴比對單方向不夠。實測 US20230293730A1 被打成
        #   US2023293730A1，**少的那個 0 在中間**，所以前綴從第 6 個字元
        #   就分歧，任何長度的前綴都對不上；但後 6 位 293730 相同。
        #   所以前綴與後綴兩個方向都要試。
        near = []
        probes = []
        for cut in (len(num_core) - 1, len(num_core) - 2, 6, 5):
            if cut >= 4:
                probes.append(("前綴", f"'{ctry}{num_core[:cut]}%'"))
        for tail in (7, 6, 5):
            if len(num_core) >= tail:
                # ⚠ 尾端要留 %。儲存格式是 CC-PATNO-KK，結尾是 kind code
                #   （A1/B2…）不是數字，寫成 '..%293730' 會對不上。
                probes.append(("後綴", f"'{ctry}%{num_core[-tail:]}%'"))
        for kind, pat_ in probes:
            r2 = con.execute(f"""
                SELECT patent_number, title FROM read_parquet({g('patents')})
                WHERE country = '{ctry}'
                  AND replace(patent_number, '-', '') LIKE {pat_}
                LIMIT 12""").fetchall()
            if r2:
                near = r2
                log(f"\n    ? {kind}比對 {pat_} 找到 {len(r2)} 筆近似值")
                log(f"      —— 專利號差一位數仍是合法格式，不會有語法錯誤，"
                    f"**先確認不是輸入有誤**：")
                for pnum, ti in r2:
                    log(f"        {pnum:<22}{str(ti)[:56]}")
                break
        if not near:
            log(f"        （前綴與後綴比對都查不到，不是輸入錯誤）")
        log(f"\n        → 確認號碼無誤後，結論是：這篇不在 patents.parquet。")
        log(f"        patents.parquet 的官方定義是 all the patents "
            f"**with compounds**，")
        log(f"        所以「不在表裡」= SureChEMBL 從該篇抽不出任何化學結構。")
        log(f"        ⚠ 這不等於 SureChEMBL 沒有這篇 —— web UI "
            f"(surechembl.org/patent/{ctry}-{num_core}-...) 可能有頁面。")
        log(f"        實測 WO2022028472A1（核酸構築物專利）就是這種情況：")
        log(f"        網頁存在、bulk 沒有，因為整篇零小分子。")
        log(f"        → 改查同家族成員。用 --inspect-patent 查家族裡有化合物的那篇。")
        return {"pn": pn, "stage": 1, "found": False,
                "near_misses": [{"patent_number": a, "title": b}
                                for a, b in near]}

    log(f"\n    ✓ (1) 在 patents 表，{len(rows)} 筆相符：")
    for pid, pnum, c, d, fam, ti in rows:
        log(f"        id={pid}  {pnum}  {c}  {d}  family={fam}")
        log(f"          title: {str(ti)[:70]}")
    pids = [r[0] for r in rows]
    pid_list = ",".join(map(str, pids))
    out = {"pn": pn, "found": True,
           "patents": [{"id": r[0], "patent_number": r[1], "country": r[2],
                        "publication_date": str(r[3]), "family_id": r[4],
                        "title": r[5]} for r in rows]}

    # 步驟 1b：同家族成員（在 bulk 裡的）
    #
    # 為何重要：實測 WO2022028472A1 因為零小分子被 bulk 排除，但同 simple
    # family 的 US20230293730A1 有化合物標註且成功命中。prior art 以發明／
    # 家族為單位判斷，所以家族層級可達就夠了。family_id 填充率 99.9%。
    fams = sorted({r[4] for r in rows if r[4] and r[4] != -1})
    if fams:
        fl = ",".join(map(str, fams))
        fam_rows = timed(con, f"同家族成員 (family_id {fams})", f"""
            SELECT patent_number, country, publication_date, family_id, title
            FROM read_parquet({g('patents')})
            WHERE family_id IN ({fl})
            ORDER BY country, patent_number
        """) or []
        log(f"\n    ✓ (1b) DOCDB simple family 在 bulk 裡的成員 "
            f"{len(fam_rows)} 篇：")
        for pnum, c, d, fam, ti in fam_rows[:20]:
            mark = " ←本篇" if canon_pn(pnum) == want else ""
            log(f"        {pnum:<22}{c:<4}{str(d):<12}{str(ti)[:40]}{mark}")
        if len(fam_rows) > 20:
            log(f"        …另 {len(fam_rows) - 20} 篇")
        log(f"        ⚠ 這是 DOCDB simple family，不是 INPADOC。"
            f"Espacenet 上的家族可能更大。")
        out["family_members"] = [{"patent_number": a, "country": b,
                                  "publication_date": str(c), "family_id": d,
                                  "title": e} for a, b, c, d, e in fam_rows]
    else:
        log(f"\n    · (1b) family_id 為 -1 或空，無家族資訊")

    # 步驟 2/3：化合物標註
    comp = timed(con, "化合物標註", f"""
        SELECT m.field_id, count(DISTINCT m.compound_id) AS n_cmp
        FROM read_parquet({g('patent_compound_map')}) m
        WHERE m.patent_id IN ({pid_list})
        GROUP BY m.field_id ORDER BY m.field_id
    """) or []
    if not comp:
        log(f"\n    ✗ (2) 完全沒有化合物標註 → 化學抽取失敗")
        out["stage"] = 2
    else:
        log(f"\n    ✓ (2) 化合物標註：")
        for fid, n in comp:
            log(f"        {FIELD_NAMES.get(fid, fid):<16}{int(n):>8} 個化合物")
        out["compounds_by_field"] = [{"field": FIELD_NAMES.get(f, f),
                                      "n": int(n)} for f, n in comp]

    if drug_skel:
        sk = drug_skel[:14].upper()
        hit = timed(con, f"目標藥 {sk} 是否在此篇", f"""
            SELECT m.field_id, c.id, c.inchi_key, c.mol_weight
            FROM read_parquet({g('patent_compound_map')}) m
            JOIN read_parquet({g('compounds')}) c ON c.id = m.compound_id
            WHERE m.patent_id IN ({pid_list})
              AND substr(upper(c.inchi_key), 1, 14) = '{sk}'
        """) or []
        if hit:
            log(f"\n    ✓ (3) 目標藥骨架 {sk} 有標註：")
            for fid, cid, ik, mw in hit:
                log(f"        {FIELD_NAMES.get(fid, fid):<14}cid={cid}  "
                    f"{ik}  MW={mw:.1f}")
            out["drug_hits"] = [{"field": FIELD_NAMES.get(f, f), "cid": c,
                                 "inchi_key": i} for f, c, i, _ in hit]
        else:
            log(f"\n    ✗ (3) 目標藥骨架 {sk} 在此篇沒有標註")
            log(f"        → name-to-structure 沒從這篇認出該藥，"
                f"或該藥在原文用了字典外的寫法（代號、商品名、譯名）")
            out["stage"] = 3

    # 步驟 4：疾病標註
    dis = timed(con, "biomedical 標註", f"""
        SELECT e.corrected_text, e.resolved_form, t.type_name,
               l.field_id, l.count
        FROM read_parquet({g('biomedical_locations')}) l
        JOIN read_parquet({g('biomedical_entities')}) e ON e.id = l.entity_id
        LEFT JOIN read_parquet({g('biomedical_types')}) t ON t.id = e.type_id
        WHERE l.patent_id IN ({pid_list})
        ORDER BY l.count DESC LIMIT 30
    """) or []
    if not dis:
        log(f"\n    ✗ (4) 完全沒有 biomedical 標註")
        out["stage"] = 4
    else:
        log(f"\n    ✓ (4) biomedical 標註（依出現次數，前 30）：")
        log(f"        {'corrected_text':<40}{'MeSH':<11}{'type':<14}"
            f"{'field':<14}{'次數':>6}")
        for ct, rf, ty, fid, n in dis:
            log(f"        {str(ct)[:39]:<40}{str(rf or '-'):<11}"
                f"{str(ty or '-')[:13]:<14}"
                f"{FIELD_NAMES.get(fid, fid):<14}{int(n or 0):>6}")
        out["biomedical"] = [{"text": ct, "mesh": rf, "type": ty,
                              "field": FIELD_NAMES.get(f, f), "count": int(n or 0)}
                             for ct, rf, ty, f, n in dis]
    log(f"\n    → 對照五種原因判定，不要憑命中與否推論。")
    return out


def find_compound(data_dir: Path, con, mw: float | None, tol: float,
                  smiles: str | None, ik_prefix: str | None,
                  limit: int = 40) -> dict:
    """
    不靠名稱在 compounds 表裡找化合物。

    為什麼需要
    ----------
    `compounds` 表只有 id / smiles / inchi / inchi_key / mol_weight，**沒有名稱**。
    所以當某個藥回 0 compound_id 時，無法分辨兩種完全不同的情況：
        (a) InChIKey 給錯      → 我方問題，改 key 即可
        (b) SureChEMBL 沒收錄  → 資料源限制，要記進缺口清單
    這兩者在結論裡的意義天差地別，猜不得。

    用分子量 + SMILES 特徵原子 + InChIKey 前綴三個條件（AND）反查，就能在
    不知道正確 key 的前提下確認該結構在不在庫裡。

    實測情境：bromocriptine（C32H40BrN5O5, MW 654.6）骨架回 0，用
        --find-mw 654.6 --find-smiles Br
    即可判定是 key 錯還是真的沒有。
    """
    def g(t):
        f = _find_table(data_dir, t)
        if not f:
            raise SystemExit(f"缺少 {t} parquet")
        return _src(f)

    conds = []
    if mw is not None:
        conds.append(f"mol_weight BETWEEN {mw - tol} AND {mw + tol}")
    if smiles:
        conds.append(f"smiles LIKE '%{smiles}%'")
    if ik_prefix:
        conds.append(f"upper(inchi_key) LIKE '{ik_prefix.upper()}%'")
    if not conds:
        log("    ✗ 至少要給 --find-mw / --find-smiles / --find-inchikey 其中之一")
        return {}

    section("compounds 反查（不靠名稱）")
    log(f"    條件: {' AND '.join(conds)}")

    # ⚠ 先數總數。第一版直接 LIMIT 就回傳，靜默截斷。
    #   實測查 bromocriptine（MW 654.61，範圍 654.1–655.1）回了 40 筆全在
    #   654.10–654.32，目標被 LIMIT 切掉，看起來像「庫裡沒有」。
    total = con.execute(f"""
        SELECT count(*) FROM read_parquet({g('compounds')})
        WHERE {' AND '.join(conds)}""").fetchone()[0]
    log(f"    符合條件共 {total:,} 筆")

    # ⚠ 排序必須用「與目標 MW 的距離」，不是 MW 本身。
    #   ORDER BY mol_weight 會讓你只看到範圍下緣。
    order = (f"abs(mol_weight - {mw})" if mw is not None else "mol_weight")
    rows = timed(con, "反查", f"""
        SELECT id, inchi_key, mol_weight, smiles
        FROM read_parquet({g('compounds')})
        WHERE {' AND '.join(conds)}
        ORDER BY {order}
        LIMIT {limit}
    """) or []
    if total > limit:
        log(f"    （只顯示最接近的 {limit} 筆；縮小 --find-mw-tol 或加 "
            f"--find-smiles 更長的片段可收斂）")
    if not rows:
        log("\n    ✗ 沒有符合的化合物。")
        log("      → 放寬 --find-mw-tol，或改用 --find-smiles 給更短的特徵片段。")
        log("      → 若放寬後仍為空，才能判定 SureChEMBL 未收錄此結構。")
        return {"found": 0, "rows": []}

    log(f"\n    {'id':>10}  {'InChIKey':<29}{'MW':>9}  SMILES")
    out = []
    for cid, ik, w, smi in rows:
        log(f"    {cid:>10}  {ik:<29}{(w or 0):>9.2f}  {str(smi)[:52]}")
        out.append({"id": cid, "inchi_key": ik, "mol_weight": w, "smiles": smi})
    skels = sorted({r[1][:14] for r in rows})
    log(f"\n    顯示 {len(rows)} / {total:,} 筆 · {len(skels)} 個骨架")
    log(f"    {skels[:8]}" + (f" …共 {len(skels)}" if len(skels) > 8 else ""))
    log("\n    → 骨架對得上你手上的 key，就是資料在庫裡；")
    log("      對不上且 total 已經很小，才能判定未收錄。")
    log("    ⚠ InChIKey 第一段是雜湊，**沒有「接近」這回事**。前幾碼相同純屬")
    log("      巧合，14 碼要嘛全對要嘛是不相干的分子，不可拿前綴相似當佐證。")
    return {"found": len(rows), "total": total, "rows": out,
            "skeletons": skels}


def phase2(data_dir: Path, con, drug_map: dict[str, list[str]],
           indications: list[str], truth: dict, out_path: Path | None,
           caps: list[int] | None = None, exact_keys: bool = False,
           dump_variants: str | None = None,
           exclude_mesh: set[str] | None = None,
           expand_family: bool = False) -> dict:
    section("Phase 2 — 本地重跑矩陣")

    def g(t):
        f = _find_table(data_dir, t)
        if not f:
            raise SystemExit(f"缺少 {t} parquet")
        return _src(f)

    con.execute(f"CREATE VIEW cmp AS SELECT * FROM read_parquet({g('compounds')})")
    con.execute(f"CREATE VIEW pat AS SELECT * FROM read_parquet({g('patents')})")
    con.execute(f"CREATE VIEW pcm AS SELECT * FROM read_parquet({g('patent_compound_map')})")
    con.execute(f"CREATE VIEW bent AS SELECT * FROM read_parquet({g('biomedical_entities')})")
    con.execute(f"CREATE VIEW bloc AS SELECT * FROM read_parquet({g('biomedical_locations')})")

    # 藥名 → compound_id
    section("drug → compound_id 解析（走 InChIKey，因 compounds 表無名稱欄）")
    #
    # 支援兩種 key：
    #   27 字元完整 InChIKey  精確比對
    #   14 字元骨架前綴        比對 substr(inchi_key,1,14)，涵蓋所有立體異構
    #
    # 骨架前綴很重要：專利常只畫平面結構或寫消旋體，SureChEMBL 抽出的立體
    # 標註未必跟 ChEMBL 的參考結構一致。只用完整 key 會漏掉這類。
    # 鹽型骨架不同（連接性不同），要靠 resolve_inchikey.py 的同義字分別查。
    if exact_keys:
        log("    --exact-keys：只用 27 碼完整 key 精確比對，"
            "14 碼骨架前綴會被略過（對照組用）")
    drug_cids: dict[str, list[int]] = {}
    for drug, keys in drug_map.items():
        if not keys:
            drug_cids[drug] = []
            log(f"    · {drug:<22} 無 InChIKey（生物藥／類別名／查無），跳過")
            continue
        exact = [k for k in keys if len(k) == 27]
        skel = [] if exact_keys else [k[:14] for k in keys if len(k) != 27]
        if exact_keys and not exact:
            drug_cids[drug] = []
            log(f"    · {drug:<22} --exact-keys 模式下無 27 碼 key，跳過")
            continue
        clauses = []
        if exact:
            clauses.append("upper(inchi_key) IN ('" + "','".join(exact) + "')")
        if skel:
            clauses.append("substr(upper(inchi_key),1,14) IN ('"
                           + "','".join(skel) + "')")
        rows = con.execute(
            f"SELECT id, inchi_key FROM cmp WHERE {' OR '.join(clauses)}"
        ).fetchall()
        drug_cids[drug] = [r[0] for r in rows]
        mark = "✓" if rows else "✗"
        kinds = (f"{len(exact)} 精確" if exact else "") + \
                (("+" if exact and skel else "") + f"{len(skel)} 骨架" if skel else "")
        log(f"    {mark} {drug:<22} {kinds} → {len(rows)} compound_id")
        if rows and skel and len(rows) > len(keys):
            ratio = len(rows) / max(1, len(keys))
            # 展開倍數僅供參考，**不要當成錯誤指標**。
            #
            # 我先前斷言 >15x 是異常，實測不成立：展開數與該藥的專利數無關
            #   darifenacin  74 變體 / N(D)=8,410
            #   methotrexate 18 變體 / N(D)=395,364
            # 也與 lift 無關（pirfenidone 30 變體，IPF lift 312.5 全表第二高）。
            # 骨架段只編碼連接性，同骨架變體只差立體／同位素／質子化，都是
            # 同一個化合物的不同標註。要看實際內容用 --dump-variants。
            warn = "  （用 --dump-variants 檢視實際變體）" if ratio > 15 else ""
            log(f"        （骨架比對展開為 {len(rows)} 個結構變體，"
                f"輸入 {len(keys)} 個 key，{ratio:.0f}x）{warn}")
        if not rows:
            log(f"        未命中。可能：SureChEMBL 未抽到此結構／該藥無專利收錄")

    if dump_variants:
        section(f"--dump-variants: {dump_variants}")
        key = dump_variants.strip().lower()
        cids = drug_cids.get(key)
        if not cids:
            log(f"    ✗ {dump_variants!r} 不在本次藥物清單，或未解析出 "
                f"compound_id。可用: {', '.join(sorted(drug_cids))}")
        else:
            rows = con.execute(f"""
                SELECT id, inchi_key, mol_weight, smiles
                FROM cmp WHERE id IN ({','.join(map(str, cids))})
                ORDER BY inchi_key
            """).fetchall()
            # 用完整 pcm（此處 pcm_f 還沒物化）。1.5 億列掃一次約 70 秒，
            # 只在 --dump-variants 時才跑。
            cl_ = ",".join(map(str, cids))
            r_ = timed(con, "各變體的專利數", f"""
                SELECT compound_id, count(DISTINCT patent_id)
                FROM pcm WHERE compound_id IN ({cl_}) GROUP BY compound_id""")
            npat = {int(a): int(b) for a, b in (r_ or [])}
            log(f"    {len(rows)} 個 compound_id：")
            log(f"    {'id':>10}  {'InChIKey':<29}{'MW':>9}{'專利數':>9}  SMILES")
            for cid, ik, mw, smi in rows:
                log(f"    {cid:>10}  {ik:<29}{(mw or 0):>9.1f}"
                    f"{npat.get(cid, 0):>9}  {str(smi)[:44]}")
            mws = [r[2] for r in rows if r[2]]
            if mws:
                log(f"\n    MW 範圍 {min(mws):.1f} – {max(mws):.1f}"
                    f"（同骨架應幾乎相同，差異大代表混入其他東西）")
            log(f"    第二段（立體／同位素）: "
                f"{len({r[1][15:25] for r in rows})} 種")

    # 適應症 → entity_id
    section("indication → entity_id 解析")
    #
    # 三段式回退。⚠ 實測 `generalized pustular psoriasis` 精確比對回 0，
    # 但近似詞 plaque / nummular / intertriginous psoriasis 都存在且
    # resolved_form 全是 D011565（= MeSH「Psoriasis」）。也就是說 SureChEMBL
    # 的字典把 psoriasis 各變型收斂到同一個 MeSH ID，GPP 沒有獨立節點。
    # 只做精確比對會誤判成「資料源沒有這個病」，實際是命名層級問題。
    #   1. exact     corrected_text / original_text 完全相符
    #   2. contains  corrected_text 含整個查詢字串（抓 "chronic X" 這類）
    #   3. keyword   查詢的關鍵詞（去掉 generalized/idiopathic 等修飾語）
    STOP = {"generalized", "idiopathic", "chronic", "acute", "severe",
            "primary", "secondary", "disease", "syndrome", "disorder"}
    ind_eids: dict[str, list[int]] = {}
    ind_mode: dict[str, str] = {}
    for ind in indications:
        low = ind.lower()
        rows = con.execute("""
            SELECT id, corrected_text, resolved_form FROM bent
            WHERE lower(corrected_text) = ? OR lower(original_text) = ?
        """, [low, low]).fetchall()
        mode = "exact"
        if not rows:
            rows = con.execute("""
                SELECT id, corrected_text, resolved_form FROM bent
                WHERE lower(corrected_text) LIKE ?
            """, [f"%{low}%"]).fetchall()
            mode = "contains"
        if not rows:
            kws = [w for w in re.split(r"[^a-z]+", low)
                   if len(w) > 3 and w not in STOP]
            if kws:
                cond = " AND ".join(
                    [f"lower(corrected_text) LIKE '%{w}%'" for w in kws])
                rows = con.execute(f"""
                    SELECT id, corrected_text, resolved_form FROM bent
                    WHERE {cond}
                """).fetchall()
                mode = f"keyword({'+'.join(kws)})"
        if exclude_mesh and rows:
            before = len(rows)
            dropped = [r for r in rows if str(r[2] or "") in exclude_mesh]
            rows = [r for r in rows if str(r[2] or "") not in exclude_mesh]
            if dropped:
                log(f"      --exclude-mesh 排除 {len(dropped)}/{before} 個實體："
                    f"{sorted({str(r[2]) for r in dropped})}")
                for _i, t, rf in dropped[:4]:
                    log(f"          排除 {t!r} → {rf}")
        ind_eids[ind] = [r[0] for r in rows]
        ind_mode[ind] = mode if rows else "none"
        mark = "✓" if rows else "✗"
        resolved = sorted({r[2] for r in rows if r[2]})
        log(f"    {mark} {ind:<40} {len(rows):>4} entity_id  [{ind_mode[ind]}]"
            + (f"  MeSH→{resolved[:3]}" if resolved else ""))
        if rows and mode != "exact":
            log(f"        ⚠ 非精確比對，實際命中的詞：")
            for _id, t, rf in rows[:6]:
                log(f"          {t!r}  →  {rf}")
            if len(rows) > 6:
                log(f"          …另 {len(rows) - 6} 個")
            if len(resolved) > 1:
                log(f"        ⚠ 收斂到 {len(resolved)} 個不同 MeSH ID，"
                    f"可能混入不同疾病，需人工確認")
        if not rows:
            log(f"        查無。SureChEMBL 字典可能無此節點，"
                f"或該病名被收斂到上位詞")

    # 先把 pcm / bloc 依「本次用到的所有 id」過濾成小表再落地。
    #
    # 原本每格都在完整 pcm(4.6GB) × bloc(1.6GB) × pat(5.5GB) 上做三方 join，
    # 23×3 = 69 格就是掃 69 次全表。改成先物化一次，之後每格都在小表上跑。
    all_cids = sorted({c for v in drug_cids.values() for c in v})
    all_eids = sorted({e for v in ind_eids.values() for e in v})
    if all_cids and all_eids:
        section("物化過濾子集（避免每格重掃全表）")
        cl_all = ",".join(map(str, all_cids))
        el_all = ",".join(map(str, all_eids))
        timed(con, f"pcm_f  ({len(all_cids)} compounds)", f"""
            CREATE TEMP TABLE pcm_f AS
            SELECT patent_id, compound_id, field_id FROM pcm
            WHERE compound_id IN ({cl_all})""")
        # ⚠ 必須帶 count。biomedical_locations.count 是「該實體在該欄位出現
        #   幾次」，是唯一可用的訊號強度指標（patent_compound_map 沒有
        #   frequency 欄位）。第一版漏掉這欄，導致無法區分「提一次」和
        #   「整篇在講它」。
        timed(con, f"bloc_f ({len(all_eids)} entities)", f"""
            CREATE TEMP TABLE bloc_f AS
            SELECT entity_id, patent_id, field_id, count FROM bloc
            WHERE entity_id IN ({el_all})""")
        timed(con, "pat_f  (只取被命中的 patent)", """
            CREATE TEMP TABLE pat_f AS
            SELECT p.id, p.patent_number, p.country FROM pat p
            WHERE p.id IN (SELECT patent_id FROM pcm_f)
              AND p.id IN (SELECT patent_id FROM bloc_f)""")
        for t in ("pcm_f", "bloc_f", "pat_f"):
            n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            log(f"      {t}: {n:,} rows")
        src_pcm, src_bloc, src_pat = "pcm_f", "bloc_f", "pat_f"
    else:
        src_pcm, src_bloc, src_pat = "pcm", "bloc", "pat"

    # 逐格 join
    section("矩陣（本地 join，零配額）")
    results = []
    for drug, cids in drug_cids.items():
        for ind in indications:
            eids = ind_eids.get(ind, [])
            if not cids or not eids:
                results.append({"drug": drug, "indication": ind, "n": 0,
                                "patents": [], "by_field": {},
                                "verdict": "unresolved"})
                continue
            cl = ",".join(map(str, cids))
            el = ",".join(map(str, eids))
            # 每格帶出所有可過濾的訊號，過濾在後面統一掃描，不寫死在查詢裡
            rows = con.execute(f"""
                SELECT p.id, p.patent_number,
                       min(m.field_id) AS drug_min_field,
                       max(CASE WHEN m.field_id = 2 THEN 1 ELSE 0 END)
                           AS drug_in_claims,
                       max(b.count) AS dis_max_count,
                       max(CASE WHEN m.field_id = b.field_id THEN 1 ELSE 0 END)
                           AS same_field,
                       -- GPSS 查 TI/AB/CL（field 2/3/4），這兩個旗標讓
                       -- 我們能做真正同範圍的比較，而不是拿全欄位對 TI/AB/CL
                       max(CASE WHEN m.field_id IN (2,3,4) THEN 1 ELSE 0 END)
                           AS drug_tiabcl,
                       max(CASE WHEN b.field_id IN (2,3,4) THEN 1 ELSE 0 END)
                           AS dis_tiabcl
                FROM {src_pcm} m
                JOIN {src_bloc} b ON b.patent_id = m.patent_id
                JOIN {src_pat}  p ON p.id        = m.patent_id
                WHERE m.compound_id IN ({cl}) AND b.entity_id IN ({el})
                GROUP BY p.id, p.patent_number
            """).fetchall()
            pns = [canon_pn(r[1]) for r in rows]
            by_field: dict[str, int] = {}
            n_claims = 0
            for _, _pn, df_, in_cl, _dc, _sf, _dt, _it in rows:
                by_field[FIELD_NAMES.get(df_, str(df_))] = \
                    by_field.get(FIELD_NAMES.get(df_, str(df_)), 0) + 1
                n_claims += int(in_cl or 0)
            results.append({"drug": drug, "indication": ind, "n": len(pns),
                            "n_claims": n_claims,
                            "patents": sorted(pns), "by_field": by_field,
                            "rows": [{"pid": int(r[0]), "pn": canon_pn(r[1]),
                                      "drug_field": int(r[2]),
                                      "in_claims": int(r[3] or 0),
                                      "dis_count": int(r[4] or 0),
                                      "same_field": int(r[5] or 0),
                                      "drug_tiabcl": int(r[6] or 0),
                                      "dis_tiabcl": int(r[7] or 0)}
                                     for r in rows],
                            "verdict": "hit" if pns else "empty"})

    # ★ 家族展開
    #
    # 實測 Bug Y：WO2022028472A1 零小分子被 bulk 排除，但同 simple family 的
    # US20230293730A1 有標註且命中。prior art 以發明／家族為單位判斷，所以
    # 命中一個成員就等於觸及該發明。反過來，若只看公開案層級會得出
    # 「這個資料源找不到 Bug Y」的錯誤結論。
    if expand_family and results:
        section("★ 家族展開（DOCDB simple family）")
        all_hit_pids = sorted({x["pid"] for r in results
                               for x in r.get("rows", [])})
        if all_hit_pids:
            pl = ",".join(map(str, all_hit_pids))
            fam = timed(con, "取命中專利的 family_id", f"""
                SELECT DISTINCT family_id FROM pat
                WHERE id IN ({pl}) AND family_id IS NOT NULL
                  AND family_id <> -1""")
            fids = [int(r[0]) for r in (fam or [])]
            log(f"    命中 {len(all_hit_pids):,} 篇 → {len(fids):,} 個家族")
            if fids:
                fl = ",".join(map(str, fids))
                sib = timed(con, "展開為家族成員", f"""
                    SELECT count(*), count(DISTINCT family_id) FROM pat
                    WHERE family_id IN ({fl})""")
                n_sib = int(sib[0][0]) if sib else 0
                log(f"    展開後 {n_sib:,} 篇（含原命中），"
                    f"新增 {n_sib - len(all_hit_pids):,} 篇手足")
                log(f"    ⚠ 手足未經 drug × indication 驗證，只是同一發明的"
                    f"其他公開案。用途是取得無化合物標註的成員"
                    f"（如 WO2022028472A1），不是擴大候選集。")
                for r in results:
                    r["family_ids"] = fids if len(results) == 1 else None
                cap_report_family = {"n_hit": len(all_hit_pids),
                                     "n_families": len(fids),
                                     "n_after_expand": n_sib}
            else:
                cap_report_family = {"n_hit": len(all_hit_pids),
                                     "n_families": 0}
        else:
            cap_report_family = {}
    else:
        cap_report_family = {}

    # ★ lift：相對於隨機共現的富集倍數
    #
    # 為何需要：實測未過濾的絕對數量完全被 base rate 支配。
    #   methotrexate × psoriasis = 102,893，不是因為關聯強，是因為兩個詞都
    #   極高頻，隨機共現就這麼多。
    # 對照 GPSS 的倍率排序發現：四個 FDA 核准配對佔據倍率最低的前四名
    #   （13/16/19/24x），第五名起 ≥51x，中間有 2.1x 落差。
    #   → 訊號在資料裡，判準是相對量不是絕對量。
    #
    #   expected(D,I) = N(D) × N(I) / N_total
    #   lift          = observed / expected
    #
    # lift 只用 SureChEMBL 自己的數字，不依賴 GPSS。
    section("★ lift 基準量（N_total / 各藥 / 各適應症的專利數）")
    n_total = con.execute("SELECT count(*) FROM pat").fetchone()[0]
    log(f"    N_total = {n_total:,}")
    n_drug: dict[str, int] = {}
    for drug, cids in drug_cids.items():
        if not cids:
            n_drug[drug] = 0
            continue
        cl = ",".join(map(str, cids))
        n_drug[drug] = con.execute(
            f"SELECT count(DISTINCT patent_id) FROM {src_pcm} "
            f"WHERE compound_id IN ({cl})").fetchone()[0]
    n_ind: dict[str, int] = {}
    for ind, eids in ind_eids.items():
        if not eids:
            n_ind[ind] = 0
            continue
        el = ",".join(map(str, eids))
        n_ind[ind] = con.execute(
            f"SELECT count(DISTINCT patent_id) FROM {src_bloc} "
            f"WHERE entity_id IN ({el})").fetchone()[0]
    log(f"\n    {'藥物':<18}{'N(D)':>12}      {'適應症':<34}{'N(I)':>12}")
    ds, is_ = list(n_drug.items()), list(n_ind.items())
    for i in range(max(len(ds), len(is_))):
        a = f"{ds[i][0]:<18}{ds[i][1]:>12,}" if i < len(ds) else " " * 30
        b = f"{is_[i][0]:<34}{is_[i][1]:>12,}" if i < len(is_) else ""
        log(f"    {a}      {b}")

    for r in results:
        nd, ni = n_drug.get(r["drug"], 0), n_ind.get(r["indication"], 0)
        exp = (nd * ni / n_total) if (nd and ni and n_total) else 0
        r["n_drug"], r["n_ind"], r["expected"] = nd, ni, round(exp, 1)
        r["lift"] = round(r["n"] / exp, 2) if exp > 0 else None

    # 矩陣圖，沿用 probe_matrix_pilot 的 render 風格
    dw = max((len(d) for d in drug_cids), default=8) + 1
    cell = {(r["drug"], r["indication"]): r for r in results}
    # ⚠ 欄寬要依實際最大值算。寫死 5 會在六位數時讓相鄰欄位黏在一起
    #   （實測 methotrexate × psoriasis = 102893 撞掉隔欄），完全不可讀。
    cw = max([len(f"{r['n']:,}") for r in results] + [5]) + 2
    log()
    for i, ind in enumerate(indications, 1):
        log(f"  {i}. {ind}")
    log()
    log(f"  {'':>{dw}} " + "".join(f"{i:>{cw}}" for i in
                                   range(1, len(indications) + 1)))
    for d in drug_cids:
        row = ""
        for ind in indications:
            r = cell[(d, ind)]
            row += (f"{r['n']:>{cw},}" if r["verdict"] != "unresolved"
                    else f"{'·':>{cw}}")
        hits = sum(1 for i in indications if cell[(d, i)]["n"] > 0)
        log(f"  {d:>{dw}} {row}   ({hits}/{len(indications)})")
    log("\n  · = drug 或 indication 未解析成 ID（不是 0 命中，是查不了）")

    # lift 矩陣 —— 這張才是排序用的
    log(f"\n  lift（observed / 隨機期望）：>1 表示富集，這張是排序用的")
    log(f"  {'':>{dw}} " + "".join(f"{i:>{cw}}" for i in
                                   range(1, len(indications) + 1)))
    for d in drug_cids:
        row = ""
        for ind in indications:
            r = cell[(d, ind)]
            lf = r.get("lift")
            row += (f"{lf:>{cw}.1f}" if lf is not None else f"{'·':>{cw}}")
        log(f"  {d:>{dw}} {row}")

    # 同一張表，只算「藥物出現在 claims」的命中 —— 訊號強度差很多
    log(f"\n  其中藥物出現在 claims 的：")
    log(f"  {'':>{dw}} " + "".join(f"{i:>{cw}}" for i in
                                   range(1, len(indications) + 1)))
    for d in drug_cids:
        row = ""
        for ind in indications:
            r = cell[(d, ind)]
            row += (f"{r.get('n_claims', 0):>{cw},}"
                    if r["verdict"] != "unresolved" else f"{'·':>{cw}}")
        log(f"  {d:>{dw}} {row}")

    # diff vs ground truth
    diff = {}
    if truth:
        section("diff vs GPSS ground truth")
        tot_new = tot_lost = tot_both = 0
        for r in results:
            key = (r["drug"], r["indication"].lower())
            t = truth.get(key)
            if t is None:
                continue
            got = set(r["patents"])
            both, new, lost = got & t, got - t, t - got
            tot_both += len(both); tot_new += len(new); tot_lost += len(lost)
            if new or lost:
                log(f"\n    {r['drug']} × {r['indication']}")
                log(f"      共同 {len(both)} · 新增 {len(new)} · GPSS有但這裡沒有 {len(lost)}")
                if new:
                    log(f"      新增: {sorted(new)[:8]}")
                if lost:
                    log(f"      漏失: {sorted(lost)[:8]}   ← 要逐筆查原因")
            diff[f"{r['drug']}|{r['indication']}"] = {
                "both": sorted(both), "new": sorted(new), "lost": sorted(lost)}
        log(f"\n    合計  共同 {tot_both} · 新增 {tot_new} · 漏失 {tot_lost}")
        if tot_lost:
            log("    ⚠ 漏失不等於資料源比較差。先分類：非小分子／SureChEMBL 未抽到結構／"
                "疾病詞未標註／US-CN-EP 以外管轄")

    # ★ 過濾訊號掃描（全部用 ground truth 校準）
    #
    # 第一版只掃 compounds-per-patent 單軸，實測被 ground truth 否決：
    #   US9415051B1 30 個化合物、US20090191183A1 823 個、US20150224078A1 508 個
    #   → 砍在 ≤200 會殺掉 2/3 真陽性，而 ≤1000 只砍掉 18.6%，等於沒過濾。
    #   假設崩塌處：合法 prior art 本身就住在大量列舉的專利裡（藥廠寫廣泛
    #   method claim 必然列舉大量化合物），列舉 ≠ 雜訊。
    #
    # 所以改掃所有可得訊號，讓資料決定哪一軸有效：
    #   in_claims   藥物落在 claims（法律上有效力的部分）
    #   dis_count   疾病提及次數（biomedical_locations.count）
    #   same_field  藥物與疾病出現在同一欄位
    #   n_desc      每篇 description 化合物數（保留作對照）
    hit_pids = sorted({r_["pid"] for r in results for r_ in r.get("rows", [])})
    cap_report = {}
    if hit_pids:
        section("★ 過濾訊號掃描（用 ground truth 校準）")
        log(f"    命中專利 {len(hit_pids):,} 篇")

        n_desc = {}
        if caps and any(c > 0 for c in caps):
            pid_list = ",".join(map(str, hit_pids))
            cnt = timed(con, "每篇 description 化合物數", f"""
                SELECT patent_id, count(DISTINCT compound_id)
                FROM pcm WHERE field_id = 1 AND patent_id IN ({pid_list})
                GROUP BY patent_id
            """)
            n_desc = {int(a): int(b) for a, b in (cnt or [])}

        # 真陽性的各項訊號值 —— 任何門檻都不能低於這些
        tp_rows = {}
        for r in results:
            for x in r.get("rows", []):
                tp_rows.setdefault(x["pn"], x)
        log(f"\n    已知真陽性的訊號值：")
        log(f"      {'專利':<18}{'claims':>8}{'疾病提及':>10}{'同欄位':>8}"
            f"{'desc 化合物':>12}")
        tp_present = []
        for _d, _i, pn in KNOWN_TRUE_POSITIVES:
            want = canon_pn(pn)
            x = tp_rows.get(want)
            if x is None:
                log(f"      · {want:<16} 本次未命中，無法校準")
                continue
            tp_present.append(want)
            log(f"      ✓ {want:<16}{x['in_claims']:>8}{x['dis_count']:>10}"
                f"{x['same_field']:>8}{n_desc.get(x['pid'], 0):>12}")
        tp_found = len(tp_present)
        if not tp_found:
            log("    ✗ 沒有任何真陽性命中，無法校準門檻")

        # 過濾器定義：(名稱, 判斷函式)
        FILTERS = [
            ("無過濾", lambda x: True),
            ("藥物在 claims", lambda x: x["in_claims"] == 1),
            ("疾病提及 ≥ 3", lambda x: x["dis_count"] >= 3),
            ("疾病提及 ≥ 10", lambda x: x["dis_count"] >= 10),
            ("疾病提及 ≥ 30", lambda x: x["dis_count"] >= 30),
            ("同欄位共現", lambda x: x["same_field"] == 1),
            ("claims + 疾病 ≥ 3",
             lambda x: x["in_claims"] == 1 and x["dis_count"] >= 3),
            ("claims + 疾病 ≥ 10",
             lambda x: x["in_claims"] == 1 and x["dis_count"] >= 10),
            ("claims + 同欄位",
             lambda x: x["in_claims"] == 1 and x["same_field"] == 1),
            # GPSS 等效：兩邊都限制在 TI/AB/CL，這才是同範圍比較
            ("GPSS 等效 (TI/AB/CL)",
             lambda x: x["drug_tiabcl"] == 1 and x["dis_tiabcl"] == 1),
            ("GPSS 等效 + 疾病 ≥ 3",
             lambda x: x["drug_tiabcl"] == 1 and x["dis_tiabcl"] == 1
                       and x["dis_count"] >= 3),
            # ★ description-only 發掘模式
            #
            # 實測 bromocriptine × SMA：GPSS 等效只保留 16/1,825 = 0.9%，
            # 而且按定義排除所有 description-only 命中 —— 那正是這個資料源
            # 相對 EPO/GPSS 的全部增量價值。
            # 兩個目標互斥，所以是兩種檢索模式各有過濾器，不是一個。
            # 這組條件是 US20230293730A1 的特徵：疾病在 TI/AB/CL 且被提 52 次
            # （這篇就是在講 SMA），藥只出現在說明書。
            ("疾病是主題+藥僅說明書",
             lambda x: x["dis_tiabcl"] == 1 and x["drug_tiabcl"] == 0
                       and x["drug_field"] == 1),
            ("同上 + 疾病 ≥ 10",
             lambda x: x["dis_tiabcl"] == 1 and x["drug_tiabcl"] == 0
                       and x["drug_field"] == 1 and x["dis_count"] >= 10),
            ("同上 + 疾病 ≥ 30",
             lambda x: x["dis_tiabcl"] == 1 and x["drug_tiabcl"] == 0
                       and x["drug_field"] == 1 and x["dis_count"] >= 30),
        ]
        for c in caps:
            if c > 0:
                FILTERS.append((f"desc 化合物 ≤ {c}",
                                lambda x, c=c: n_desc.get(x["pid"], 0) <= c))

        total = sum(len(r.get("rows", [])) for r in results)
        log(f"\n    {'過濾器':<20}{'保留':>10}{'保留率':>8}"
            f"{'真陽性':>9}{'truth 共同':>11}{'truth 漏失':>11}")
        for label, fn in FILTERS:
            kept = set()
            for r in results:
                for x in r.get("rows", []):
                    if fn(x):
                        kept.add((r["drug"], r["indication"].lower(), x["pn"]))
            all_pn = {pn for _d, _i, pn in kept}
            tp_kept = sum(1 for pn in tp_present if pn in all_pn)
            both = lost = 0
            if truth:
                for (d, i), t in truth.items():
                    got = {pn for dd, ii, pn in kept if dd == d and ii == i}
                    both += len(got & t)
                    lost += len(t - got)
            rate = 100 * len(kept) / total if total else 0
            mark = "" if tp_kept == tp_found else "  ← 殺到真陽性"
            log(f"    {label:<20}{len(kept):>10,}{rate:>7.1f}%"
                f"{tp_kept:>7}/{tp_found:<2}{both:>11}{lost:>11}{mark}")
            cap_report[label] = {"kept": len(kept), "rate": rate,
                                 "tp_kept": tp_kept, "tp_found": tp_found,
                                 "truth_both": both, "truth_lost": lost}
        log(f"\n    → 挑「真陽性仍是滿分、保留率最低」的過濾器。")
        log(f"      有 truth 時以 truth 漏失為準，真陽性只有 3 筆樣本太小。")

    # exit criteria 檢查
    section("Exit criteria")
    all_pns = {p for r in results for p in r["patents"]}
    # ⚠ 只檢查本次查詢範圍內的案例。實測跑單格 Bug Y 時，三個 IPF 真陽性
    #   因為不在 --drug-map 裡而全顯示 ✗，看起來像回歸失敗，實際只是不在範圍。
    #   「沒查」與「查了沒中」必須分開，跟矩陣的 · vs 0 同一個道理。
    in_scope = {(d, i.lower()) for d in drug_cids for i in indications}
    n_checked = 0
    for drug, ind, pn in KNOWN_TRUE_POSITIVES:
        want = canon_pn(pn)
        if (drug, ind.lower()) not in in_scope:
            log(f"    · {drug} × {ind} → {want}   本次未查（不在 "
                f"--drug-map / --indications 範圍）")
            continue
        n_checked += 1
        hit = want in all_pns
        log(f"    {'✓' if hit else '✗'} {drug} × {ind} → {want}")
    if not n_checked:
        log(f"    （本次沒有任何 KNOWN_TRUE_POSITIVES 在查詢範圍內）")
    log()
    bugy_want = canon_pn(BUG_Y_CASE["expect_patent"])
    bugy_key = (BUG_Y_CASE["drug"], BUG_Y_CASE["indication"])
    bugy_cell = cell.get(bugy_key)
    if bugy_cell is None:
        log(f"    · Bug Y case 不在本次矩陣範圍（{bugy_key}），phase 3 單獨測")
    else:
        hit = bugy_want in set(bugy_cell["patents"])
        log(f"    {'✓' if hit else '✗'} Bug Y: {BUG_Y_CASE['drug']} × "
            f"{BUG_Y_CASE['indication']} → {bugy_want}")
        if hit:
            log(f"        by_field = {bugy_cell['by_field']}")
            log("        若 drug_field=1(Description)，Bug Y 在此資料源可解，"
                "GPSS_handoff L156 與 design_data_source_selection 都要改")

    payload = {"probe": "surechembl_bulk_phase2",
               "ts": datetime.now(timezone.utc).isoformat(),
               "data_dir": str(data_dir),
               "indications": indications,
               "drug_compound_ids": {k: v for k, v in drug_cids.items()},
               "indication_entity_ids": ind_eids,
               "indication_match_mode": ind_mode,
               "results": results, "diff": diff,
               "cap_sweep": cap_report,
               "family_expansion": cap_report_family}
    if out_path:
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        log(f"\n  artifact → {out_path}")
    return payload


# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="SureChEMBL bulk parquet probe (phases 0-2)")
    ap.add_argument("--phase", type=int, choices=[0, 1, 2])
    ap.add_argument("--inspect-patent", default=None, metavar="PN",
                    help="檢視單篇專利的完整標註，判定沒命中的真正原因")
    ap.add_argument("--inspect-drug-key", default=None, metavar="SKELETON",
                    help="--inspect-patent 時附帶檢查此藥骨架是否標註於該篇")
    ap.add_argument("--find-mw", type=float, default=None, metavar="MW",
                    help="不靠名稱反查 compounds：分子量")
    ap.add_argument("--find-mw-tol", type=float, default=0.5,
                    help="--find-mw 的容差，預設 ±0.5")
    ap.add_argument("--find-smiles", default=None, metavar="SUBSTR",
                    help="不靠名稱反查 compounds：SMILES 含此片段（如 Br）")
    ap.add_argument("--find-inchikey", default=None, metavar="PREFIX",
                    help="不靠名稱反查 compounds：InChIKey 前綴")
    ap.add_argument("--list-entities", default=None, metavar="KEYWORDS",
                    help="查 biomedical 字典有哪些實體符合關鍵詞（附專利數）。"
                         "查新適應症前先跑這個，避免拼法不符被誤判成查無")
    ap.add_argument("--base-url", default=BULK_URL)
    ap.add_argument("--data-dir", type=Path, default=Path("./sc_bulk"))
    ap.add_argument("--download", action="store_true",
                    help="phase 0：清單後直接下載")
    ap.add_argument("--release", default="latest",
                    help="phase 0：release 目錄 YYYY-MM-DD，預設最新版")
    ap.add_argument("--skip-files", default="fpsim2",
                    help="phase 0 下載時略過檔名含這些字串的檔（逗號分隔）。"
                         "預設略過 fpsim2_fingerprints.h5（1.3GB，本 probe 用不到）。"
                         "設空字串則全下")
    ap.add_argument("--drug-map", type=Path,
                    help="phase 2：TSV  drug_name<TAB>inchikey[,inchikey...]")
    ap.add_argument("--indications", default="",
                    help="phase 2：逗號分隔")
    ap.add_argument("--truth", type=Path,
                    help="phase 2：既有 GPSS 矩陣 xlsx/csv")
    ap.add_argument("--truth-cols", default=None,
                    help="phase 2：明確指定 truth 的三個欄名，逗號分隔，"
                         "順序為 drug,indication,patent")
    ap.add_argument("--truth-header", type=int, default=None,
                    help="phase 2：truth 的表頭列索引（0 起）。"
                         "預設自動掃描 0-6 列")
    ap.add_argument("--expand-family", action="store_true",
                    help="phase 2：把命中專利展開為 DOCDB simple family。"
                         "用於取得無化合物標註因而被 bulk 排除的家族成員")
    ap.add_argument("--exclude-mesh", default=None, metavar="ID,ID",
                    help="phase 2：排除指定 resolved_form 的實體。"
                         "例：查 SMA 要排除 D055534（Kennedy disease）")
    ap.add_argument("--entity-type", default="Disease",
                    help="--list-entities 的 type 過濾，預設 Disease。"
                         "設空字串則不限")
    ap.add_argument("--dump-variants", default=None, metavar="DRUG",
                    help="phase 2：倒出某個藥骨架展開後的所有 compound_id、"
                         "InChIKey、MW、專利數、SMILES，用來判斷展開是否合理")
    ap.add_argument("--exact-keys", action="store_true",
                    help="phase 2 對照組：只用 27 碼完整 InChIKey，"
                         "不做骨架前綴展開。用來量骨架比對放大了多少")
    ap.add_argument("--caps", default="0,1000,200,50,10",
                    help="phase 2：compounds-per-patent 門檻掃描值，"
                         "0 = 無上限。用 ground truth 校準門檻")
    ap.add_argument("--out", type=Path, help="artifact JSON 路徑")
    ap.add_argument("--sample", type=float, default=1.0,
                    help="phase 1 重聚合的 patent 抽樣百分比，預設 1.0；"
                         "100 = 掃全表（15-35 億列，很可能 OOM）")
    ap.add_argument("--mem-limit", default=None,
                    help="DuckDB memory_limit，如 24GB。預設讓 DuckDB 自訂")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--approx", action="store_true",
                    help="field_id 分佈改用 approx_count_distinct。"
                         "實測誤差可達 32%%，只在精確版跑不動時用")
    ap.add_argument("--remote", nargs="?", const="latest", default=None,
                    metavar="YYYY-MM-DD",
                    help="phase 1：不下載，直接讀 FTP 上的 parquet。"
                         "Colab 磁碟是暫時的時適用。phase 2 不建議")
    ap.add_argument("--tmp-dir", type=Path, default=Path("./duckdb_tmp"),
                    help="DuckDB spill 目錄。必須在有數十 GB 空間的磁碟上")
    args = ap.parse_args()

    log("=" * 70)
    mode = (f"phase {args.phase}" if args.phase is not None
            else f"list-entities {args.list_entities!r}" if args.list_entities
            else f"inspect-patent {args.inspect_patent}" if args.inspect_patent
            else "find-compound")
    log(f"  SureChEMBL bulk probe — {mode}")
    log(f"  {datetime.now(timezone.utc).isoformat()}")
    log("=" * 70)

    if args.phase == 0:
        skip = [x.strip() for x in args.skip_files.split(",") if x.strip()]
        r = phase0(args.base_url, args.release, args.download, args.data_dir, skip)
        if args.out:
            args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        return 0 if r.get("ok") else 1

    if not args.remote and not args.data_dir.exists():
        log(f"✗ --data-dir 不存在: {args.data_dir}（先跑 phase 0 --download）")
        return 1

    remote_url = None
    if args.remote:
        chosen, releases = resolve_release(args.base_url, args.remote)
        remote_url = args.base_url.rstrip("/") + "/" + chosen
        log(f"  remote release: {chosen}（共 {len(releases)} 版）")

    con, httpfs_ok, httpfs_err = connect_duck(
        args.tmp_dir, args.mem_limit, args.threads)

    if args.inspect_patent:
        if not args.data_dir.exists():
            log(f"✗ --data-dir 不存在: {args.data_dir}")
            return 1
        r = inspect_patent(args.data_dir, con, args.inspect_patent,
                           args.inspect_drug_key)
        if args.out:
            args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2,
                                           default=str), encoding="utf-8")
            log(f"\n  artifact → {args.out}")
        return 0

    if args.find_mw is not None or args.find_smiles or args.find_inchikey:
        if not args.data_dir.exists():
            log(f"✗ --data-dir 不存在: {args.data_dir}")
            return 1
        r = find_compound(args.data_dir, con, args.find_mw, args.find_mw_tol,
                          args.find_smiles, args.find_inchikey)
        if args.out:
            args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2,
                                           default=str), encoding="utf-8")
        return 0

    if args.list_entities:
        if not args.data_dir.exists():
            log(f"✗ --data-dir 不存在: {args.data_dir}")
            return 1
        r = list_entities(args.data_dir, con, args.list_entities,
                          entity_type=args.entity_type or None)
        if args.out:
            args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2,
                                           default=str), encoding="utf-8")
            log(f"\n  artifact → {args.out}")
        return 0

    if args.phase is None:
        log("✗ 需要 --phase 0/1/2、--list-entities 或 --find-mw/--find-smiles")
        return 1

    if remote_url and not httpfs_ok:
        log(f"\n✗ --remote 需要 DuckDB httpfs extension，載入失敗：")
        log(f"    {httpfs_err}")
        log("  受限網路擋掉 extensions.duckdb.org 就會這樣。改用："
            "\n    phase 0 --download 落地後再跑 phase 1（不加 --remote）")
        return 1

    if args.phase == 1:
        r = phase1(args.data_dir, con, args.sample, remote_url, args.approx)
        if args.out:
            args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2,
                                           default=str), encoding="utf-8")
            log(f"\n  artifact → {args.out}")
        return 0

    # phase 2
    if not args.drug_map:
        log("✗ phase 2 需要 --drug-map（compounds 表沒有名稱欄位，見檔頭陷阱 1）")
        return 1
    dm = load_drug_map(args.drug_map)
    inds = [i.strip() for i in args.indications.split(",") if i.strip()]
    if not inds:
        log("✗ phase 2 需要 --indications")
        return 1
    truth = load_truth(args.truth, args.truth_cols,
                       args.truth_header) if args.truth else {}
    caps = [int(c) for c in args.caps.split(",") if c.strip()]
    excl = {x.strip() for x in (args.exclude_mesh or "").split(",") if x.strip()}
    phase2(args.data_dir, con, dm, inds, truth, args.out, caps,
           args.exact_keys, args.dump_variants, excl, args.expand_family)
    return 0


if __name__ == "__main__":
    sys.exit(main())
