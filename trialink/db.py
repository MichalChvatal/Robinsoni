"""IRIS persistence for REFERENCE data only (studies, criteria, contacts, import audit). Never patient data.

All objects live in the TriaLink schema. Nothing outside it is created, altered or dropped.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

from .catalog import Contact, Study
from .sonis import ImportReport, StudyRecord

SCHEMA = "TriaLink"

DDL = {
    "SourceImport": """CREATE TABLE TriaLink.SourceImport (
        SourceName VARCHAR(500), SourceHash VARCHAR(64), ImportedAt TIMESTAMP, Records INTEGER,
        NewVersions INTEGER, Unchanged INTEGER, Notes VARCHAR(200000))""",
    "Study": """CREATE TABLE TriaLink.Study (
        StudyId VARCHAR(50) NOT NULL, Version INTEGER NOT NULL, Active BIT, VersionHash VARCHAR(64),
        Acronym VARCHAR(200), Protocol VARCHAR(200), Indication VARCHAR(2000), Diagnoses VARCHAR(500),
        Review VARCHAR(30), SourceHash VARCHAR(64), RecordJson VARCHAR(3000000), CohortsJson VARCHAR(1000000),
        ImportedAt TIMESTAMP)""",
    "Criterion": """CREATE TABLE TriaLink.Criterion (
        StudyId VARCHAR(50) NOT NULL, Version INTEGER NOT NULL, CohortId VARCHAR(50), CriterionId VARCHAR(50),
        Kind VARCHAR(20), Field VARCHAR(50), RuleJson VARCHAR(20000), Quote VARCHAR(2000), Review VARCHAR(30))""",
    "Contact": """CREATE TABLE TriaLink.Contact (
        StudyId VARCHAR(50) NOT NULL, Version INTEGER NOT NULL, ContactRole VARCHAR(10), Name VARCHAR(200),
        Email VARCHAR(200), Extension VARCHAR(20), ExtensionSimulated BIT)""",
}


def connect():
    import iris  # intersystems-irispython

    missing = [k for k in ("IRIS_HOST", "IRIS_PASSWORD") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Chybí proměnné prostředí: {', '.join(missing)} (viz .env.example)")
    return iris.connect(
        hostname=os.environ["IRIS_HOST"], port=int(os.getenv("IRIS_PORT", "1972")),
        namespace=os.getenv("IRIS_NAMESPACE", "USER"), username=os.getenv("IRIS_USER", "_SYSTEM"),
        password=os.environ["IRIS_PASSWORD"],
    )


def ensure_schema(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", [SCHEMA])
    existing = {r[0] for r in cur.fetchall()}
    created = []
    for name, ddl in DDL.items():
        if name not in existing:
            cur.execute(ddl)
            created.append(name)
    conn.commit()
    return created


def version_hash(study: Study) -> str:
    payload = json.dumps({"record": study.record.content_hash, "cohorts": study.cohorts, "review": study.review}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _keep_curated(cur, s: Study) -> None:
    """A fallback study must not overwrite criteria already curated in IRIS for the same study text."""
    if s.review != "nestrukturovano":
        return
    cur.execute("SELECT RecordJson, CohortsJson, Review FROM TriaLink.Study WHERE StudyId = ? AND Active = 1", [s.study_id])
    row = cur.fetchone()
    if row and row[2] != "nestrukturovano" and json.loads(row[0]).get("hash") == s.record.content_hash:
        s.cohorts, s.review = json.loads(row[1]), row[2]


def active_versions(conn) -> dict[str, tuple[int, str, str]]:
    """StudyId -> (Version, VersionHash, Review) of active versions."""
    cur = conn.cursor()
    cur.execute("SELECT StudyId, Version, VersionHash, Review FROM TriaLink.Study WHERE Active = 1")
    return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}


def save_study(conn, study: Study, source_name: str = "ruční zadání") -> dict[str, int]:
    report = ImportReport(source_name, study.record.content_hash, records=1)
    return save_catalog(conn, [study], report, [])


def save_catalog(conn, studies: list[Study], report: ImportReport, notes: list[str]) -> dict[str, int]:
    """Idempotent: unchanged studies are skipped; changed ones get a new active version, old versions are kept."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_versions = unchanged = 0
    for s in studies:
        _keep_curated(cur, s)
        h = version_hash(s)
        cur.execute("SELECT Version, VersionHash FROM TriaLink.Study WHERE StudyId = ? AND Active = 1", [s.study_id])
        row = cur.fetchone()
        if row and row[1] == h:
            unchanged += 1
            continue
        cur.execute("SELECT MAX(Version) FROM TriaLink.Study WHERE StudyId = ?", [s.study_id])
        version = (cur.fetchone()[0] or 0) + 1
        cur.execute("UPDATE TriaLink.Study SET Active = 0 WHERE StudyId = ?", [s.study_id])
        cur.execute(
            "INSERT INTO TriaLink.Study (StudyId, Version, Active, VersionHash, Acronym, Protocol, Indication, Diagnoses, Review,"
            " SourceHash, RecordJson, CohortsJson, ImportedAt) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [s.study_id, version, h, s.acronym, s.protocol, s.indication, s.diagnoses_raw, s.review, report.source_hash,
             json.dumps({"fields": s.record.fields, "hash": s.record.content_hash, "repaired": s.record.repaired}, ensure_ascii=False),
             json.dumps(s.cohorts, ensure_ascii=False), now],
        )
        rows = [[s.study_id, version, cohort["id"], c["id"], c["kind"], c["field"], json.dumps(c, ensure_ascii=False), c.get("quote", ""), s.review]
                for cohort in s.cohorts for c in cohort["criteria"]]
        if rows:
            cur.executemany("INSERT INTO TriaLink.Criterion (StudyId, Version, CohortId, CriterionId, Kind, Field, RuleJson, Quote, Review)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        contacts = [[s.study_id, version, c.role, c.name, c.email, c.extension, 1 if c.extension_simulated else 0] for c in s.contacts]
        if contacts:
            cur.executemany("INSERT INTO TriaLink.Contact (StudyId, Version, ContactRole, Name, Email, Extension, ExtensionSimulated)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?)", contacts)
        new_versions += 1
    cur.execute(
        "INSERT INTO TriaLink.SourceImport (SourceName, SourceHash, ImportedAt, Records, NewVersions, Unchanged, Notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [report.source_name, report.source_hash, now, report.records, new_versions, unchanged,
         json.dumps({"repaired": report.repaired, "quarantined": report.quarantined, "duplicates": report.duplicates, "notes": notes}, ensure_ascii=False)],
    )
    conn.commit()
    return {"new_versions": new_versions, "unchanged": unchanged}


