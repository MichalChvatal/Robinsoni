"""Study catalog = SONIS export + reviewed/draft structured criteria + contacts. Reference data only."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import sonis
from .therapies import ALL_CLASSES

DATA_DIR = Path(__file__).parent / "data"
CRITERIA_FILE = DATA_DIR / "criteria.json"
EXTENSIONS_FILE = DATA_DIR / "sc_klapky_simulovane.csv"
REPO_ROOT = Path(__file__).resolve().parent.parent

FIELDS = {
    "diagnosis", "histology", "disease_extent", "setting", "prior_palliative_lines", "her2_status", "biomarker",
    "biomarker_negative", "biomarker_value", "prior_therapy", "ecog", "age", "sex", "can_swallow", "manual", "any_of",
}


@dataclass
class Contact:
    role: str
    name: str
    email: str
    extension: str = ""
    extension_simulated: bool = False


@dataclass
class Study:
    study_id: str
    acronym: str
    protocol: str
    indication: str
    diagnoses_raw: str
    record: sonis.StudyRecord
    cohorts: list[dict[str, Any]]
    review: str
    contacts: list[Contact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def referral_blockers(self) -> list[str]:
        out = []
        for role in ("PI", "SC"):
            people = [c for c in self.contacts if c.role == role]
            if not people:
                out.append(f"Chybí kontakt {role} v exportu")
            elif any(not c.email or not c.name for c in people):
                out.append(f"Neúplný nebo nespárovaný kontakt {role} – nutná ruční kontrola")
        return out


def default_export_path() -> Path:
    data_root = next(d for d in REPO_ROOT.glob("RAKATHON*/RAKATHON*") if d.is_dir())
    return next(data_root.glob("*NIS*/*.csv"))


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _load_extensions(path: Path = EXTENSIONS_FILE) -> dict[str, str]:
    with path.open(encoding="utf-8") as f:
        return {row["jmeno"].strip(): row["klapka"].strip() for row in csv.DictReader(f, delimiter=";")}


def _contacts(rec: sonis.StudyRecord, extensions: dict[str, str]) -> list[Contact]:
    f = rec.fields
    out = [Contact("PI", n, m) for n, m in sonis.split_people(f["hlavni_zkousejici"], f["hlavni_zkousejici_mail"])]
    for n, m in sonis.split_people(f["kontaktni_osoba"], f["kontaktni_osoba_mail"]):
        ext = f["kontaktni_osoba_klapka"] if f["kontaktni_osoba_klapka"] not in ("", "NA") else ""
        simulated = False
        if not ext and n in extensions:
            ext, simulated = extensions[n], True
        out.append(Contact("SC", n, m, ext, simulated))
    return out


def _check_criterion(c: dict[str, Any], haystack: str, where: str, top: bool = True) -> list[str]:
    errors = []
    if c.get("field") not in FIELDS:
        errors.append(f"{where}: neznámé pole {c.get('field')}")
    if top:
        if c.get("kind") not in ("inclusion", "exclusion"):
            errors.append(f"{where}: neplatný typ {c.get('kind')}")
        quote = c.get("quote", "")
        if not quote or _normalize_ws(quote) not in haystack:
            errors.append(f"{where}: citace nenalezena v textu studie: {quote!r}")
    if c.get("field") == "prior_therapy":
        unknown = [v for v in c["value"] if v not in ALL_CLASSES]
        if unknown:
            errors.append(f"{where}: neznámé skupiny léčby {unknown}")
    if c.get("field") == "any_of":
        for i, sub in enumerate(c["value"]):
            errors += _check_criterion(sub, haystack, f"{where}/{i}", top=False)
    return errors


def validate(studies: list[Study]) -> list[str]:
    errors = []
    for s in studies:
        haystack = _normalize_ws(" ".join(s.record.fields.values()))
        for cohort in s.cohorts:
            for c in cohort["criteria"]:
                errors += _check_criterion(c, haystack, f"{s.study_id}/{cohort['id']}/{c.get('id')}")
    return errors


def build(records: list[sonis.StudyRecord], criteria: dict[str, Any]) -> tuple[list[Study], list[str]]:
    extensions = _load_extensions()
    studies: list[Study] = []
    notes: list[str] = []
    for rec in records:
        f = rec.fields
        entry = criteria["studies"].get(rec.study_id)
        warnings: list[str] = []
        if entry is None:
            cats, warnings = sonis.parse_diagnoses(f["prirazene_diagnozy"])
            cohorts = [{"id": "main", "label": f["indikace"], "criteria": [
                {"id": "dg", "kind": "inclusion", "field": "diagnosis", "op": "prefix_in", "value": cats, "quote": f["prirazene_diagnozy"]},
                {"id": "review", "kind": "inclusion", "field": "manual", "op": "review", "value": "Kritéria zatím nestrukturována", "quote": f["indikace"]},
            ] if cats else [
                {"id": "review", "kind": "inclusion", "field": "manual", "op": "review", "value": "Kritéria zatím nestrukturována", "quote": f["indikace"] or f["zkratka"]},
            ]}]
            review = "nestrukturovano"
            notes.append(f"{rec.study_id}: bez strukturovaných kritérií")
        else:
            cohorts, review = entry["cohorts"], entry.get("review", "navrh")
            _, warnings = sonis.parse_diagnoses(f["prirazene_diagnozy"])
        studies.append(Study(
            study_id=rec.study_id, acronym=f["zkratka"].strip(), protocol=f["cislo_protokolu"], indication=f["indikace"],
            diagnoses_raw=f["prirazene_diagnozy"], record=rec, cohorts=cohorts, review=review,
            contacts=_contacts(rec, extensions), warnings=warnings,
        ))
    missing = set(criteria["studies"]) - {r.study_id for r in records}
    notes += [f"{sid}: pravidla existují, ale studie v exportu chybí" for sid in sorted(missing)]
    return studies, notes


def load_catalog(export_path: Path | None = None) -> tuple[list[Study], sonis.ImportReport, list[str]]:
    path = export_path or default_export_path()
    records, report = sonis.parse_export(path.read_bytes(), path.name)
    criteria = json.loads(CRITERIA_FILE.read_text(encoding="utf-8"))
    studies, notes = build(records, criteria)
    return studies, report, notes + validate(studies)
