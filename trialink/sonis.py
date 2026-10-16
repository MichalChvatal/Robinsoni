"""Parser for the SONIS/NIS export of recruiting trials (tab-separated, unquoted multi-line fields)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

COLUMNS = [
    "typ_projektu", "id_projektu", "cislo_protokolu", "zkratka", "prirazene_diagnozy", "indikace",
    "hlavni_zkousejici", "hlavni_zkousejici_mail", "kontaktni_osoba", "kontaktni_osoba_mail",
    "kontaktni_osoba_klapka", "popis_text", "kriteria_vstupni_text", "kriteria_vylucujici_text",
    "rameno_1_text", "rameno_2_text", "rameno_3_text", "rameno_4_text",
]
_DESCRIPTION = COLUMNS.index("popis_text")
_TAIL = len(COLUMNS) - _DESCRIPTION - 1
_RECORD_START = re.compile(r"^[A-Z]{1,6}\t\d{4}[/_]")


@dataclass
class StudyRecord:
    fields: dict[str, str]
    content_hash: str
    repaired: bool = False

    @property
    def study_id(self) -> str:
        return self.fields["id_projektu"]


@dataclass
class ImportReport:
    source_name: str
    source_hash: str
    records: int = 0
    repaired: list[str] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def clean(text: str) -> str:
    return text.replace("_x000D_", "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_fields(raw: str) -> tuple[list[str] | None, bool]:
    parts = raw.split("\t")
    if len(parts) == len(COLUMNS):
        return parts, False
    if len(parts) > len(COLUMNS):
        # Extra tabs observed only inside popis_text; head and tail columns stay aligned.
        head = parts[:_DESCRIPTION]
        tail = parts[-_TAIL:]
        middle = "\t".join(parts[_DESCRIPTION:len(parts) - _TAIL])
        return head + [middle] + tail, True
    return None, False


def parse_export(data: bytes, source_name: str = "export") -> tuple[list[StudyRecord], ImportReport]:
    text = data.decode("utf-8-sig")
    report = ImportReport(source_name, hashlib.sha256(data).hexdigest())
    lines = re.split(r"\r\n|\n", text)
    if not lines or not lines[0].startswith("typ_projektu\t"):
        raise ValueError("Neočekávaná hlavička exportu SONIS")
    header = lines[0].split("\t")
    if header != COLUMNS:
        raise ValueError(f"Neočekávané sloupce exportu: {header}")

    starts = [i for i, line in enumerate(lines) if i > 0 and _RECORD_START.match(line)]
    records: list[StudyRecord] = []
    seen: set[str] = set()
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        raw = "\n".join(lines[a:b]).rstrip("\n")
        parts, repaired = _split_fields(raw)
        if parts is None:
            report.quarantined.append({"line": a + 1, "reason": f"{raw.count(chr(9)) + 1} polí místo {len(COLUMNS)}"})
            continue
        fields = {col: clean(val) for col, val in zip(COLUMNS, parts)}
        rec = StudyRecord(fields, hashlib.sha256(raw.encode("utf-8")).hexdigest(), repaired)
        if rec.study_id in seen:
            report.duplicates.append(rec.study_id)
            continue
        seen.add(rec.study_id)
        if repaired:
            report.repaired.append(rec.study_id)
        records.append(rec)
    report.records = len(records)
    return records, report


def parse_diagnoses(raw: str) -> tuple[list[str], list[str]]:
    """'C180-C20' -> ['C18','C19','C20']. Returns (3-character MKN-10 categories, warnings)."""
    cats: list[str] = []
    warnings: list[str] = []
    for token in re.split(r"[,;]", raw or ""):
        token = token.replace(" ", "")
        if not token:
            continue
        ends = token.split("-")
        codes = [re.fullmatch(r"([A-Z])(\d{2})\d*", e) for e in ends]
        if not all(codes) or len(ends) > 2:
            warnings.append(f"Nerozpoznaný kód diagnózy: {token}")
            continue
        letter = codes[0].group(1)
        lo = int(codes[0].group(2))
        hi = int(codes[-1].group(2))
        if codes[-1].group(1) != letter or hi < lo:
            warnings.append(f"Neplatný rozsah diagnóz: {token}")
            hi = lo
        if any(len(e) > 4 for e in ends):
            warnings.append(f"Nestandardní zápis kódu: {token}")
        cats += [f"{letter}{n:02d}" for n in range(lo, hi + 1)]
    return sorted(set(cats)), warnings


def split_people(names: str, mails: str) -> list[tuple[str, str]]:
    ns = [n.strip() for n in (names or "").split(",") if n.strip()]
    ms = [m.strip() for m in (mails or "").split(",") if m.strip()]
    if len(ns) == len(ms):
        return list(zip(ns, ms))
    # Unpaired names/e-mails stay separate so referral is blocked until a human pairs them.
    return [(n, "") for n in ns] + [("", m) for m in ms]
