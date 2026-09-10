# SureChEMBL Bulk Patent Chemistry Tool — 使用說明

> 給同事參考用。這個工具可以獨立使用，不需要在 prior-art-tool repo 底下。

---

## 這個工具解決什麼問題

做 drug repurposing 的 prior art 分析時，要找「哪些專利同時提到這個藥和這個適應症」。

**問題是：現有的檢索都只搜標題、摘要、請求項，搜不到說明書。**

| 檢索範圍 | 能看到的化合物數 |
|---|---|
| EPO OPS `ta=`（標題 + 摘要）| 514,139 |
| GPSS `TI/AB/CL`（+ 請求項）| 5,486,097 |
| **SureChEMBL（+ 說明書）** | **19,354,812** |

差 **37.6 倍**。而且一個藥被提到在說明書裡，佔全部提及的 **80.3%**。

實際案例：`bromocriptine × 脊髓性肌肉萎縮症`。GPSS 回 0 筆。SureChEMBL 撈到
**1,825 筆**，其中 `US20230293730A1` 的說明書同時有 bromocriptine 與 SMA
（提 52 次）—— 那是一篇 AAV 基因治療專利，把 bromocriptine 列在既有療法裡。

另外 SureChEMBL 比對的是**化學結構不是藥名**，所以繞過同義字問題：
`nerandomilast` 在 GPSS 是 0 筆（專利寫代號 `BI 1015550`），
SureChEMBL 撈到 21 筆 —— 而該藥全庫只有 28 筆專利，**21/28 都在講 IPF**。

---

## 安裝方式

```bash
pip install duckdb pyarrow          # openpyxl 只有讀 .xlsx truth 檔時才需要

# 下載資料（15.7 GB，公開 FTP 免申請，每兩週一版）
python3 probe_surechembl_bulk.py --phase 0 --download --data-dir ./sc_bulk
```

跑完的目錄結構：

```
./
├── probe_surechembl_bulk.py      ← 主工具
├── resolve_inchikey.py           ← 藥名 → InChIKey（phase 2 的前置）
├── sc_bulk/                      ← 7 個 parquet + _release.txt（15.7 GB）
└── duckdb_tmp/                   ← DuckDB spill，需數十 GB 空間
```

⚠ **`--tmp-dir` 必須設**。表有 15.4 億列，沒 spill 目錄會直接 OOM。

---

## ★ 三個一定要先知道的邊界

### 1. 疾病標註斷在 2025-12-31

2026 年起共 **290,950 篇美國專利有零個疾病標註**（連續八個月精確 0.0%），
而化學標註跟書目資料收到 2026-08。

| 查詢型態 | 2026 專利 |
|---|---|
| 「哪些專利提到藥 X」（化合物單邊）| ✓ 可用 |
| **「藥 X × 適應症 Y」（二維交叉）** | ✗ **完全不可用** |

→ **不要回報「該藥在此適應症無專利」**，那可能只是標註缺失。

**每換一版 release 都要重驗**（這條邊界會不會推進尚未確認）：

```python
import duckdb
con = duckdb.connect()
con.execute("SET temp_directory='./duckdb_tmp'")
print(con.execute("""
  WITH p AS (SELECT id, TRY_CAST(publication_date AS DATE) d
             FROM read_parquet('./sc_bulk/patents.parquet')
             WHERE country='US' AND TRY_CAST(publication_date AS DATE) >= DATE '2025-01-01'),
       b AS (SELECT DISTINCT patent_id FROM read_parquet('./sc_bulk/biomedical_locations.parquet'))
  SELECT date_trunc('month', p.d) mon, count(*) n,
         round(100.0*count(b.patent_id)/count(*),1) pct
  FROM p LEFT JOIN b ON b.patent_id = p.id GROUP BY 1 ORDER BY 1
""").fetchall())
```

`pct` 從 83% 掉到 0.0 的那個月就是邊界。2026-09-01 版是 2025-12-31。

### 2. 查得到的上限是「字典」不是「文本」

這份資料**沒有專利原文**，只有「實體 X 出現在專利 Z 的欄位 Y，N 次」的標註。
字典裡沒有的病名會**靜默回 0**，看起來就像資料源沒收。

→ **查任何新適應症之前，先跑 `--list-entities`**（第 4 節）。這是流程要求不是建議。

### 3. 撈得到不等於分析得到

主 pipeline 的 Phase 4 只讀 title / abstract / claims，**不讀說明書**。
所以說明書裡找到的專利，LLM 分析階段會判 Low。

→ 目前這個工具的產出適合當**候選清單與排序**，接進 Phase 4 之前需要
先把說明書的相關段落抽出來（未實作）。

---

## 常用指令

### 查某個適應症在字典裡長什麼樣（必跑）

```bash
python3 probe_surechembl_bulk.py --list-entities "pustular psoriasis" \
    --data-dir ./sc_bulk --tmp-dir ./duckdb_tmp
```

