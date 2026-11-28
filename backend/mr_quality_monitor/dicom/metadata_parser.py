"""Header-only extraction of standard metadata with explicit frame provenance."""

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from ..protocols.models import ProstateChecks

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
            "OperatorsName",
            "PatientAge",
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
    **{
        name: F("float", "mm")
        for name in (
            "SliceThickness",
            "SpacingBetweenSlices",
            "SliceGap",
            "PhaseResolution",
            "FrequencyResolution",
        )
    },
    "FlipAngle": F("float", "deg"),
    "PixelSpacing": F("float", "mm", 2),
    "FieldOfView": F("float", "mm", 2),
    "ImagePositionPatient": F("float", "mm", 3),
    "ImageOrientationPatient": F("float", count=6),
    "DiffusionGradientOrientation": F("float", count=3),
    "DiffusionBValue": F("float", "s/mm2"),
    "PrivateDiffusionBValue": F("float", "s/mm2"),
    "SliceGap": F("float", "mm"),
    "PhaseResolution": F("float", "mm"),
    "FrequencyResolution": F("float", "mm"),
    "MagneticFieldStrength": F("float", "T"),
    "PixelBandwidth": F("float", "Hz/pixel"),
    "NumberOfAverages": F("float"),
    "PatientWeight": F("float", "kg"),
    "PatientSize": F("float", "m"),
    "PatientBMI": F("float", "kg/m2"),
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
DERIVED_FIELDS = {
    "PrivateDiffusionBValue",
    "SliceGap",
    "FieldOfView",
    "PhaseResolution",
    "FrequencyResolution",
}


def prostate_metadata(dataset, fields, profile, vendor):
    def derive(name, inputs, calculate):
        values = [fields[key] for key in inputs]
        if all(value.state == "known" for value in values):
            result = calculate(*(value.value for value in values))
            if result is not None:
                fields[name] = MetadataValue(
                    result,
                    FIELDS[name].unit,
                    "known",
                    tuple(source for value in values for source in value.sources),
                )

    derive(
        "SliceGap",
        ("SpacingBetweenSlices", "SliceThickness"),
        lambda spacing, thickness: (
            float(Decimal(str(spacing)) - Decimal(str(thickness)))
            if spacing > 0 and thickness > 0
            else None
        ),
    )
    derive(
        "FieldOfView",
        ("Rows", "Columns", "PixelSpacing"),
        lambda rows, cols, spacing: (
            tuple(round(size * step, 2) for size, step in zip((rows, cols), spacing, strict=True))
            if min(rows, cols, *spacing) > 0
            else None
        ),
    )
    for index, name in enumerate(("PhaseResolution", "FrequencyResolution")):
        derive(
            name,
            ("PixelSpacing", "InPlanePhaseEncodingDirection"),
            lambda spacing, direction: (
                spacing[index if direction == "ROW" else 1 - index]
                if direction in {"ROW", "COL"} and min(spacing) > 0
                else None
            ),
        )

    sources = []
    if vendor == "siemens":
        for source in profile.private_b_values:
            if str(
                dataset.get("ManufacturerModelName", "")
            ) != source.model or source.software_version not in raw_strings(
                dataset.get("SoftwareVersions")
            ):
                continue
            tag = int(source.tag, 16)
            element = dataset.get(tag)
            creator = dataset.get((tag >> 16, (tag & 0xFFFF) >> 8))
            if element is None or creator is None or creator.value != source.creator:
                continue
            invalid = element.VR != source.vr
            try:
                values = raw_strings(element.value)
            except ValueError:
                values, invalid = (), True
            sources.append(
                Evidence(
                    f"private[{source.creator}].{source.tag}",
                    source.tag,
                    values,
                    "s/mm2",
                    "mou-private/1",
                    invalid,
                )
            )
    fields["PrivateDiffusionBValue"] = normalize(FIELDS["PrivateDiffusionBValue"], tuple(sources))


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


def _derived_evidence(path: str, values: tuple[float, ...], unit: str | None) -> Evidence:
    return Evidence(
        path=f"derived:{path}",
        tag="00000000",
        raw=tuple(f"{value:.6f}".rstrip("0").rstrip(".") or "0" for value in values),
        unit=unit,
        parser="derived/1",
    )


