# Ampicillin Formulation — Prior Art Evidence Table

> Goal: Validate excipient pipeline recommendations (real-world formulation cases)  
> Date: 2026-05-11  
> Config: `configs/ampicillin_formulation_evidence.py`

---

## Search Methods

### Automated Search (Prior Art Tool)
EPO OPS queries via `python3 main.py`，output: `gap_analysis_20260508_1645.xlsx` **(rule-based, USE_LLM=False)**

| Strategy | Query | Results |
|----------|-------|---------|
| 1 | `ta=Ampicillin AND pd within "2000 2024"` | 100 |
| 2 | `ta=Ampicillin AND pn=EPB AND pd within "2000 2024"` | 45 |
| 3 | `ta=Ampicillin AND ta=formulation AND pd within "2000 2024"` | 16 |
| 4 | `ta=Ampicillin AND ta=tablet AND pd within "2000 2024"` | 20 |
| 5 | `ta=Ampicillin AND ta=capsule AND pd within "2000 2024"` | 7 |
| **Total** | | **188** (5 High / 39 Medium / 144 Low) |

### Manual Search
- **Espacenet Advanced Search** — keyword queries: `Ampicillin + [excipient name] + composition`
- **Google Patents** — full-text inspection of claims and disclosure sections

---

## Evidence Table

| Patent No. | Title | Source | FTO Risk (auto) | Ampicillin | Lactose Anhydrous | MCC | Polymethacrylates | Ammonium Alginate | Notes |
|---|---|---|---|---|---|---|---|---|---|
| US7108864B1 | Tablet formation | Auto + Manual | Low (rule) / Medium (LLM) | ✅ claim | ✅ disclosure [0033] | ✅ disclosure [0033][0095][0099] | | | Found by both auto and manual. Rule mode → Low (insufficient keyword match). LLM mode → Medium (correctly identifies formulation relevance). |
| US2009062404A1 | PHARMACEUTICAL COMPOSITION | Manual only | — | ✅ | ✅ | ✅ | | | Tablet composition with ampicillin, lactose, MCC inferred from disclosure |
| US2013029965A1 | Pharmaceutical solid dispersions | Manual only | — | ✅ disclosure [0069] | ✅ disclosure [0095] | ✅ disclosure [0095][0099] | ✅ claim 11 / disclosure [0084] | | Ampicillin listed as antibiotic example [0069]. Polymethacrylates as concentration-enhancing polymer. Other matrix materials include lactose + MCC [0095] |
| EP4609860A1 | Pharmaceutical composition for preventing or treating hearing loss comprising ammonium lactate | Manual only | — | ❌ | | | | ✅ (main API) | Ammonium lactate is main API, not ampicillin. Less relevant. |
| US2023139922A1 | Bacitracin-alginate oligomer conjugates | Manual only | — | ❌ | | | | ✅ | Irrelevant — no ampicillin formulation |
| US10195282B2 | Use of alginate formulation for intraincisional drug delivery | Manual only | — | ❌ | | | | ✅ | Irrelevant — no ampicillin formulation |
| KR20130055998A | THE COMPOSITION OF AMPICILLIN POWDER PREPARATION TO IMPROVE THE SOLUBILITY OF THE ACTIVE SUBSTANCE | Auto only | High (rule) → **Low (LLM)** | ✅ | ❌ (not mentioned) | | | | LLM 降低 risk 至 Low。Powder preparation. Lactose not mentioned. No examples extracted from EPO (KR coverage limited). |
| NZ575435A | SOFT CHEWABLE, TABLET, AND LONG-ACTING INJECTABLE VETERINARY ANTIBIOTIC FORMULATIONS | Auto only | High (rule) → **Medium (LLM)** | ✅ | | ✅ claim (disintegrant list includes MCC) | | | Veterinary formulation. Disintegrant candidates include MCC, sodium starch glycolate, crospovidone etc. |
| CN103830190A | Ampicillin tablet and preparation method thereof | Auto only | High (rule) → **Medium (LLM)** | ✅ | ✅ claim 1 | ✅ claim 1 | | | Claim 1: polysaccharide can be sucrose, lactose, MCC, starch or dextrin |
| EA200100024A1 | A PHARMACEUTICAL TABLET COMPOSITION OF DURABLE ACTION | Auto only | High (rule) → **Medium (LLM)** | ✅ claim 27 (列舉) | ✅ claim 13-16 | ✅ claim 9-12 | | | ⚠️ Ampicillin sodium 出現在 claim 27 的藥物列舉清單，但主角是 delavirdine mesylate（claim 28-32）。LLM 判斷 Medium，佐證價值低。見下方說明。 |
| EA004311B1 | A PHARMACEUTICAL TABLET COMPOSITION OF DURABLE ACTION | Auto only | High (rule) → **Medium (LLM)** | ✅ | ✅ detailed description | ✅ detailed description | | | Detailed description: tablets require MCC (10-50%), optional lactose (0-80%). Ampicillin sodium + lactose + MCC disclosed. |

