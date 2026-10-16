from datetime import date

import pytest

from trialink import dictionaries as d
from trialink.demo_cases import CASES

HEADER = "skupina;skupina_nazev;topo;morfo;platnost_od;platnost_do;record_id\n"
SAMPLE = HEADER + "\n".join([
    "1;Tlusté střevo;C186;81403;01.01.2018;;1",
    "1;Tlusté střevo;C187;84803;01.01.2018;;2",
    "2;Plíce;C341;81403;01.01.2018;;3",
    "3;Plíce NET;C341;82403;01.01.2018;;4",
    "4;Stará;C341;80703;01.01.2010;31.12.2015;5",
    "55;Hodgkinův lymfom;    ;96503;01.01.2018;;6",
]) + "\n"


@pytest.fixture
def index():
    rows, errors = d.parse(SAMPLE.encode("cp1250"))
    assert not errors
    return d.TopoMorphoIndex(rows, day=date(2026, 1, 1))


def test_parse_cp1250_and_errors():
    bad = SAMPLE + "1;X;C18;81403;;;7\n1;X;C186;81403;;;1\n"
    rows, errors = d.parse(bad.encode("cp1250"))
    assert len(rows) == 6 and rows[0].skupina_nazev == "Tlusté střevo"
    assert rows[5].topo is None
    assert len(errors) == 2


def test_normalize_and_format():
    assert d.normalize_morpho("8140/33") == "81403"
    assert d.normalize_morpho("8140/3") == "81403"
    assert d.normalize_morpho("8140") is None
    assert d.format_morpho("81403") == "8140/3 – adenokarcinom, maligní"
    assert d.histology_from_morpho("85003") == "duktalni_nst"


def test_morphologies_restricted_by_topography(index):
    assert index.morphologies("C186") == ["81403"]
    assert index.morphologies("C18") == ["81403", "84803"]
    assert index.morphologies("C341") == ["81403", "82403"]  # expired row excluded
    assert index.morphologies("C999") == []
    assert index.is_allowed("C186", "84803") is False
    assert index.is_allowed("C18", "84803") is True
    assert index.is_allowed("C999", "81403") is None


def test_group_depends_on_pair(index):
    assert index.group_names("C341", "81403") == ["Plíce"]
    assert index.group_names("C341", "82403") == ["Plíce NET"]
    assert index.group_names("C341") == ["Plíce", "Plíce NET"]


def test_diagnosis_options_and_labels(index):
    assert index.diagnosis_codes()[:4] == ["C18", "C186", "C187", "C34"]
    assert "C341" not in index.diagnosis_codes()  # single subsite -> only the 3-char site
    assert "C81" in index.diagnosis_codes()
    assert d.diagnosis_label("C186") == "C18.6 – Tlusté střevo · sestupný tračník"
    assert d.diagnosis_label("C34") == "C34 – Průdušky a plíce"
    assert d.diagnosis_label("C619") == "C61.9 – Prostata · neurčená část"


def test_lymphoma_rows_without_topography(index):
    assert index.morphologies("C81.1") == ["96503"]
    assert "96503" not in index.morphologies("C18")
    assert index.group_names("C811", "96503") == ["Hodgkinův lymfom"]
    assert index.is_allowed("C186", "96503") is True


def test_demo_cases_allowed_in_real_dictionary():
    try:
        rows, errors, _, _ = d.load_file()
    except FileNotFoundError:
        pytest.skip("cis_skupina.csv není k dispozici")
    assert not errors and len(rows) == 23051
    idx = d.TopoMorphoIndex(rows)
    for make in CASES.values():
        p = make()
        if p.morphology:
            assert idx.is_allowed(p.icd10, p.morphology), (p.icd10, p.morphology)
