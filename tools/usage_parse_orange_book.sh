#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# parse_orange_book — Usage Guide & Test Commands
#
# FDA Orange Book patent expiry lookup tool.
# Orange Book 只收 FDA NDA-approved 且 patent 未過期的美國藥品。
# 到期日包含 PTE (Patent Term Extension)，比 EPO filing+20yr 更準確。
#
# 前置條件：cache/orange_book/patents_lookup.json 已存在
#   （首次使用先跑 --download 或 --parse-only）
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. 首次設定：下載 + 解析 ──────────────────────────────────────────────────
# 從 FDA 下載 Orange Book ZIP，解壓並 parse Patent.txt + Products.txt
# 結果存入 cache/orange_book/patents_lookup.json（之後查詢不用重新 parse）
python3 -m tools.parse_orange_book --download

# 如果 FDA 網路被擋，手動下載 ZIP 放到 cache/orange_book/orange_book.zip 後：
# python3 -m tools.parse_orange_book --parse-only

# ── 2. 總覽統計 ──────────────────────────────────────────────────────────────
# 看有多少 patents、多少 drugs、日期範圍
python3 -m tools.parse_orange_book --stats

# ── 3. 用 patent number 查詢 ─────────────────────────────────────────────────
# 接受三種格式：bare number / EPO-style / 分號分隔

# 查單筆（bare number）
python3 -m tools.parse_orange_book 7326708
# → Patent 7326708 → JANUVIA (SITAGLIPTIN PHOSPHATE) expires 2026-11-24

# EPO 格式也行（自動 strip US prefix + kind code）
python3 -m tools.parse_orange_book US7326708B2

# 多筆查詢
python3 -m tools.parse_orange_book 7326708 12016858

# 分號分隔（Espacenet paste 格式）
python3 -m tools.parse_orange_book '7326708;12016858;9415051'

# 不在 Orange Book 的 patent（非 NDA 藥品、或 patent 已過期被移除）
python3 -m tools.parse_orange_book 9415051
# → 9415051  ✗ NOT IN ORANGE BOOK

# ── 4. JSON 輸出（供下游 script 消費）───────────────────────────────────────
python3 -m tools.parse_orange_book 7326708 --json
# → 標準 JSON，可 pipe 給 jq 或 Python

# ── 5. 用藥名查詢（反向查詢：drug → patents）────────────────────────────────
# 接受 trade name 或 active ingredient，case-insensitive substring match

# 用 trade name
python3 -m tools.parse_orange_book --drug januvia

# 用 active ingredient（拉出所有含此成分的藥）
python3 -m tools.parse_orange_book --drug sitagliptin
# → JANUVIA, JANUMET, JANUMET XR, STEGLUJAN, JUVISYNC

# 每筆 patent 會標狀態：
#   ⚪ EXPIRED       — 已過期
#   🟡 EXPIRING SOON — 一年內到期
#   🟢 ACTIVE        — 還有效

# JSON 輸出也可以搭配
python3 -m tools.parse_orange_book --drug januvia --json

# ── 6. 跟 EPO filing+20yr 比較（顯示 PTE gap）──────────────────────────────
# 需要 tools/fetch_dates.py 在同一 repo（會 subprocess call EPO API）
python3 -m tools.parse_orange_book 7326708 --compare-epo
# → OB=2026-11-24  EPO=2024-06-23  gap=+2.4yr (= PTE 長度)

# 多筆比較
python3 -m tools.parse_orange_book 7326708 12016858 --compare-epo

# ── 7. 批次查詢：CMAP compound table ────────────────────────────────────────
# 讀 TSV/CSV，用 cmap_name 欄位查 Orange Book
# 如果 cmap_name 是 BRD code（查不到），自動 fallback 到 compound_aliases
python3 -m tools.parse_orange_book --batch compoundinfo_beta.txt

# JSON 輸出（方便下游分析）
python3 -m tools.parse_orange_book --batch compoundinfo_beta.txt --json > ob_status.json

# ── 8. Dump 全部 Orange Book 藥物 ───────────────────────────────────────────
# 把 Orange Book 裡所有 drug 的專利到期狀態倒出來（不需要外部 compound list）
# Terminal 表格
python3 -m tools.parse_orange_book --dump

# 色標 Excel（交給同事最方便）
python3 -m tools.parse_orange_book --dump --xlsx output/ob_drugs.xlsx

# JSON（給 downstream script 吃）
python3 -m tools.parse_orange_book --dump --json > ob_all_drugs.json

# 搭配 --stats
python3 -m tools.parse_orange_book --stats --dump

# ── 9. 注意事項 ──────────────────────────────────────────────────────────────
#
# Orange Book 的 *PED 記錄：
#   某些 patent number 帶 *PED 後綴（例如 12016858*PED），
#   這是 Pediatric Exclusivity 延伸，到期日比原始 patent 多 6 個月。
#   Tool 會把它當獨立記錄列出，方便對照差異：
#     python3 -m tools.parse_orange_book 12016858      → expires 2036-03-07
#     python3 -m tools.parse_orange_book '12016858*PED' → expires 2036-09-07
#
# Orange Book 只含：
#   - FDA NDA-approved 的美國藥品（不含 generic ANDA）
#   - 尚未過期的 patents（已過期會被 FDA 移除）
#   - 所以 "NOT IN ORANGE BOOK" 不代表沒有 patent，可能是已過期或非 NDA 產品
#
# --batch 的 match rate：
#   CMAP 有 ~39000 compounds，大部分是 research compounds 不在 Orange Book
#   In OB ~2000 是正常比例（≈5%），不代表 matching 有問題
#
# 資料更新：
#   Orange Book data files 每月更新一次
#   重新跑 --download 即可更新 cache
