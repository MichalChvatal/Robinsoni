---
name: trialink-iris-db
description: "Use when working with the InterSystems IRIS database in TriaLink: connection via .env, TriaLink schema tables, idempotent versioned upserts, loading active studies, IRIS SQL syntax, VECTOR columns. Use for: db.py changes, new tables, import into IRIS, connection errors."
---

# TriaLink × InterSystems IRIS

Code: [trialink/db.py](../../../trialink/db.py). Driver: `intersystems-irispython` (`import iris`).

## Rules
- Only **reference data** (studies, criteria, contacts, import audit). Never patient data, extraction results or match history.
- Only the `TriaLink` schema. No `DROP`/`ALTER` outside it; never reuse tutorial scripts from `DockerFHIR/` that `DROP TABLE`.
- Connection from env: `IRIS_HOST`, `IRIS_PORT` (1972), `IRIS_NAMESPACE` (`USER`), `IRIS_USER`, `IRIS_PASSWORD`. Never print or commit credentials.
- Always parameterized SQL (`?`), close cursors/connections (Community edition has a connection limit).

## Schema
| Table | Purpose |
|---|---|
| `SourceImport` | one row per import: source hash, counts, repaired/quarantined notes |
| `Study` | one row per study **version**; `Active=1` marks current; `RecordJson` = original 18 fields, `CohortsJson` = rules |
| `Criterion` | flattened rules per version (for SQL facets / review) |
| `Contact` | PI/SC per version; `ExtensionSimulated=1` for demo extensions |

New tables: add DDL to `DDL` dict; `ensure_schema` creates missing ones only (idempotent).

## Versioning
`version_hash` = record hash + cohorts + review. Unchanged → skip. Changed → previous `Active=0`, insert next `Version`. Old versions are kept for audit.

## Commands
- Read-only check: `.venv\Scripts\python.exe -m trialink.db`
- Import: `.venv\Scripts\python.exe -m trialink.import_studies`

## Vectors (optional, not in MVP)
`VECTOR(DOUBLE, 1536)` + `TO_VECTOR(?)` + `VECTOR_COSINE(col, TO_VECTOR(?, DOUBLE))`, see `DockerFHIR/skills/iris-vector-search/SKILL.md`. Embed only study criterion text; never send patient text/vectors to IRIS. Vector similarity may only rank/explain, never override a failed rule.
