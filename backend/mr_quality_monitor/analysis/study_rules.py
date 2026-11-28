"""Study-level clinical compliance rules (T2W count, DWI+RESOLVE fallback, geometry & coverage)."""

from dataclasses import dataclass
from typing import Literal

from ..protocols.models import (
    CoverageCheckRule,
    FallbackGroupRule,
    GeometryAlignmentRule,
    MinValidGroupRule,
    Protocol,
)
from .series_aggregator import AcquisitionGroup, slice_normal

Status = Literal["GREEN", "ORANGE", "RED", "GRAY"]


@dataclass(frozen=True)
class StudyCheckResult:
    rule_id: str
    kind: str
    status: Status
    reason: str
    details: dict
    action: str = ""


def _severity_status(severity: str) -> Status:
    return {"critical": "RED", "warning": "ORANGE", "unknown": "GRAY"}[severity]


def _group_by_seq(acquisitions: tuple, groups: tuple[AcquisitionGroup, ...]) -> dict[str, list]:
    groups_map = {group.key: group for group in groups}
    by_seq: dict[str, list[tuple]] = {}
    for ev in acquisitions:
        if ev.sequence_id is not None and ev.group_key in groups_map:
            by_seq.setdefault(ev.sequence_id, []).append((ev, groups_map[ev.group_key]))
    return by_seq


def _eval_min_valid(
    rule: MinValidGroupRule,
    protocol: Protocol,
    by_seq: dict[str, list[tuple]],
    finalized: bool,
) -> StudyCheckResult:
    seq_ids = [seq.sequence_id for seq in protocol.sequences if seq.group == rule.group]
    valid_seq_ids = []
    present_seq_ids = []
    for sid in seq_ids:
        entries = by_seq.get(sid, [])
        if entries:
            present_seq_ids.append(sid)
            if any(ev.status in {"GREEN", "ORANGE"} for ev, _ in entries):
                valid_seq_ids.append(sid)
    details = {
        "group": rule.group,
        "required_min_valid": rule.min_valid_count,
        "valid_sequences": valid_seq_ids,
        "present_sequences": present_seq_ids,
    }
    if len(valid_seq_ids) < rule.min_valid_count:
        status: Status = _severity_status(rule.severity) if finalized else "GRAY"
        reason = (
            "group_min_valid_not_met"
            if finalized
            else "group_min_valid_pending"
        )
        return StudyCheckResult(
            rule.rule_id, "min_valid_group", status, reason, details, rule.action
        )
    if rule.preferred_sequence_id and rule.preferred_sequence_id not in valid_seq_ids:
        return StudyCheckResult(
            rule.rule_id,
            "min_valid_group",
            _severity_status(rule.missing_preferred_severity),
            "preferred_sequence_missing_or_invalid",
            {**details, "preferred_sequence_id": rule.preferred_sequence_id},
            rule.action,
        )
    return StudyCheckResult(
        rule.rule_id, "min_valid_group", "GREEN", "group_requirement_met", details
    )


def _eval_fallback(
    rule: FallbackGroupRule,
    by_seq: dict[str, list[tuple]],
    finalized: bool,
) -> StudyCheckResult:
    primary_ok = []
    primary_bad = []
    primary_missing = []
    for sid in rule.primary_sequence_ids:
        entries = by_seq.get(sid, [])
        if not entries:
            primary_missing.append(sid)
        elif any(ev.status in {"GREEN", "ORANGE"} for ev, _ in entries):
            primary_ok.append(sid)
        else:
            primary_bad.append(sid)

    fallback_ok = [
        sid
        for sid in rule.fallback_sequence_ids
        if any(ev.status in {"GREEN", "ORANGE"} for ev, _ in by_seq.get(sid, []))
    ]
    details = {
        "primary_ok": primary_ok,
        "primary_bad": primary_bad,
        "primary_missing": primary_missing,
        "fallback_ok": fallback_ok,
    }
    if not primary_bad and not primary_missing:
        return StudyCheckResult(
            rule.rule_id, "fallback_group", "GREEN", "primary_set_valid", details
        )
    if fallback_ok:
        return StudyCheckResult(
            rule.rule_id,
            "fallback_group",
            _severity_status(rule.fallback_used_severity),
            "fallback_sequence_used",
            details,
            rule.action,
        )
    if primary_bad or finalized:
        return StudyCheckResult(
            rule.rule_id,
            "fallback_group",
            _severity_status(rule.severity),
            "primary_invalid_without_resolve",
            details,
            rule.action,
        )
    return StudyCheckResult(rule.rule_id, "fallback_group", "GRAY", "primary_set_pending", details)


