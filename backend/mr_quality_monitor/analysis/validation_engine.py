"""Reproducible DEMO validation, separate from storage, UI and image analysis."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ..protocols.manager import ProtocolSnapshot
from ..protocols.models import FrozenModel
from .metadata_normalizer import Value
from .sequence_identifier import Identification, identify
from .series_aggregator import AcquisitionGroup
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
    evaluated_at: datetime | None = None,
) -> Evaluation:
    """Validate all members; never treat one good image as representative of the series.

    group.complete is intentionally not a trusted signal. Image assessment is not
    implemented and stays GRAY; only an explicit metadata-only DEMO may ignore it.
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
    }
    input_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    checks = []
    completeness: Status = "GRAY"
    image_required = True
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
        elif completion is not None:
            completeness = "GREEN"
        else:
            reasons.append("completion_not_confirmed")
        reasons.extend(check.reason for check in checks if check.status != "GREEN")
    reasons.append("image_not_evaluated" if image_required else "image_not_requested_in_demo")
    overall = aggregate(
        (metadata, completeness, "GRAY") if image_required else (metadata, completeness)
    )
    if overall == "GREEN":
        reasons.append("demo_metadata_requirements_passed")
    return Evaluation(
        group.key,
        identification.sequence_id,
        overall,
        metadata,
        completeness,
        "GRAY",
        image_required,
        tuple(sorted(set(reasons))),
        tuple(checks),
        identification,
        protocol.protocol_id,
        protocol.version,
        snapshot.sha256,
        input_hash,
        evaluation_time(evaluated_at),
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
    engine_version: str = ENGINE_VERSION
    mode: str = "DEMO"
    clinical_quality_evaluated: bool = False

    def to_dict(self) -> dict:
        result = asdict(self)
        result["finalization"] = (
            self.finalization.model_dump(mode="json") if self.finalization else None
        )
        return result


def evaluate_study(
    groups: tuple[AcquisitionGroup, ...],
    snapshot: ProtocolSnapshot,
    *,
    completions: dict[str, CompletionEvidence] | None = None,
    finalization: CompletionEvidence | None = None,
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
    time = evaluation_time(evaluated_at)
    timestamp = datetime.fromisoformat(time)
    results = tuple(
        evaluate(group, snapshot, completion=completions.get(group.key), evaluated_at=timestamp)
        for group in sorted(groups, key=lambda item: item.key)
    )
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
        elif finalization is None:
            status, reason, keys = "GRAY", "required_sequence_pending", ()
        else:
            status = severity_status(sequence.missing_severity)
            reason, keys = "required_sequence_absent_at_finalization", ()
        presence.append(
            SequencePresence(sequence.sequence_id, sequence.required, status, reason, keys)
        )
    overall = aggregate(
        tuple(item.status for item in results)
        + tuple(item.status for item in presence if item.required)
        + (("GRAY",) if finalization is None else ())
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
    )
