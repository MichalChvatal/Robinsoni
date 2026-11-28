"""Reproducible DEMO validation, separate from storage, UI and image analysis."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ..protocols.manager import ProtocolSnapshot
from ..protocols.models import FrozenModel
from .image_quality import ArtifactFinding
from .metadata_normalizer import Value
from .prostate_geometry import alignment
from .sequence_identifier import Identification, identify
from .series_aggregator import AcquisitionGroup
from .study_rules import StudyCheckResult, evaluate_study_rules
from .validation_rules import matches

Status = Literal["GREEN", "ORANGE", "RED", "GRAY"]
ENGINE_VERSION = "validation/1"


class CompletionEvidence(FrozenModel):
    """Explicit external/manual completion supplied by the future orchestration layer."""

    source: str = Field(min_length=1)
    reference: str = Field(min_length=1)


def aggregate(statuses: tuple[Status, ...]) -> Status:
    for status in ("RED", "GRAY", "ORANGE", "GREEN"):
        if status in statuses:
            return status
    return "GREEN"


def severity_status(severity: str) -> Status:
    return {"critical": "RED", "warning": "ORANGE", "unknown": "GRAY"}[severity]


@dataclass(frozen=True)
class CheckResult:
    rule_id: str
    field: str | None
    status: Status
    reason: str
    actual: Value
    expected: dict
    unit: str | None = None
    sop_uid: str | None = None
    frame_index: int | None = None
    paths: tuple[str, ...] = ()
    action: str = ""
    per_image: tuple["CheckResult", ...] = ()


@dataclass(frozen=True)
class Evaluation:
    group_key: str
    sequence_id: str | None
    status: Status
    metadata_status: Status
    completeness_status: Status
    image_status: Status
    image_required: bool
    reasons: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    identification: Identification
    protocol_id: str
    protocol_version: str
    protocol_sha256: str
    input_sha256: str
    evaluated_at: str
    engine_version: str = ENGINE_VERSION
    mode: str = "DEMO"
    clinical_quality_evaluated: bool = False
    completion_details: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def evaluation_time(value: datetime | None) -> str:
    value = value or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Evaluation time requires a timezone")
    return value.astimezone(UTC).isoformat()


def evaluate(
    group: AcquisitionGroup,
    snapshot: ProtocolSnapshot,
    *,
    completion: CompletionEvidence | None = None,
    image_findings: tuple[ArtifactFinding, ...] | None = None,
    evaluated_at: datetime | None = None,
) -> Evaluation:
    """Validate all members; never treat one good image as representative of the series.

    group.complete is intentionally not a trusted signal. When image_findings is not
    supplied, image assessment stays GRAY and only metadata-only DEMO may ignore it.
    """
    if not group.members:
        raise ValueError("Cannot evaluate an empty acquisition")
    protocol = snapshot.protocol
    identification = identify(group, protocol)
    content = {
        "group_key": group.key,
        "members": sorted(
            (record.to_dict() for record in group.members),
            key=lambda item: json.dumps(item, sort_keys=True, allow_nan=False),
        ),
        "geometry_issues": sorted(group.issues),
        "completion": completion.model_dump() if completion else None,
        "image_findings": [asdict(item) for item in (image_findings or ())],
    }
    input_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    checks = []
    completeness: Status = "GRAY"
    image_status: Status = "GRAY"
    image_required = True
    clinical_quality_evaluated = False
    completion_details = {
        "actual_members": len(group.members),
        "policy": None,
        "evidence": completion.model_dump() if completion else None,
    }
    reasons = []
    if identification.state != "identified":
        metadata: Status = "GRAY"
        reasons.append(f"sequence_{identification.state}")
    else:
        definition = next(
            item for item in protocol.sequences if item.sequence_id == identification.sequence_id
        )
        image_required = definition.image_required
        completion_details["policy"] = definition.completion.model_dump(mode="json")
        for record in group.members:
            for rule in definition.parameters:
                value = record.fields.get(rule.field)
                state = value.state if value else "missing"
                status: Status = "GREEN"
                reason = "within_requirement"
                if state == "missing":
                    if not rule.required:
                        reason = "optional_metadata_absent"
                    else:
                        status = severity_status(rule.on_missing)
                        reason = "required_metadata_absent"
                elif state != "known":
                    status, reason = "GRAY", f"metadata_{state}"
                elif value.unit != rule.unit:
                    status, reason = "GRAY", "metadata_unit_mismatch"
                else:
                    match = matches(value.value, rule)
                    if match == "warning":
                        status, reason = "ORANGE", "within_warning_band"
                    elif match == "fail":
                        status, reason = severity_status(rule.severity), "outside_requirement"
                checks.append(
                    CheckResult(
                        rule.rule_id,
                        rule.field,
                        status,
                        reason,
                        value.value if value else None,
                        rule.model_dump(mode="json"),
                        rule.unit,
                        record.known("SOPInstanceUID"),
                        record.frame_index,
                        tuple(source.path for source in value.sources) if value else (),
                        rule.action if status != "GREEN" else "",
                    )
                )
        if group.issues:
            checks.append(
                CheckResult(
                    "geometry",
                    None,
                    severity_status(definition.geometry_severity),
                    "geometry_issues",
                    group.issues,
                    {"policy": definition.geometry_severity},
                    action="Review geometry findings",
                )
            )
        metadata = aggregate(tuple(check.status for check in checks))
        if image_required and metadata not in {"GREEN", "ORANGE"}:
            reasons.append(
                "image_skipped_parameter_error" if metadata == "RED"
                else "image_waiting_for_parameters"
            )
        if image_findings is not None and image_required and metadata in {"GREEN", "ORANGE"}:
            for finding in image_findings:
                checks.append(
                    CheckResult(
                        f"image:{finding.detector_id}",
                        finding.artifact_type,
                        finding.status,
                        finding.reason,
                        finding.score,
                        finding.threshold,
                        paths=tuple(f"slice[{idx}]" for idx in finding.affected_slices),
                        action=finding.action if finding.status != "GREEN" else "",
                        per_image=tuple(
                            CheckResult(
                                f"image:{detail.detector_id}", detail.artifact_type,
                                detail.status, detail.reason, detail.score, detail.threshold,
                                sop_uid=detail.sop_uid,
                                action=detail.action if detail.status != "GREEN" else "",
                            )
                            for detail in finding.per_image
                        ),
                    )
                )
            if image_findings:
                image_status = aggregate(tuple(item.status for item in image_findings))
                clinical_quality_evaluated = protocol.mode == "CLINICAL" and any(
                    item.status != "GRAY" for item in image_findings
                )
        if definition.completion.mode == "expected_count":
            identities = {
                (record.known("SOPInstanceUID"), record.frame_index) for record in group.members
            }
            if None in {identity[0] for identity in identities} or len(identities) != len(
                group.members
            ):
                reasons.append("completion_identity_uncertain")
            elif len(group.members) == definition.completion.expected_members:
                completeness = "GREEN"
            else:
                reasons.append("completion_count_not_met")
        elif definition.completion.mode == "geometric_stack":
            blocking_geo = {
                "incomplete_geometry",
                "invalid_geometry",
                "repeated_slice_position",
                "irregular_slice_spacing",
                "declared_spacing_mismatch",
            }
            if len(group.members) < (definition.completion.min_members or 1):
                reasons.append("completion_count_not_met")
            elif any(issue in blocking_geo for issue in group.issues):
                reasons.append("completion_geometry_incomplete")
            else:
                completeness = "GREEN"
        elif completion is not None:
            completeness = "GREEN"
        else:
            reasons.append("completion_not_confirmed")
        reasons.extend(check.reason for check in checks if check.status != "GREEN")
    if not image_required:
        reasons.append(
            "image_not_requested_in_demo" if protocol.mode == "DEMO" else "image_not_required"
        )
    elif image_status == "GRAY":
        reasons.append("image_not_evaluated")
    overall = aggregate(
        (metadata, completeness, image_status) if image_required else (metadata, completeness)
    )
    if overall == "GREEN":
        reasons.append(
            "clinical_requirements_passed"
            if protocol.mode == "CLINICAL"
            else "demo_metadata_requirements_passed"
        )
    return Evaluation(
        group.key,
        identification.sequence_id,
        overall,
        metadata,
        completeness,
        image_status,
        image_required,
        tuple(sorted(set(reasons))),
        tuple(checks),
        identification,
        protocol.protocol_id,
        protocol.version,
        snapshot.sha256,
        input_hash,
        evaluation_time(evaluated_at),
        mode=protocol.mode,
        clinical_quality_evaluated=clinical_quality_evaluated,
        completion_details=completion_details,
    )


@dataclass(frozen=True)
class SequencePresence:
    sequence_id: str
    required: bool
    status: Status
    reason: str
    candidate_groups: tuple[str, ...]


@dataclass(frozen=True)
class StudyEvaluation:
    status: Status
    sequences: tuple[SequencePresence, ...]
    acquisitions: tuple[Evaluation, ...]
    finalized: bool
    finalization: CompletionEvidence | None
    protocol_sha256: str
    evaluated_at: str
    protocol_id: str
    protocol_version: str
    study_checks: tuple[StudyCheckResult, ...] = ()
    engine_version: str = ENGINE_VERSION
    mode: str = "DEMO"
    clinical_quality_evaluated: bool = False
    checks: tuple[CheckResult, ...] = ()

    def to_dict(self) -> dict:
        result = asdict(self)
        result["finalization"] = (
            self.finalization.model_dump(mode="json") if self.finalization else None
        )
        return result


T2_ROLES = ("T2_SAG", "T2_COR", "T2_TRA")
DWI_ROLES = ("DWI_LOW", "DWI_INTERMEDIATE", "DWI_HIGH", "DWI_ADC")


def prostate_alignment(results, groups, profile):
    group_by_key = {group.key: group for group in groups}
    reference = [item for item in results if item.sequence_id == "T2_TRA"]
    output = []
    expected = {
        "position_tolerance_mm": profile.position_tolerance_mm,
        "orientation_tolerance_degrees": profile.orientation_tolerance_degrees,
    }
    configured = all(value is not None for value in expected.values())
    for item in results:
        if item.sequence_id not in DWI_ROLES:
            output.append(item)
            continue
        reason, metrics = "alignment_axial_unavailable", None
        if len(reference) == 1:
            reason, metrics = alignment(
                group_by_key[item.group_key], group_by_key[reference[0].group_key]
            )
        status = "GRAY"
        if metrics is not None and configured:
            deviation, spacing, angle = metrics
            status = (
                "GREEN"
                if max(deviation, spacing) <= profile.position_tolerance_mm
                and angle <= profile.orientation_tolerance_degrees
                else "RED"
            )
            if status == "RED":
                reason = "alignment_outside_tolerance"
        elif configured and reason == "alignment_no_overlap":
            status = "RED"
        check = CheckResult("dwi_alignment", "DWIAlignment", status, reason, metrics, expected)
        output.append(
            replace(
                item,
                status=aggregate((item.status, status)) if configured else item.status,
                checks=(*item.checks, check),
            )
        )
    return tuple(output)


def prostate_study_checks(presence, results, groups, profile, finalized):
    viable_planes = {
        item.sequence_id
        for item in results
        if item.sequence_id in T2_ROLES and item.status == "GREEN"
    }
    uncertain_planes = {
        item.sequence_id
        for item in results
        if item.sequence_id in T2_ROLES and item.status == "GRAY"
    }
    count = len(viable_planes)
    potential_count = len(viable_planes | uncertain_planes)
    t2_status = "GREEN" if count >= 2 else "GRAY"
    if finalized and potential_count < 2:
        t2_status = "RED"
    axial_received = any(item.sequence_id == "T2_TRA" for item in results)
    dwi = [item for item in presence if item.sequence_id in DWI_ROLES]
    dwi_status = aggregate(tuple(item.status for item in dwi))
    resolve_required = any(item.status == "RED" for item in dwi)
    checks = (
        CheckResult(
            "t2_planes",
            "T2PlaneCount",
            t2_status,
            "t2_plane_count",
            count,
            {"minimum": 2},
        ),
        CheckResult(
            "axial_t2",
            "T2Axial",
            "GREEN" if axial_received else ("ORANGE" if finalized else "GRAY"),
            "t2_axial_present" if axial_received else "t2_axial_missing",
            None,
            {},
        ),
        CheckResult(
            "dwi_set",
            "DWISet",
            dwi_status,
            "dwi_resolve_required" if resolve_required else "dwi_set_status",
            None,
            {},
        ),
    )
    if not resolve_required:
        return checks
    identities = {(item.protocol_name, item.sequence_name) for item in profile.resolve_identities}
    received = any(
        all(
            (record.known("ProtocolName"), record.known("SequenceName")) in identities
            for record in group.members
        )
        for group in groups
    )
    status, reason = "GRAY", "resolve_identity_unknown"
    if received:
        reason = "resolve_present_unverified"
    elif identities:
        status = "RED" if finalized else "GRAY"
        reason = "resolve_missing" if finalized else "resolve_pending"
    return (*checks, CheckResult("resolve_presence", "ResolvePresence", status, reason, None, {}))


def evaluate_study(
    groups: tuple[AcquisitionGroup, ...],
    snapshot: ProtocolSnapshot,
    *,
    completions: dict[str, CompletionEvidence] | None = None,
    finalization: CompletionEvidence | None = None,
    image_findings: dict[str, tuple[ArtifactFinding, ...]] | None = None,
    seg_bounds_mm: dict[str, tuple[float, float]] | None = None,
    evaluated_at: datetime | None = None,
) -> StudyEvaluation:
    """Check presence of required sequences; never silently select the best repeat.

    All received repeats remain in the overall result. Missing required sequences
    become errors only at explicit study finalization. Uncertain matches stay GRAY.
    Protocol order is display order, not network/acquisition chronology enforcement.
    """
    if len({group.key for group in groups}) != len(groups):
        raise ValueError("Duplicate acquisition group keys")
    study_uids = {record.known("StudyInstanceUID") for group in groups for record in group.members}
    if groups and (len(study_uids) != 1 or None in study_uids):
        raise ValueError("Study evaluation requires exactly one known StudyInstanceUID")
    identities = [
        (record.known("SOPInstanceUID"), record.frame_index)
        for group in groups
        for record in group.members
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("An instance/frame cannot belong to multiple input groups")
    completions = completions or {}
    if set(completions) - {group.key for group in groups}:
        raise ValueError("Completion evidence references an unknown acquisition")
    image_findings = image_findings or {}
    time = evaluation_time(evaluated_at)
    timestamp = datetime.fromisoformat(time)
    profile = snapshot.protocol.prostate_checks
    ordered_groups = sorted(groups, key=lambda item: item.key)
    results = tuple(
        evaluate(
            group,
            snapshot,
            completion=completions.get(group.key) or (finalization if profile else None),
            evaluated_at=timestamp,
        )
        for group in ordered_groups
    )
    blocked = {}
    for result in results:
        if result.sequence_id and result.image_required and result.metadata_status not in {
            "GREEN", "ORANGE",
        }:
            reason = (
                "image_skipped_parameter_error" if result.metadata_status == "RED"
                else "image_waiting_for_parameters"
            )
            if blocked.get(result.sequence_id) != "image_skipped_parameter_error":
                blocked[result.sequence_id] = reason
    evaluated = []
    for group, result in zip(ordered_groups, results, strict=True):
        if result.sequence_id in blocked:
            result = replace(
                result, reasons=tuple(sorted(set((*result.reasons, blocked[result.sequence_id])))),
            )
        elif group.key in image_findings:
            result = evaluate(
                group, snapshot,
                completion=completions.get(group.key) or (finalization if profile else None),
                image_findings=image_findings[group.key], evaluated_at=timestamp,
            )
        evaluated.append(result)
    results = tuple(evaluated)
    if profile:
        results = prostate_alignment(results, groups, profile)
    study_checks = evaluate_study_rules(
        snapshot.protocol,
        results,
        groups,
        finalized=finalization is not None,
        seg_bounds_mm=seg_bounds_mm,
    )
    fallback_covered = {
        sid
        for check in study_checks
        if check.kind == "fallback_group" and check.status == "ORANGE"
        for sid in check.details.get("primary_missing", ()) + check.details.get("primary_bad", ())
    }
    presence = []
    for sequence in sorted(snapshot.protocol.sequences, key=lambda item: item.order):
        matched = tuple(item for item in results if item.sequence_id == sequence.sequence_id)
        uncertain = tuple(
            item
            for item in results
            if item.sequence_id is None
            and any(
                candidate.sequence_id == sequence.sequence_id and candidate.decision != "mismatch"
                for candidate in item.identification.candidates
            )
        )
        if matched:
            status = aggregate(tuple(item.status for item in matched))
            reason = "received_all_repeats_retained"
            keys = tuple(item.group_key for item in matched)
        elif uncertain:
            status, reason = "GRAY", "identification_uncertain"
            keys = tuple(item.group_key for item in uncertain)
        elif not sequence.required:
            status, reason, keys = "GRAY", "optional_sequence_absent", ()
        elif sequence.sequence_id in fallback_covered:
            status, reason, keys = "ORANGE", "fallback_sequence_used", ()
        elif finalization is None:
            status, reason, keys = "GRAY", "required_sequence_pending", ()
        else:
            status = severity_status(sequence.missing_severity)
            reason, keys = "required_sequence_absent_at_finalization", ()
        presence.append(
            SequencePresence(sequence.sequence_id, sequence.required, status, reason, keys)
        )
    checks = (
        prostate_study_checks(presence, results, groups, profile, finalization is not None)
        if profile
        else ()
    )
    overall = aggregate(
        tuple(item.status for item in results)
        + tuple(item.status for item in presence if item.required)
        + tuple(check.status for check in study_checks)
        + (("GRAY",) if finalization is None else ())
    )
    if profile:
        overall = aggregate(
            tuple(item.status for item in checks) + (("GRAY",) if finalization is None else ())
        )
    return StudyEvaluation(
        overall,
        tuple(presence),
        results,
        finalization is not None,
        finalization,
        snapshot.sha256,
        time,
        protocol_id=snapshot.protocol.protocol_id,
        protocol_version=snapshot.protocol.version,
        checks=checks,
        study_checks=study_checks,
        mode=snapshot.protocol.mode,
        clinical_quality_evaluated=any(item.clinical_quality_evaluated for item in results),
    )