def _check_pair_alignment(
    ref_group: AcquisitionGroup,
    tgt_group: AcquisitionGroup,
    rule: GeometryAlignmentRule,
) -> tuple[bool, dict]:
    ref_first = ref_group.members[0]
    tgt_first = tgt_group.members[0]
    ref_ori = ref_first.known("ImageOrientationPatient")
    tgt_ori = tgt_first.known("ImageOrientationPatient")
    if ref_ori is None or tgt_ori is None:
        return False, {"reason": "missing_orientation"}
    try:
        ref_norm = slice_normal(ref_ori)
        tgt_norm = slice_normal(tgt_ori)
    except ValueError:
        return False, {"reason": "invalid_orientation"}
    ori_diff = max(abs(a - b) for a, b in zip(ref_ori, tgt_ori, strict=True))
    dot = abs(sum(a * b for a, b in zip(ref_norm, tgt_norm, strict=True)))
    if ori_diff > rule.orientation_tolerance and (1.0 - dot) > rule.orientation_tolerance:
        return False, {"reason": "orientation_mismatch", "max_ori_diff": round(ori_diff, 6)}

    ref_sp = ref_first.known("SpacingBetweenSlices") or ref_first.known("SliceThickness")
    tgt_sp = tgt_first.known("SpacingBetweenSlices") or tgt_first.known("SliceThickness")
    if (
        ref_sp is not None
        and tgt_sp is not None
        and abs(ref_sp - tgt_sp) > rule.spacing_tolerance_mm
    ):
        return False, {
            "reason": "slice_spacing_mismatch",
            "reference_spacing_mm": ref_sp,
            "target_spacing_mm": tgt_sp,
        }

    ref_pos = ref_group.projected_positions_mm
    tgt_pos = tgt_group.projected_positions_mm
    if not ref_pos or not tgt_pos:
        return False, {"reason": "missing_slice_positions"}

    # Check overlapping slice-to-slice positions (allows differing slice counts).
    ref_min = min(ref_pos) - rule.position_tolerance_mm
    ref_max = max(ref_pos) + rule.position_tolerance_mm
    overlapping = [pos for pos in tgt_pos if ref_min <= pos <= ref_max]
    if not overlapping:
        return False, {"reason": "no_slice_overlap"}
    max_pos_err = max(min(abs(pos - r) for r in ref_pos) for pos in overlapping)
    if max_pos_err > rule.position_tolerance_mm:
        return False, {
            "reason": "slice_position_mismatch",
            "max_position_error_mm": round(max_pos_err, 4),
        }
    return True, {
        "max_position_error_mm": round(max_pos_err, 4),
        "overlapping_slices": len(overlapping),
    }


def _eval_alignment(
    rule: GeometryAlignmentRule,
    by_seq: dict[str, list[tuple]],
) -> StudyCheckResult:
    refs = by_seq.get(rule.reference_sequence_id, [])
    if not refs:
        return StudyCheckResult(
            rule.rule_id,
            "geometry_alignment",
            "GRAY",
            "reference_sequence_missing",
            {"reference_sequence_id": rule.reference_sequence_id},
        )
    ref_group = refs[-1][1]
    checked = {}
    mismatches = {}
    for sid in rule.target_sequence_ids:
        for _, tgt_group in by_seq.get(sid, []):
            ok, info = _check_pair_alignment(ref_group, tgt_group, rule)
            checked[sid] = info
            if not ok:
                mismatches[sid] = info
    if not checked:
        return StudyCheckResult(
            rule.rule_id,
            "geometry_alignment",
            "GRAY",
            "target_sequences_pending",
            {"reference_sequence_id": rule.reference_sequence_id},
        )
    if mismatches:
        return StudyCheckResult(
            rule.rule_id,
            "geometry_alignment",
            _severity_status(rule.severity),
            "geometry_alignment_mismatch",
            {"mismatches": mismatches, "checked": checked},
            rule.action,
        )
    return StudyCheckResult(
        rule.rule_id,
        "geometry_alignment",
        "GREEN",
        "geometry_aligned",
        {"checked": checked},
    )


