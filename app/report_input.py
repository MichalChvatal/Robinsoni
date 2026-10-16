"""Turn an uploaded report (text file or photo/scan) into editable text – locally, without the cloud LLM."""
from __future__ import annotations

from trialink import ocr
from trialink.extraction import MAX_REPORT_CHARS
from trialink.ocr import MAX_IMAGE_BYTES, OcrError

UPLOAD_TYPES = ["txt", "jpg", "jpeg", "png"]


def text_from_upload(name: str, data: bytes) -> str:
    if not data:
        raise OcrError("Nejprve vyberte fotografii nebo textový soubor.")
    if len(data) > MAX_IMAGE_BYTES:
        raise OcrError("Soubor je příliš velký. Použijte nejvýše 10 MB.")
    if name.lower().endswith(".txt"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("cp1250")
            except UnicodeDecodeError:
                raise OcrError("Textový soubor má neznámé kódování. Uložte jej jako UTF-8.") from None
    else:
        text = ocr.image_to_text(data)
    if not text.strip():
        raise OcrError("Soubor neobsahuje text.")
    if len(text) > MAX_REPORT_CHARS:
        raise OcrError("Text přesahuje 40 000 znaků. Nahrajte kratší část zprávy.")
    return text
