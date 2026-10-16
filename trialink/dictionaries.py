"""TNM group dictionary cis_skupina.csv: allowed topography (ICD-O-3 / MKN-10) x morphology combinations.

Morphology codes have 5 digits = ICD-O-3 histology (4) + behaviour (1), e.g. 81403 = 8140/3.
Empty topography = morphology valid for any site (lymphoma groups 55/56).
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COLUMNS = ["skupina", "skupina_nazev", "topo", "morfo", "platnost_od", "platnost_do", "record_id"]
# MKN-10 diagnoses of lymphoid/haematopoietic neoplasms – their morphology is not bound to a topography.
ANY_SITE_DIAGNOSIS = re.compile(r"C(8[1-9]|9[0-6])")

BEHAVIOUR = {"0": "benigní", "1": "nejisté chování", "2": "in situ", "3": "maligní", "6": "metastáza", "9": "nejisté zda primární"}
# Common ICD-O-3 histology codes; others are shown by code only.
MORPHO_LABELS = {
    "8000": "novotvar", "8010": "karcinom NOS", "8012": "velkobuněčný karcinom", "8013": "velkobuněčný neuroendokrinní karcinom",
    "8020": "nediferencovaný karcinom", "8041": "malobuněčný karcinom", "8046": "nemalobuněčný karcinom",
    "8050": "papilární karcinom", "8070": "dlaždicobuněčný karcinom", "8071": "dlaždicobuněčný karcinom rohovějící",
    "8072": "dlaždicobuněčný karcinom nerohovějící", "8083": "bazaloidní dlaždicobuněčný karcinom", "8090": "bazaliom",
    "8120": "uroteliální karcinom", "8140": "adenokarcinom", "8144": "adenokarcinom intestinálního typu",
    "8170": "hepatocelulární karcinom", "8160": "cholangiokarcinom", "8200": "adenoidně cystický karcinom",
    "8246": "neuroendokrinní karcinom", "8260": "papilární adenokarcinom", "8310": "světlobuněčný adenokarcinom",
    "8312": "renální karcinom", "8330": "folikulární adenokarcinom", "8340": "papilární karcinom, folikulární varianta",
    "8380": "endometrioidní adenokarcinom", "8441": "serózní cystadenokarcinom", "8460": "papilární serózní cystadenokarcinom",
    "8480": "mucinózní adenokarcinom", "8490": "karcinom z prstenčitých buněk", "8500": "invazivní duktální karcinom (NST)",
    "8520": "lobulární karcinom", "8522": "duktální a lobulární karcinom", "8550": "acinární karcinom",
    "8560": "adenoskvamózní karcinom", "8720": "maligní melanom", "8890": "leiomyosarkom", "8850": "liposarkom",
    "9440": "glioblastom", "9400": "astrocytom",
}
HISTOLOGY_BY_MORPHO = {
    "8140": "adenokarcinom", "8144": "adenokarcinom", "8260": "adenokarcinom", "8480": "adenokarcinom", "8490": "adenokarcinom",
    "8070": "skvamozni", "8071": "skvamozni", "8072": "skvamozni", "8083": "skvamozni",
    "8046": "nemalobunecny_nsclc", "8012": "velkobunecny", "8041": "malobunecny",
    "8500": "duktalni_nst", "8520": "lobularni", "8310": "svetlobunecny",
}


def normalize_morpho(code: str | None) -> str | None:
    """'8140/33', '8140/3', '81403' -> '81403' (grade digit is dropped)."""
    if not code:
        return None
    digits = re.sub(r"\D", "", code)
    return digits[:5] if len(digits) >= 5 else None


def format_morpho(code: str) -> str:
    hist, beh = code[:4], code[4:5]
    label = MORPHO_LABELS.get(hist)
    return f"{hist}/{beh}" + (f" – {label}" if label else "") + (f", {BEHAVIOUR[beh]}" if beh in BEHAVIOUR else "")


def histology_from_morpho(code: str | None) -> str | None:
    return HISTOLOGY_BY_MORPHO.get(code[:4]) if code else None


@dataclass(frozen=True)
class TopoMorphoRow:
    record_id: int
    skupina: int
    skupina_nazev: str
    topo: str | None  # None = any topography
    morfo: str
    platnost_od: date | None
    platnost_do: date | None

    def valid_on(self, day: date) -> bool:
        return (self.platnost_od is None or self.platnost_od <= day) and (self.platnost_do is None or day <= self.platnost_do)


def _date(text: str) -> date | None:
    text = (text or "").strip()
    return datetime.strptime(text, "%d.%m.%Y").date() if text else None


def parse(data: bytes) -> tuple[list[TopoMorphoRow], list[str]]:
    """Returns (rows, errors). Invalid rows are reported, never guessed."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1250")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if reader.fieldnames != COLUMNS:
        raise ValueError(f"Neočekávané sloupce číselníku: {reader.fieldnames}")
    rows, errors, seen = [], [], set()
    for n, r in enumerate(reader, start=2):
        try:
            topo, morfo = r["topo"].strip().upper() or None, r["morfo"].strip()
            if (topo and not re.fullmatch(r"C\d{3}", topo)) or not re.fullmatch(r"\d{5}", morfo):
                raise ValueError(f"neplatný kód topo={topo!r} morfo={morfo!r}")
            row = TopoMorphoRow(int(r["record_id"]), int(r["skupina"]), r["skupina_nazev"].strip(), topo, morfo,
                                _date(r["platnost_od"]), _date(r["platnost_do"]))
        except (ValueError, TypeError) as exc:
            errors.append(f"řádek {n}: {exc}")
            continue
        if row.record_id in seen:
            errors.append(f"řádek {n}: duplicitní record_id {row.record_id}")
            continue
        seen.add(row.record_id)
        rows.append(row)
    return rows, errors


