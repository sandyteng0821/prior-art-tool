"""
render_jsonl_report — Google Patents JSONL → 單一 HTML 瀏覽報告

給專家看的報告。一頁載入所有專利,可搜尋、可依 jurisdiction/狀態篩選、
claims 和 full_text 預設折疊。純前端,產出單一 .html 檔,瀏覽器直接開,
不需要任何依賴。

Usage:
    python3 tools/render_jsonl_report.py \
        data/global_patents_archive_EB_tiagabine_idlist_20260727.jsonl

    # 自訂輸出檔名 + 標題
    python3 tools/render_jsonl_report.py \
        data/..._tiagabine_...jsonl \
        --output reports/tiagabine_eb_review.html \
        --title "Tiagabine x EB — Patent Review"

    # 只放 clean(不含 dirty)
    python3 tools/render_jsonl_report.py data/....jsonl --clean-only
"""

import argparse
import html
import json
import os
from collections import Counter

DIRTY_PREFIXES = ("Not Found", "Error")
SENTINELS = {"N/A", "n/a", "N/a", "", None}


def is_missing(val):
    return val is None or (isinstance(val, str) and val.strip() in SENTINELS)


def is_dirty(rec):
    title = rec.get("title", "")
    return any(title.startswith(p) for p in DIRTY_PREFIXES)


def esc(val):
    """HTML-escape, converting sentinels to a muted placeholder."""
    if is_missing(val):
        return '<span class="missing">—</span>'
    return html.escape(str(val)).replace("\n", "<br>")


def esc_plain(val, fallback=""):
    if is_missing(val):
        return fallback
    return html.escape(str(val))


def load(path, clean_only):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec["_dirty"] = is_dirty(rec)
            if clean_only and rec["_dirty"]:
                continue
            rows.append(rec)
    return rows


def build_html(rows, title):
    total = len(rows)
    dirty = sum(1 for r in rows if r["_dirty"])
    clean = total - dirty

    jur = Counter()
    for r in rows:
        pid = r.get("requested_id", "") or r.get("formatted_id", "")
        jur[pid[:2].upper()] += 1
    jur_opts = "".join(
        f'<option value="{cc}">{cc} ({n})</option>'
        for cc, n in jur.most_common()
    )

    cards = []
    for i, r in enumerate(rows):
        pid = esc_plain(r.get("requested_id"), f"row_{i}")
        cc = pid[:2].upper()
        dirty_flag = r["_dirty"]
        url = esc_plain(r.get("google_patent_url"))

        badge = '<span class="badge dirty">DIRTY</span>' if dirty_flag else ""
        # searchable text blob (lowercased) for JS filtering
        blob = " ".join(str(r.get(k, "")) for k in
                        ("requested_id", "title", "abstract", "assignee")).lower()
        blob = html.escape(blob, quote=True)

        has_claims = not is_missing(r.get("claims"))
        has_fulltext = not is_missing(r.get("full_text"))

        claims_block = ""
        if has_claims:
            claims_block = f"""
            <details class="collapse">
              <summary>Claims</summary>
              <div class="longtext">{esc(r.get("claims"))}</div>
            </details>"""

        fulltext_block = ""
        if has_fulltext:
            fulltext_block = f"""
            <details class="collapse">
              <summary>Full text / Description</summary>
              <div class="longtext">{esc(r.get("full_text"))}</div>
            </details>"""

        link = f'<a href="{url}" target="_blank" rel="noopener">Google Patents ↗</a>' if url else ""

        card = f"""
        <article class="card {'is-dirty' if dirty_flag else ''}"
                 data-cc="{cc}" data-dirty="{str(dirty_flag).lower()}"
                 data-search="{blob}">
          <div class="card-head">
            <span class="pid">{esc(r.get("requested_id"))}</span>
            {badge}
            <span class="cc-tag">{cc}</span>
          </div>
          <h2 class="title">{esc(r.get("title"))}</h2>
          <div class="meta">
            <span><b>Assignee:</b> {esc(r.get("assignee"))}</span>
            <span><b>Published:</b> {esc(r.get("publication_date"))}</span>
            <span><b>Expiry:</b> {esc(r.get("expiration_date"))}</span>
          </div>
          <div class="abstract"><b>Abstract:</b> {esc(r.get("abstract"))}</div>
          {claims_block}
          {fulltext_block}
          <div class="card-foot">{link}</div>
        </article>"""
        cards.append(card)

    cards_html = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --bg: #f7f8fa; --card: #ffffff; --border: #e2e5ea;
    --text: #1f2430; --muted: #8a91a0; --accent: #2f5496;
    --dirty: #c0392b; --dirty-bg: #fdecea;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, Arial, "PingFang TC",
                 "Microsoft JhengHei", sans-serif;
    background: var(--bg); color: var(--text); margin: 0; line-height: 1.5;
  }}
  header {{
    position: sticky; top: 0; z-index: 10; background: var(--card);
    border-bottom: 1px solid var(--border); padding: 14px 20px;
  }}
  header h1 {{ margin: 0 0 8px; font-size: 18px; }}
  .stats {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; }}
  .stats b {{ color: var(--text); }}
  .controls {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
  .controls input, .controls select {{
    padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px;
    font-size: 13px; background: #fff;
  }}
  .controls input[type=search] {{ flex: 1; min-width: 200px; }}
  .controls label {{ font-size: 13px; color: var(--muted); display: flex;
    align-items: center; gap: 5px; cursor: pointer; }}
  main {{ padding: 16px 20px; max-width: 980px; margin: 0 auto; }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 18px; margin-bottom: 14px;
  }}
  .card.is-dirty {{ background: var(--dirty-bg); border-color: #f0c8c2; }}
  .card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
  .pid {{ font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 13px; color: var(--accent); font-weight: 600; }}
  .cc-tag {{ margin-left: auto; font-size: 11px; color: var(--muted);
    border: 1px solid var(--border); padding: 1px 7px; border-radius: 10px; }}
  .badge.dirty {{ background: var(--dirty); color: #fff; font-size: 10px;
    padding: 1px 6px; border-radius: 4px; font-weight: 700; letter-spacing: .3px; }}
  .title {{ font-size: 15px; margin: 2px 0 10px; }}
  .meta {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 12px;
    color: var(--muted); margin-bottom: 10px; }}
  .meta b {{ color: var(--text); font-weight: 600; }}
  .abstract {{ font-size: 13.5px; margin-bottom: 10px; }}
  .abstract b {{ color: var(--accent); }}
  .collapse {{ margin: 6px 0; }}
  .collapse summary {{ cursor: pointer; font-size: 13px; color: var(--accent);
    font-weight: 600; padding: 4px 0; user-select: none; }}
  .longtext {{ font-size: 12.5px; white-space: normal; margin-top: 6px;
    padding: 10px 12px; background: #fafbfc; border: 1px solid var(--border);
    border-radius: 6px; max-height: 380px; overflow-y: auto; }}
  .card-foot {{ margin-top: 10px; }}
  .card-foot a {{ font-size: 12.5px; color: var(--accent); text-decoration: none; }}
  .card-foot a:hover {{ text-decoration: underline; }}
  .missing {{ color: var(--muted); }}
  .hidden {{ display: none; }}
  #noresult {{ text-align: center; color: var(--muted); padding: 40px;
    font-size: 14px; display: none; }}