def _populate_derived_fields(fields: dict[str, MetadataValue]) -> None:
    def _known(name: str):
        item = fields.get(name)
        return item.value if item and item.state == "known" else None

    rows, cols = _known("Rows"), _known("Columns")
    spacing = _known("PixelSpacing")
    if (
        isinstance(rows, int)
        and isinstance(cols, int)
        and rows > 0
        and cols > 0
        and isinstance(spacing, tuple)
        and len(spacing) == 2
        and all(value > 0 for value in spacing)
    ):
        fov_row = round(rows * spacing[0], 4)
        fov_col = round(cols * spacing[1], 4)
        fields["FieldOfView"] = normalize(
            FIELDS["FieldOfView"],
            (
                _derived_evidence(
                    "Rows*PixelSpacing[0],Columns*PixelSpacing[1]", (fov_row, fov_col), "mm"
                ),
            ),
        )
        direction = _known("InPlanePhaseEncodingDirection")
        acq_matrix = _known("AcquisitionMatrix")
        if direction in {"ROW", "COL"}:
            phase_steps = None
            freq_steps = None
            src_suffix = "PixelSpacing+InPlanePhaseEncodingDirection"
            if isinstance(acq_matrix, tuple) and len(acq_matrix) == 4:
                if direction == "ROW" and acq_matrix[2] > 0 and acq_matrix[1] > 0:
                    phase_steps, freq_steps = acq_matrix[2], acq_matrix[1]
                    src_suffix = "FOV/AcquisitionMatrix"
                elif direction == "COL" and acq_matrix[3] > 0 and acq_matrix[0] > 0:
                    phase_steps, freq_steps = acq_matrix[3], acq_matrix[0]
                    src_suffix = "FOV/AcquisitionMatrix"
            if direction == "ROW":
                phase_res = fov_row / phase_steps if phase_steps else spacing[0]
                freq_res = fov_col / freq_steps if freq_steps else spacing[1]
            else:
                phase_res = fov_col / phase_steps if phase_steps else spacing[1]
                freq_res = fov_row / freq_steps if freq_steps else spacing[0]
            fields["PhaseResolution"] = normalize(
                FIELDS["PhaseResolution"],
                (_derived_evidence(src_suffix, (round(phase_res, 6),), "mm"),),
            )
            fields["FrequencyResolution"] = normalize(
                FIELDS["FrequencyResolution"],
                (_derived_evidence(src_suffix, (round(freq_res, 6),), "mm"),),
            )

    thickness = _known("SliceThickness")
    between = _known("SpacingBetweenSlices")
    if isinstance(thickness, float) and isinstance(between, float):
        gap = round(between - thickness, 6)
        fields["SliceGap"] = normalize(
            FIELDS["SliceGap"],
            (_derived_evidence("SpacingBetweenSlices-SliceThickness", (gap,), "mm"),),
        )

    weight = _known("PatientWeight")
    size = _known("PatientSize")
    if isinstance(weight, float) and isinstance(size, float) and weight > 0 and size > 0:
        bmi = round(weight / (size * size), 2)
        fields["PatientBMI"] = normalize(
            FIELDS["PatientBMI"],
            (_derived_evidence("PatientWeight/(PatientSize^2)", (bmi,), "kg/m2"),),
        )


def parse_dataset(
    dataset: Dataset,
    *,
    include_identifiers: bool = False,
    adapter: VendorAdapter | None = None,
    b_value_tags: tuple[str, ...] | None = None,
    prostate_checks: "ProstateChecks | None" = None,
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
    tags = tuple(b_value_tags) if b_value_tags else ()
    adapter = adapter or adapter_for(manufacturer, b_value_tags=tags)
    # A caller cannot use a Siemens binding against a differently identified vendor.
    detected = adapter_for(manufacturer, b_value_tags=tags)
    if adapter.name != "generic" and adapter.name != detected.name:
        raise MetadataStructureError("Vendor adapter does not match Manufacturer")
    private = adapter.extract(dataset)
    if set(private) - set(FIELDS):
        raise MetadataStructureError("Vendor binding references an unknown normalized field")
    records = []
    for frame in range(frames):
        fields = {}
        for keyword, spec in FIELDS.items():
            if keyword in DERIVED_FIELDS:
                if prostate_checks is not None:
                    fields[keyword] = MetadataValue(
                        None, spec.unit, "missing", reason="not_available"
                    )
                continue
            if keyword in IDENTIFIERS and not include_identifiers:
                fields[keyword] = MetadataValue(None, spec.unit, "missing", reason="suppressed")
                continue
            if keyword in {
                "FieldOfView",
                "SliceGap",
                "PhaseResolution",
                "FrequencyResolution",
                "PatientBMI",
            }:
                fields[keyword] = MetadataValue(None, spec.unit, "missing", reason="not_available")
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
        _populate_derived_fields(fields)
        if prostate_checks is not None:
            prostate_metadata(dataset, fields, prostate_checks, detected.name)
        records.append(
            MetadataRecord(
                fields, frame_index=frame if per_frame else None, vendor_parser=adapter.identifier
            )
        )
    return tuple(records)


def parse_file(
    path: Path,
    *,
    include_identifiers: bool = False,
    b_value_tags: tuple[str, ...] | None = None,
    prostate_checks: "ProstateChecks | None" = None,
) -> tuple[MetadataRecord, ...]:
    """Load a file header only, leaving stored pixel data unallocated and undecoded."""
    return parse_dataset(
        dcmread(path, stop_before_pixels=True),
        include_identifiers=include_identifiers,
        b_value_tags=b_value_tags,
        prostate_checks=prostate_checks,
    )
