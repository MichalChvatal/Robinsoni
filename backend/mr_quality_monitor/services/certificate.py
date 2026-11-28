"""Generate a DICOM Secondary Capture study-closing QC certificate.

The certificate is a 512×512 RGB image with a semaphore summary table rendered
in pure Python (no Pillow, no numpy). It is returned as a pydicom Dataset ready
for C-STORE to PACS, or for saving to disk.

Usage::

    from mr_quality_monitor.services.certificate import build_certificate
    ds = build_certificate(study_uid, evaluation, config, operator="BLAZKOVA")
"""

from __future__ import annotations

import hashlib
import struct
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    ExplicitVRLittleEndian,
    SecondaryCaptureImageStorage,
    generate_uid,
)

if TYPE_CHECKING:
    from ..analysis.validation_engine import StudyEvaluation
    from ..config import ReceiverConfig

# ---------------------------------------------------------------------------
# Minimal bitmap renderer (no external deps)
# ---------------------------------------------------------------------------

_WIDTH = 512
_HEIGHT = 512
_BG = (245, 247, 251)
_FG = (36, 52, 73)
_STATUS_COLORS = {
    "GREEN": (221, 243, 230),
    "ORANGE": (255, 240, 205),
    "RED": (252, 226, 226),
    "GRAY": (237, 240, 244),
}
_STATUS_FG = {
    "GREEN": (23, 100, 59),
    "ORANGE": (135, 85, 0),
    "RED": (160, 36, 36),
    "GRAY": (83, 97, 115),
}


def _make_canvas(width: int = _WIDTH, height: int = _HEIGHT) -> list[bytearray]:
    """Return height rows of width×3 bytes, pre-filled with background."""
    row = bytearray((_BG[0], _BG[1], _BG[2]) * width)
    return [bytearray(row) for _ in range(height)]


def _set_pixel(canvas: list[bytearray], x: int, y: int, rgb: tuple[int, int, int]) -> None:
    if 0 <= x < _WIDTH and 0 <= y < _HEIGHT:
        base = x * 3
        row = canvas[y]
        row[base], row[base + 1], row[base + 2] = rgb


def _fill_rect(
    canvas: list[bytearray],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    rgb: tuple[int, int, int],
) -> None:
    for y in range(max(0, y0), min(_HEIGHT, y1)):
        row = canvas[y]
        for x in range(max(0, x0), min(_WIDTH, x1)):
            base = x * 3
            row[base], row[base + 1], row[base + 2] = rgb


# Tiny 5×7 bitmap font (ASCII 32–126, stored as column bytes, LSB=top)
# This is a minimal built-in glyph table — only uppercase, digits and basic
# punctuation are needed for the certificate.
_GLYPH_W = 5
_GLYPH_H = 7

_FONT: dict[str, list[int]] = {
    " ": [0x00, 0x00, 0x00, 0x00, 0x00],
    "A": [0x7E, 0x11, 0x11, 0x11, 0x7E],
    "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x22],
    "D": [0x7F, 0x41, 0x41, 0x22, 0x1C],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41],
    "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x7A],
    "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00],
    "J": [0x20, 0x40, 0x41, 0x3F, 0x01],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41],
    "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x0C, 0x02, 0x7F],
    "N": [0x7F, 0x04, 0x08, 0x10, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E],
    "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E],
    "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31],
    "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F],
    "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x3F, 0x40, 0x38, 0x40, 0x3F],
    "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x07, 0x08, 0x70, 0x08, 0x07],
    "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E],
    "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46],
    "3": [0x21, 0x41, 0x45, 0x4B, 0x31],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10],
    "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3C, 0x4A, 0x49, 0x49, 0x30],
    "7": [0x01, 0x71, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36],
    "9": [0x06, 0x49, 0x49, 0x29, 0x1E],
    ":": [0x00, 0x36, 0x36, 0x00, 0x00],
    "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "/": [0x20, 0x10, 0x08, 0x04, 0x02],
    ".": [0x00, 0x60, 0x60, 0x00, 0x00],
    "●": [0x1C, 0x3E, 0x7F, 0x3E, 0x1C],
    "▲": [0x08, 0x1C, 0x3E, 0x7F, 0x00],
    "■": [0x7F, 0x7F, 0x7F, 0x7F, 0x7F],
    "○": [0x1C, 0x22, 0x41, 0x22, 0x1C],
}
_UNKNOWN_GLYPH = [0x41, 0x63, 0x55, 0x49, 0x63]

_STATUS_SYMBOLS = {"GREEN": "●", "ORANGE": "▲", "RED": "■", "GRAY": "○"}


def _draw_char(
    canvas: list[bytearray],
    ch: str,
    x: int,
    y: int,
    rgb: tuple[int, int, int],
    scale: int = 1,
) -> int:
    """Draw a character; returns new x position."""
    cols = _FONT.get(ch.upper(), _UNKNOWN_GLYPH)
    for col_idx, bits in enumerate(cols):
        for row_idx in range(_GLYPH_H):
            if bits & (1 << row_idx):
                for sy in range(scale):
                    for sx in range(scale):
                        _set_pixel(
                            canvas,
                            x + col_idx * scale + sx,
                            y + row_idx * scale + sy,
                            rgb,
                        )
    return x + (len(cols) + 1) * scale


