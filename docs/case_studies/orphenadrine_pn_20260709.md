# Case Study: Orphenadrine × Prurigo Nodularis
## Jenna's new proposal search — first end-to-end pipeline test run

> Date: 2026-07-09
> Config: `configs/orphenadrine_pn.py`
> Ref: SOP1 Section A case study

---

## 1. Manual steps

**FDA Orange Book check**：Orphenadrine 無 active patent 列於 Orange Book。

**Search strategy build（via Claude Opus 4.6）**：
Orphenadrine 是 diphenhydramine 的 monomethyl 衍生物，dirty drug（H1 antihistamine / muscarinic antagonist / Nav1.7-1.9 blocker / NMDA antagonist / NE reuptake inhibitor）。
產出 7-layer CUSTOM_QUERIES config：drug×disease, drug×mechanism, disease sweep, structural analogs (diphenhydramine, doxepin), competitor landscape (dupilumab, nemolizumab, serlopitant), NMDA×itch, neuroimmune pathway (IL-31, substance P, mast cell).

**Google Patents cross-validation**：
關鍵字 "Orphenadrine Prurigo nodularis"，手動檢視 4 筆命中：

| Patent ID | Match Location | Pipeline Status | Final Risk |
|-----------|---------------|-----------------|------------|
| US10702499B2 | Background 文獻引述（Ständer et al.） | ✅ 有收，Low | Low |
| US11504342B2 | Disclosure（nalmefene for PN） | ✅ 有 family，Low | Low |
| AU2018347514B2 | [0092] drug list + [0177] disease list | ❌ **Pipeline 遺漏** | Low（claims 精讀確認） |
| US8853266B2 | Disclosure（drug only，無 PN） | 需確認 | Low |

AU2018347514B2 是 ECM delivery platform 專利（19 claims），claims 不涵蓋 orphenadrine 或 PN。Description laundry list only。與 Darifenacin 案例（KR20100120296A）同 pattern。

---

## 2. Partially automated steps

**Pipeline run**：EPO OPS fetch → rule-based scoring → LLM scoring（gpt-4o-mini screening / gpt-4o analysis）。

**Rule-based vs LLM 差異**：

| | Rule-based | LLM |
|--|--|--|
| is_target_drug=TRUE | 46 | 7 |
| High | 24 | 0 |
| Medium | 17 | 5 |
| Low | 5 | 2 |

Rule-based 嚴重高估，主因：`diphenhydramine` 在 `RULE_DRUG_KEYWORDS` 中，導致大量含 diphenhydramine 的 CN/JP OTC 抗癢外用製劑被標為 target drug。手動精讀 CA2497820A1（rule 4/4 hit）確認：claim 只涵蓋 steroid + diphenhydramine cream，與 orphenadrine 完全無關。

**Coverage gap 發現**：EPO OPS `ta=` 只搜 title + abstract。AU2018347514B2 的 orphenadrine 和 PN 只出現在 description，pipeline 無法偵測。驗證 Task I（Google Patents JSONL import）的必要性。

---

## 3. Final Decision

**FTO 結論：no patent claims orphenadrine × PN at claims level. Gap open.**

Two Medium-risk directions feedback to Jenna：

**A. Nav1.7 Inhibitors × Itch（JP2025506252A / KR20240147689A，同 family）**
- Active to 2043
- Claim: Nav1.7 inhibitors for itch relief
- Risk: orphenadrine 具有 Nav1.7 off-target activity，若 claim scope 涵蓋 non-selective blockers 可能波及
- 緩解因素：未指名 orphenadrine 或 PN；orphenadrine 的 antipruritic rationale 是多靶點而非 pure Nav1.7
- Status: claims 精讀 pending

**B. Transdermal Orphenadrine Delivery（US2015045437A1）**
- Active to 2034
- Claim: orphenadrine transdermal delivery kits
- Risk: 直接 claim orphenadrine，但限定 transdermal route
- Gap: oral tablet / topical cream 不在 claim 範圍內
- 結論：不阻擋 oral/topical for PN

---

## Findings → action items

| Finding | Action | Priority |
|---------|--------|----------|
| diphenhydramine 汙染 | Move from `RULE_DRUG_KEYWORDS` to `RULE_ADDITIONAL_INDICATION_KEYWORDS` | Config fix, next run |
| EPO OPS description blind spot | Manual Google Patents cross-validation as standard step; accelerate Task I | Process + roadmap |
| LLM captures mechanism-level match that rule misses (JP2025506252A) | Validates LLM scoring value over pure keyword matching | No action needed |

---

## Deliverables

| File | Audience |
|------|----------|
| `configs/orphenadrine_pn.py` | Developer（已 commit） |
| `orphenadrine_pn_fto_report.md` | Proposal team (Jenna) |
| `orphenadrine_pn_fto_assessment.md` | Developer (Sandy) |
| `orphenadrine_pn_discovery_comparison.md` | Developer (Sandy) |
