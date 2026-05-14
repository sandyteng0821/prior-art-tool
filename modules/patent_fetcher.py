# modules/patent_fetcher.py
# Module 2：透過 EPO OPS API 抓取專利資料
#
# 查詢優先順序：
#   1. patent_store (本地 SQLite DB) ← 優先，不打 API
#   2. diskcache (短期 API 快取)
#   3. EPO OPS API ← 最後才打
#
# 新增：_fetch_description() 抓全文，_parse_examples() 切出 Examples 區塊
# 每筆抓完後自動 upsert 進 patent_store

import os
import re
import json
import time
import diskcache
import epo_ops
import xmltodict
from dotenv import load_dotenv
from config import FETCH_SIZE, CLAIMS_MAX_CHARS, DRUG_ALIASES
from modules.patent_store import get_by_id, upsert_patent, log_search, mark_family_fetched, get_family_members
from config import TARGET_PRODUCT

load_dotenv()

# ── EPO client 初始化 ─────────────────────────────────────────────────────────
client = epo_ops.Client(
    key=os.getenv("EPO_CONSUMER_KEY"),
    secret=os.getenv("EPO_CONSUMER_SECRET"),
    accept_type="json",
)

# ── 短期磁碟快取（避免同一 session 重複打 API） ───────────────────────────────
cache = diskcache.Cache("cache/epo")

# 專案名稱（給 search_log 用，從 TARGET_PRODUCT 簡化）
_PROJECT = TARGET_PRODUCT[:30].replace(" ", "_")


def fetch_patents(cql_query: str, size: int = FETCH_SIZE) -> list[dict]:
    """
    用 CQL 字串搜尋 EPO OPS，回傳標準化的 dict list。
    每個 dict 包含：patent_id, title, abstract, claims,
                   examples_extracted, status, year
    """
    cache_key = f"search::{cql_query}::{size}"
    if cache_key in cache:
        return cache[cache_key]

    all_refs = []
    batch_size = 100  # EPO 單次上限
    fetched = 0

    while fetched < size:
        begin = fetched + 1
        end   = min(fetched + batch_size, size)
        try:
            response = client.published_data_search(
                cql=cql_query,
                range_begin=begin,
                range_end=end,
            )
            data = response.json()
        except Exception as e:
            if "404" in str(e):
                if fetched == 0:
                    print(f"  -> 查無結果（EPO 404 = no results）")
                break
            print(f"  [fetch_patents] Search failed: {e}")
            break

        try:
            refs = (
                data["ops:world-patent-data"]
                    ["ops:biblio-search"]
                    ["ops:search-result"]
                    ["ops:publication-reference"]
            )
            if isinstance(refs, dict):
                refs = [refs]
            all_refs.extend(refs)
            fetched += len(refs)
            if len(refs) < batch_size:
                break  # 已經到最後一頁
        except KeyError:
            break

    refs = all_refs
    if not refs:
        return []

    results = []
    seen_in_fetch = set()
    for ref in refs:
        doc_id    = ref.get("document-id", {})
        country   = doc_id.get("country", {}).get("$", "")
        number    = doc_id.get("doc-number", {}).get("$", "")
        kind      = doc_id.get("kind", {}).get("$", "")
        year      = doc_id.get("date", {}).get("$", "")[:4]
        patent_id = f"{country}{number}{kind}".strip()

        if not patent_id:
            continue

        patent = _get_or_fetch(patent_id, year)
        if patent:
            log_search(_PROJECT, cql_query, patent_id)
            results.append(patent)
            seen_in_fetch.add(patent_id)
            # 把 family members 也加進去
            for member in patent.pop("_family_members", []):
                if member["patent_id"] not in seen_in_fetch:
                    results.append(member)
                    seen_in_fetch.add(member["patent_id"])

        time.sleep(0.5)  # 避免超過 EPO rate limit

    cache.set(cache_key, results, expire=60 * 60 * 24 * 7)  # 快取 7 天
    return results


# ── Patent ID 解析 ────────────────────────────────────────────────────────────

def _parse_patent_id(patent_id: str) -> tuple[str, str]:
    """
    把 'US2024335435A1' 拆成 ('US2024335435', 'A1')。
    EPO Epodoc 需要號碼和 kind code 分開傳。
    """
    m = re.match(r'^([A-Z]{2}\d+)([A-Z]\d*)$', patent_id)
    if m:
        return m.group(1), m.group(2)
    # fallback：沒有 kind code
    return patent_id, ""


# ── Formulation snippet 切句 ─────────────────────────────────────────────────