def load_active_studies(conn) -> list[Study]:
    cur = conn.cursor()
    cur.execute("SELECT StudyId, Version, Acronym, Protocol, Indication, Diagnoses, Review, RecordJson, CohortsJson"
                " FROM TriaLink.Study WHERE Active = 1 ORDER BY StudyId")
    studies = []
    for sid, version, acronym, protocol, indication, diagnoses, review, record_json, cohorts_json in cur.fetchall():
        rec = json.loads(record_json)
        cur2 = conn.cursor()
        cur2.execute("SELECT ContactRole, Name, Email, Extension, ExtensionSimulated FROM TriaLink.Contact WHERE StudyId = ? AND Version = ?", [sid, version])
        contacts = [Contact(r[0], r[1] or "", r[2] or "", r[3] or "", bool(r[4])) for r in cur2.fetchall()]
        studies.append(Study(sid, acronym, protocol, indication, diagnoses or "", StudyRecord(rec["fields"], rec["hash"], rec["repaired"]),
                             json.loads(cohorts_json), review, contacts))
    return studies


if __name__ == "__main__":
    # Read-only connectivity check: python -m trialink.db
    from dotenv import load_dotenv

    load_dotenv()
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT $ZVERSION")
        print("Připojeno:", cur.fetchone()[0])
        cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ?", [SCHEMA])
        print("Tabulky TriaLink:", [r[0] for r in cur.fetchall()] or "zatím žádné")
    finally:
        conn.close()
