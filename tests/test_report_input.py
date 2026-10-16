import importlib
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from trialink.extraction import Extraction, ExtractionError
from trialink.model import PatientProfile
from trialink.ocr import OcrError

APP = Path(__file__).resolve().parent.parent / "app"


class _Upload(BytesIO):
    def __init__(self, data=b"synthetic image", name="photo.png"):
        super().__init__(data)
        self.name = name
        self.size = len(data)


@pytest.fixture
def services(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    common = importlib.import_module("common")
    inputs = importlib.import_module("app.report_input")
    extraction = importlib.import_module("trialink.extraction")
    monkeypatch.setenv("TRIALINK_KROSS_KEY", "mock-all")
    monkeypatch.setattr(common, "get_catalog", lambda: ([], "test", []))
    monkeypatch.setattr(inputs.st, "file_uploader", Mock(return_value=_Upload()))
    local_ocr = Mock(return_value="OCR text – ECOG 1")
    cloud = Mock(return_value=Extraction(PatientProfile(ecog=1)))
    monkeypatch.setattr(inputs, "image_to_text", local_ocr)
    monkeypatch.setattr(extraction, "extract_profile", cloud)
    return inputs, local_ocr, cloud


def _page(prefix):
    page = "1_Lekar.py" if prefix == "doc" else "2_Pacient.py"
    at = AppTest.from_file(str(APP / "pages" / page), default_timeout=30).run()
    assert not at.exception
    return at


def _button(at, label):
    return next(button for button in at.button if button.label == label)


@pytest.mark.parametrize("prefix", ["doc", "pat"])
def test_ocr_prefills_editable_report_without_cloud_call(services, prefix):
    _, local_ocr, cloud = services
    at = _page(prefix)
    local_ocr.assert_not_called()
    cloud.assert_not_called()

    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    assert not at.exception
    local_ocr.assert_called_once_with(b"synthetic image")
    cloud.assert_not_called()
    assert at.text_area(key=f"{prefix}_report_text").value == "OCR text – ECOG 1"

    at.text_area(key=f"{prefix}_report_text").input("Opravená anonymizovaná zpráva").run()
    cloud.assert_not_called()
    label = "Extrahovat parametry" if prefix == "doc" else "Vyplnit formulář ze zprávy"
    _button(at, label).click().run()
    assert not at.exception
    cloud.assert_called_once_with("Opravená anonymizovaná zpráva")
    assert at.text_area(key=f"{prefix}_report_text").value == ""
    assert at.session_state[f"{prefix}_ecog"] == 1
    assert f"{prefix}_submitted_report" not in at.session_state


def test_local_ocr_error_preserves_draft_and_never_calls_cloud(services):
    _, local_ocr, cloud = services
    local_ocr.side_effect = OcrError("Lokální OCR není dostupné.")
    at = _page("doc")
    at.text_area(key="doc_report_text").input("Rozepsaná zpráva").run()
    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == "Rozepsaná zpráva"
    assert any("Lokální OCR není dostupné" in error.value for error in at.error)
    cloud.assert_not_called()


def test_cloud_error_clears_submitted_report(services):
    _, _, cloud = services
    cloud.side_effect = ExtractionError("Služba není dostupná.")
    at = _page("doc")
    at.text_area(key="doc_report_text").input("Anonymizovaný test").run()
    _button(at, "Extrahovat parametry").click().run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == ""
    assert "doc_submitted_report" not in at.session_state
    assert any("Služba není dostupná" in error.value for error in at.error)


def test_txt_import_is_also_reviewed_before_cloud_extraction(services, monkeypatch):
    inputs, local_ocr, cloud = services
    monkeypatch.setattr(inputs.st, "file_uploader",
                        Mock(return_value=_Upload("Textová zpráva".encode("utf-8-sig"), "report.txt")))
    at = _page("doc")
    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == "Textová zpráva"
    local_ocr.assert_not_called()
    cloud.assert_not_called()


def test_reset_clears_ocr_draft(services):
    at = _page("doc")
    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    _button(at, "🗑️ Vymazat údaje pacienta").click().run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == ""


def test_timeout_clears_ocr_draft(services):
    at = _page("doc")
    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    at.session_state["doc_last_seen"] = 1
    at.run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == ""
    assert any("Relace vypršela" in info.value for info in at.info)


@pytest.mark.parametrize(
    "upload",
    [None, _Upload(b"\xff", "report.txt"), _Upload(b"   ", "report.txt"),
     _Upload(b"x" * 40_001, "report.txt")],
    ids=["missing", "invalid-utf8", "empty-text", "too-long"],
)
def test_invalid_upload_preserves_report_and_never_calls_cloud(services, monkeypatch, upload):
    inputs, _, cloud = services
    monkeypatch.setattr(inputs.st, "file_uploader", Mock(return_value=upload))
    at = _page("doc")
    at.text_area(key="doc_report_text").input("Původní testovací text").run()
    _button(at, "Načíst text do zprávy (lokálně)").click().run()
    assert not at.exception
    assert at.text_area(key="doc_report_text").value == "Původní testovací text"
    assert at.error
    cloud.assert_not_called()