def _eval_coverage(
    rule: CoverageCheckRule,
    by_seq: dict[str, list[tuple]],
    seg_bounds_mm: tuple[float, float] | None,
) -> StudyCheckResult:
    seg_entries = by_seq.get(rule.segmentation_sequence_id, [])
    if not seg_entries:
        return StudyCheckResult(
            rule.rule_id,
            "anatomical_coverage",
            "GRAY",
            "segmentation_not_present",
            {"segmentation_sequence_id": rule.segmentation_sequence_id},
        )
    if seg_bounds_mm is None:
        return StudyCheckResult(
            rule.rule_id,
            "anatomical_coverage",
            "GRAY",
            "segmentation_mask_bounds_unavailable",
            {"segmentation_sequence_id": rule.segmentation_sequence_id},
        )
    seg_min, seg_max = seg_bounds_mm
    uncovered = {}
    checked = {}
    for sid in rule.target_sequence_ids:
        for _, grp in by_seq.get(sid, []):
            if not grp.projected_positions_mm:
                continue
            tgt_min, tgt_max = min(grp.projected_positions_mm), max(grp.projected_positions_mm)
            checked[sid] = {"target_range_mm": [round(tgt_min, 3), round(tgt_max, 3)]}
            if tgt_min > seg_min + rule.margin_mm or tgt_max < seg_max - rule.margin_mm:
                uncovered[sid] = {
                    "target_range_mm": [round(tgt_min, 3), round(tgt_max, 3)],
                    "prostate_range_mm": [round(seg_min, 3), round(seg_max, 3)],
                }
    if not checked:
        return StudyCheckResult(rule.rule_id, "anatomical_coverage", "GRAY", "targets_pending", {})
    if uncovered:
        return StudyCheckResult(
            rule.rule_id,
            "anatomical_coverage",
            _severity_status(rule.severity),
            "prostate_coverage_incomplete",
            {"uncovered": uncovered, "prostate_range_mm": [round(seg_min, 3), round(seg_max, 3)]},
            rule.action,
        )
    return StudyCheckResult(
        rule.rule_id,
        "anatomical_coverage",
        "GREEN",
        "prostate_coverage_ok",
        {"prostate_range_mm": [round(seg_min, 3), round(seg_max, 3)], "checked": checked},
    )


def evaluate_study_rules(
    protocol: Protocol,
    acquisitions: tuple,
    groups: tuple[AcquisitionGroup, ...],
    *,
    finalized: bool,
    seg_bounds_mm: dict[str, tuple[float, float]] | None = None,
) -> tuple[StudyCheckResult, ...]:
    rules = protocol.study_rules
    if rules is None:
        return ()
    by_seq = _group_by_seq(acquisitions, groups)
    seg_bounds_mm = seg_bounds_mm or {}
    results = []
    for rule in rules.min_valid_groups:
        results.append(_eval_min_valid(rule, protocol, by_seq, finalized))
    for rule in rules.fallback_groups:
        results.append(_eval_fallback(rule, by_seq, finalized))
    for rule in rules.geometry_alignments:
        results.append(_eval_alignment(rule, by_seq))
    for rule in rules.coverage_checks:
        seg_bounds = seg_bounds_mm.get(rule.segmentation_sequence_id)
        results.append(_eval_coverage(rule, by_seq, seg_bounds))
    return tuple(results)
