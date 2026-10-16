# TriaLink – instrukce pro agenty

Webová aplikace (hackathon MVP) pro párování onkologických pacientů s klinickými studiemi MOÚ. Zadání: [NazevVyzvy.md](../NazevVyzvy.md).

## Neporušitelná pravidla
- **Žádná pacientská data se neukládají** – ne do IRIS, na disk, do logů, cache, URL ani do chybových hlášek. Pacient existuje jen v RAM (`PatientProfile`, `st.session_state`).
- IRIS (schéma `TriaLink`) obsahuje jen referenční data: studie, verze, kritéria, kontakty, audit importu. Nikdy nemazat/měnit objekty mimo schéma `TriaLink`.
- Párování je deterministické (`trialink/matching.py`), 4 stavy: `splneno`, `nesplneno`, `nezname`, `rozpor`. Chybějící údaj ≠ negativní výsledek. LLM nikdy nerozhoduje o způsobilosti.
- Každé kritérium v `trialink/data/criteria.json` musí mít doslovnou citaci z exportu (`quote`); validace ji ověřuje. Stav `navrh` = čeká na kontrolu onkologem.
- Varianty se párují přesně (`KRAS G12C` ≠ `G13D`); normalizace přes `normalize_variant`.
- Referral je deterministická šablona bez identifikace pacienta; odeslání je pouze simulace (`simulate_send`), žádné SMTP.
- Extrakce (`trialink/extraction.py`) používá OpenAI se `store=False`; údaje bez doslovné citace ze zprávy se zahodí. Požadavek zákazníka na lokální LLM tím NENÍ splněn – nezamlčovat.
- UI texty česky; pacientské rozhraní laicky s nápovědami (`trialink/lay_terms.py`).

## Struktura
- `trialink/` – jádro: `sonis.py` (parser exportu), `catalog.py` (studie+kritéria+kontakty, validace citací), `matching.py`, `therapies.py` (lék → skupina), `extraction.py`, `referral.py`, `db.py` (IRIS), `import_studies.py` (CLI), `demo_cases.py` (Case 1–5).
- `app/` – Streamlit: `Home.py`, `pages/1_Lekar.py`, `pages/2_Pacient.py`, `common.py`.
- `tests/` – pytest; `test_cases.py` = regresní očekávání Case 1–5.

## Příkazy (Windows, z kořene repozitáře)
- Testy: `.venv\Scripts\python.exe -m pytest -q`
- Validace katalogu: `.venv\Scripts\python.exe -m trialink.import_studies --check`
- Test připojení k IRIS (jen čtení): `.venv\Scripts\python.exe -m trialink.db`
- Import do IRIS: `.venv\Scripts\python.exe -m trialink.import_studies`
- Číselník TNM skupin (topo × morfologie, `cis_skupina.csv` → `TriaLink.TopoMorpho`): `.venv\Scripts\python.exe -m trialink.dictionaries [--check]`; prázdné topo = lymfomy (platí pro libovolnou lokalizaci, ve formuláři pro dg. C81–C96)
- Aplikace: `.venv\Scripts\python.exe -m streamlit run app/Home.py`
- Konfigurace v `.env` (vzor `.env.example`); `.env` se nikdy necommituje a hesla se nevypisují.
