import importlib
from pathlib import Path

import pytest
from conftest import requires_export

from trialink.dictionaries import histology_from_morpho
from trialink.model import sex_for_diagnosis

APP = Path(__file__).resolve().parent.parent / "app"
ALLOWED = ["81403", "80103", "85003"]


@pytest.fixture
def qe(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    module = importlib.import_module("quick_entry")
    monkeypatch.setattr(module, "ss", {})  # load_profile/current_profile only touch the module-level session alias
    return module


@pytest.fixture
def topo(qe):
    index, _ = importlib.import_module("common").get_topo_morpho()
    if index is None:
        pytest.skip("číselník topografie × morfologie není k dispozici")
    return index


def test_test_codes_are_real():
    assert histology_from_morpho("81403") == "adenokarcinom"
    assert histology_from_morpho("80103") is None
    assert sex_for_diagnosis("C619") == "M" and sex_for_diagnosis("C539") == "F"
    assert sex_for_diagnosis("C509") is None


def test_first_field_is_diagnosis(qe):
    assert qe.next_field({}, set(), ALLOWED) == "icd"
    assert qe.relevant_fields({}, ALLOWED)[0] == "icd"


def test_morpho_skipped_without_diagnosis_or_allowed_list(qe):
    assert "morpho" not in qe.relevant_fields({}, ALLOWED)
    assert "morpho" not in qe.relevant_fields({"icd": "C186"}, [])
    assert "morpho" in qe.relevant_fields({"icd": "C186"}, ALLOWED)
    assert qe.next_field({"icd": "C186"}, {"icd"}, ALLOWED) == "morpho"
    assert qe.next_field({"icd": "C186"}, {"icd"}, []) == "histology"


def test_histology_skipped_when_derivable_from_morphology(qe):
    derivable = {"icd": "C186", "morpho": "81403"}
    assert "histology" not in qe.relevant_fields(derivable, ALLOWED)
    assert qe.next_field(derivable, {"icd", "morpho"}, ALLOWED) == "extent"
    generic = {"icd": "C186", "morpho": "80103"}
    assert "histology" in qe.relevant_fields(generic, ALLOWED)
    assert qe.next_field(generic, {"icd", "morpho"}, ALLOWED) == "histology"
    assert "histology" in qe.relevant_fields({"icd": "C186"}, ALLOWED)


@pytest.mark.parametrize("icd, asked", [("C509", True), ("C162", True), ("C186", True), ("C341", False),
                                        ("C619", False), (None, False)])
def test_her2_only_for_her2_relevant_diagnoses(qe, icd, asked):
    assert ("her2" in qe.relevant_fields({"icd": icd}, ALLOWED)) is asked


@pytest.mark.parametrize("icd, asked", [("C619", False), ("C539", False), ("C509", True), ("C186", True), (None, True)])
def test_sex_skipped_for_sex_specific_diagnosis(qe, icd, asked):
    assert ("sex" in qe.relevant_fields({"icd": icd}, ALLOWED)) is asked


@pytest.mark.parametrize("sex, asked", [("F", True), ("M", False), (None, False)])
def test_sex_conflicting_with_diagnosis_stays_editable(qe, sex, asked):
    assert ("sex" in qe.relevant_fields({"icd": "C619", "sex": sex}, ALLOWED)) is asked


@pytest.mark.parametrize("her2, asked", [("low", True), (None, False)])
def test_loaded_her2_on_non_her2_site_stays_editable(qe, her2, asked):
    assert ("her2" in qe.relevant_fields({"icd": "C341", "her2": her2}, ALLOWED)) is asked


def test_fixing_sex_conflict_continues_flow(qe):
    values = {"icd": "C619", "sex": "F"}
    answered = set(qe.FIELDS)
    assert qe.next_field(values, answered - {"sex"}, ALLOWED) == "sex"
    values["sex"] = "M"
    assert "sex" not in qe.relevant_fields(values, ALLOWED)
    assert qe.next_field(values, answered - {"sex"}, ALLOWED) is None


def test_answered_fields_are_skipped_and_none_when_all_answered(qe):
    values = {"icd": "C341", "morpho": "81403"}
    relevant = qe.relevant_fields(values, ALLOWED)
    assert qe.next_field(values, {"icd", "morpho", "extent"}, ALLOWED) == "lines"
    assert qe.next_field(values, set(relevant[:-1]), ALLOWED) == relevant[-1] == "swallow"
    assert qe.next_field(values, set(relevant), ALLOWED) is None
    assert qe.next_field(values, set(qe.FIELDS), ALLOWED) is None


def test_order_follows_fields(qe):
    relevant = qe.relevant_fields({"icd": "C509", "morpho": "80103"}, ALLOWED)
    assert relevant == list(qe.FIELDS)


def _cases():
    from trialink.demo_cases import CASES
    return list(CASES.items())


def _snapshot(p):
    return {
        "icd10": p.icd10, "morphology": p.morphology, "histology": p.histology, "disease_extent": p.disease_extent,
        "setting": p.setting, "prior_palliative_lines": p.prior_palliative_lines, "her2_status": p.her2_status,
        "ecog": p.ecog, "age": p.age, "sex": p.sex, "can_swallow": p.can_swallow,
        "therapies": None if p.therapies is None else [(t.name, t.setting) for t in p.therapies],
        "biomarkers": {k: (b.status, b.variant, list(b.values)) for k, b in p.biomarkers.items()},
        "conflicts": list(p.conflicts),
    }


@pytest.mark.parametrize("name, make", _cases(), ids=[n.split(" –")[0] for n, _ in _cases()])
def test_demo_case_round_trip(qe, topo, name, make):
    original = make()
    qe.load_profile("doc", original)
    assert _snapshot(qe.current_profile("doc")) == _snapshot(make())
    assert qe.ss["doc_quick_field"] == qe.next_field(qe.ss["doc_quick"], qe.ss["doc_quick_answered"],
                                                     topo.morphologies(original.icd10))


def test_load_profile_marks_known_values_answered(qe, topo):
    from trialink.demo_cases import case1
    qe.load_profile("doc", case1())
    answered = qe.ss["doc_quick_answered"]
    assert {"icd", "morpho", "histology", "extent", "lines", "therapies", "biomarkers", "ecog", "age", "sex",
            "swallow"} <= answered
    assert "her2" not in answered and qe.ss["doc_quick_field"] == "her2"


def test_empty_profile_starts_with_diagnosis(qe, topo):
    qe.load_profile("doc", None)
    assert qe.ss["doc_quick_field"] == "icd"
    assert qe.ss["doc_quick_answered"] == set()
    p = qe.current_profile("doc")
    assert p.icd10 is None and p.therapies is None and p.biomarkers == {}


def test_sex_implied_by_diagnosis(qe, topo):
    from trialink.model import PatientProfile
    qe.load_profile("doc", PatientProfile(icd10="C619"))
    assert qe.current_profile("doc").sex == "M"
    assert "sex" not in qe.relevant_fields(qe.ss["doc_quick"], topo.morphologies("C619"))


def test_sync_profile_reloads_changes_and_keeps_unknown_answers(qe, topo):
    from dataclasses import replace

    from trialink.demo_cases import case1
    qe.load_profile("doc", case1())
    qe._on_unknown("doc", "her2")
    assert "her2" in qe.ss["doc_quick_answered"] and qe.ss["doc_quick_field"] is None
    profile = qe.current_profile("doc")
    qe.ss["doc_quick_synced"] = profile
    store = qe.ss["doc_quick"]
    qe.sync_profile("doc", replace(profile))  # equal profile = keep the store as is
    assert qe.ss["doc_quick"] is store
    qe.sync_profile("doc", replace(profile, ecog=2))
    assert qe.ss["doc_quick"]["ecog"] == 2 and qe.current_profile("doc").ecog == 2
    assert "her2" in qe.ss["doc_quick_answered"] and qe.ss["doc_quick_field"] is None


@pytest.fixture(scope="module")
def studies():
    from trialink.catalog import load_catalog
    return load_catalog()[0]


@requires_export
@pytest.mark.parametrize("name, make", _cases(), ids=[n.split(" –")[0] for n, _ in _cases()])
def test_matching_same_as_original_profile(qe, topo, studies, name, make):
    from trialink.matching import match
    qe.load_profile("doc", make())
    via_quick = {r.study.study_id: (r.status, r.cohort["id"]) for r in match(studies, qe.current_profile("doc"))}
    direct = {r.study.study_id: (r.status, r.cohort["id"]) for r in match(studies, make())}
    assert via_quick == direct