---

## Excipient Pipeline Validation Summary

| Excipient (Recommended) | Score | Evidence Found | Patents | Strength |
|---|---|---|---|---|
| Polymethacrylates | 8 | ✅ | US2013029965A1 | Moderate — ampicillin as example drug, polymethacrylates as concentration-enhancing polymer (indirect disclosure) |
| Cellulose, MCC | 7 | ✅ | US7108864B1, US2009062404A1, US2013029965A1, NZ575435A, CN103830190A, EA004311B1 | Strong — multiple patents disclose ampicillin + MCC combination |
| Lactose Anhydrous | — | ✅ | US7108864B1, US2009062404A1, CN103830190A, EA004311B1 | Strong — multiple patents disclose ampicillin + lactose combination |
| Ammonium Alginate | 3 | ❌ | EP4609860A1 (irrelevant), US2023139922A1 (irrelevant) | Weak — no relevant ampicillin + ammonium alginate formulation found |

---

## Key Observations

**Search coverage:**
- Auto tool (188 patents, rule-based, `gap_analysis_20260508_1645.xlsx`): 5 High risk patents identified
- Manual search (Espacenet + Google Patents): 6 patents identified
- Overlap: 1 patent found by both (US7108864B1, scored Low by auto tool)
- Auto only (not found by manual): KR20130055998A, NZ575435A, CN103830190A, EA200100024A1, EA004311B1
- Manual only (not found by auto tool): US2009062404A1, US2013029965A1, EP4609860A1, US2023139922A1, US10195282B2

**LLM vs Rule mode comparison:**
- `KR20130055998A`: Rule → High；LLM → **Low**（LLM 判斷與 Ampicillin formulation 相關性低）
- `NZ575435A`, `CN103830190A`, `EA200100024A1`, `EA004311B1`: Rule → High；LLM → **Medium**
- `US7108864B1`: Rule → Low；LLM → **Medium**（LLM 正確識別配方相關性）
- LLM mode 整體更準確，適合 formulation evidence 用途

**EPO OPS vs Google Patents data quality:**
- `EA200100024A1`: EPO OPS returned full claims text (claim 1-32, ~1500 words); Google Patents only showed abstract-level description
- EPO OPS claims data can be more complete than Google Patents for certain patent offices (EA = Eurasian Patent Organization)
- This means LLM analysis based on EPO OPS claims can catch details that manual Google Patents inspection would miss

**LLM hallucination check:**
- `EA200100024A1` LLM reasoning: "Ampicillin sodium is mentioned, but claims focus on formulation, not specific diseases"
- Verified against DB: Ampicillin sodium confirmed in claim 27 drug list ✅ — not a hallucination
- However, main protected drug is delavirdine mesylate (claim 28-32); ampicillin is one of ~25 listed examples

---

## EA200100024A1 — Detailed Note

EPO OPS claims (verified from DB):
- Claim 27 lists ampicillin sodium as one of ~25 drug examples for the "rapidly precipitating drug" formulation
- Claims 28-32 focus specifically on delavirdine mesylate
- Claims 9-16 disclose MCC (10-50%) and lactose (up to 80%) as excipients

Google Patents only showed:
> "A pharmaceutical composition for a tablet with a non-prolonged effect is disclosed, which includes a rapidly precipitating drug..."

**Conclusion:** EPO OPS data is richer than Google Patents for this patent. LLM correctly identified ampicillin mention. High risk score is technically justified but the formulation evidence for ampicillin specifically is indirect (listed as one of many example drugs).

---

## Limitations

- No examples extracted from the 5 High risk patents — EPO OPS does not provide description full-text for KR/CN/EA patents
- Claims and disclosure inference used instead of direct experimental examples
- EA200100024A1: Ampicillin is one of ~25 listed example drugs in claim 27; main protected drug is delavirdine mesylate

---

## Next Steps

- [ ] Option A: Pass `description` to LLM analyzer to enable automatic disclosure-based formulation inference
- [ ] Validate Polymethacrylates evidence more carefully (indirect disclosure vs explicit formulation)
- [ ] Consider adding Google Patents as a second data source for KR/CN coverage
