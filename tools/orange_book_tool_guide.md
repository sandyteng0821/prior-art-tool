# Orange Book Patent Expiry Lookup Tool — 使用說明

> 給同事參考用。這個工具可以獨立使用，不需要在 prior-art-tool repo 底下。

---

## 這個工具解決什麼問題

我們在做 drug repurposing 的 prior art 分析時，需要知道目標藥物的專利什麼時候到期。

**問題是：專利到期日不是直接查得到的單一數字。**

一般算法是 `filing date + 20 年`，但美國 FDA 核准的藥品專利可以申請
Patent Term Extension (PTE)，額外延長 2-5 年。例如：

| 來源 | Sitagliptin (US7326708) 到期日 |
|------|-------------------------------|
| EPO filing + 20yr | 2024-06-23 |
| **FDA Orange Book** | **2026-11-24** |

差了 2 年 5 個月，就是 PTE。

**Orange Book 是 FDA 官方出版物**，裡面的到期日已經包含 PTE，
是美國藥品專利到期日最權威的來源。這個工具自動下載、解析、查詢 Orange Book data files。

---

## 安裝方式

**零依賴**。只需要 Python 3.10+ 和一個檔案：

```bash
# 複製工具到你的工作目錄
cp parse_orange_book.py ~/your-workspace/
cd ~/your-workspace/

# 首次使用：從 FDA 下載資料（~1MB ZIP，每月更新）
python3 parse_orange_book.py --download
```

下載完會在同目錄下建立 `cache/orange_book/`，之後查詢都從 local cache 讀，不需要網路。

跑完後的目錄結構：

```
./
├── parse_orange_book.py        ← 工具本體（唯一需要複製的檔案）
└── cache/
    └── orange_book/
        ├── orange_book.zip     ← FDA 原始資料（~1MB）
        └── patents_lookup.json ← 解析後的查詢 cache（~4MB）
```

---

## 常用指令

### 查藥名 → 看所有專利到期狀態

```bash
# 用 generic name（你 CMAP 表裡的 cmap_name 可以直接用）
python3 parse_orange_book.py --drug sitagliptin
```

輸出：

```
  JANUVIA
  Active ingredient: SITAGLIPTIN PHOSPHATE
  NDA: 021995  Applicant: MERCK SHARP AND DOHME LLC
  ────────────────────────────────────────────────────────────────
  Patent       Expires        Status           Use Code   Flags
  ────────────────────────────────────────────────────────────────
  7326708      2026-11-24     🟡 EXPIRING SOON  U-802      S,P
  6699871      2028-07-12     🟢 ACTIVE         U-802      S
  ────────────────────────────────────────────────────────────────
  Total: 2 patents  | Active: 2  | Expired: 0
```

狀態說明：
- ⚪ **EXPIRED** — 已過期
- 🟡 **EXPIRING SOON** — 一年內到期
- 🟢 **ACTIVE** — 還有效

### 批次查詢你的 CMAP compound table

```bash
python3 parse_orange_book.py --batch compoundinfo_beta.txt
```

工具會自動讀 `cmap_name` 欄位去比對 Orange Book。
如果 `cmap_name` 是 BRD code（例如 `BRD-A35931254`），
會 fallback 到 `compound_aliases` 欄位再查一次。

輸出 summary table：

```
  Compound                       OB Drug              Patents  Latest Expiry  Status
  ──────────────────────────────────────────────────────────────────────────────────────────
  sitagliptin                    JANUVIA                    5  2026-11-24     🟡 EXPIRING SOON
  BRD-A35931254 → apomorphine    APOKYN                     3  2029-01-15     🟢 ACTIVE
  l-theanine                     —                          0  —
  ──────────────────────────────────────────────────────────────────────────────────────────
  Total: 39321  |  In OB: 2044  |  Not found: 37277
```

> **Not found 比例高是正常的。** Orange Book 只收 FDA NDA-approved 且專利未過期的藥品。
> CMAP 裡大部分是 research compounds，本來就不在 Orange Book 裡。

### JSON 輸出（方便你 downstream 處理）

```bash
# 單筆
python3 parse_orange_book.py --drug sitagliptin --json

# 批次
python3 parse_orange_book.py --batch compoundinfo_beta.txt --json > ob_status.json
```

---

## 其他指令

```bash
# 查 patent number（接受 bare number 或 EPO 格式）
python3 parse_orange_book.py 7326708
python3 parse_orange_book.py US7326708B2

# 看統計（多少 patents / drugs / 日期範圍）
python3 parse_orange_book.py --stats

# 更新資料（FDA 每月更新，重跑 --download 就好，URL 不會變）
python3 parse_orange_book.py --download
```

---

## Name Matching 說明

你的 CMAP 用的是 INN generic name（例如 `sitagliptin`），
Orange Book 用的是 FDA registered name（例如 `SITAGLIPTIN PHOSPHATE`）。

工具的比對邏輯是 **case-insensitive substring match**：
1. 先找 trade name（JANUVIA, AUSTEDO...）
2. 沒命中 → 找 active ingredient（SITAGLIPTIN PHOSPHATE...）

所以 `sitagliptin` 會 match 到 `SITAGLIPTIN PHOSPHATE`，大部分情況直接 work。

**可能 match 不到的情況：**
- Compound 不是 FDA-approved 藥（research compound, supplement 等）
- 專利已全部過期（FDA 會從 Orange Book 移除）
- 名稱差異太大（罕見，例如有些 prodrug 的名字跟 active form 完全不同）

---

## 資料來源與限制

| 項目 | 說明 |
|------|------|
| 來源 | FDA Orange Book Data Files (`fda.gov/media/76860/download`) |
| 更新頻率 | 每月 |
| 涵蓋範圍 | 美國 NDA-approved 藥品，專利未過期者 |
| 到期日精度 | 包含 PTE，不含 Pediatric Exclusivity（`*PED` 記錄另列） |
| 不含 | Generic (ANDA)、已過期專利、非美國專利、maintenance fee 未繳導致的提前失效 |
