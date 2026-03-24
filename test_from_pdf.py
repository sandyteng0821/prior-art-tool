# test_from_pdf.py
# 給 Google Patents URL 或直接給 PDF URL，下載 PDF 後解析再丟進 llm_analyzer
#
# 執行方式：
#   python test_from_pdf.py
#   python test_from_pdf.py https://patents.google.com/patent/US10357486B2/en
#   python test_from_pdf.py https://patentimages.storage.googleapis.com/b4/14/23/8e80c19e6daf91/US10357486.pdf
#
# 安裝需求（在原有 requirements 基礎上加）：
#   pip install pdfplumber

import sys
import os
import re
import io
import time
import requests
import pdfplumber

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))

from modules.llm_analyzer  import analyze_patent
from modules.output_writer import save_results, print_summary

# ── 預設測試 URL ──────────────────────────────────────────────────────────────
DEFAULT_URLS = [
    "https://patents.google.com/patent/US10357486B2/en",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1：從 Google Patents HTML 頁面找出 PDF 直連 URL
# ─────────────────────────────────────────────────────────────────────────────

def resolve_pdf_url(input_url: str) -> tuple[str, str]:
    """
    輸入 Google Patents URL 或 PDF 直連 URL。
    回傳 (pdf_url, patent_id)。
    """
    patent_id = _extract_patent_id(input_url)

    # 已經是 PDF 直連
    if input_url.endswith(".pdf") or "patentimages.storage.googleapis.com" in input_url:
        return input_url, patent_id

    # 從 Google Patents HTML 頁面抓 PDF 連結
    print(f"  解析 Google Patents 頁面中...")
    try:
        resp = requests.get(input_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"  [WARN] 無法載入頁面（{e}），改用 ID 推算 PDF URL")
        return _guess_pdf_url(patent_id), patent_id

    # 嘗試從 HTML 找 PDF 直連
    # Google Patents 通常有 <a href="...pdf"> 或 meta download link
    m = re.search(
        r'(https://patentimages\.storage\.googleapis\.com/[^\s"\']+\.pdf)',
        html
    )
    if m:
        pdf_url = m.group(1)
        print(f"  ✓ 找到 PDF URL：{pdf_url}")
        return pdf_url, patent_id

    # fallback：用 patent_id 推算（成功率約 80%）
    print(f"  [WARN] HTML 中找不到 PDF 連結，改用 ID 推算")
    return _guess_pdf_url(patent_id), patent_id


def _extract_patent_id(url: str) -> str:
    # 從 Google Patents URL 取出 patent ID
    m = re.search(r"/patent/([A-Z]{2}\d+[A-Z]\d?)/", url)
    if m:
        return m.group(1)
    # 從 PDF URL 取出
    m = re.search(r"/((?:US|EP|WO|JP|CN)\d+[A-Z]?\d?)\.pdf", url)
    if m:
        return m.group(1)
    return "UNKNOWN"


def _guess_pdf_url(patent_id: str) -> str:
    """
    Google Patents PDF 的儲存路徑有規律但不完全固定。
    這裡用最常見的 US 專利格式推算，其他國家的成功率較低。
    """
    # 最直接的猜測格式（約 60-80% 成功率）
    return f"https://patentimages.storage.googleapis.com/{patent_id}.pdf"


# ─────────────────────────────────────────────────────────────────────────────
# Step 2：下載 PDF 並用 pdfplumber 解析文字
# ─────────────────────────────────────────────────────────────────────────────

def parse_pdf(pdf_url: str) -> dict[str, str]:
    """
    下載 PDF，回傳 {title, abstract, claims, year, full_text}。
    """
    print(f"  下載 PDF：{pdf_url}")
    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
        pdf_bytes = io.BytesIO(resp.content)
        print(f"  ✓ 下載完成（{len(resp.content) // 1024} KB）")
    except Exception as e:
        raise RuntimeError(f"PDF 下載失敗：{e}")

    # pdfplumber 解析
    pages_text = []
    with pdfplumber.open(pdf_bytes) as pdf:
        print(f"  ✓ PDF 共 {len(pdf.pages)} 頁，開始解析...")
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                pages_text.append(text)

    full_text = "\n".join(pages_text)

    if not full_text.strip():
        raise RuntimeError("PDF 解析後無文字，可能是掃描圖片版（需 OCR）")

    return {
        "title":     _parse_title(full_text),
        "abstract":  _parse_abstract(full_text),
        "claims":    _parse_claims(full_text),
        "year":      _parse_year(full_text),
        "full_text": full_text,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step 3：從全文提取各區塊
# 專利 PDF 的結構通常是：Title → Abstract → Description → Claims
# ─────────────────────────────────────────────────────────────────────────────

def _parse_title(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # 專利 PDF 第一頁通常有 "(12) United States Patent" 或類似標題列
    # 真正的發明標題通常在前幾行，全大寫或緊接在 "(54)" 之後
    for i, line in enumerate(lines[:30]):
        if re.match(r"\(54\)", line):
            # (54) 後面接著的就是標題
            title_parts = []
            for j in range(i, min(i + 5, len(lines))):
                part = re.sub(r"^\(\d+\)\s*", "", lines[j]).strip()
                if part and not re.match(r"^\(\d+\)", lines[j + 1] if j + 1 < len(lines) else ""):
                    title_parts.append(part)
                else:
                    title_parts.append(part)
                    break
            return " ".join(title_parts)

    # fallback：取第一行看起來像標題的文字
    for line in lines[:10]:
        if len(line) > 20 and not re.match(r"^\d+$", line):
            return line
    return ""


def _parse_abstract(text: str) -> str:
    # 尋找 ABSTRACT 區塊
    m = re.search(
        r"(?:ABSTRACT|Abstract)\s*\n(.*?)(?=\n(?:DESCRIPTION|BRIEF DESCRIPTION|BACKGROUND|CLAIMS|FIELD OF|1\.))",
        text, re.DOTALL | re.IGNORECASE
    )
    if m:
        abstract = re.sub(r"\s+", " ", m.group(1)).strip()
        return abstract[:2000]

    # fallback：找 (57) 欄位（USPTO 格式）
    m = re.search(r"\(57\).*?ABSTRACT\s*\n(.*?)(?=\n\s*\n)", text, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:2000]

    return ""


def _parse_claims(text: str) -> str:
    # 找 CLAIMS 區塊到文件結尾或下一個大標題
    m = re.search(
        r"(?:^|\n)CLAIMS\s*\n(.*?)(?=\n(?:ABSTRACT|DESCRIPTION|DRAWINGS|$))",
        text, re.DOTALL | re.IGNORECASE
    )
    if m:
        claims = re.sub(r"\s+", " ", m.group(1)).strip()
        return claims[:3000]

    # fallback：找編號開頭的請求項
    m = re.search(r"((?:^|\n)\s*1\.\s+(?:A method|A composition|An apparatus|A pharmaceutical).*)",
                  text, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:3000]

    return ""


def _parse_year(text: str) -> str:
    # 找公告日期，格式通常是 "Date of Patent: Jan. 23, 2019"
    m = re.search(r"Date of Patent[:\s]+\w+\.?\s+\d+,\s+(\d{4})", text)
    if m:
        return m.group(1)
    # fallback：找 4 位數年份
    m = re.search(r"\b(20\d{2}|19\d{2})\b", text)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def process_url(url: str) -> dict | None:
    """單筆 URL 的完整處理流程。"""
    print(f"\n{'─' * 50}")
    print(f"  處理：{url}")

    try:
        pdf_url, patent_id = resolve_pdf_url(url)
        parsed = parse_pdf(pdf_url)
    except Exception as e:
        print(f"  [ERROR] {e}")
        print("  → 請改用 test_without_epo.py 手動貼入文字")
        return None

    patent = {
        "patent_id": patent_id,
        "title":     parsed["title"],
        "abstract":  parsed["abstract"],
        "claims":    parsed["claims"],
        "status":    "Unknown",
        "year":      parsed["year"],
        "source_url": url,
    }

    print(f"  ✓ title    : {patent['title'][:60]}")
    print(f"  ✓ abstract : {len(patent['abstract'])} 字元")
    print(f"  ✓ claims   : {len(patent['claims'])} 字元")
    print(f"  ✓ year     : {patent['year']}")
    return patent


def run_test(urls: list[str]):
    print("=" * 50)
    print("  Prior Art Tool — PDF Parse Test")
    print(f"  測試筆數：{len(urls)}")
    print("=" * 50)

    # 抓取所有 PDF
    patents = []
    for url in urls:
        patent = process_url(url)
        if patent:
            patents.append(patent)
        time.sleep(1)  # 避免太快連打

    if not patents:
        print("\n沒有成功解析任何專利。")
        print("建議改用 test_without_epo.py 手動貼入文字測試 LLM 分析。")
        return

    # LLM 分析
    print(f"\n{'─' * 50}")
    print(f"  成功解析 {len(patents)} 筆，開始 LLM 分析...\n")

    results = []
    for i, patent in enumerate(patents, 1):
        print(f"[{i}/{len(patents)}] {patent['patent_id']} — {patent['title'][:50]}...")
        try:
            result = analyze_patent(patent)
            results.append(result)
            print(f"  → fto_risk    : {result['fto_risk']}")
            print(f"  → reasoning   : {result['reasoning']}")
            print(f"  → gap         : {result['gap_opportunity']}")
            print(f"  → routes      : {result['delivery_routes']}")
            print(f"  → indications : {result['indications']}\n")
        except Exception as e:
            print(f"  [ERROR] LLM 分析失敗：{e}\n")

    if results:
        save_results(results, prefix="test_pdf")
        print_summary(results)


if __name__ == "__main__":
    urls = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_URLS
    run_test(urls)