def _draw_text(
    canvas: list[bytearray],
    text: str,
    x: int,
    y: int,
    rgb: tuple[int, int, int],
    scale: int = 1,
) -> None:
    cx = x
    for ch in text:
        cx = _draw_char(canvas, ch, cx, y, rgb, scale)


def _canvas_to_bytes(canvas: list[bytearray]) -> bytes:
    return b"".join(bytes(row) for row in canvas)


# ---------------------------------------------------------------------------
# Certificate builder
# ---------------------------------------------------------------------------

_STATUS_LABEL = {"GREEN": "OK", "ORANGE": "VAROVANI", "RED": "KRITICKY", "GRAY": "NEVYHOD."}


def build_certificate(
    study_uid: str,
    evaluation: "StudyEvaluation",
    config: "ReceiverConfig",
    *,
    operator: str = "",
) -> Dataset:
    """Return a pydicom Dataset (DICOM Secondary Capture) with a QC summary image."""
    now = datetime.now(UTC)
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H%M%S")

    canvas = _make_canvas()

    # Header bar
    _fill_rect(canvas, 0, 0, _WIDTH, 24, (36, 52, 73))
    _draw_text(canvas, "MR QUALITY CERTIFICATE", 8, 8, (255, 255, 255), scale=2)

    # Overall status badge
    overall = evaluation.status
    badge_rgb = _STATUS_COLORS.get(overall, _BG)
    badge_fg = _STATUS_FG.get(overall, _FG)
    _fill_rect(canvas, 0, 24, _WIDTH, 52, badge_rgb)
    sym = _STATUS_SYMBOLS.get(overall, "○")
    label = _STATUS_LABEL.get(overall, overall)
    _draw_text(canvas, f"{sym} CELKOVY STAV: {label}", 8, 32, badge_fg, scale=2)

    # Meta lines
    _draw_text(canvas, f"DATUM: {now.strftime('%Y-%m-%d %H:%M')} UTC", 8, 60, _FG)
    if operator:
        _draw_text(canvas, f"OPERATOR: {operator[:20].upper()}", 8, 72, _FG)
    protocol_name = ""
    try:
        protocol_name = evaluation.sequences[0].sequence_id.split("_")[0] if evaluation.sequences else ""
    except Exception:
        pass

    # Sequence rows
    row_y = 90
    _fill_rect(canvas, 0, row_y - 2, _WIDTH, row_y, (200, 210, 225))
    _draw_text(canvas, "SEKVENCE", 8, row_y, _FG)
    _draw_text(canvas, "STAV", 360, row_y, _FG)
    row_y += 12

    # From sequences presence list
    for item in evaluation.sequences:
        if row_y > _HEIGHT - 20:
            break
        status = item.status
        bg = _STATUS_COLORS.get(status, _BG)
        fg = _STATUS_FG.get(status, _FG)
        _fill_rect(canvas, 0, row_y - 1, _WIDTH, row_y + 9, bg)
        name = item.sequence_id[:34].upper()
        _draw_text(canvas, name, 8, row_y, fg)
        sym = _STATUS_SYMBOLS.get(status, "○")
        _draw_text(canvas, f"{sym} {_STATUS_LABEL.get(status, status)}", 360, row_y, fg)
        row_y += 10

    # Footer
    _fill_rect(canvas, 0, _HEIGHT - 16, _WIDTH, _HEIGHT, (220, 226, 235))
    _draw_text(canvas, f"MR.BUDDY AUTO-QC  STUDY:{study_uid[:30]}", 4, _HEIGHT - 12, (100, 110, 130))

    pixel_data = _canvas_to_bytes(canvas)

    # Build DICOM dataset
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    sop_instance_uid = generate_uid()
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = file_meta
    ds.is_implicit_VR = False
    ds.is_little_endian = True

    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = sop_instance_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.Modality = "OT"
    ds.ConversionType = "WSD"
    ds.ContentDate = date_str
    ds.ContentTime = time_str
    ds.StudyDate = date_str
    ds.StudyTime = time_str
    ds.ImageType = ["DERIVED", "SECONDARY"]
    ds.SamplesPerPixel = 3
    ds.PhotometricInterpretation = "RGB"
    ds.PlanarConfiguration = 0
    ds.Rows = _HEIGHT
    ds.Columns = _WIDTH
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.PixelData = pixel_data

    if operator:
        ds.OperatorsName = operator

    # Minimal content description
    ds.SeriesDescription = f"MR Quality Certificate — {overall}"
    ds.InstanceNumber = "1"

    # Embed study evaluation summary as a JSON private block for future extraction
    summary = {
        "overall_status": overall,
        "finalized": evaluation.finalized,
        "sequences": [
            {"id": s.sequence_id, "status": s.status, "reason": s.reason}
            for s in evaluation.sequences
        ],
    }
    import json  # noqa: PLC0415

    ds.ImageComments = json.dumps(summary, ensure_ascii=False)[:10240]

    return pydicom.dcmwrite if False else ds  # type: ignore[return-value]  # always returns ds


def send_certificate(ds: Dataset, config: "ReceiverConfig") -> None:
    """Forward the certificate dataset to PACS via C-STORE."""
    if not config.pacs.enabled:
        return
    from ..dicom.sender import send_dataset  # noqa: PLC0415

    # Write to a temp file, then send
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    with tempfile.NamedTemporaryFile(suffix=".dcm", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        pydicom.dcmwrite(str(tmp_path), ds)
        send_dataset(tmp_path, config.pacs)
    finally:
        tmp_path.unlink(missing_ok=True)
