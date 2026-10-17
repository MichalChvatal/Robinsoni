"""PDF text extraction and Czech OCR in an isolated, time-limited local process."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from contextlib import closing
from io import BytesIO

import pypdfium2 as pdfium

from .extraction import MAX_REPORT_CHARS
from .ocr import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, OcrError, image_to_text

MAX_PDF_PAGES = 10
PDF_TIMEOUT_S = 120
PDF_DPI = 300


def pdf_to_text(data: bytes, *, force_ocr: bool = False) -> str:
    """Keep document bytes in RAM; a separate process bounds native PDF operations."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise OcrError("Nahrajte PDF o velikosti nejvýše 10 MB.")
    command = [sys.executable, "-m", "trialink.pdf"]
    if force_ocr:
        command.append("--force-ocr")
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=PDF_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise OcrError("Zpracování PDF překročilo 120 sekund. Nahrajte méně stránek.") from None
    except OSError:
        raise OcrError("Lokální zpracování PDF se nepodařilo spustit.") from None
    if result.returncode:
        raise OcrError("PDF nelze zpracovat. Zkontrolujte soubor nebo nahrajte jednotlivé stránky jako obrázky.")
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        raise OcrError("Zpracování PDF nevrátilo platný výsledek.") from None
    if not isinstance(response, dict):
        raise OcrError("Zpracování PDF nevrátilo platný výsledek.")
    error = response.get("error")
    if isinstance(error, str):
        raise OcrError(error)
    text = response.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPORT_CHARS:
        raise OcrError("PDF neobsahuje text nebo přesahuje limit 40 000 znaků.")
    return text


def _extract_pdf_text(data: bytes, *, force_ocr: bool = False) -> str:
    deadline = time.monotonic() + PDF_TIMEOUT_S
    try:
        with pdfium.PdfDocument(data) as document:
            if document.get_version() is None:
                raise OcrError("PDF má neplatný formát.")
            # Even empty-password encryption must not bypass the encrypted-document policy.
            if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
                raise OcrError("Zaheslované PDF není podporováno. Nahrajte nešifrovaný dokument.")
            if not 0 < len(document) <= MAX_PDF_PAGES:
                raise OcrError("PDF musí obsahovat 1 až 10 stránek.")
            pages: list[str] = []
            for index in range(len(document)):
                if time.monotonic() >= deadline:
                    raise OcrError("Zpracování PDF překročilo 120 sekund. Nahrajte méně stránek.")
                with closing(document[index]) as page:
                    text = ""
                    if not force_ocr:
                        with closing(page.get_textpage()) as textpage:
                            if textpage.count_chars() > MAX_REPORT_CHARS:
                                raise OcrError("Text PDF přesahuje 40 000 znaků. Nahrajte kratší dokument.")
                            text = textpage.get_text_bounded(errors="strict").strip()
                    if not text:
                        text = _ocr_page(page, index + 1, deadline)
                    if text:
                        pages.append(text)
                    if sum(len(part) for part in pages) + max(0, len(pages) - 1) * 2 > MAX_REPORT_CHARS:
                        raise OcrError("Text PDF přesahuje 40 000 znaků. Nahrajte kratší dokument.")
            if not pages:
                raise OcrError("PDF neobsahuje rozpoznatelný text.")
            return "\n\n".join(pages)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise OcrError("Zaheslované PDF není podporováno. Nahrajte nešifrovaný dokument.") from None
        raise OcrError("PDF je poškozené nebo jej nelze načíst.") from None
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, OcrError):
            raise
        raise OcrError("PDF nelze bezpečně přečíst. Nahrajte platný dokument.") from None


def _ocr_page(page: pdfium.PdfPage, number: int, deadline: float) -> str:
    width, height = page.get_size()
    scale = PDF_DPI / 72
    if (not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0
            or math.ceil(width * scale) * math.ceil(height * scale) > MAX_IMAGE_PIXELS):
        raise OcrError(f"Stránka {number} je pro OCR příliš velká (limit 20 megapixelů při 300 DPI).")
    with closing(page.render(scale=scale)) as bitmap:
        with bitmap.to_pil() as image:
            # Truly blank pages do not require OCR. Other unrecognizable pages must not be silently skipped.
            with image.convert("L") as gray:
                if gray.getextrema() == (255, 255):
                    return ""
            with BytesIO() as buffer:
                image.save(buffer, format="PNG")
                data = buffer.getvalue()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OcrError("Zpracování PDF překročilo 120 sekund. Nahrajte méně stránek.")
    try:
        return image_to_text(data, timeout=min(60, remaining))
    except OcrError as exc:
        raise OcrError(f"Stránka {number}: {exc}") from None


def _main() -> None:
    data = sys.stdin.buffer.read(MAX_IMAGE_BYTES + 1)
    try:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise OcrError("Nahrajte PDF o velikosti nejvýše 10 MB.")
        response = {"text": _extract_pdf_text(data, force_ocr="--force-ocr" in sys.argv[1:])}
    except OcrError as exc:
        response = {"error": str(exc)}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=True).encode("utf-8"))


if __name__ == "__main__":
    _main()
