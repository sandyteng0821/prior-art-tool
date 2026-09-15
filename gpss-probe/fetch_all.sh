#!/usr/bin/env bash
# fetch_all.sh — 依序跑四份清單，每份都帶 --resume（冪等，斷了重跑同一行即可接續）
#
# 用法：
#   ./fetch_all.sh              # 正常跑
#   ./fetch_all.sh --limit 10   # 每份只跑前 10 筆試水（參數原樣傳給 fetch_by_pn.py）
#
# 放在 gpss-probe/ 底下跟 fetch_by_pn.py 同層執行。
# 任一份撞 quota（fetch_by_pn.py 回傳非 0）就停整串——重跑同一個指令會從
# --resume 續上，不會重複下載。

set -euo pipefail

DATA=~/dev-workplace/prior_art_tool/data/plainid
OUT=./data

# 清單 → 輸出檔 對照。要加減資料集就改這裡。
LISTS=(
  "GPP_idlist_20260709.txt|gpss_GPP_idlist_20260709.jsonl"
  "IPF_idlist_20260709.txt|gpss_IPF_idlist_20260709.jsonl"
  "pioglitazone_eb_idlist.txt|gpss_pioglitazone_eb_idlist.jsonl"
  "tiagabine_eb_idlist.txt|gpss_tiagabine_eb_idlist.jsonl"
)

for pair in "${LISTS[@]}"; do
  ids="${DATA}/${pair%%|*}"
  out="${OUT}/${pair##*|}"
  echo "══ ${pair%%|*} ═══════════════════════════════════════════════"
  python3 fetch_by_pn.py --ids "$ids" --out "$out" --resume --pause 3 "$@"
done

echo "══ 全部完成 ══"
