---
name: trialink-ui-referral
description: "Use when changing the TriaLink Streamlit web app or referral: doctor decision-tree page, patient wizard with Czech lay tooltips, session reset/timeout, results tabs, referral template and simulated send, 30-second workflow, mobile layout, AppTest UI tests. Use for: app/*.py, referral.py, lay_terms.py."
---

# Rozhraní a referral

Code: [app/Home.py](../../../app/Home.py), [app/wizard.py](../../../app/wizard.py), [app/common.py](../../../app/common.py), [app/report_input.py](../../../app/report_input.py), [trialink/anonymize.py](../../../trialink/anonymize.py), [trialink/referral.py](../../../trialink/referral.py), [trialink/lay_terms.py](../../../trialink/lay_terms.py).
Run: `.venv\Scripts\python.exe -m streamlit run app/Home.py`. Tests: `tests/test_app.py`, `tests/test_report_input.py` (Streamlit `AppTest`).

## Conventions
- Single-page wizard in `Home.py` → `wizard.run`: role → (lékař: zpráva/formulář) → report (text / soubor / fotka, lokální OCR) → review (anonymizace + potvrzení, pak teprve OpenAI) → detail (`profile_form`) → results. Navigation state `wiz_step`, `wiz_history`; every step has `wiz_back` (← Zpět) and `wiz_restart`.
- Widgets of hidden steps lose their state → the confirmed `PatientProfile` is kept in `{prefix}_profile` and re-applied via `apply_profile` when the detail step is shown again.
- Raw report text is deleted from session right after successful extraction.
- Shared form `profile_form(prefix, lay=...)` – decision tree: 1 diagnóza → 2 rozsah → 3 léčebná situace/linie/léčba → 4 biomarkery → 5 ECOG/věk/pohlaví/polykání. Every input has an "unknown" option.
- Each form section is `st.container(border=True, key=f"{prefix}_sec{n}")` styled by `_form_css` (white card, own accent colour, white inputs with visible frame). App theme is forced light in `.streamlit/config.toml` so widgets do not follow the OS dark mode.
- Keys are prefixed (`wiz_`, `doc_`, `pat_`); `reset(prefix)` deletes them; inactivity timeout 15 min (`SESSION_TIMEOUT_S`) clears all three.
- Prefill via `apply_profile` (demo case or extraction) before widgets render, then `st.rerun()`.
- `st.cache_resource` only for reference catalog (`get_catalog`), never for patient data.
- Patient UI: lay Czech, tooltips from `TOOLTIPS`, shows only candidates / to-verify, no referral sending, advises discussing with oncologist.
- Doctor UI: tabs Kandidáti / K ověření / Napříč diagnózami / Vyloučené; each result shows satisfied / open / failed reasons and source quotes.

## Referral
- `build_referral` = deterministic template (no LLM), no patient identifiers, includes match reasons, open items and conflicts.
- Recipients from export (PI + SC); `referral_blockers` disable sending when contacts are missing/unpaired.
- `simulate_send` performs no I/O and stores nothing; UI labels it as simulation and shows elapsed time from case load (30 s target).
