import subprocess
from io import BytesIO
from unittest.mock import Mock

import pytest
from PIL import Image

from trialink import ocr


def _image_bytes(format="PNG", size=(40, 20)):
    with Image.new("RGB", size, "white") as image, BytesIO() as buffer:
        image.save(buffer, format=format)
        return buffer.getvalue()


@pytest.mark.parametrize("format", ["PNG", "JPEG"])
def test_ocr_uses_only_stdin_stdout(monkeypatch, format):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "Zpráva: ECOG 1\n".encode(), b""))
    monkeypatch.setattr(ocr.subprocess, "run", run)
    monkeypatch.setenv("TESSERACT_CMD", r"C:\OCR\tesseract.exe")
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)

    assert ocr.image_to_text(_image_bytes(format)) == "Zpráva: ECOG 1"
    args, kwargs = run.call_args
    assert args == ([r"C:\OCR\tesseract.exe", "stdin", "stdout", "-l", "ces", "--oem", "1",
                     "--tessdata-dir", str(ocr.BUNDLED_TESSDATA)],)
    assert kwargs["input"].startswith(b"\x89PNG")
    assert kwargs == {"input": kwargs["input"], "capture_output": True, "timeout": 60, "check": False}


def test_cloud_uses_path_executable_and_project_model(monkeypatch):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, b"OCR", b""))
    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.image_to_text(_image_bytes()) == "OCR"
    assert run.call_args.args[0][0] == "tesseract"
    assert run.call_args.args[0][-2:] == ["--tessdata-dir", str(ocr.BUNDLED_TESSDATA)]


def test_explicit_language_directory_is_preserved(monkeypatch):
    monkeypatch.setenv("TESSDATA_PREFIX", "custom-models")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, b"OCR", b""))
    monkeypatch.setattr(ocr.subprocess, "run", run)
    assert ocr.image_to_text(_image_bytes()) == "OCR"
    assert "--tessdata-dir" not in run.call_args.args[0]


def test_missing_bundled_model_is_reported(monkeypatch, tmp_path):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    monkeypatch.setattr(ocr, "BUNDLED_TESSDATA", tmp_path)
    run = Mock()
    monkeypatch.setattr(ocr.subprocess, "run", run)
    with pytest.raises(ocr.OcrError, match="Chybí přibalený"):
        ocr.image_to_text(_image_bytes())
    run.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"x" * (ocr.MAX_IMAGE_BYTES + 1), _image_bytes("GIF")],
    ids=["empty", "invalid", "oversized", "gif"],
)
def test_bad_input_never_starts_ocr(monkeypatch, data):
    run = Mock()
    monkeypatch.setattr(ocr.subprocess, "run", run)
    with pytest.raises(ocr.OcrError):
        ocr.image_to_text(data)
    run.assert_not_called()


def test_pixel_limit_is_checked_before_ocr(monkeypatch):
    monkeypatch.setattr(ocr, "MAX_IMAGE_PIXELS", 10)
    run = Mock()
    monkeypatch.setattr(ocr.subprocess, "run", run)
    with pytest.raises(ocr.OcrError, match="20 megapixelů"):
        ocr.image_to_text(_image_bytes())
    run.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess([], 1, b"private report", b"private report"),
        subprocess.CompletedProcess([], 0, b" \n", b""),
        subprocess.CompletedProcess([], 0, b"\xff", b""),
    ],
)
def test_ocr_errors_do_not_expose_output(monkeypatch, result):
    monkeypatch.setattr(ocr.subprocess, "run", Mock(return_value=result))
    with pytest.raises(ocr.OcrError) as exc:
        ocr.image_to_text(_image_bytes())
    assert "private report" not in str(exc.value)
    assert exc.value.__cause__ is None


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("private report"), OSError("private report"),
     subprocess.TimeoutExpired("tesseract", 60, output=b"private report", stderr=b"private report")],
)
def test_process_errors_are_sanitized(monkeypatch, error):
    monkeypatch.setattr(ocr.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(ocr.OcrError) as exc:
        ocr.image_to_text(_image_bytes())
    assert "private report" not in str(exc.value)
    assert exc.value.__cause__ is None
