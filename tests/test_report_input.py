import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from trialink import anonymize, extraction, ocr
from trialink.extraction import Extraction, ExtractionError
from trialink.model import PatientProfile
from trialink.ocr import OcrError

APP = Path(__file__).resolve().parent.parent / "app"
REPORT = "Jméno: Jan Testovací\nRČ 450101/1234, tel. +420 777 123 456\nAdenokarcinom C18.6, ECOG 1."


@pytest.fixture
def inputs(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    return importlib.import_module("report_input")


@pytest.fixture
def services(monkeypatch, inputs):
    common = importlib.import_module("common")
    monkeypatch.setattr(common, "get_catalog", lambda: ([], "test", []))
    local_ocr = Mock(return_value="OCR text – ECOG 1")
    cloud = Mock(return_value=Extraction(PatientProfile(icd10="C186", ecog=1), dropped=["her2_status"]))
    monkeypatch.setattr(ocr, "image_to_text", local_ocr)
    monkeypatch.setattr(extraction, "extract_profile", cloud)
    return local_ocr, cloud


def test_text_from_upload_txt_and_image(inputs, monkeypatch):
    assert inputs.text_from_upload("a.txt", "Zpráva ECOG 1".encode("utf-8-sig")) == "Zpráva ECOG 1"
    assert inputs.text_from_upload("a.txt", "Žaludek".encode("cp1250")) == "Žaludek"
    local_ocr = Mock(return_value="OCR")
    monkeypatch.setattr(ocr, "image_to_text", local_ocr)
    assert inputs.text_from_upload("foto.jpg", b"img") == "OCR"
    local_ocr.assert_called_once_with(b"img")


@pytest.mark.parametrize("name,data", [("a.txt", b""), ("a.txt", b"   "), ("a.txt", b"x" * 40_001)],
                         ids=["missing", "empty", "too-long"])
def test_text_from_upload_rejects_bad_input(inputs, name, data):
    with pytest.raises(OcrError):
        inputs.text_from_upload(name, data)


def test_anonymize_finds_and_masks_identifiers():
    found = anonymize.find(REPORT)
    assert set(found) == {"osobní údaj v řádku (jméno, adresa…)", "rodné číslo", "telefon"}
    masked = anonymize.mask(REPORT)
    assert "Jan Testovací" not in masked and "450101" not in masked and "777" not in masked
    assert "Jméno: [ANONYMIZOVÁNO]" in masked
    assert "Adenokarcinom C18.6, ECOG 1." in masked


def _patient_review(report: str) -> AppTest:
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=30).run()
    at.button(key="wiz_role_pat").click().run()
    at.radio(key="pat_mode").set_value("text").run()
    at.text_area(key="pat_draft").input(report).run()
    at.button(key="wiz_load").click().run()
    assert not at.exception, at.exception
    return at


def test_upload_is_default_and_camera_only_on_demand(services):
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=30).run()
    at.button(key="wiz_role_pat").click().run()
    assert at.radio(key="pat_mode").value == "file"
    assert not at.get("camera_input")
    assert "wiz_demo" not in {b.key for b in at.button}
    at.radio(key="pat_mode").set_value("photo").run()
    assert at.get("camera_input")


def test_missing_facts_default_to_unknown(services):
    at = _patient_review("Adenokarcinom C18.6, ECOG 1.")
    at.checkbox(key="pat_report_ok").check().run()
    at.button(key="wiz_to_detail").click().run()
    assert at.selectbox(key="pat_lines").value is None
    assert at.radio(key="pat_extent").value is None
    assert at.radio(key="pat_ther_mode").value is None
    assert at.selectbox(key="pat_lines").options[0] == "— nevím / nezadáno —"


def test_report_goes_to_review_without_cloud_call(services):
    _, cloud = services
    at = _patient_review(REPORT)
    assert at.session_state["wiz_step"] == "review"
    assert at.text_area(key="pat_report_text").value == REPORT
    assert at.warning  # suspected identifiers reported
    assert at.button(key="wiz_to_results").disabled
    cloud.assert_not_called()


def test_masking_confirmation_and_search(services):
    _, cloud = services
    at = _patient_review(REPORT)
    at.button(key="wiz_mask").click().run()
    masked = at.text_area(key="pat_report_text").value
    assert "450101" not in masked
    at.checkbox(key="pat_report_ok").check().run()
    at.button(key="wiz_to_results").click().run()
    assert not at.exception, at.exception
    cloud.assert_called_once_with(masked)
    assert at.session_state["wiz_step"] == "results"
    assert "pat_report_text" not in at.session_state  # raw text dropped after extraction
    at.button(key="wiz_back").click().run()
    assert at.session_state["wiz_step"] == "review"


def test_show_detail_prefills_form(services):
    at = _patient_review("Adenokarcinom C18.6, ECOG 1.")
    at.checkbox(key="pat_report_ok").check().run()
    at.button(key="wiz_to_detail").click().run()
    assert not at.exception, at.exception
    assert at.session_state["wiz_step"] == "detail"
    assert at.session_state["pat_icd"] == "C186"
    assert any("her2_status" in i.value for i in at.info)


def test_cloud_error_keeps_text_on_review(services):
    _, cloud = services
    cloud.side_effect = ExtractionError("Služba není dostupná.")
    at = _patient_review("Adenokarcinom C18.6")
    at.checkbox(key="pat_report_ok").check().run()
    at.button(key="wiz_to_results").click().run()
    assert not at.exception
    assert at.session_state["wiz_step"] == "review"
    assert any("Služba není dostupná" in e.value for e in at.error)


def test_empty_report_shows_error_and_stays(services):
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=30).run()
    at.button(key="wiz_role_pat").click().run()
    at.button(key="wiz_load").click().run()
    assert at.session_state["wiz_step"] == "report"
    assert at.error


def test_timeout_clears_draft(services):
    at = _patient_review("Adenokarcinom C18.6")
    at.session_state["wiz_last_seen"] = 1
    at.run()
    assert not at.exception
    assert "pat_report_text" not in at.session_state
    assert any("Relace vypršela" in i.value for i in at.info)
