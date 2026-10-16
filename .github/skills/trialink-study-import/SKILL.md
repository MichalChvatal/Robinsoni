---
name: trialink-study-import
description: "Use when importing or updating the SONIS/NIS export of clinical trials in TriaLink: tab-separated multi-line records, repair of extra tabs, quarantine, duplicates, diagnosis ranges (C180-C20), contacts PI/SC and simulated phone extensions, TNM and biomarker dictionaries. Use for: new export file, import errors, sonis.py or catalog.py changes."
---

# Import exportu studií (SONIS)

Code: [trialink/sonis.py](../../../trialink/sonis.py), [trialink/catalog.py](../../../trialink/catalog.py), CLI [trialink/import_studies.py](../../../trialink/import_studies.py).

## Export format (verified on KH_otevreny_nabor_k_2026-09-29.csv)
- UTF-8, header of 18 tab-separated columns (`sonis.COLUMNS`), CRLF, **no quoting**; fields contain raw newlines and literal `_x000D_`.
- A record starts with a line matching `^[A-Z]{1,6}\t\d{4}[/_]` (e.g. `KH\t2024/59`, `KH\t2025_55`).
- Extra tabs occur inside `popis_text` (study 2021/18F): head 11 + tail 6 columns are aligned, the middle is joined back → `report.repaired`.
- Fewer fields → `report.quarantined` (never guessed). Duplicate `id_projektu` → `report.duplicates`.
- Mojibake (`?`, `e`, `d` instead of ≥/≤, `pokroèilými`) is kept as is. Never auto-repair clinical thresholds.

## Diagnoses
`parse_diagnoses("C180-C20") -> ["C18","C19","C20"]`; nonstandard codes (`C5000`, `C09-C010`, `víc diagnóz`) produce warnings. Rules in `criteria.json` define the reviewed diagnosis list.

## Contacts
- PI = `hlavni_zkousejici(_mail)`, SC = `kontaktni_osoba(_mail)`; multiple names comma-separated, paired by order only when counts match.
- `kontaktni_osoba_klapka` is `NA` in export → simulated extensions from `trialink/data/sc_klapky_simulovane.csv` (flag `extension_simulated`).
- Missing/unpaired contact → `Study.referral_blockers` (referral cannot be sent).

## New export workflow
1. `.venv\Scripts\python.exe -m trialink.import_studies --check --export <path>` – read counts, repaired, quarantine, warnings.
2. Studies without rules get a fallback cohort (diagnosis + manual review) and review state `nestrukturovano`; add rules (skill `trialink-criteria-llm`).
3. Run tests, then import into IRIS without `--check`.

## Dictionaries
- TNM: `cis_skupina.csv` (topo+morfo → skupina), `cis_stadium.csv` (skupina+T/N/M → stadium), semicolon-separated.
- Biomarkers: `Biomarkery_OPAT_ciselniky_2026-09-29.xlsx` sheets `prediktor` (46), `hodnoceni_vysledek_vysetreni`, `metoda`. TMB, HRD, STK11, TROP2, MTAP, CCNE1 are missing → TriaLink extension, mark as such.