def _extract_formulation_snippets(text: str, drug_aliases: list[str]) -> list[str]:
    """
    從 text 中切出 formulation 相關句子。
    條件：句子同時包含 drug alias 和劑型關鍵字。
    """
    KEYWORDS = [
        "composition", "formulation", "comprises",
        "excipient", "tablet", "capsule", "carrier"
    ]

    sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text)
    snippets = []

    for s in sentences:
        s_lower = s.lower()
        has_drug = any(alias.lower() in s_lower for alias in drug_aliases)
        has_keyword = any(k in s_lower for k in KEYWORDS)
        if has_drug and has_keyword:
            snippets.append(s.strip())

    return snippets[:20]


def _collect_snippets(claims: str, description: str) -> str:
    """
    從 claims（優先）和 description 切出 formulation snippets，
    回傳 JSON string（hard cap 30 句）。
    輸入為空字串時也能正確處理。
    """
    snippets: list[str] = []
    if claims:
        snippets += _extract_formulation_snippets(claims, DRUG_ALIASES)
    if description:
        snippets += _extract_formulation_snippets(description, DRUG_ALIASES)
    snippets = snippets[:30]  # hard cap
    return json.dumps(snippets)


def _get_or_fetch(patent_id: str, year: str = "") -> dict | None:
    """
    查詢優先順序：本地 DB → EPO API。
    抓完後自動存入本地 DB。
    """
    # ── 1. 優先查本地 DB ──────────────────────────────────────────────────────
    stored = get_by_id(patent_id)
    if stored:
        print(f"  [DB hit] {patent_id}")
        number, kind = _parse_patent_id(patent_id)
        if kind in ("A1", "A2"):
            if not stored.get("family_fetched"):
                # 還沒展開過，打 family API
                family_members = _fetch_and_store_family(patent_id, stored.get("year", ""))
                stored["_family_members"] = family_members
            else:
                # 已展開過，直接從 DB 拿
                print(f"  [family DB hit] {patent_id}")
                stored["_family_members"] = get_family_members(patent_id)
        return stored

    # ── 2. 從 EPO API 抓取 ───────────────────────────────────────────────────
    print(f"  [EPO fetch] {patent_id}")
    abstract    = _fetch_abstract(patent_id)
    claims      = _fetch_claims(patent_id)
    title       = _fetch_title(patent_id)
    description = _fetch_description(patent_id)
    examples    = _parse_examples(description)

    claims_str      = claims if isinstance(claims, str) else ""
    description_str = description if isinstance(description, str) else ""

    patent = {
        "patent_id":            patent_id,
        "title":                title if isinstance(title, str) else "",
        "abstract":             abstract if isinstance(abstract, str) else "",
        "claims":               claims_str[:CLAIMS_MAX_CHARS],
        "examples_extracted":   examples if isinstance(examples, str) else "",
        "formulation_snippets": _collect_snippets(claims_str, description_str),
        "status":               "Unknown",
        "year":                 year,
        "source":               "epo",
    }

    # ── 3. 存入本地 DB ────────────────────────────────────────────────────────
    upsert_patent(patent)

    # ── 4. 若為 A1/A2，順便嘗試抓對應的 B1（granted 版本）────────────────────
    number, kind = _parse_patent_id(patent_id)
    if kind in ("A1", "A2"):
        b1_id = f"{number}B1"
        if not get_by_id(b1_id):
            try:
                b1_title       = _fetch_title(b1_id)
                b1_claims      = _fetch_claims(b1_id)
                b1_abstract    = _fetch_abstract(b1_id)
                b1_description = _fetch_description(b1_id)
                b1_examples    = _parse_examples(b1_description)
                if b1_title or b1_claims:
                    b1_claims_str      = b1_claims if isinstance(b1_claims, str) else ""
                    b1_description_str = b1_description if isinstance(b1_description, str) else ""
                    upsert_patent({
                        "patent_id":            b1_id,
                        "title":                b1_title if isinstance(b1_title, str) else "",
                        "abstract":             b1_abstract if isinstance(b1_abstract, str) else "",
                        "claims":               b1_claims_str[:CLAIMS_MAX_CHARS],
                        "examples_extracted":   b1_examples if isinstance(b1_examples, str) else "",
                        "formulation_snippets": _collect_snippets(b1_claims_str, b1_description_str),
                        "status":               "Active",
                        "year":                 year,
                        "source":               "epo",
                    })
                    print(f"  [B1 auto-fetch] {b1_id}")
            except Exception:
                pass
        # ── 5. 展開 patent family（新增）─────────────────────────────────────
        # _fetch_and_store_family(patent_id, year)
        # family 展開，把結果存起來供 fetch_patents 使用
        family_members = _fetch_and_store_family(patent_id, year) 
        # 把 family members 附在 patent 上回傳
        patent["_family_members"] = family_members

    return patent


