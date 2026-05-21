# Prior Art Tool — Formulation Evidence Extraction Spec

> 設計決策紀錄。寫完即鎖定，不隨實作調整。  
> 討論來源：ChatGPT（初稿）→ Gemini（架構討論）→ Claude（spec 產出）

---

## 背景與問題

### 現有系統架構
```
Query → Fetch → Store → Analyze → Output
```

現有系統在 fetch 時已有 `_parse_examples()` 從 description 切出 Examples 段落，存入 `examples_extracted`。LLM 分析時吃 claims 全文做 FTO 風險評估。

### 三個互相卡住的問題

1. Formulation evidence 主要存在於 claims / disclosure，**examples 極少**（Ampicillin 驗證時確認）
2. DB 沒存 description 全文（省空間），`examples_extracted` 也幾乎抓不到配方資訊
3. Query 時才做 extraction → 成本高、延遲高、結果不穩定

### 根本原因

這是一個 **formulation inference 問題**，不是 keyword retrieval。

判斷的不是「有沒有 mention lactose」，而是：
> Ampicillin + lactose + MCC → 是否形成 formulation pattern？

這種推論幾乎一定要看 claims / description 的句子，光靠 examples 段落不夠。

### 從 Ampicillin 驗證得到的實證

- Rule mode 對 formulation 準確度不足（KR 專利 rule → High，LLM → Low）
- LLM 吃 claims 全文才能正確判斷配方相關性
- EPO OPS 對 KR/CN/EA 專利不提供 description 全文，只能靠 claims 推論
- `examples_extracted` 對 formulation evidence 任務幾乎沒有幫助

---

## 設計原則

**不存 description 全文，只在 fetch 時切出 formulation 相關句子（snippets）存入 DB**

- 全文 description 可能數萬字，DB 無法負荷
- Formulation evidence 本質是 local pattern detection（局部句子），不需要全文推理
- Snippets 與現有的 `examples_extracted` 不同：來自全文（claims + description），不限 Examples 段落

---

## 新版架構

```
Query → Fetch → [Snippet Extraction] → Store → Analyze → Output
```

新增的 Snippet Extraction 層插在 Fetch 和 Store 之間，是唯一的核心改動。

### 兩個欄位的差異（重要）

| 欄位 | 來源 | 邏輯 | 用途 |
|------|------|------|------|
| `examples_extracted` | description 的 Examples 段落 | 既有，切段落 | FTO 一般分析 |
| `formulation_snippets` | claims + description 全文 | 新增，切句子 | Formulation evidence |

### Snippet 篩選邏輯

一個句子同時滿足以下兩個條件才保留：
1. 包含 target drug 名稱（來自 config 的 `DRUG_ALIASES`）
2. 包含任一劑型關鍵字：`composition`, `formulation`, `comprises`, `excipient`, `tablet`, `capsule`, `carrier`

每筆專利上限 30 句，存為 JSON list。

### 資料來源優先級
1. Claims（高優先，EPB 最完整）
2. Description（有的話補充，KR/CN/EA 通常沒有）

---

## 分析策略（兩層）

| 層級 | 方式 | 適用情境 | 成本 |
|------|------|----------|------|
| Layer 1 | Rule-based，比對已知 excipient list | 快速篩選，無需 LLM | 免費 |
| Layer 2 | LLM，只吃 snippets（非全文） | 需語意推論時 | 可控 |

LLM token 用量預計降低 80–90%（從吃 claims 全文改成只吃 snippets）。

---

## 預期產出

每筆專利新增 `formulation_snippets` 欄位後，可以做到：

- API + Excipient co-occurrence table
- Evidence Table（API + excipients + patent 來源）
- LLM formulation extraction 成本可控、雜訊降低

---

## 明確不做的事

- 不重新設計 query strategy
- 不建 full knowledge graph
- 不存 description 全文
- 不重寫現有架構，只新增一層
- 不取代現有 `examples_extracted` 欄位
