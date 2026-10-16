---
name: trialink-ui-referral
description: "Use when changing the TriaLink Streamlit web app or referral: doctor decision-tree page, patient wizard with Czech lay tooltips, session reset/timeout, results tabs, referral template and simulated send, 30-second workflow, mobile layout, AppTest UI tests. Use for: app/*.py, referral.py, lay_terms.py."
---

# Rozhraní a referral

Code: [app/common.py](../../../app/common.py), [app/pages/1_Lekar.py](../../../app/pages/1_Lekar.py), [app/pages/2_Pacient.py](../../../app/pages/2_Pacient.py), [trialink/referral.py](../../../trialink/referral.py), [trialink/lay_terms.py](../../../trialink/lay_terms.py).
Run: `.venv\Scripts\python.exe -m streamlit run app/Home.py`. Tests: `tests/test_app.py` (Streamlit `AppTest`).

## Conventions
- Two separate pages, shared form `profile_form(prefix, lay=...)` – decision tree: 1 diagnóza → 2 rozsah → 3 léčebná situace/linie/léčba → 4 biomarkery → 5 ECOG/věk/pohlaví/polykání. Every input has an "unknown" option.
- Widget keys are prefixed (`doc_`, `pat_`); `reset(prefix)` deletes all of them; inactivity timeout 15 min (`SESSION_TIMEOUT_S`).
- Prefill via `apply_profile` (demo case or extraction) before widgets render, then `st.rerun()`.
- `st.cache_resource` only for reference catalog (`get_catalog`), never for patient data.
- Patient UI: lay Czech, tooltips from `TOOLTIPS`, shows only candidates / to-verify, no referral sending, advises discussing with oncologist.
- Doctor UI: tabs Kandidáti / K ověření / Napříč diagnózami / Vyloučené; each result shows satisfied / open / failed reasons and source quotes.

## Referral
- `build_referral` = deterministic template (no LLM), no patient identifiers, includes match reasons, open items and conflicts.
- Recipients from export (PI + SC); `referral_blockers` disable sending when contacts are missing/unpaired.
- `simulate_send` performs no I/O and stores nothing; UI labels it as simulation and shows elapsed time from case load (30 s target).
