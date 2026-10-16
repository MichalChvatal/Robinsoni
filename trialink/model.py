"""Transient patient model. Instances live only in RAM (request/session) and are never persisted."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HISTOLOGIES = {
    "adenokarcinom": "Adenokarcinom",
    "skvamozni": "Skvamózní (dlaždicobuněčný) karcinom",
    "nemalobunecny_nsclc": "NSCLC bez bližšího určení",
    "velkobunecny": "Velkobuněčný karcinom",
    "malobunecny": "Malobuněčný karcinom",
    "duktalni_nst": "Invazivní duktální karcinom (NST)",
    "lobularni": "Lobulární karcinom",
    "svetlobunecny": "Světlobuněčný karcinom",
    "jiny": "Jiný",
}

EXTENTS = {
    "lokalizovane_resekabilni": "Časné / lokalizované, resekabilní",
    "lokalne_pokrocile_neresekabilni": "Lokálně pokročilé, neresekabilní",
    "metastaticke": "Metastatické",
}

SETTINGS = {
    "neoadjuvance": "Neoadjuvance (léčba před operací)",
    "adjuvance": "Adjuvance (léčba po operaci)",
    "paliativni": "Paliativní léčba pokročilého onemocnění",
}

BIOMARKER_STATUSES = {
    "pozitivni": "pozitivní",
    "negativni": "negativní",
    "nelze": "nelze stanovit",
    "nevysetreno": "nevyšetřeno / čeká se",
}

HER2_STATUSES = {
    "pozitivni": "HER2 pozitivní (IHC 3+ nebo ISH+)",
    "low": "HER2-low (IHC 1+ nebo 2+/ISH−)",
    "ultralow": "HER2-ultralow",
    "0": "HER2 0",
}

_THREE_TO_ONE = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E", "Gly": "G",
    "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P", "Ser": "S",
    "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Ter": "*",
}


def normalize_icd(code: str | None) -> str | None:
    if not code:
        return None
    c = re.sub(r"[^A-Z0-9]", "", code.upper())
    return c or None


def normalize_variant(variant: str | None) -> str | None:
    """'p.(Gly12Cys)', 'p.G12C', 'g12c' -> 'G12C'. Unknown formats are upper-cased unchanged."""
    if not variant:
        return None
    v = variant.strip().replace("p.", "").replace("(", "").replace(")", "").replace(" ", "")
    m = re.fullmatch(r"([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2})", v)
    if m and m.group(1) in _THREE_TO_ONE and m.group(3) in _THREE_TO_ONE:
        return f"{_THREE_TO_ONE[m.group(1)]}{m.group(2)}{_THREE_TO_ONE[m.group(3)]}"
    return v.upper()


@dataclass
class BiomarkerResult:
    marker: str
    status: str = "nevysetreno"
    variant: str | None = None
    values: list[float] = field(default_factory=list)
    method: str | None = None
    conflict: bool = False


@dataclass
class TherapyEpisode:
    name: str
    setting: str | None = None


@dataclass
class PatientProfile:
    icd10: str | None = None
    morphology: str | None = None
    histology: str | None = None
    disease_extent: str | None = None
    setting: str | None = None
    prior_palliative_lines: int | None = None
    her2_status: str | None = None
    biomarkers: dict[str, BiomarkerResult] = field(default_factory=dict)
    # None = therapy history unknown; [] = known to be treatment-naive.
    therapies: list[TherapyEpisode] | None = None
    ecog: int | None = None
    age: int | None = None
    sex: str | None = None
    can_swallow: bool | None = None
    conflicts: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)

    def biomarker(self, marker: str) -> BiomarkerResult | None:
        return self.biomarkers.get(marker.upper())

    def set_biomarker(self, result: BiomarkerResult) -> None:
        result.marker = result.marker.upper()
        self.biomarkers[result.marker] = result
