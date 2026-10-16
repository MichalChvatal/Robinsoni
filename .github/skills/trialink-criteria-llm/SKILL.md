---
name: trialink-criteria-llm
description: "Use when adding or editing structured eligibility criteria for trials in TriaLink (trialink/data/criteria.json): criterion JSON grammar, fields and operators, cohorts, verbatim quote requirement, review states, drafting criteria with an LLM. Use for: new study, changed study text, oncologist review, 'citace nenalezena' errors."
---

# Strukturovaná kritéria studií

File: [trialink/data/criteria.json](../../../trialink/data/criteria.json). Validation: `catalog.validate` (runs in `load_catalog`, CLI and tests).

## Grammar
```json
{"id": "kras", "kind": "inclusion|exclusion", "field": "biomarker", "op": "positive",
 "value": {"marker": "KRAS", "variant": "G12C"}, "quote": "verbatim text from the study record"}
```
| field | op | value |
|---|---|---|
| `diagnosis` | `prefix_in` | MKN-10 prefixes `["C18","C19","C20"]` |
| `histology` | `in` | keys of `model.HISTOLOGIES` (`nemalobunecny_nsclc` = any NSCLC) |
| `disease_extent` / `setting` / `her2_status` | `in` | keys of `EXTENTS` / `SETTINGS` / `HER2_STATUSES` |
| `prior_palliative_lines` | `between` | `[min, max]` (1. linie = `[0,0]`) |
| `biomarker` | `positive` | `{"marker","variant"?}` (variant str or list) |
| `biomarker_negative` | `negative` | wild type / negative required |
| `biomarker_value` | `< <= >= >` / `between` | `{"marker","threshold"}` / `{"marker","min","max"}` |
| `prior_therapy` | `required` / `received` (+ `"scope":"paliativni"`) | classes from `therapies.ALL_CLASSES` |
| `ecog`, `age` | `<=`, `>=` | number |
| `sex` | `eq` | `F`/`M` |
| `can_swallow` | `eq` | bool (exclusion with `false` = "cannot swallow excludes") |
| `manual` | `review` | text shown as "K ověření" (always unknown) |
| `any_of` | `any` | list of sub-criteria without `kind`/`quote` |

Exclusion criteria describe the condition that excludes (`kind: exclusion`, condition true → `nesplneno`).

## Rules
- `quote` must be an exact substring of the study record (whitespace-insensitive). Copy from the export, keep mojibake.
- Encode only what the text clearly states; unclear → `manual` or omit. Distinct populations → separate cohorts. Treatment arms are not cohorts.
- `review`: `navrh` (draft) → `zkontrolovano` only after oncologist review.
- After editing: `.venv\Scripts\python.exe -m pytest -q` (Case 1–5 regressions in `tests/test_cases.py`).

## Drafting with an LLM
Study texts contain no patient data, so a cloud model may draft criteria (approved for studies). Give the model this grammar + the study record, require verbatim quotes, then run validation; every quote error means the draft is rejected or fixed by hand. Never mark LLM output as `zkontrolovano`.