def _fetch_and_store_family(patent_id: str, year: str = "") -> None:
    """
    呼叫 EPO family API，把所有 family members 存進 DB。
    只在 A1/A2 時呼叫，避免無限遞迴。
    """
    fetched_members = []
    number, _ = _parse_patent_id(patent_id)
    try:
        resp = client.family(
            "publication",
            epo_ops.models.Epodoc(number),  # 不帶 kind code
            None,
            ["biblio"],
        )
        data = resp.json()
        members = (
            data["ops:world-patent-data"]
                ["ops:patent-family"]
                ["ops:family-member"]
        )
        if isinstance(members, dict):
            members = [members]

        for member in members:
            pub_refs = member.get("publication-reference", {})
            doc_ids  = pub_refs.get("document-id", [])
            if isinstance(doc_ids, dict):
                doc_ids = [doc_ids]

            for doc_id in doc_ids:
                if doc_id.get("@document-id-type") != "docdb":
                    continue
                country = doc_id.get("country", {}).get("$", "")
                num     = doc_id.get("doc-number", {}).get("$", "")
                kind    = doc_id.get("kind", {}).get("$", "")
                if not (country and num and kind):
                    continue

                member_id = f"{country}{num}{kind}"

                # 已在 DB 就跳過，避免重複抓
                existing = get_by_id(member_id)
                if existing:
                    # 補 family_of（如果還沒有的話）
                    if not existing.get("family_of"):
                        upsert_patent({**existing, "family_of": patent_id})
                        existing["family_of"] = patent_id
                    fetched_members.append(existing)
                    continue

                # 只抓 granted 版本（B1/B2），A1 已經有了
                if kind not in ("B1", "B2"):
                    continue

                print(f"  [family member] {member_id}")
                title       = _fetch_title(member_id)
                abstract    = _fetch_abstract(member_id)
                claims      = _fetch_claims(member_id)
                description = _fetch_description(member_id)
                examples    = _parse_examples(description)

                if title or abstract:
                    claims_str      = claims if isinstance(claims, str) else ""
                    description_str = description if isinstance(description, str) else ""
                    patent_dict = {
                        "patent_id":            member_id,
                        "title":                title if isinstance(title, str) else "",
                        "abstract":             abstract if isinstance(abstract, str) else "",
                        "claims":               claims_str[:CLAIMS_MAX_CHARS],
                        "examples_extracted":   examples if isinstance(examples, str) else "",
                        "formulation_snippets": _collect_snippets(claims_str, description_str),
                        "status":               "Active",
                        "year":                 year,
                        "source":               "epo",
                        "family_of":            patent_id,
                    }
                    upsert_patent(patent_dict)
                    fetched_members.append(patent_dict)    
                time.sleep(0.5)

    except Exception as e:
        print(f"  [family API] {patent_id} failed: {e}")
    mark_family_fetched(patent_id)
    return fetched_members


# ── Examples 解析 ─────────────────────────────────────────────────────────────

def _parse_examples(description: str) -> str:
    """
    從 description 全文切出 Examples 區塊。
    專利的 Examples 有幾種常見標題格式，依序嘗試。
    切不到時回傳空字串（不會 crash）。
    """
    if not description:
        return ""

    # 常見的 Examples 起始標題
    start_patterns = [
        r"(?:^|\n)\s*EXAMPLES?\s*\n",
        r"(?:^|\n)\s*EXAMPLE\s+\d+\s*\n",
        r"(?:^|\n)\s*WORKING EXAMPLES?\s*\n",
        r"(?:^|\n)\s*EXPERIMENTAL\s*\n",
        r"(?:^|\n)\s*Example\s+1[\.\:]",
    ]
    # Examples 之後的下一個大區塊（終止條件）
    end_patterns = [
        r"\n\s*CLAIMS?\s*\n",
        r"\n\s*WHAT IS CLAIMED",
        r"\n\s*INDUSTRIAL APPLICABILITY",
        r"\n\s*REFERENCES?\s*\n",
    ]

    text = description

    # 找起始位置
    start_idx = None
    for pat in start_patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            start_idx = m.start()
            break

    if start_idx is None:
        return ""  # 找不到 Examples 區塊

    text_from_examples = text[start_idx:]

    # 找終止位置
    end_idx = len(text_from_examples)
    for pat in end_patterns:
        m = re.search(pat, text_from_examples, re.IGNORECASE | re.MULTILINE)
        if m:
            end_idx = min(end_idx, m.start())

    examples = text_from_examples[:end_idx].strip()

    # 壓縮多餘空白，但保留段落結構
    examples = re.sub(r"\n{3,}", "\n\n", examples)
    examples = re.sub(r"[ \t]+", " ", examples)

    return examples


