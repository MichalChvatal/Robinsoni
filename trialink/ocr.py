"""Local OCR over stdin/stdout; images and recognized text never use temporary files."""
from __future__ import annotations

import os
import subprocess
import warnings
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
BUNDLED_TESSDATA = Path(__file__).resolve().parent / "data" / "ocr"


class OcrError(ValueError):
    """A safe, user-facing error without image contents or OCR output."""


def image_to_text(data: bytes, *, timeout: float = 60) -> str:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise OcrError("Nahrajte fotografii JPG nebo PNG o velikosti nejvýše 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"}:
                    raise OcrError("OCR podporuje pouze fotografie JPG a PNG.")
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise OcrError("Fotografie je příliš velká. Použijte nejvýše 20 megapixelů.")
                prepared = ImageOps.exif_transpose(image).convert("RGB")
                with BytesIO() as buffer:
                    prepared.save(buffer, format="PNG")
                    png = buffer.getvalue()
                prepared.close()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise OcrError("Fotografii nelze bezpečně načíst. Použijte platný soubor JPG nebo PNG.") from None

    try:
        command = [os.getenv("TESSERACT_CMD") or "tesseract", "stdin", "stdout", "-l", "ces", "--oem", "1"]
        if not os.getenv("TESSDATA_PREFIX"):
            if not (BUNDLED_TESSDATA / "ces.traineddata").is_file():
                raise OcrError("Chybí přibalený český OCR model. Nasaďte i trialink/data/ocr/ces.traineddata "
                               "nebo nastavte TESSDATA_PREFIX na adresář s českým modelem.")
            command.extend(["--tessdata-dir", str(BUNDLED_TESSDATA)])
        result = subprocess.run(
            command,
            input=png, capture_output=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        raise OcrError("Lokální OCR není dostupné. Nainstalujte Tesseract s českým jazykem ces "
                       "a případně nastavte TESSERACT_CMD.") from None
    except subprocess.TimeoutExpired:
        raise OcrError("Rozpoznávání trvalo příliš dlouho. Zkuste menší nebo ostřejší fotografii.") from None
    except OSError:
        raise OcrError("Lokální OCR se nepodařilo spustit. Ověřte instalaci Tesseractu.") from None
    if result.returncode:
        raise OcrError("OCR selhalo. Ověřte instalaci Tesseractu a českých jazykových dat ces.")
    try:
        text = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise OcrError("OCR vrátilo neplatný text. Ověřte instalaci Tesseractu.") from None
    if not text:
        raise OcrError("Na fotografii se nepodařilo rozpoznat text. Zkuste ostřejší snímek.")
    return text
