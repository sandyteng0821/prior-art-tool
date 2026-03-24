# modules/query_builder.py
# Module 1：根據 config 產生 EPO CQL 搜尋字串
#
# EPO search 支援欄位：ti= ta= ab= pa= in= ic= pd=
# ta= = 標題 + 摘要（比 ti= 廣，不漏掉標題沒有關鍵字的專利）
# 注意：EPO 404 = 查無結果（不是語法錯誤）

from config import (
    DRUG_ALIASES, MECHANISMS, FORMULATIONS, INDICATIONS,
    SEARCH_ONLY_GRANTED, SEARCH_YEAR_RANGE,
)


def _quote(term: str) -> str:
    return f'"{term}"' if " " in term else term


def _add_filters(query: str) -> str:
    """根據 config 加上 granted / 年份過濾。"""
    if SEARCH_ONLY_GRANTED:
        query += " AND (pn=EP OR pn=US)"
    if SEARCH_YEAR_RANGE:
        query += f' AND pd within "{SEARCH_YEAR_RANGE}"'
    return query


def _add_filters_epb(query: str) -> str:
    """EPB 專用：只搜 EP granted。"""
    result = query + " AND pn=EPB"
    if SEARCH_YEAR_RANGE:
        result += f' AND pd within "{SEARCH_YEAR_RANGE}"'
    return result


def build_queries() -> list[str]:
    """
    Strategy A：ta=藥物名 × EP/US（最廣，127 筆）
    Strategy D：ta=藥物名 × EPB（claims/examples 最完整，23 筆）
    Strategy F：cognitive impairment × PDE4（確保抓到 US10357486B2，12 筆）
    Strategy G：ta=spinocerebellar（SCA 相關所有專利，200 筆）
    """
    queries = []

    # ── Strategy A：藥物名 × EP/US ────────────────────────────────────────────
    for drug in DRUG_ALIASES[:1]:   # 只用 Roflumilast，其他別名 EPO 查無結果
        queries.append(_add_filters(f"ta={_quote(drug)}"))

    # ── Strategy D：藥物名 × EPB ──────────────────────────────────────────────
    for drug in DRUG_ALIASES[:1]:
        queries.append(_add_filters_epb(f"ta={_quote(drug)}"))

    # ── Strategy F：cognitive impairment × PDE4 ───────────────────────────────
    queries.append(_add_filters('ta="cognitive impairment" AND ta="PDE4"'))

    # ── Strategy G：SCA 直接搜 ────────────────────────────────────────────────
    queries.append(_add_filters('ta=spinocerebellar'))

    return queries


if __name__ == "__main__":
    for i, q in enumerate(build_queries(), 1):
        print(f"Query {i}: {q}")