def default_path() -> Path:
    if os.getenv("TRIALINK_TNM_SKUPINA"):
        return Path(os.environ["TRIALINK_TNM_SKUPINA"])
    for base in (REPO_ROOT, REPO_ROOT.parent):
        for path in base.glob("RAKATHON*/RAKATHON*/*TNM*/cis_skupina.csv"):
            return path
    raise FileNotFoundError("Číselník cis_skupina.csv nebyl nalezen (nastavte TRIALINK_TNM_SKUPINA).")


def load_file(path: Path | None = None) -> tuple[list[TopoMorphoRow], list[str], str, str]:
    path = path or default_path()
    data = path.read_bytes()
    rows, errors = parse(data)
    return rows, errors, path.name, hashlib.sha256(data).hexdigest()


class TopoMorphoIndex:
    """Allowed morphologies per topography, restricted to rows valid on the given day."""

    def __init__(self, rows: list[TopoMorphoRow], day: date | None = None):
        day = day or date.today()
        self.by_topo: dict[str, set[str]] = defaultdict(set)
        self.groups: dict[tuple[str, str], set[tuple[int, str]]] = defaultdict(set)
        self.topo_groups: dict[str, set[str]] = defaultdict(set)
        self.any_site: set[str] = set()
        self.any_site_groups: dict[str, set[tuple[int, str]]] = defaultdict(set)
        for r in rows:
            if not r.valid_on(day):
                continue
            if r.topo is None:
                self.any_site.add(r.morfo)
                self.any_site_groups[r.morfo].add((r.skupina, r.skupina_nazev))
            else:
                self.by_topo[r.topo].add(r.morfo)
                self.groups[(r.topo, r.morfo)].add((r.skupina, r.skupina_nazev))
                self.topo_groups[r.topo].add(r.skupina_nazev)

    @staticmethod
    def _clean(icd: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", (icd or "").upper())

    def is_any_site(self, icd: str | None) -> bool:
        """Lymphoma/haematological diagnosis (C81–C96): morphologies from rows without topography."""
        return bool(ANY_SITE_DIAGNOSIS.match(self._clean(icd)))

    def topographies(self, icd: str | None) -> list[str]:
        """'C18' -> all C18x topographies; 'C186' -> ['C186'] if known."""
        icd = self._clean(icd)
        if not icd:
            return []
        return sorted(t for t in self.by_topo if t.startswith(icd)) if len(icd) < 4 else ([icd] if icd in self.by_topo else [])

    def morphologies(self, icd: str | None) -> list[str]:
        if self.is_any_site(icd):
            return sorted(self.any_site)
        return sorted({m for t in self.topographies(icd) for m in self.by_topo[t]})

    def is_allowed(self, icd: str | None, morfo: str | None) -> bool | None:
        """None when it cannot be decided (unknown topography or no morphology)."""
        if not morfo:
            return None
        if morfo in self.any_site:
            return True
        topos = self.topographies(icd)
        if not topos:
            return None
        return any(morfo in self.by_topo[t] for t in topos)

    def group_names(self, icd: str | None, morfo: str | None = None) -> list[str]:
        if self.is_any_site(icd):
            pairs = self.any_site_groups.get(morfo, ()) if morfo else {g for gs in self.any_site_groups.values() for g in gs}
            return sorted({name for _, name in pairs})
        topos = self.topographies(icd)
        if morfo:
            return sorted({name for t in topos for _, name in self.groups.get((t, morfo), ())})
        return sorted({name for t in topos for name in self.topo_groups[t]})


if __name__ == "__main__":
    # python -m trialink.dictionaries [--check]   – validate and import cis_skupina.csv into IRIS
    import sys

    from dotenv import load_dotenv

    rows, errors, name, digest = load_file()
    print(f"{name}: {len(rows)} platných řádků, chyb {len(errors)}, sha256={digest[:12]}…")
    for e in errors[:20]:
        print("  CHYBA", e)
    if errors or "--check" in sys.argv:
        raise SystemExit(1 if errors else 0)
    load_dotenv()
    from . import db

    conn = db.connect()
    try:
        db.ensure_schema(conn)
        print("IRIS:", db.save_topo_morpho(conn, rows, name, digest))
    finally:
        conn.close()
