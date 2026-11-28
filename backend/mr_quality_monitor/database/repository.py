"""Transactional, append-only analysis history over durably received DICOM objects."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

from pydicom.uid import MRImageStorage
from sqlalchemy import exists, func, or_, select

from ..analysis.image_quality.base import ArtifactFinding
from ..analysis.metadata_normalizer import MetadataRecord
from ..analysis.series_aggregator import AcquisitionGroup
from ..analysis.validation_engine import CompletionEvidence, evaluate_study
from ..dicom.metadata_parser import IDENTIFIERS
from ..protocols.manager import ProtocolSnapshot
from .models import (
    AcquisitionMember,
    AnalysisBranch,
    AnalysisJob,
    ArtifactFindingRow,
    DicomInstance,
    HistoryEvent,
    LogicalAcquisition,
    MetadataSnapshot,
    Patient,
    ProtocolVersion,
    SequenceDefinitionRow,
    Series,
    Study,
    ValidationIssue,
    ValidationResult,
    ValidationRun,
)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def checked_content(content: str, sha256: str) -> dict:
    if digest(content) != sha256:
        raise ValueError("Stored history content checksum mismatch")
    return json.loads(content)


def label(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("A nonempty audit identifier of at most 256 characters is required")
    return value.strip()


class HistoryRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @contextmanager
    def _write(self):
        # Serialize decision + insert, not just individual INSERTs, across processes.
        with self.sessions() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _protocol(session, snapshot: ProtocolSnapshot) -> None:
        protocol = snapshot.protocol
        old = session.scalar(
            select(ProtocolVersion).where(
                ProtocolVersion.protocol_id == protocol.protocol_id,
                ProtocolVersion.version == protocol.version,
            )
        )
        if old is not None:
            if old.sha256 != snapshot.sha256 or old.content != snapshot.content:
                raise ValueError("Existing protocol version has different content")
            return
        session.add(
            ProtocolVersion(
                sha256=snapshot.sha256,
                protocol_id=protocol.protocol_id,
                version=protocol.version,
                content=snapshot.content,
            )
        )
        session.flush()
        for sequence in protocol.sequences:
            session.add(
                SequenceDefinitionRow(
                    id=uuid4().hex,
                    protocol_sha256=snapshot.sha256,
                    sequence_id=sequence.sequence_id,
                    position=sequence.order,
                    content=canonical(sequence.model_dump(mode="json")),
                )
            )
        session.flush()

    def begin_study(
        self,
        study_uid: str,
        snapshot: ProtocolSnapshot,
        *,
        actor: str,
        require_intake: bool = False,
        intake_instance_id: str | None = None,
    ) -> str:
        """Pin the original protocol permanently and return the main branch ID.

        Patient identity is deliberately local to this study. No patient ID/name
        is indexed or merged across studies without an approved identity policy.
        """
        actor = label(actor)
        if (
            not study_uid
            or len(study_uid) > 64
            or not all(
                part.isascii() and part.isdigit() and (part == "0" or not part.startswith("0"))
                for part in study_uid.split(".")
            )
        ):
            raise ValueError("Invalid StudyInstanceUID")
        with self._write() as session:
            if require_intake or intake_instance_id:
                intake = select(DicomInstance.id).where(DicomInstance.study_uid == study_uid)
                if intake_instance_id:
                    intake = intake.where(DicomInstance.id == intake_instance_id)
                if not session.scalar(intake.limit(1)):
                    raise ValueError("Study intake is no longer available")
            old = session.get(Study, study_uid)
            if old is not None:
                if old.protocol_sha256 != snapshot.sha256:
                    raise ValueError(
                        "Study protocol is pinned; create an explicit reassessment branch"
                    )
                branch = session.scalar(
                    select(AnalysisBranch).where(
                        AnalysisBranch.study_uid == study_uid, AnalysisBranch.label == "main"
                    )
                )
                return branch.id
            self._protocol(session, snapshot)
            patient = Patient(id=uuid4().hex, token=uuid4().hex)
            session.add(patient)
            session.flush()
            session.add(
                Study(study_uid=study_uid, patient_id=patient.id, protocol_sha256=snapshot.sha256)
            )
            session.flush()
            branch = AnalysisBranch(
                id=uuid4().hex,
                study_uid=study_uid,
                protocol_sha256=snapshot.sha256,
                label="main",
                actor=actor,
                reason="Initial protocol binding",
            )
            session.add(branch)
            session.add(
                HistoryEvent(
                    study_uid=study_uid,
                    code="study_protocol_pinned",
                    actor=actor,
                    reference_id=branch.id,
                )
            )
            return branch.id

    def create_branch(
        self,
        study_uid: str,
        snapshot: ProtocolSnapshot,
        *,
        branch_label: str,
        actor: str,
        reason: str,
    ) -> str:
        branch_label, actor, reason = label(branch_label), label(actor), label(reason)
        if branch_label == "main":
            raise ValueError("The main branch is reserved for the original protocol")
        with self._write() as session:
            if session.get(Study, study_uid) is None:
                raise ValueError("Study must be begun before reassessment")
            self._protocol(session, snapshot)
            old = session.scalar(
                select(AnalysisBranch).where(
                    AnalysisBranch.study_uid == study_uid, AnalysisBranch.label == branch_label
                )
            )
            if old is not None:
                if (old.protocol_sha256, old.actor, old.reason) != (snapshot.sha256, actor, reason):
                    raise ValueError("Existing reassessment branch cannot change")
                return old.id
            branch = AnalysisBranch(
                id=uuid4().hex,
                study_uid=study_uid,
                protocol_sha256=snapshot.sha256,
                label=branch_label,
                actor=actor,
                reason=reason,
            )
            session.add(branch)
            session.add(
                HistoryEvent(
                    study_uid=study_uid,
                    code="reassessment_branch_created",
                    actor=actor,
                    reference_id=branch.id,
                )
            )
            return branch.id

    def get_protocol(self, study_uid: str, branch_id: str | None = None) -> ProtocolSnapshot:
        with self.sessions() as session:
            branch = self._branch(session, study_uid, branch_id)
            row = session.get(ProtocolVersion, branch.protocol_sha256)
            return ProtocolSnapshot(row.content, row.sha256)

    @staticmethod
    def _branch(session, study_uid: str, branch_id: str | None):
        branch = (
            session.get(AnalysisBranch, branch_id)
            if branch_id
            else session.scalar(
                select(AnalysisBranch).where(
                    AnalysisBranch.study_uid == study_uid, AnalysisBranch.label == "main"
                )
            )
        )
        if branch is None or branch.study_uid != study_uid:
            raise ValueError("Unknown study/analysis branch")
        return branch

    @staticmethod
    def _metadata(session, study: Study, record: MetadataRecord, actor: str) -> str:
        if record.known("StudyInstanceUID") != study.study_uid:
            raise ValueError("Metadata belongs to another study")
        if any(
            field in record.fields
            and (record.fields[field].value is not None or record.fields[field].sources)
            for field in IDENTIFIERS
        ):
            raise ValueError("Patient identifiers must be suppressed before history persistence")
        instance = session.scalar(
            select(DicomInstance).where(DicomInstance.sop_uid == record.known("SOPInstanceUID"))
        )
        if instance is None or instance.integrity_state != "stored":
            raise ValueError("Metadata requires a durably received, intact DICOM instance")
        if (instance.study_uid, instance.series_uid, instance.sop_class_uid) != (
            study.study_uid,
            record.known("SeriesInstanceUID"),
            record.known("SOPClassUID"),
        ):
            raise ValueError("Metadata identity does not match received instance")
        series = session.get(Series, instance.series_uid)
        if series is not None and series.study_uid != study.study_uid:
            raise ValueError("Series UID is associated with another study")
        if series is None:
            session.add(
                Series(
                    series_uid=instance.series_uid,
                    study_uid=study.study_uid,
                    number=record.known("SeriesNumber"),
                    description=record.known("SeriesDescription"),
                )
            )
            session.flush()
        content = canonical(record.to_dict())
        sha = digest(content)
        if session.get(MetadataSnapshot, sha) is None:
            session.add(
                MetadataSnapshot(
                    sha256=sha,
                    instance_id=instance.id,
                    frame_index=record.frame_index if record.frame_index is not None else -1,
                    parser_version=record.parser_version,
                    vendor_parser=record.vendor_parser,
                    content=content,
                )
            )
            study.revision += 1
            if study.state == "closed":
                study.state = "active"
                session.add(
                    HistoryEvent(
                        study_uid=study.study_uid,
                        code="study_reopened_on_new_metadata",
                        actor=actor,
                        reference_id=sha,
                    )
                )
            session.flush()
        return sha

    def _group(self, session, study: Study, group: AcquisitionGroup, actor: str) -> str:
        series = {record.known("SeriesInstanceUID") for record in group.members}
        if len(series) != 1 or None in series:
            raise ValueError("An acquisition must belong to one known physical series")
        positions = group.projected_positions_mm
        pairs = []
        for index, record in enumerate(group.members):
            sha = self._metadata(session, study, record, actor)
            position = positions[index] if len(positions) == len(group.members) else None
            pairs.append({"metadata_sha256": sha, "position_mm": position})
        pairs.sort(
            key=lambda item: (
                item["position_mm"] is None,
                item["position_mm"] or 0,
                item["metadata_sha256"],
            )
        )
        if len({item["metadata_sha256"] for item in pairs}) != len(pairs):
            raise ValueError("Duplicate acquisition member")
        content = canonical({"key": group.key, "members": pairs, "issues": sorted(group.issues)})
        sha = digest(content)
        if session.get(LogicalAcquisition, sha) is None:
            session.add(
                LogicalAcquisition(
                    sha256=sha,
                    group_key=group.key,
                    study_uid=study.study_uid,
                    series_uid=next(iter(series)),
                    content=content,
                )
            )
            session.flush()
            for item in pairs:
                session.add(
                    AcquisitionMember(
                        acquisition_sha256=sha, metadata_sha256=item["metadata_sha256"]
                    )
                )
            session.flush()
        return sha

    def save_evaluation(
        self,
        study_uid: str,
        groups: tuple[AcquisitionGroup, ...],
        *,
        request_id: str,
        actor: str,
        branch_id: str | None = None,
        completions: dict[str, CompletionEvidence] | None = None,
        finalization: CompletionEvidence | None = None,
        evaluated_at: datetime | None = None,
        intake_job_ids: tuple[int, ...] = (),
        image_findings: dict[str, tuple[ArtifactFinding, ...]] | None = None,
        expected_run_id: str | None = None,
    ) -> str:
        """Evaluate outside the writer transaction, then atomically persist every input/result.

        Retrying the same request and input returns its original run. Reusing a
        request for a different input is an error. A new request appends history.
        """
        request_id, actor = label(request_id), label(actor)
        snapshot = self.get_protocol(study_uid, branch_id)
        evaluation = evaluate_study(
            groups,
            snapshot,
            completions=completions,
            finalization=finalization,
            image_findings=image_findings,
            evaluated_at=evaluated_at,
        )
        payload = canonical(evaluation.to_dict())
        if finalization is not None or intake_job_ids:
            submitted = {
                record.known("SOPInstanceUID") for group in groups for record in group.members
            }
        else:
            submitted = None
        # Engine input hashes include metadata and completion evidence but exclude clock time.
        signature = digest(
            canonical(
                {
                    "protocol": snapshot.sha256,
                    "actor": actor,
                    "engine": evaluation.engine_version,
                    "acquisitions": sorted(item.input_sha256 for item in evaluation.acquisitions),
                    "finalization": finalization.model_dump() if finalization else None,
                }
            )
        )
        with self._write() as session:
            branch = self._branch(session, study_uid, branch_id)
            if branch.protocol_sha256 != snapshot.sha256:
                raise ValueError("Pinned protocol changed unexpectedly")
            old = session.scalar(
                select(ValidationRun).where(
                    ValidationRun.study_uid == study_uid, ValidationRun.request_id == request_id
                )
            )
            if old is not None:
                if old.input_sha256 != signature or old.branch_id != branch.id:
                    raise ValueError("Request ID was already used for another evaluation input")
                return old.id
            study = session.get(Study, study_uid)
            if expected_run_id is not None:
                latest_id = session.scalar(
                    select(ValidationRun.id)
                    .where(ValidationRun.branch_id == branch.id)
                    .order_by(ValidationRun.sequence_number.desc())
                    .limit(1)
                )
                unfinished = session.scalar(
                    select(DicomInstance.id)
                    .outerjoin(AnalysisJob)
                    .where(
                        DicomInstance.study_uid == study_uid,
                        DicomInstance.sop_class_uid == MRImageStorage,
                        or_(
                            DicomInstance.integrity_state != "stored",
                            AnalysisJob.id.is_(None),
                            AnalysisJob.state != "done",
                        ),
                    )
                    .limit(1)
                )
                if latest_id != expected_run_id or unfinished or study.state == "closed":
                    raise ValueError("Study is not ready for finalization")
            if intake_job_ids:
                jobs = list(
                    session.execute(
                        select(AnalysisJob, DicomInstance.sop_uid)
                        .join(DicomInstance)
                        .where(
                            AnalysisJob.id.in_(intake_job_ids),
                            AnalysisJob.state == "pending",
                            DicomInstance.study_uid == study_uid,
                        )
                    )
                )
                if len(jobs) != len(intake_job_ids) or any(
                    sop_uid not in submitted for _, sop_uid in jobs
                ):
                    raise ValueError("Intake jobs must be pending and represented in the study")
                for job, _ in jobs:
                    job.state = "done"
            if finalization is not None:
                received = set(
                    session.scalars(
                        select(DicomInstance.sop_uid).where(
                            DicomInstance.study_uid == study_uid,
                            DicomInstance.sop_class_uid == MRImageStorage,
                        )
                    )
                )
                if received - submitted:
                    raise ValueError("Finalization requires metadata for every received instance")
            acquisitions = {
                group.key: self._group(session, study, group, actor) for group in groups
            }
            previous = session.scalar(
                select(ValidationRun)
                .where(ValidationRun.branch_id == branch.id)
                .order_by(ValidationRun.sequence_number.desc())
            )
            run = ValidationRun(
                id=uuid4().hex,
                study_uid=study_uid,
                branch_id=branch.id,
                request_id=request_id,
                input_sha256=signature,
                protocol_sha256=snapshot.sha256,
                engine_version=evaluation.engine_version,
                study_revision=study.revision,
                sequence_number=previous.sequence_number + 1 if previous else 1,
                status=evaluation.status,
                evaluated_at=evaluation.evaluated_at,
                actor=actor,
                supersedes_id=previous.id if previous else None,
                content=payload,
                content_sha256=digest(payload),
            )
            session.add(run)
            session.flush()
            definitions = {
                row.sequence_id: row.id
                for row in session.scalars(
                    select(SequenceDefinitionRow).where(
                        SequenceDefinitionRow.protocol_sha256 == snapshot.sha256
                    )
                )
            }
            for result in evaluation.acquisitions:
                row = ValidationResult(
                    id=uuid4().hex,
                    run_id=run.id,
                    acquisition_sha256=acquisitions[result.group_key],
                    sequence_definition_id=definitions.get(result.sequence_id),
                    scope="acquisition",
                    status=result.status,
                    content=canonical(result.to_dict()),
                )
                session.add(row)
                session.flush()
                for check in result.checks:
                    if check.status != "GREEN":
                        session.add(
                            ValidationIssue(
                                result_id=row.id,
                                rule_id=check.rule_id,
                                status=check.status,
                                reason=check.reason,
                                content=canonical(asdict(check)),
                            )
                        )
                if result.status != "GREEN":
                    session.add(
                        ValidationIssue(
                            result_id=row.id,
                            rule_id="acquisition_state",
                            status=result.status,
                            reason=";".join(result.reasons),
                            content=canonical({"reasons": result.reasons}),
                        )
                    )
                # Persist artifact findings for this acquisition
                for finding in (image_findings or {}).get(result.group_key, ()):
                    if not any(
                        check.rule_id == f"image:{finding.detector_id}" for check in result.checks
                    ):
                        continue
                    import json as _json  # noqa: PLC0415
                    session.add(
                        ArtifactFindingRow(
                            result_id=row.id,
                            detector_id=finding.detector_id,
                            artifact_type=finding.artifact_type,
                            status=finding.status,
                            score=finding.score,
                            threshold_json=_json.dumps(finding.threshold, allow_nan=False),
                            affected_slices_json=_json.dumps(list(finding.affected_slices)),
                        )
                    )
            for presence in evaluation.sequences:
                row = ValidationResult(
                    id=uuid4().hex,
                    run_id=run.id,
                    acquisition_sha256=None,
                    sequence_definition_id=definitions[presence.sequence_id],
                    scope="sequence_presence",
                    status=presence.status,
                    content=canonical(asdict(presence)),
                )
                session.add(row)
                session.flush()
                if presence.status != "GREEN":
                    session.add(
                        ValidationIssue(
                            result_id=row.id,
                            rule_id="sequence_presence",
                            status=presence.status,
                            reason=presence.reason,
                            content=canonical(asdict(presence)),
                        )
                    )
            if branch.label == "main" and evaluation.finalized:
                study.state = "closed"
            session.add(
                HistoryEvent(
                    study_uid=study_uid,
                    code="validation_run_saved",
                    actor=actor,
                    reference_id=run.id,
                )
            )
            return run.id

    def load_evaluation(self, run_id: str) -> dict:
        with self.sessions() as session:
            row = session.get(ValidationRun, run_id)
            if row is None:
                raise ValueError("Unknown validation run")
            return checked_content(row.content, row.content_sha256)

    def load_group(self, acquisition_sha256: str) -> AcquisitionGroup:
        with self.sessions() as session:
            row = session.get(LogicalAcquisition, acquisition_sha256)
            if row is None:
                raise ValueError("Unknown acquisition snapshot")
            data = checked_content(row.content, row.sha256)
            members = []
            for item in data["members"]:
                snapshot = session.get(MetadataSnapshot, item["metadata_sha256"])
                members.append(
                    MetadataRecord.from_dict(checked_content(snapshot.content, snapshot.sha256))
                )
            return AcquisitionGroup(
                data["key"],
                tuple(members),
                tuple(
                    item["position_mm"]
                    for item in data["members"]
                    if item["position_mm"] is not None
                ),
                tuple(data["issues"]),
            )

    def finalize_current(self, study_uid: str, branch_id: str, *, actor: str) -> str:
        """Close the latest fully processed clinical snapshot with operator evidence."""
        with self.sessions() as session:
            branch = self._branch(session, study_uid, branch_id)
            protocol = self.get_protocol(study_uid, branch_id).protocol
            if branch.label != "main" or not (
                protocol.study_identifiers or protocol.prostate_checks or protocol.study_rules
            ):
                raise ValueError("Only the main clinical evaluation can be closed here")
            run = session.scalar(
                select(ValidationRun)
                .where(ValidationRun.branch_id == branch_id)
                .order_by(ValidationRun.sequence_number.desc())
                .limit(1)
            )
            if run is None:
                raise ValueError("No evaluation is available")
            payload = checked_content(run.content, run.content_sha256)
            image_findings = {
                result["group_key"]: tuple(
                    ArtifactFinding(
                        detector_id=check["rule_id"].removeprefix("image:"),
                        artifact_type=check["field"],
                        status=check["status"],
                        reason=check["reason"],
                        score=check["actual"],
                        threshold=check["expected"],
                        affected_slices=tuple(int(path[6:-1]) for path in check["paths"]),
                        action=check["action"],
                        per_image=tuple(
                            ArtifactFinding(
                                detector_id=detail["rule_id"].removeprefix("image:"),
                                artifact_type=detail["field"], status=detail["status"],
                                reason=detail["reason"], score=detail["actual"],
                                threshold=detail["expected"], action=detail["action"],
                                sop_uid=detail["sop_uid"],
                            )
                            for detail in check.get("per_image", ())
                        ),
                    )
                    for check in result["checks"]
                    if check["rule_id"].startswith("image:")
                )
                for result in payload["acquisitions"]
            }
            acquisitions = tuple(
                session.scalars(
                    select(ValidationResult.acquisition_sha256)
                    .where(
                        ValidationResult.run_id == run.id, ValidationResult.scope == "acquisition"
                    )
                    .distinct()
                )
            )
        return self.save_evaluation(
            study_uid,
            tuple(self.load_group(sha) for sha in acquisitions),
            request_id=uuid4().hex,
            actor=actor,
            branch_id=branch_id,
            finalization=CompletionEvidence(source="operator", reference=run.id),
            expected_run_id=run.id,
            image_findings=image_findings,
        )

    def history(self, study_uid: str, branch_id: str | None = None) -> list[dict]:
        with self.sessions() as session:
            branch = self._branch(session, study_uid, branch_id)
            rows = session.scalars(
                select(ValidationRun)
                .where(ValidationRun.branch_id == branch.id)
                .order_by(ValidationRun.sequence_number)
            )
            return [
                {
                    "run_id": row.id,
                    "status": row.status,
                    "evaluated_at": row.evaluated_at,
                    "protocol_sha256": row.protocol_sha256,
                    "supersedes_id": row.supersedes_id,
                    "sequence_number": row.sequence_number,
                }
                for row in rows
            ]

    def study_summary(self, study_uid: str, branch_id: str | None = None) -> dict:
        with self.sessions() as session:
            branch = self._branch(session, study_uid, branch_id)
            study = session.get(Study, study_uid)
            latest = session.scalar(
                select(ValidationRun)
                .where(ValidationRun.branch_id == branch.id)
                .order_by(ValidationRun.sequence_number.desc())
            )
            unindexed = session.scalar(
                select(func.count())
                .select_from(DicomInstance)
                .where(
                    DicomInstance.study_uid == study_uid,
                    DicomInstance.sop_class_uid == MRImageStorage,
                    or_(
                        DicomInstance.integrity_state != "stored",
                        ~exists().where(MetadataSnapshot.instance_id == DicomInstance.id),
                        exists().where(
                            AnalysisJob.instance_id == DicomInstance.id,
                            AnalysisJob.state == "failed",
                        ),
                    ),
                )
            )
            stale = latest is None or latest.study_revision != study.revision or bool(unindexed)
            return {
                "state": "active" if unindexed else study.state,
                "recorded_state": study.state,
                "revision": study.revision,
                "unindexed_instances": unindexed,
                "latest_run_id": latest.id if latest else None,
                "stale": stale,
                "recorded_status": latest.status if latest else None,
                "effective_status": (
                    "RED"
                    if latest is not None and latest.status == "RED"
                    else "GRAY"
                    if stale
                    else latest.status
                ),
                "protocol_sha256": branch.protocol_sha256,
            }
