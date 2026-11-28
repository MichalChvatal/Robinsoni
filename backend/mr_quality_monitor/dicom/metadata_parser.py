"""Header-only extraction of standard metadata with explicit frame provenance."""

from pathlib import Path

from pydicom import dcmread
from pydicom.dataset import Dataset

from ..analysis.metadata_normalizer import (
    Evidence,
    FieldSpec,
    MetadataRecord,
    MetadataValue,
    normalize,
)
from ..vendors.base import VendorAdapter, adapter_for, raw_strings

F = FieldSpec
FIELDS: dict[str, FieldSpec] = {
    **{
        name: F("text")
        for name in (
            "PatientID",
            "PatientName",
            "IssuerOfPatientID",
            "StudyDate",
            "StudyTime",
            "AccessionNumber",
            "SeriesDescription",
            "ProtocolName",
            "SequenceName",
            "Modality",
            "MRAcquisitionType",
            "InPlanePhaseEncodingDirection",
            "Manufacturer",
            "ManufacturerModelName",
            "TimezoneOffsetFromUTC",
            "StudyDescription",
        )
    },
    **{
        name: F("uid")
        for name in (
            "StudyInstanceUID",
            "SeriesInstanceUID",
            "SOPInstanceUID",
            "SOPClassUID",
            "FrameOfReferenceUID",
        )
    },
    **{
        name: F("text", count=None)
        for name in (
            "ImageType",
            "ScanningSequence",
            "SequenceVariant",
            "SoftwareVersions",
        )
    },
    **{name: F("float", "ms") for name in ("RepetitionTime", "EchoTime", "InversionTime")},
    **{name: F("float", "mm") for name in ("SliceThickness", "SpacingBetweenSlices")},
    "FlipAngle": F("float", "deg"),
    "PixelSpacing": F("float", "mm", 2),
    "ImagePositionPatient": F("float", "mm", 3),
    "ImageOrientationPatient": F("float", count=6),
    "DiffusionGradientOrientation": F("float", count=3),
    "DiffusionBValue": F("float", "s/mm2"),
    "MagneticFieldStrength": F("float", "T"),
    "PixelBandwidth": F("float", "Hz/pixel"),
    "NumberOfAverages": F("float"),
    "AcquisitionMatrix": F("int", count=4),
    **{
        name: F("int")
        for name in (
            "Rows",
            "Columns",
            "EchoTrainLength",
            "SeriesNumber",
            "InstanceNumber",
            "EchoNumbers",
            "TemporalPositionIdentifier",
            "TemporalPositionIndex",
            "NumberOfFrames",
            "AcquisitionNumber",
        )
    },
}

# A deliberately explicit supported subset, not recursive first-match tag searching.
# EchoTime is normalized from EffectiveEchoTime in the enhanced MR echo macro.
FUNCTIONAL_PATHS = {
    "RepetitionTime": ("MRTimingAndRelatedParametersSequence", "RepetitionTime"),
    "FlipAngle": ("MRTimingAndRelatedParametersSequence", "FlipAngle"),
    "EchoTime": ("MREchoSequence", "EffectiveEchoTime"),
    "PixelSpacing": ("PixelMeasuresSequence", "PixelSpacing"),
    "SliceThickness": ("PixelMeasuresSequence", "SliceThickness"),
    "SpacingBetweenSlices": ("PixelMeasuresSequence", "SpacingBetweenSlices"),
    "ImagePositionPatient": ("PlanePositionSequence", "ImagePositionPatient"),
    "ImageOrientationPatient": ("PlaneOrientationSequence", "ImageOrientationPatient"),
    "DiffusionBValue": ("MRDiffusionSequence", "DiffusionBValue"),
    "DiffusionGradientOrientation": (
        "MRDiffusionSequence",
        "DiffusionGradientDirectionSequence",
        "DiffusionGradientOrientation",
    ),
    "TemporalPositionIndex": ("FrameContentSequence", "TemporalPositionIndex"),
}
IDENTIFIERS = {"PatientID", "PatientName", "IssuerOfPatientID", "AccessionNumber"}


class MetadataStructureError(ValueError):
    """Malformed functional groups cannot be interpreted as valid per-frame metadata."""


