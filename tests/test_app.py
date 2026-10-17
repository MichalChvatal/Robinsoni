from pathlib import Path

import pytest
from conftest import requires_catalog

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def _demo_cases(monkeypatch):
    monkeypatch.setenv("TRIALINK_DEMO", "1")


def _run(page: str = "Home.py") -> AppTest:
    at = AppTest.from_file(str(APP / page), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


def _doctor_with_case(case: str) -> AppTest:
    at = _run()
    _click(at, "wiz_role_doc")
    _click(at, "wiz_choice_report")
    at.selectbox(key="doc_demo").select(case).run()
    return _click(at, "wiz_demo")


@requires_catalog
def test_home_renders_role_choice():
    at = _run()
    assert "TriaLink" in at.button(key="wiz_home").label
    assert {b.key for b in at.button} >= {"wiz_role_doc", "wiz_role_pat"}


@requires_catalog
def test_doctor_choice_offers_form_first():
    at = _run()
    _click(at, "wiz_role_doc")
    keys = [b.key for b in at.button if b.key in ("wiz_choice_form", "wiz_choice_report")]
    assert keys == ["wiz_choice_form", "wiz_choice_report"]


@requires_catalog
def test_trialink_title_goes_home():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_home")
    assert "wiz_step" not in at.session_state and "doc_icd" not in at.session_state


@requires_catalog
def test_results_offer_pdf_export():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_search")
    assert at.get("download_button")


@requires_catalog
def test_doctor_flow_case2_to_simulated_referral():
    at = _doctor_with_case("Case 2 – skvamózní ca jícnu, po 1. linii")
    assert at.session_state["wiz_step"] == "detail"
    _click(at, "wiz_search")
    assert at.session_state["wiz_step"] == "results"
    detail = [e for e in at.expander if e.label.startswith("Detail kritérií")]
    assert detail and all(not e.proto.expanded for e in detail)
    text = " ".join(m.value for m in at.markdown)
    assert "MSD - jícen" in text
    _click(at, "doc_ref_2025/R46")
    body = at.text_area(key="doc_ref_body_2025/R46")
    assert "MSD - jícen" in body.value
    body.input(body.value + "\nDoplněk lékaře: prosím o rychlé posouzení.").run()
    send = [b for b in at.button if b.label == "Odeslat (simulace)"]
    assert send and not send[0].disabled
    send[0].click().run()
    assert any("SIMULACE" in s.value for s in at.success)
    assert any("Doplněk lékaře" in t.value for t in at.text)


@requires_catalog
def test_back_button_walks_history_and_keeps_form_values():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_search")
    _click(at, "wiz_back")
    assert at.session_state["wiz_step"] == "detail"
    assert at.session_state["doc_icd"] == "C186"
    assert at.session_state["doc_morpho"] == "81403"
    for expected in ("report", "doc_choice", "role"):
        _click(at, "wiz_back")
        assert at.session_state["wiz_step"] == expected


@requires_catalog
def test_restart_clears_patient_state():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    assert at.session_state["doc_icd"] == "C186"
    _click(at, "wiz_restart")
    assert "doc_icd" not in at.session_state
    assert "doc_bio_init" not in at.session_state
    assert "wiz_role_doc" in {b.key for b in at.button}


@requires_catalog
def test_patient_without_report_goes_to_lay_form():
    at = _run()
    _click(at, "wiz_role_pat")
    assert at.session_state["wiz_step"] == "report"
    _click(at, "wiz_no_report")
    assert at.session_state["wiz_step"] == "detail"
    assert any(s.label.startswith("Kde nádor vznikl") for s in at.selectbox)
