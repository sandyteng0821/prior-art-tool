# modules/patent_store.py
# 本地專利資料庫（SQLite）
# 職責：
#   - 永久儲存抓過的專利（避免重複打 EPO API）
#   - 存 examples_extracted 供後續劑型分析
#   - 提供跨專案查詢介面
#
# DB 位置：cache/patents.db（與 diskcache 放在同一目錄）

import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join("cache", "patents.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs("cache", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建立資料表（若已存在則跳過）。"""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS patents (
                patent_id            TEXT PRIMARY KEY,
                title                TEXT,
                abstract             TEXT,
                claims               TEXT,
                examples_extracted   TEXT,
                formulation_snippets TEXT,
                status               TEXT,
                year                 TEXT,
                source               TEXT,
                fetched_at           TEXT,
                family_fetched       INTEGER DEFAULT 0,
                family_of            TEXT
            );

            CREATE TABLE IF NOT EXISTS search_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project     TEXT,
                query       TEXT,
                patent_id   TEXT,
                searched_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_patents_year
                ON patents(year);
            CREATE INDEX IF NOT EXISTS idx_search_log_project
                ON search_log(project);
        """)

    # Migration：對已存在的 DB 補加欄位
    with _get_conn() as conn:
        for sql in [
            "ALTER TABLE patents ADD COLUMN family_fetched INTEGER DEFAULT 0",
            "ALTER TABLE patents ADD COLUMN family_of TEXT",
            "ALTER TABLE patents ADD COLUMN formulation_snippets TEXT",
            "CREATE INDEX IF NOT EXISTS idx_patents_family_of ON patents(family_of)",
        ]:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 欄位已存在，跳過

        # Migration：補加 index
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_patents_family_of ON patents(family_of)")
        except sqlite3.OperationalError:
            pass


# ── 寫入 ──────────────────────────────────────────────────────────────────────

def upsert_patent(patent: dict) -> None:
    """
    存入或更新一筆專利。
    patent dict 需包含：patent_id（必填），其餘欄位選填。
    """
    init_db()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO patents
                (patent_id, title, abstract, claims,
                 examples_extracted, formulation_snippets,
                 status, year, source, fetched_at,
                 family_fetched, family_of)
            VALUES
                (:patent_id, :title, :abstract, :claims,
                 :examples_extracted, :formulation_snippets,
                 :status, :year, :source, :fetched_at,
                 :family_fetched, :family_of)
            ON CONFLICT(patent_id) DO UPDATE SET
                title                = excluded.title,
                abstract             = excluded.abstract,
                claims               = excluded.claims,
                examples_extracted   = excluded.examples_extracted,
                formulation_snippets = excluded.formulation_snippets,
                status               = excluded.status,
                year                 = excluded.year,
                source               = excluded.source,
                fetched_at           = excluded.fetched_at,
                family_fetched       = CASE
                    WHEN excluded.family_fetched = 1 THEN 1
                    ELSE patents.family_fetched
                END,
                family_of            = COALESCE(excluded.family_of, patents.family_of)
        """, {
            "patent_id":            patent.get("patent_id", ""),
            "title":                patent.get("title", ""),
            "abstract":             patent.get("abstract", ""),
            "claims":               patent.get("claims", ""),
            "examples_extracted":   patent.get("examples_extracted", ""),
            "formulation_snippets": patent.get("formulation_snippets", None),
            "status":               patent.get("status", "Unknown"),
            "year":                 patent.get("year", ""),
            "source":               patent.get("source", "unknown"),
            "fetched_at":           datetime.now().isoformat(),
            "family_fetched":       patent.get("family_fetched", 0),
            "family_of":            patent.get("family_of", None),
        })


def mark_family_fetched(patent_id: str) -> None:
    """標記這筆專利的 family 已經展開過。"""
    init_db()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE patents SET family_fetched = 1 WHERE patent_id = ?",
            (patent_id,)
        )


def get_family_members(patent_id: str) -> list[dict]:
    """取得某筆 A1 的所有 family members（從 DB）。"""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM patents WHERE family_of = ?",
            (patent_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def log_search(project: str, query: str, patent_id: str) -> None:
    """記錄哪個專案的哪個 query 找到了哪筆專利。"""
    init_db()
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO search_log (project, query, patent_id, searched_at)
            VALUES (?, ?, ?, ?)
        """, (project, query, patent_id, datetime.now().isoformat()))


# ── 查詢 ──────────────────────────────────────────────────────────────────────

def get_by_id(patent_id: str) -> dict | None:
    """用 patent_id 取得單筆專利，找不到回傳 None。"""
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patents WHERE patent_id = ?", (patent_id,)
        ).fetchone()
    return dict(row) if row else None


def get_formulation_snippets(patent_id: str) -> list[str]:
    """
    取得某筆專利的 formulation snippets（已解析為 list）。
    找不到專利或欄位為 NULL 時回傳空 list。
    """
    init_db()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT formulation_snippets FROM patents WHERE patent_id = ?",
            (patent_id,)
        ).fetchone()
    if not row:
        return []
    return json.loads(row["formulation_snippets"] or "[]")


def search_examples(keyword: str) -> list[dict]:
    """在 examples_extracted 欄位搜尋關鍵字。"""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT patent_id, title, year,
                   substr(examples_extracted, 1, 300) AS examples_preview
            FROM patents
            WHERE examples_extracted LIKE ?
              AND examples_extracted != ''
            ORDER BY year DESC
        """, (f"%{keyword}%",)).fetchall()
    return [dict(r) for r in rows]


def search_claims(keyword: str) -> list[dict]:
    """在 claims 欄位搜尋關鍵字。"""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT patent_id, title, year,
                   substr(claims, 1, 300) AS claims_preview
            FROM patents
            WHERE claims LIKE ?
            ORDER BY year DESC
        """, (f"%{keyword}%",)).fetchall()
    return [dict(r) for r in rows]


def list_all(limit: int = 100) -> list[dict]:
    """列出所有已存專利（預設最多 100 筆）。"""
    init_db()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT patent_id, title, year, source, fetched_at,
                   CASE WHEN examples_extracted != '' THEN 'yes' ELSE 'no' END
                   AS has_examples,
                   family_fetched,
                   family_of
            FROM patents
            ORDER BY fetched_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    """回傳 DB 統計資訊。"""
    init_db()
    with _get_conn() as conn:
        total          = conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]
        has_ex         = conn.execute(
            "SELECT COUNT(*) FROM patents WHERE examples_extracted != ''"
        ).fetchone()[0]
        family_fetched = conn.execute(
            "SELECT COUNT(*) FROM patents WHERE family_fetched = 1"
        ).fetchone()[0]
        has_family_of  = conn.execute(
            "SELECT COUNT(*) FROM patents WHERE family_of IS NOT NULL"
        ).fetchone()[0]
        by_source      = conn.execute(
            "SELECT source, COUNT(*) as n FROM patents GROUP BY source"
        ).fetchall()
    return {
        "total_patents":        total,
        "with_examples":        has_ex,
        "without_examples":     total - has_ex,
        "family_fetched":       family_fetched,
        "family_members_in_db": has_family_of,
        "by_source":            {r["source"]: r["n"] for r in by_source},
    }
