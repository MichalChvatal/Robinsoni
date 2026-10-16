---
name: trialink-matching-rules
description: "Use when changing how TriaLink evaluates patients against trial criteria: 4-state logic (splneno/nesplneno/nezname/rozpor), cohort status, ranking, variant-level biomarker matching, drug-class dictionary, Case 1-5 regression expectations. Use for: matching.py, therapies.py, model.py, new criterion field, wrong match result."
---

# Párovací logika

Code: [trialink/matching.py](../../../trialink/matching.py), [trialink/therapies.py](../../../trialink/therapies.py), [trialink/model.py](../../../trialink/model.py). Tests: `tests/test_core.py`, `tests/test_cases.py`.

## Semantics
- Missing value → `nezname`, never false. Conflicting values (`BiomarkerResult.values` disagree or `conflict=True`) → `rozpor`.
- Exclusion inverts the condition: condition true → `nesplneno`.
- Cohort: any `nesplneno` → `vyloucena`; else any `nezname`/`rozpor`/`manual` → `k_overeni`; else `kandidat`. Study shows its best cohort.
- Sort: tier → targeted (diagnosis matched) first → more biomarker/diagnosis hits → fewer open items.
- Variant matching is exact after `normalize_variant` (`p.(Gly12Cys)` = `G12C`; `G13D` ≠ `G12C`). Positive marker without variant when a variant is required → `nezname`.
- Prior therapy: drug/regimen names → classes (`DRUGS`), classes imply parents (`CLASS_PARENTS`, e.g. Enhertu → T-DXd → anti-HER2 ADC → anti-HER2). Unknown drug name → `nezname`, not `nesplneno`. `scope: paliativni` counts only palliative episodes. `therapies=None` = history unknown; `[]` = treatment-naive.

## Expected Case 1–5 (tests/test_cases.py)
| Case | Must hold |
|---|---|
| 1 CRC KRAS G13D | CodeBreak 301 excluded (G12C); HARMONi-GI3 + ROSETTA candidates; AndroMETa to verify |
| 2 ESCC after FOLFOX+nivolumab, cannot swallow | 2025/R46 candidate; 2024/R41 (1L) excluded; PERPETUM excluded (swallowing) |
| 3 NSCLC KRAS G12C, PD-L1 TPS 100 % | CodeBreaK 202 excluded only for PD-L1; squamous/neoadjuvant/CRC trials excluded |
| 4 HER2+ breast after T-DM1 (adj) and T-DXd (pall.) | BreNNa candidate; ECI830 breast cohort excluded |
| 5 TNBC, neoadjuvance, ER 0 vs 1 % | TROFUSE-32 to verify (ER conflict), metastatic/adjuvant TNBC trials excluded |

## Adding a criterion field
1. Implement in `_condition` returning `(state, Czech reason)`.
2. Add to `catalog.FIELDS`, document in skill `trialink-criteria-llm`.
3. Add unit tests (each state) and rerun Case regressions.
