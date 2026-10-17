import json
import subprocess
from io import BytesIO
from unittest.mock import Mock

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from trialink import pdf
from trialink.ocr import OcrError


def make_pdf(pages):
    """Generate synthetic PDFs entirely in RAM, without adding a PDF writer dependency."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for content in pages:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        stream_id = page_id + 1
        resource_id = page_id + 2
        if isinstance(content, bytes):
            resources = f"/XObject << /Im1 {resource_id} 0 R >>"
            stream = b"q 300 0 0 150 0 0 cm /Im1 Do Q"
            resource = (f"<< /Type /XObject /Subtype /Image /Width 600 /Height 300 "
                        f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                        f"/Length {len(content)} >>\nstream\n").encode() + content + b"\nendstream"
        else:
            resources = f"/Font << /F1 {resource_id} 0 R >>"
            stream = f"BT /F1 14 Tf 20 80 Td ({content}) Tj ET".encode("ascii")
            resource = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        objects.extend([
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 150] "
             f"/Resources << {resources} >> /Contents {stream_id} 0 R >>").encode(),
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
            resource,
        ])
    objects[1] = f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(kids)}] >>".encode()
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend((f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
                   f"startxref\n{xref}\n%%EOF\n").encode())
    return bytes(result)


@pytest.fixture
def scanned_page():
    with Image.new("RGB", (600, 300), "white") as image:
        ImageDraw.Draw(image).text((30, 30), "Synthetic scan ECOG 1", fill="black")
        with BytesIO() as buffer:
            image.save(buffer, "JPEG")
            return buffer.getvalue()


def test_text_pdf_does_not_invoke_ocr(monkeypatch):
    ocr = Mock()
    monkeypatch.setattr(pdf, "image_to_text", ocr)
    assert pdf._extract_pdf_text(make_pdf(["Report ECOG 1", "KRAS G12C"])) == "Report ECOG 1\n\nKRAS G12C"
    ocr.assert_not_called()


def test_public_pdf_reader_runs_in_isolated_process():
    assert pdf.pdf_to_text(make_pdf(["Report ECOG 1"])) == "Report ECOG 1"


def test_mixed_pdf_preserves_page_order_and_uses_ocr_only_for_scan(monkeypatch, scanned_page):
    ocr = Mock(return_value="Česká zpráva ze skenu")
    monkeypatch.setattr(pdf, "image_to_text", ocr)
    assert pdf._extract_pdf_text(make_pdf(["First", scanned_page, "Last"])) == (
        "First\n\nČeská zpráva ze skenu\n\nLast"
    )
    ocr.assert_called_once()
    assert 0 < ocr.call_args.kwargs["timeout"] <= 60
    with Image.open(BytesIO(ocr.call_args.args[0])) as image:
        assert image.size == (1250, 625)  # 300 DPI from a 300 x 150 point page.


def test_force_ocr_ignores_existing_text(monkeypatch):
    ocr = Mock(side_effect=["Opravená první", "Opravená druhá"])
    monkeypatch.setattr(pdf, "image_to_text", ocr)
    assert pdf._extract_pdf_text(make_pdf(["Wrong layer", "Also wrong"]), force_ocr=True) == (
        "Opravená první\n\nOpravená druhá"
    )
    assert ocr.call_count == 2


def test_white_blank_page_is_skipped_without_ocr(monkeypatch):
    ocr = Mock()
    monkeypatch.setattr(pdf, "image_to_text", ocr)
    assert pdf._extract_pdf_text(make_pdf(["", "Report"])) == "Report"
    ocr.assert_not_called()


def test_all_blank_pdf_is_rejected():
    with pytest.raises(OcrError, match="neobsahuje"):
        pdf._extract_pdf_text(make_pdf([""]))


def test_failed_scan_does_not_return_partial_text(monkeypatch, scanned_page):
    monkeypatch.setattr(pdf, "image_to_text", Mock(side_effect=OcrError("Text nelze rozpoznat.")))
    with pytest.raises(OcrError, match="Stránka 2"):
        pdf._extract_pdf_text(make_pdf(["First", scanned_page]))


def test_pdf_page_limit():
    with pytest.raises(OcrError, match="1 až 10"):
        pdf._extract_pdf_text(make_pdf(["Report"] * 11))


def test_text_length_limit(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_REPORT_CHARS", 10)
    with pytest.raises(OcrError, match="40 000"):
        pdf._extract_pdf_text(make_pdf(["123456", "123456"]))


def test_render_pixel_limit(monkeypatch, scanned_page):
    monkeypatch.setattr(pdf, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(OcrError, match="20 megapixelů"):
        pdf._extract_pdf_text(make_pdf([scanned_page]))


def test_invalid_pdf_returns_safe_error():
    with pytest.raises(OcrError) as exc:
        pdf.pdf_to_text(b"private medical data")
    assert "private medical data" not in str(exc.value)


def test_encrypted_pdf_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(pdf.pdfium, "PdfDocument",
                        Mock(side_effect=pdfium.PdfiumError("private data", err_code=pdfium.raw.FPDF_ERR_PASSWORD)))
    with pytest.raises(OcrError, match="Zaheslované") as exc:
        pdf._extract_pdf_text(b"encrypted")
    assert "private data" not in str(exc.value)


def test_encryption_with_empty_password_is_also_rejected(monkeypatch):
    monkeypatch.setattr(pdf.pdfium.raw, "FPDF_GetSecurityHandlerRevision", Mock(return_value=2))
    with pytest.raises(OcrError, match="Zaheslované"):
        pdf._extract_pdf_text(make_pdf(["Report"]))


def test_deadline_stops_before_reading_more_pages(monkeypatch):
    monkeypatch.setattr(pdf.time, "monotonic", Mock(side_effect=[0, pdf.PDF_TIMEOUT_S + 1]))
    with pytest.raises(OcrError, match="120 sekund"):
        pdf._extract_pdf_text(make_pdf(["Report"]))


@pytest.mark.parametrize("data", [b"", b"x" * (pdf.MAX_IMAGE_BYTES + 1)], ids=["empty", "too-large"])
def test_input_limits_do_not_start_worker(monkeypatch, data):
    run = Mock()
    monkeypatch.setattr(pdf.subprocess, "run", run)
    with pytest.raises(OcrError):
        pdf.pdf_to_text(data)
    run.assert_not_called()


def test_total_timeout_is_enforced_and_sanitized(monkeypatch):
    run = Mock(side_effect=subprocess.TimeoutExpired("pdf", 120, output=b"private text"))
    monkeypatch.setattr(pdf.subprocess, "run", run)
    with pytest.raises(OcrError, match="120 sekund") as exc:
        pdf.pdf_to_text(b"pdf", force_ocr=True)
    assert "private text" not in str(exc.value)
    assert run.call_args.kwargs["timeout"] == 120
    assert "--force-ocr" in run.call_args.args[0]


def test_crashed_worker_never_exposes_output(monkeypatch):
    monkeypatch.setattr(pdf.subprocess, "run",
                        Mock(return_value=subprocess.CompletedProcess([], 1, b"private text", b"private text")))
    with pytest.raises(OcrError) as exc:
        pdf.pdf_to_text(b"pdf")
    assert "private text" not in str(exc.value)


@pytest.mark.parametrize("output", [b"private text", b"[]", b"{}", json.dumps({"text": ""}).encode()])
def test_invalid_worker_output_is_rejected(monkeypatch, output):
    monkeypatch.setattr(pdf.subprocess, "run", Mock(return_value=subprocess.CompletedProcess([], 0, output, b"")))
    with pytest.raises(OcrError) as exc:
        pdf.pdf_to_text(b"pdf")
    assert "private text" not in str(exc.value)
