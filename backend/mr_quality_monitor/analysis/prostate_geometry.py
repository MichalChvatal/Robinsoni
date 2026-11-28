"""Compare DWI and axial T2 planes in one DICOM frame of reference."""

from math import acos, degrees
from statistics import median

from .series_aggregator import AcquisitionGroup, slice_normal


def dot(first, second):
    return sum(a * b for a, b in zip(first, second, strict=True))


def alignment(dwi: AcquisitionGroup, axial: AcquisitionGroup):
    groups = (dwi, axial)
    frame_ids = {
        record.known("FrameOfReferenceUID") for group in groups for record in group.members
    }
    if len(frame_ids) != 1 or None in frame_ids:
        return "alignment_reference_unknown", None
    orientations = [
        {record.known("ImageOrientationPatient") for record in group.members} for group in groups
    ]
    if any(len(values) != 1 or None in values for values in orientations):
        return "alignment_geometry_unknown", None
    dwi_axes, axial_axes = (next(iter(values)) for values in orientations)
    try:
        normal = slice_normal(axial_axes)
        slice_normal(dwi_axes)
    except ValueError:
        return "alignment_geometry_unknown", None
    angles = []
    for start in (0, 3):
        left, right = dwi_axes[start : start + 3], axial_axes[start : start + 3]
        cosine = dot(left, right) / (dot(left, left) * dot(right, right)) ** 0.5
        angles.append(degrees(acos(max(-1, min(1, cosine)))))
    positions = []
    for group in groups:
        points = [record.known("ImagePositionPatient") for record in group.members]
        if any(point is None for point in points):
            return "alignment_geometry_unknown", None
        positions.append(sorted({dot(normal, point) for point in points}))
    left, right = positions
    if len(left) < 2 or len(right) < 2:
        return "alignment_geometry_unknown", None
    # Cut at the nearest sampled edge of the shared region. Include its nearest plane even
    # when grids are shifted. Ignore extra slices outside the other group's coverage.
    low, high = max(left[0], right[0]), min(left[-1], right[-1])
    if low > high:
        return "alignment_no_overlap", None
    errors = [
        min(abs(point - other) for other in target)
        for source, target in ((left, right), (right, left))
        for point in source
        if low <= point <= high
    ]
    if not errors:
        return "alignment_geometry_unknown", None
    spacing = [median(b - a for a, b in zip(values, values[1:])) for values in positions]
    return "alignment_measured", (max(errors), abs(spacing[0] - spacing[1]), max(angles))
