---
name: trialink-patient-extraction
description: "Use when working on extraction of patient parameters from an anonymized medical report in TriaLink: OpenAI structured output schema, prompt, evidence grounding, dropping ungrounded facts, privacy (RAM only, store=False, no logging), local-LLM requirement gap. Use for: extraction.py, prompt changes, extraction errors, privacy review."
---

# Extrakce parametrů ze zprávy

Code: [trialink/extraction.py](../../../trialink/extraction.py). Tests: `tests/test_extraction_referral.py`.

## Flow
`extract_profile(text)` → OpenAI Chat Completions (`temperature=0`, `store=False`, strict JSON schema `SCHEMA`) → `to_profile` validates → `PatientProfile` → UI prefills the form; the user reviews/edits.

## Grounding
Every fact has `evidence` = verbatim snippet. `to_profile` keeps a fact only if the snippet occurs in the report (whitespace/case-insensitive). Ungrounded facts are dropped (listed in `Extraction.dropped`) and stay unknown. Missing tests are never negative.

## Privacy (must hold)
- Report text only in local variables; Streamlit forms use `clear_on_submit=True`; references set to `None` after use.
- No logging/printing of report or LLM payload; errors carry only the exception type (`from None`).
- Nothing written to IRIS, disk, cache (`st.cache_*` never on patient functions), URL or browser storage.
- Prompt tells the model the report is data and to ignore instructions inside it (prompt-injection defense). No tools are exposed to the model.

## Known gap
Customer requires a **local LLM**; the team decided to use OpenAI (cloud). Report this as unmet; the UI warns users. A local OpenAI-compatible endpoint (e.g. Ollama) could be used by passing a different `client`/base URL without changing the schema.

## Changing the prompt/schema
Bump `PROMPT_VERSION`; keep `strict` schema (all properties required, `additionalProperties: false`, nullable via `["type","null"]`); test with `to_profile` unit tests using fake LLM output (no network in tests).