# ── EPO API 輔助函式 ──────────────────────────────────────────────────────────

def _fetch_description(patent_id: str) -> str:
    """
    抓取 description 全文（供 _parse_examples 使用）。
    結果不存進 diskcache（太大），只走 patent_store 的永久快取。
    """
    try:
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="description",
        )
        try:
            data = resp.json()
            paras = (
                data["ops:world-patent-data"]
                    ["ftxt:fulltext-documents"]
                    ["ftxt:fulltext-document"]
                    ["description"]
                    ["p"]
            )
        except Exception:
            data = xmltodict.parse(resp.text)
            paras = (
                data.get("ops:world-patent-data", {})
                    .get("ftxt:fulltext-documents", {})
                    .get("ftxt:fulltext-document", {})
                    .get("description", {})
                    .get("p", [])
            )

        if isinstance(paras, list):
            result = "\n".join(
                p.get("$", "") if isinstance(p, dict) else str(p)
                for p in paras
            )
        elif isinstance(paras, dict):
            result = paras.get("$", "")
        else:
            result = str(paras)

    except Exception:
        result = ""

    time.sleep(0.3)
    return result


def _fetch_abstract(patent_id: str) -> str:
    cache_key = f"abstract::{patent_id}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="abstract",
        )
        try:
            data  = resp.json()
            doc   = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
            texts = doc.get("abstract", {})
        except Exception:
            data  = xmltodict.parse(resp.text)
            doc   = data.get("ops:world-patent-data", {}).get("exchange-documents", {}).get("exchange-document", {})
            texts = doc.get("abstract", {})

        if isinstance(texts, list):
            for t in texts:
                if isinstance(t, dict) and t.get("@lang", "") == "en":
                    p = t.get("p", {})
                    result = p.get("$", "") if isinstance(p, dict) else str(p)
                    break
            else:
                p = texts[0].get("p", {}) if texts else {}
                result = p.get("$", "") if isinstance(p, dict) else ""
        elif isinstance(texts, dict):
            p = texts.get("p", {})
            result = p.get("$", "") if isinstance(p, dict) else str(p)
        else:
            result = ""

    except Exception:
        result = ""

    cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result


def _fetch_claims(patent_id: str) -> str:
    cache_key = f"claims::{patent_id}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="claims",
        )
        data = resp.json()
        doc = (data["ops:world-patent-data"]
                   ["ftxt:fulltext-documents"]
                   ["ftxt:fulltext-document"])

        # claims 是 list，每個元素是一個語言版本，優先取英文
        claims_list = doc.get("claims", [])
        if isinstance(claims_list, dict):
            claims_list = [claims_list]

        # 找英文版本，找不到就取第一個
        target = None
        for c in claims_list:
            if isinstance(c, dict) and c.get("@lang", "").upper() == "EN":
                target = c
                break
        if target is None and claims_list:
            target = claims_list[0]

        if target is None:
            result = ""
        else:
            claim_items = target.get("claim", {})
            claim_texts = claim_items.get("claim-text", [])
            if isinstance(claim_texts, list):
                result = " ".join(
                    t.get("$", "") if isinstance(t, dict) else str(t)
                    for t in claim_texts
                )
            elif isinstance(claim_texts, dict):
                result = claim_texts.get("$", "")
            else:
                result = str(claim_texts)

    except Exception:
        result = ""

    cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result


def _fetch_title(patent_id: str) -> str:
    cache_key = f"title::{patent_id}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        number, kind = _parse_patent_id(patent_id)
        resp = client.published_data(
            reference_type="publication",
            input=epo_ops.models.Epodoc(number, kind),
            endpoint="biblio",
        )
        try:
            data   = resp.json()
            doc    = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
            titles = doc["bibliographic-data"].get("invention-title", {})
        except Exception:
            data   = xmltodict.parse(resp.text)
            doc    = data.get("ops:world-patent-data", {}).get("exchange-documents", {}).get("exchange-document", {})
            titles = doc.get("bibliographic-data", {}).get("invention-title", {})

        if isinstance(titles, list):
            for t in titles:
                if isinstance(t, dict) and t.get("@lang", "") == "en":
                    result = t.get("$", "")
                    break
            else:
                result = titles[0].get("$", "") if titles else ""
        elif isinstance(titles, dict):
            result = titles.get("$", "")
        else:
            result = str(titles)

    except Exception:
        result = ""

    cache.set(cache_key, result, expire=60 * 60 * 24 * 30)
    time.sleep(0.3)
    return result
