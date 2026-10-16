import pytest

from trialink.demo_cases import case5
from trialink.matching import evaluate_cohort, sex_caution
from trialink.model import sex_for_diagnosis


class _Study:
    study_id, acronym, indication, contacts = "X1", "TEST", "Karcinom prsu", []


def _result(criteria):
    cohort = {"label": "prs", "criteria": criteria}
    return evaluate_cohort(_Study(), cohort, case5())


BREAST = {"id": "dg", "kind": "inclusion", "field": "diagnosis", "op": "prefix_in", "value": ["C50"], "quote": "prs"}


def test_sex_for_diagnosis():
    assert sex_for_diagnosis("C56") == "F"
    assert sex_for_diagnosis("C61.9") == "M"
    assert sex_for_diagnosis("C50.4") is None


def test_sex_caution_for_male_breast_cancer():
    r = _result([BREAST])
    assert r.targeted
    p = case5()
    assert sex_caution(r, p) is None
    p.sex = "M"
    assert "muže" in sex_caution(r, p)
    r_with_rule = _result([BREAST, {"id": "sex", "kind": "inclusion", "field": "sex", "op": "eq", "value": "F", "quote": "ženy"}])
    assert sex_caution(r_with_rule, p) is None


def test_pdf_export_contains_czech_text():
    pytest.importorskip("fpdf")
    from trialink.report_pdf import build_pdf

    r = _result([BREAST])
    data = build_pdf(case5(), [("Kandidáti", [r]), ("Studie pro více druhů nádorů", [])], lay=True)
    assert data.startswith(b"%PDF") and len(data) > 1000