</style>
</head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="stats">
    Total: <b>{total}</b> &nbsp;·&nbsp; Clean: <b>{clean}</b>
    &nbsp;·&nbsp; Dirty: <b>{dirty}</b>
    &nbsp;·&nbsp; Showing: <b id="shown">{total}</b>
  </div>
  <div class="controls">
    <input type="search" id="q" placeholder="Search ID / title / abstract / assignee...">
    <select id="ccFilter">
      <option value="">All jurisdictions</option>
      {jur_opts}
    </select>
    <label><input type="checkbox" id="hideDirty"> Hide dirty</label>
  </div>
</header>
<main>
  {cards_html}
  <div id="noresult">No patents match the current filters.</div>
</main>
<script>
  const q = document.getElementById('q');
  const ccFilter = document.getElementById('ccFilter');
  const hideDirty = document.getElementById('hideDirty');
  const shown = document.getElementById('shown');
  const noresult = document.getElementById('noresult');
  const cards = Array.from(document.querySelectorAll('.card'));

  function apply() {{
    const term = q.value.trim().toLowerCase();
    const cc = ccFilter.value;
    const hd = hideDirty.checked;
    let count = 0;
    cards.forEach(c => {{
      const matchTerm = !term || c.dataset.search.includes(term);
      const matchCC = !cc || c.dataset.cc === cc;
      const matchDirty = !hd || c.dataset.dirty === 'false';
      const show = matchTerm && matchCC && matchDirty;
      c.classList.toggle('hidden', !show);
      if (show) count++;
    }});
    shown.textContent = count;
    noresult.style.display = count === 0 ? 'block' : 'none';
  }}
  q.addEventListener('input', apply);
  ccFilter.addEventListener('change', apply);
  hideDirty.addEventListener('change', apply);
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Render Google Patents JSONL to HTML report.")
    ap.add_argument("input", help="Path to JSONL file")
    ap.add_argument("--output", "-o", help="Output HTML path (default: alongside input)")
    ap.add_argument("--title", help="Report title (default: derived from filename)")
    ap.add_argument("--clean-only", action="store_true", help="Exclude dirty rows")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"[ERROR] Not found: {args.input}")
        return

    rows = load(args.input, args.clean_only)
    if not rows:
        print("[ERROR] No rows to render.")
        return

    title = args.title or os.path.splitext(os.path.basename(args.input))[0]
    out = args.output or os.path.splitext(args.input)[0] + "_report.html"

    html_str = build_html(rows, title)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_str)

    print(f"Rendered {len(rows)} patents → {out}")
    print(f"Open in browser: file://{os.path.abspath(out)}")


if __name__ == "__main__":
    main()