def evidence_at(
    dataset: Dataset, parts: tuple[str, ...], prefix: str, unit: str | None
) -> Evidence | None:
    current = dataset
    path = prefix
    for keyword in parts[:-1]:
        sequence = getattr(current, keyword, None)
        if sequence is None:
            return None
        if len(sequence) != 1 or not isinstance(sequence[0], Dataset):
            raise MetadataStructureError("Expected one item in functional group macro")
        current = sequence[0]
        path += f"{keyword}[0]."
    if parts[-1] not in current:
        return None
    element = current.data_element(parts[-1])
    if element is None:
        return None
    try:
        raw = raw_strings(element.value)
    except ValueError:
        # Binary scalar values are not guessed or converted to strings.
        return Evidence(path + parts[-1], f"{int(element.tag):08X}", (), unit, invalid=True)
    return Evidence(path + parts[-1], f"{int(element.tag):08X}", raw, unit)


def parse_dataset(
    dataset: Dataset, *, include_identifiers: bool = False, adapter: VendorAdapter | None = None
) -> tuple[MetadataRecord, ...]:
    """Return one record per frame without touching pixel_array.

    Per-frame, shared and top-level sources are retained in priority order.
    Differing populated sources are marked conflicting. Vendor values supplement
    missing standard values; a disagreement never silently overrides a standard value.
    """
    try:
        frames = int(getattr(dataset, "NumberOfFrames", 1))
        if frames < 1 or frames > 100000:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise MetadataStructureError("Invalid NumberOfFrames") from None
    per_frame = getattr(dataset, "PerFrameFunctionalGroupsSequence", ())
    shared = getattr(dataset, "SharedFunctionalGroupsSequence", ())
    if len(shared) > 1 or (per_frame and len(per_frame) != frames):
        raise MetadataStructureError("Functional group count mismatch")
    if frames > 1 and not per_frame:
        raise MetadataStructureError("Multiframe parsing requires per-frame functional groups")
    if any(not isinstance(item, Dataset) for item in (*per_frame, *shared)):
        raise MetadataStructureError("Invalid functional group item")
    manufacturer = str(getattr(dataset, "Manufacturer", ""))
    adapter = adapter or adapter_for(manufacturer)
    # A caller cannot use a Siemens binding against a differently identified vendor.
    detected = adapter_for(manufacturer)
    if adapter.name != "generic" and adapter.name != detected.name:
        raise MetadataStructureError("Vendor adapter does not match Manufacturer")
    private = adapter.extract(dataset)
    if set(private) - set(FIELDS):
        raise MetadataStructureError("Vendor binding references an unknown normalized field")
    records = []
    for frame in range(frames):
        fields = {}
        for keyword, spec in FIELDS.items():
            if keyword in IDENTIFIERS and not include_identifiers:
                fields[keyword] = MetadataValue(None, spec.unit, "missing", reason="suppressed")
                continue
            sources = []
            macro_path = FUNCTIONAL_PATHS.get(keyword)
            if macro_path:
                if per_frame:
                    candidate = evidence_at(
                        per_frame[frame],
                        macro_path,
                        f"PerFrameFunctionalGroupsSequence[{frame}].",
                        spec.unit,
                    )
                    if candidate is not None:
                        sources.append(candidate)
                if shared:
                    candidate = evidence_at(
                        shared[0], macro_path, "SharedFunctionalGroupsSequence[0].", spec.unit
                    )
                    if candidate is not None:
                        sources.append(candidate)
            candidate = evidence_at(dataset, (keyword,), "", spec.unit)
            if candidate is not None:
                sources.append(candidate)
            sources.extend(private.get(keyword, ()))
            fields[keyword] = normalize(spec, tuple(sources))
        records.append(
            MetadataRecord(
                fields, frame_index=frame if per_frame else None, vendor_parser=adapter.identifier
            )
        )
    return tuple(records)


def parse_file(path: Path, *, include_identifiers: bool = False) -> tuple[MetadataRecord, ...]:
    """Load a file header only, leaving stored pixel data unallocated and undecoded."""
    return parse_dataset(
        dcmread(path, stop_before_pixels=True), include_identifiers=include_identifiers
    )