```
       id  corrected_text                    MeSH       type       專利數    總次數
    39062  PUSTULAR PSORIASIS                D011565    Disease     6,880    15,383
   651797  Pustular psoriasis of the Barber  D011565    Disease       107       134
   324008  generalised pustular psoriasis    D011565    Disease        42        86

── 按 MeSH ID 彙總（★ 檢查有沒有混入不同疾病）
   D011565        10      6,905   100.0%  PUSTULAR PSORIASIS / ...
```

**為什麼必跑**：專利幾乎不寫全稱。`PUSTULAR PSORIASIS` 有 6,880 筆，
`generalised pustular psoriasis` 只有 47 筆，**差 146 倍**。
用全稱查會以為沒資料。

**按 MeSH 彙總那張表要看**：查 `spinal muscular atrophy` 會撈到 60 個實體，
其中 `D055534`（Kennedy disease，另一種病）佔 **15.4%**。要排除就加
`--exclude-mesh D055534`。

### 藥名 → InChIKey

```bash
python3 resolve_inchikey.py --drugs drugs.txt \
    --out drug_inchikey.tsv --report resolve_report.md
```

`compounds` 表**沒有名稱欄位**，所以藥名要先轉成 InChIKey 才能查。

⚠ **送下一步之前先看 `resolve_report.md`**。ChEMBL 的搜尋 API 是全文檢索不是
名稱查詢，查「cromolyn sodium」會回氯化鈉、氰化鉀、疊氮化鈉（都是合法的
InChIKey）。工具有三道過濾擋掉，但擋掉的東西會列在報告裡，要確認沒誤殺。

### 跑 drug × indication 矩陣

```bash
python3 probe_surechembl_bulk.py --phase 2 \
    --data-dir ./sc_bulk --tmp-dir ./duckdb_tmp --mem-limit 9GB \
    --drug-map drug_inchikey.tsv \
    --indications "psoriasis,idiopathic pulmonary fibrosis" \
    --out phase2.json
```

會出三張表。**排序看 lift 那張，不是筆數那張**：

```
  絕對數量                      lift（observed / 隨機期望）
                Pso     IPF                   Pso     IPF
  acitretin    7,272   2,057    acitretin    78.5   133.7
  methotrexate 102,893 20,732   methotrexate 39.2    47.5
  nintedanib   1,578   1,978    nintedanib   26.7   201.6
```

`methotrexate × psoriasis` 有 10 萬筆不是因為關聯強，是因為兩個詞都極高頻。
lift 做了 base rate 校正 —— 實測 Psoriasis 和 IPF 兩欄，**臨床已知的核准藥
都排在前三名**。

`·` 表示**藥或適應症沒解析成 ID（查不了）**，跟 `0`（真的沒命中）不一樣。

### 某篇專利為什麼沒被撈到

```bash
python3 probe_surechembl_bulk.py --inspect-patent "US20230293730A1" \
    --inspect-drug-key OZVBMTJYIDMWIL \
    --data-dir ./sc_bulk --tmp-dir ./duckdb_tmp
```

逐層告訴你卡在哪：

| 層 | 意義 |
|---|---|
| (1) 不在 patents 表 | 整篇零小分子（核酸／抗體專利會整篇缺席）|
| (2) 無化合物標註 | 化學抽取失敗 |
| (3) 有化合物但沒目標藥 | 藥名沒被辨識出結構 |
| (4) 有藥但無疾病標註 | 字典沒認出，**或落在 2025-12-31 之後** |

也會列出同家族在資料裡的成員 —— 有時本篇缺席但家族成員在。

### 某個藥回 0，是 key 錯還是真的沒有

```bash
python3 probe_surechembl_bulk.py --find-mw 654.6 --find-smiles Br \
    --data-dir ./sc_bulk --tmp-dir ./duckdb_tmp
```

用分子量加 SMILES 特徵原子反查，不靠名稱。回傳的骨架跟你手上的 key 一比就知道。

⚠ **InChIKey 沒有「接近」這回事**。第一段是雜湊，14 碼要嘛全對，
要嘛是完全不相干的分子。前綴相似不是佐證。

---

## 資料來源與限制

| 項目 | 說明 |
|---|---|
| 來源 | EMBL-EBI SureChEMBL bulk data（`ftp.ebi.ac.uk/pub/databases/chembl/SureChEMBL/bulk_data/`）|
| 授權 | **CC BY 4.0** —— 義務只有標示出處 |
| 更新頻率 | 每兩週一版，各版獨立完整，只需下載一版 |
| 涵蓋 | 4,513 萬篇專利。**CN 2,400 萬（53%，英譯全文）** · US 975 萬 · EP 529 萬 · JP 306 萬 · WO 302 萬 |
| **不含 KR** | 韓國完全沒有 → 需要 KR 得用 GPSS |
| JP 日期 | **39% 的公開日是 `0001-01-01` 哨兵值**，不可當年份用 |
| 家族 | DOCDB simple family，**不是 INPADOC**，且只涵蓋有化學標註的成員 → 完整家族仍須 EPO |
| 不含 | 法律狀態、到期日、說明書原文、非小分子專利（整篇零化合物即整篇缺席）|
| 疾病標註 | **斷在 2025-12-31**，見上方邊界 1 |
