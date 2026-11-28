"""Conservative geometry grouping and slice ordering, without a completion claim."""

import hashlib
import json
import math
from dataclasses import dataclass

from .metadata_normalizer import MetadataRecord


@dataclass(frozen=True)
class AcquisitionGroup:
    key: str
    members: tuple[MetadataRecord, ...]
    projected_positions_mm: tuple[float, ...]
    issues: tuple[str, ...]
    complete: bool = False  # Receiving data or geometric consistency never proves completion.


def cross(left: tuple, right: tuple) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def slice_normal(orientation: tuple) -> tuple[float, float, float]:
    left, right = orientation[:3], orientation[3:]
    if (
        abs(sum(value * value for value in left) - 1) > 1e-4
        or abs(sum(value * value for value in right) - 1) > 1e-4
        or abs(sum(a * b for a, b in zip(left, right, strict=True))) > 1e-4
    ):
        raise ValueError("invalid_orientation")
    return cross(left, right)


DIMENSIONS = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "FrameOfReferenceUID",
    "SOPClassUID",
    "Rows",
    "Columns",
    "PixelSpacing",
    "ImageOrientationPatient",
    "SliceThickness",
    "ImageType",
    "EchoTime",
    "EchoNumbers",
    "TemporalPositionIdentifier",
    "TemporalPositionIndex",
    "DiffusionBValue",
    "DiffusionGradientOrientation",
    "AcquisitionNumber",
)
REQUIRED_GEOMETRY = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "Rows",
    "Columns",
    "PixelSpacing",
    "ImageOrientationPatient",
    "ImagePositionPatient",
)


def group_acquisitions(
    records: tuple[MetadataRecord, ...], *, spacing_tolerance_mm: float = 0.05
) -> tuple[AcquisitionGroup, ...]:
    """Separate known dimensions and order by position projected onto slice normal.

    Records with incomplete/conflicting geometry are isolated and cannot be stacked.
    Missing optional dimensions remain unknown. A group is a candidate, not a
    claim that diffusion/temporal dimensions have been fully recovered.
    """
    if not math.isfinite(spacing_tolerance_mm) or spacing_tolerance_mm < 0:
        raise ValueError("Invalid spacing tolerance")
    groups: dict[str, list[tuple[float | None, MetadataRecord]]] = {}
    problems: dict[str, set[str]] = {}
    seen: dict[tuple, dict] = {}
    for index, record in enumerate(records):
        identity = (record.known("SOPInstanceUID"), record.frame_index)
        if identity[0] is not None:
            snapshot = record.to_dict()
            if identity in seen:
                if seen[identity] != snapshot:
                    raise ValueError("Conflicting duplicate instance/frame metadata")
                continue
            seen[identity] = snapshot
        issues = set()
        projection = None
        if any(record.known(keyword) is None for keyword in REQUIRED_GEOMETRY):
            issues.add("incomplete_geometry")
        if any(
            record.fields[keyword].state in {"invalid", "conflicting"} for keyword in DIMENSIONS
        ):
            issues.add("uncertain_dimension")
        if not issues:
            try:
                if record.known("Rows") <= 0 or record.known("Columns") <= 0:
                    raise ValueError
                if any(value <= 0 for value in record.known("PixelSpacing")):
                    raise ValueError
                normal = slice_normal(record.known("ImageOrientationPatient"))
                projection = sum(
                    a * b for a, b in zip(record.known("ImagePositionPatient"), normal, strict=True)
                )
            except (ValueError, TypeError):
                issues.add("invalid_geometry")
        dimensions = {keyword: record.known(keyword) for keyword in DIMENSIONS}
        if issues:
            dimensions["isolated_record"] = (identity[0], identity[1], index)
        key = hashlib.sha256(json.dumps(dimensions, sort_keys=True).encode()).hexdigest()
        groups.setdefault(key, []).append((projection, record))
        problems.setdefault(key, set()).update(issues)
    result = []
    for key, members in groups.items():
        ordered = sorted(members, key=lambda item: item[0] if item[0] is not None else 0)
        positions = tuple(item[0] for item in ordered if item[0] is not None)
        gaps = tuple(b - a for a, b in zip(positions, positions[1:]))
        if any(gap <= 1e-6 for gap in gaps):
            problems[key].add("repeated_slice_position")
        if gaps and max(gaps) - min(gaps) > spacing_tolerance_mm:
            problems[key].add("irregular_slice_spacing")
        expected = {item[1].known("SpacingBetweenSlices") for item in ordered}
        expected.discard(None)
        if len(expected) > 1:
            problems[key].add("conflicting_declared_spacing")
        elif expected and gaps:
            spacing = next(iter(expected))
            if spacing <= 0 or any(abs(gap - spacing) > spacing_tolerance_mm for gap in gaps):
                problems[key].add("declared_spacing_mismatch")
        result.append(
            AcquisitionGroup(
                key, tuple(item[1] for item in ordered), positions, tuple(sorted(problems[key]))
            )
        )
    return tuple(sorted(result, key=lambda group: group.key))
