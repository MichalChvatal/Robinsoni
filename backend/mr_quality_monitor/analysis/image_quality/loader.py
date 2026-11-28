"""Pure-Python DICOM pixel loader (no numpy dependency) for artifact detection."""

from array import array
from pathlib import Path

from pydicom import dcmread
from pydicom.dataset import Dataset

from .base import SlicePixels


def decode_dataset_pixels(
    dataset: Dataset,
    *,
    phase_direction: str | None = None,
    slice_position_mm: float | None = None,
) -> SlicePixels | None:
    """Decode uncompressed single-frame 8/16-bit monochrome pixel data into float array."""
    rows = int(getattr(dataset, "Rows", 0) or 0)
    cols = int(getattr(dataset, "Columns", 0) or 0)
    samples = int(getattr(dataset, "SamplesPerPixel", 1) or 1)
    bits = int(getattr(dataset, "BitsAllocated", 0) or 0)
    signed = int(getattr(dataset, "PixelRepresentation", 0) or 0)
    raw = getattr(dataset, "PixelData", None)
    if rows <= 0 or cols <= 0 or samples != 1 or bits not in (8, 16) or not raw:
        return None
    count = rows * cols
    typecode = "B" if bits == 8 and signed == 0 else "b" if bits == 8 else "H" if signed == 0 else "h"
    arr = array(typecode)
    expected_bytes = count * (bits // 8)
    if len(raw) < expected_bytes:
        return None
    arr.frombytes(raw[:expected_bytes])
    slope = float(getattr(dataset, "RescaleSlope", 1.0) or 1.0)
    intercept = float(getattr(dataset, "RescaleIntercept", 0.0) or 0.0)
    if slope == 1.0 and intercept == 0.0:
        floats = array("f", arr)
    else:
        floats = array("f", (val * slope + intercept for val in arr))
    return SlicePixels(
        sop_uid=str(getattr(dataset, "SOPInstanceUID", "")),
        rows=rows,
        columns=cols,
        pixels=floats,
        phase_direction=phase_direction,
        slice_position_mm=slice_position_mm,
    )


def load_slice_pixels(
    path: Path,
    *,
    phase_direction: str | None = None,
    slice_position_mm: float | None = None,
) -> SlicePixels | None:
    dataset = dcmread(path, stop_before_pixels=False)
    return decode_dataset_pixels(
        dataset,
        phase_direction=phase_direction,
        slice_position_mm=slice_position_mm,
    )


def nonzero_mask_bounds_mm(slices: tuple[SlicePixels, ...]) -> tuple[float, float] | None:
    """Return min/max slice position among slices containing nonzero prostate segmentation."""
    positions = [
        sl.slice_position_mm
        for sl in slices
        if sl.slice_position_mm is not None and any(val > 0 for val in sl.pixels[::4])
    ]
    if not positions:
        return None
    return (min(positions), max(positions))
