import json
from datetime import date
from pathlib import Path

import pytest
from conftest import requires_catalog

from trialink import biomarkers as bm
from trialink.demo_cases import CASES
from trialink.extraction import KNOWN_MARKERS, to_profile
from trialink.model import BiomarkerResult as B

ROOT = Path(__file__).resolve().parent.parent
HEADER = ";".join(bm.COLUMNS) + "\n"


@pytest.fixture(scope="module")
def bio():
    rows, errors, _, _ = bm.load_file()
    assert not errors
    return bm.BiomarkerDictionary(rows)


def _criteria_markers() -> set[str]:
    found = set()

    def walk(o):
        if isinstance(o, dict):
            if o.get("field") in ("biomarker", "biomarker_negative", "biomarker_value"):
                found.add(o["value"]["marker"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads((ROOT / "trialink" / "data" / "criteria.json").read_text(encoding="utf-8")))
    return found


def test_packaged_csv_covers_criteria_and_extraction(bio):
    assert len(bio.codes) == 61
    assert {"KRAS", "PD-L1 TPS", "PD-L1 CPS", "dMMR/MSI-H", "CCNE1", "MTAP", "HPV", "TROP2"} <= set(bio.codes)
    missing = {m for m in _criteria_markers() if bio.canonical(m) is None}
    assert not missing, f"markery z kritérií chybí v číselníku: {missing}"
    assert KNOWN_MARKERS == bio.codes


def test_aliases_are_canonicalized(bio):
    assert bio.canonical("Ki-67") == "Ki67"
    assert bio.canonical("KI67") == "Ki67"
    assert bio.canonical("BRAF mutace") == "BRAF"
    assert bio.canonical("DMMR/MSI-H") == "dMMR/MSI-H"
    assert bio.canonical("MSI-H") == "dMMR/MSI-H"
    assert bio.canonical("HRD") == "GIS/HRD"
    assert bio.canonical("CLDN 18-2") == "CLDN18.2"
    assert bio.canonical("Kodelécia 1p/19q") == "1p/19q"
    assert bio.canonical("PD-L1") is None  # TPS or CPS must be chosen
    assert bio.canonical("XYZ1") is None


def test_allowed_combinations_produce_no_warning(bio):
    ok = [B("KRAS", "pozitivni", "G12C", result_type="mutace"), B("ALK", "pozitivni", result_type="fuze"),
          B("ER", "pozitivni", values=[80], result_type="exprese"), B("PD-L1 TPS", "pozitivni", values=[50]),
          B("MGMTp", "pozitivni", result_type="metylace"), B("BRCA1", "nevysetreno"), B("TMB", "pozitivni", values=[12])]
    assert bio.check_all(ok) == []


@pytest.mark.parametrize("result, fragment", [
    (B("ALK", "pozitivni", "G12C"), "varianta se u tohoto biomarkeru nezadává"),
    (B("CDK4", "pozitivni", result_type="mutace"), "není pro tento biomarker povolen"),
    (B("KRAS", "pozitivni", "G12C", result_type="exprese"), "není pro tento biomarker povolen"),
    (B("MET", "pozitivni", "Y1003", result_type="amplifikace"), "varianta patří jen k typu"),
    (B("BRAF", "pozitivni", values=[5]), "hodnota (% / skóre) se u tohoto biomarkeru nezadává"),
    (B("XYZ1", "pozitivni"), "není v číselníku"),
])
def test_invalid_combinations_warn(bio, result, fragment):
    issues = bio.check(result)
    assert len(issues) >= 1 and fragment in " ".join(issues)


def test_demo_cases_use_valid_combinations(bio):
    for name, make in CASES.items():
        assert bio.check_all(make().biomarkers.values()) == [], name


def test_parse_reports_invalid_lines():
    bad = HEADER + "\n".join([
        "KRAS;KRAS;14;x;;;;;;;test",
        "kras;KRAS 2;;x;;;;;;;test",       # duplicate code (case-insensitive)
        "AR;AR;;;;;;;;;test",              # no result type
        "ER;ER;abc;;x;;;;;;test",          # invalid OPAT code
        ";bez kódu;;x;;;;;;;test",
    ]) + "\n"
    rows, errors = bm.parse(bad.encode("utf-8"))
    assert [r.code for r in rows] == ["KRAS"]
    assert len(errors) == 4
    with pytest.raises(ValueError):
        bm.parse(b"kod;nazev\nKRAS;KRAS\n")


def test_csv_round_trip():
    rows, _, _, _ = bm.load_file()
    again, errors = bm.parse(bm.to_csv(rows).encode("utf-8"))
    assert not errors and sorted(again, key=str) == sorted(rows, key=str)


def test_csv_in_sync_with_excel():
    xlsx = ROOT / "biomarkery_prehled_upraveno.xlsx"
    if not xlsx.is_file():
        pytest.skip("biomarkery_prehled_upraveno.xlsx není k dispozici")
    from_excel, errors = bm.from_xlsx(xlsx)
    assert not errors
    key = lambda r: (r.code, r.result_type, r.note, r.label)  # noqa: E731 – OPAT codes need the customer file
    assert sorted(map(key, from_excel)) == sorted(map(key, bm.load_file()[0]))


def test_extraction_canonicalizes_marker_and_keeps_result_type():
    report = "Ki-67 30 %. KRAS mutace p.G12C."
    data = {k: {"value": None, "evidence": None} for k in bm_scalars()}
    data.update(therapy_history_known=False, therapies=[], conflicts=[], biomarkers=[
        {"marker": "Ki-67", "status": "pozitivni", "result_type": "exprese", "variant": None, "values": [30], "method": None,
         "conflict": False, "evidence": "Ki-67 30 %"},
        {"marker": "KRAS", "status": "pozitivni", "result_type": "mutace", "variant": "G12C", "values": [], "method": None,
         "conflict": False, "evidence": "KRAS mutace p.G12C"},
    ])
    p = to_profile(data, report, date(2026, 10, 17)).profile
    assert p.biomarker("Ki67").result_type == "exprese" and "biomarker:KI67" in p.evidence
    assert p.biomarker("KRAS").result_type == "mutace"


def bm_scalars():
    from trialink.extraction import SCALARS
    return SCALARS


@requires_catalog
def test_form_warns_about_invalid_combination(monkeypatch):
    from test_app import _doctor_with_case

    monkeypatch.setenv("TRIALINK_DEMO", "1")
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    assert not [w for w in at.warning if "číselníku biomarkerů" in w.value]
    at.session_state["doc_bio_init"] = [{"marker": "ALK", "status": "pozitivni", "typ": "mutace", "varianta": "G12C",
                                         "hodnoty": "", "rozpor": False}]
    at.session_state["doc_editor_rev"] = at.session_state["doc_editor_rev"] + 1
    at.run()
    assert not at.exception, at.exception
    text = " ".join(w.value for w in at.warning)
    assert "ALK: typ „Mutace / varianta“ není pro tento biomarker povolen" in text
    assert "ALK: varianta se u tohoto biomarkeru nezadává" in text
