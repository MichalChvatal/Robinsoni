import pytest

from trialink import sonis
from trialink.catalog import load_catalog
from trialink.matching import CONF, NOT, SAT, UNK, evaluate_criterion
from trialink.model import BiomarkerResult, PatientProfile, TherapyEpisode, normalize_variant
from trialink.therapies import classes_of

HEADER = "\t".join(sonis.COLUMNS)


def _record(sid: str, description: str = "popis") -> str:
    fields = ["KH", sid, "P1", "ACR", "C34", "plíce", "PI Jméno", "pi@mou.cz", "SC Jméno", "sc@mou.cz", "NA", description,
              "vstupní\nřádek 2", "vylučující", "r1", "r2", "r3", "r4"]
    return "\t".join(fields)


def test_parse_multiline_and_repair():
    data = "\r\n".join([HEADER, _record("2025/1"), _record("2025/2", "a\tb\tc")]).encode("utf-8")
    records, report = sonis.parse_export(data)
    assert report.records == 2
    assert report.repaired == ["2025/2"]
    assert records[0].fields["kriteria_vstupni_text"] == "vstupní\nřádek 2"
    assert records[1].fields["popis_text"] == "a\tb\tc"
    assert records[1].fields["kriteria_vstupni_text"] == "vstupní\nřádek 2"


def test_parse_quarantines_short_and_duplicates():
    short = "KH\t2025/3\tonly\tthree"
    data = "\n".join([HEADER, _record("2025/1"), _record("2025/1"), short]).encode("utf-8")
    records, report = sonis.parse_export(data)
    assert len(records) == 1
    assert report.duplicates == ["2025/1"]
    assert len(report.quarantined) == 1


def test_diagnosis_ranges():
    assert sonis.parse_diagnoses("C180-C20")[0] == ["C18", "C19", "C20"]
    cats, warnings = sonis.parse_diagnoses("C5000-C509")
    assert cats == ["C50"] and warnings


def test_real_export_all_quotes_verified():
    studies, report, issues = load_catalog()
    assert report.records == 41
    assert not report.quarantined
    assert [i for i in issues if "citace" in i or "neznám" in i] == []


def test_variant_normalization():
    assert normalize_variant("p.(Gly12Cys)") == "G12C"
    assert normalize_variant("p.G12C") == "G12C"
    assert normalize_variant("g13d") == "G13D"


def _p(**kw) -> PatientProfile:
    return PatientProfile(**kw)


KRAS_G12C = {"id": "k", "kind": "inclusion", "field": "biomarker", "op": "positive", "value": {"marker": "KRAS", "variant": "G12C"}}


@pytest.mark.parametrize("result,expected", [
    (BiomarkerResult("KRAS", "pozitivni", "G12C"), SAT),
    (BiomarkerResult("KRAS", "pozitivni", "p.(Gly12Cys)"), SAT),
    (BiomarkerResult("KRAS", "pozitivni", "G13D"), NOT),
    (BiomarkerResult("KRAS", "negativni"), NOT),
    (BiomarkerResult("KRAS", "pozitivni"), UNK),
    (BiomarkerResult("KRAS", "nevysetreno"), UNK),
    (BiomarkerResult("KRAS", "pozitivni", "G12C", conflict=True), CONF),
    (None, UNK),
])
def test_variant_level_matching(result, expected):
    p = _p()
    if result:
        p.set_biomarker(result)
    assert evaluate_criterion(KRAS_G12C, p).state == expected


def test_exclusion_inverts_condition():
    c = {"id": "b", "kind": "exclusion", "field": "biomarker", "op": "positive", "value": {"marker": "BRAF", "variant": "V600E"}}
    p = _p()
    assert evaluate_criterion(c, p).state == UNK
    p.set_biomarker(BiomarkerResult("BRAF", "pozitivni", "V600E"))
    assert evaluate_criterion(c, p).state == NOT
    p.set_biomarker(BiomarkerResult("BRAF", "negativni"))
    assert evaluate_criterion(c, p).state == SAT


def test_values_conflict_and_thresholds():
    c = {"id": "t", "kind": "inclusion", "field": "biomarker_value", "op": "<", "value": {"marker": "PD-L1 TPS", "threshold": 1}}
    p = _p()
    p.set_biomarker(BiomarkerResult("PD-L1 TPS", "pozitivni", values=[100]))
    assert evaluate_criterion(c, p).state == NOT
    p.set_biomarker(BiomarkerResult("PD-L1 TPS", "negativni", values=[0, 5]))
    assert evaluate_criterion(c, p).state == CONF


def test_any_of():
    c = {"id": "a", "kind": "inclusion", "field": "any_of", "op": "any", "value": [
        {"field": "biomarker", "op": "positive", "value": {"marker": "BRCA1"}},
        {"field": "biomarker", "op": "positive", "value": {"marker": "BRCA2"}},
    ]}
    p = _p()
    assert evaluate_criterion(c, p).state == UNK
    p.set_biomarker(BiomarkerResult("BRCA1", "negativni"))
    assert evaluate_criterion(c, p).state == UNK
    p.set_biomarker(BiomarkerResult("BRCA2", "pozitivni"))
    assert evaluate_criterion(c, p).state == SAT


def test_prior_therapy_classes_and_scope():
    assert "anti-HER2 ADC" in classes_of("Enhertu")
    assert "anti-HER2" in classes_of("Kadcyla")
    assert {"platina", "anti-PD-1/PD-L1"} <= classes_of("FOLFOX + nivolumab")
    c = {"id": "x", "kind": "inclusion", "field": "prior_therapy", "op": "required", "scope": "paliativni", "value": ["anti-HER2"]}
    assert evaluate_criterion(c, _p(therapies=[TherapyEpisode("Kadcyla", "adjuvance")])).state == NOT
    assert evaluate_criterion(c, _p(therapies=[TherapyEpisode("Enhertu", "paliativni")])).state == SAT
    assert evaluate_criterion(c, _p(therapies=None)).state == UNK
    assert evaluate_criterion(c, _p(therapies=[TherapyEpisode("neznámý lék XY", "paliativni")])).state == UNK
