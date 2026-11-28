"""Consume durable intake jobs without owning the DICOM receiver or the GUI."""

import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from uuid import uuid4

from pydicom.errors import InvalidDicomError
from pydicom.uid import MRImageStorage
from sqlalchemy import exists, func, select, update

from ..analysis.image_quality import default_registry, load_slice_pixels
from ..analysis.image_quality.base import ArtifactFinding
from ..analysis.metadata_normalizer import MetadataRecord
from ..analysis.sequence_identifier import identify, match_study_protocol
from ..analysis.series_aggregator import group_acquisitions
from ..config import ReceiverConfig
from ..database.models import AnalysisJob, DicomInstance, Study, SystemEvent
from ..database.repository import HistoryRepository
from ..dicom.metadata_parser import parse_file
from ..dicom.storage import Storage, checksum
from ..process_lock import ProcessLock
from ..protocols.manager import ProtocolSnapshot

LOGGER = logging.getLogger(__name__)
ACTOR = "analysis_worker"


class InvalidAnalysisInput(ValueError):
    """A stored object cannot be used for metadata evaluation."""


@contextmanager
def open_worker(
    root: Path,
    protocol: ProtocolSnapshot | tuple[ProtocolSnapshot, ...],
    config: ReceiverConfig | None = None,
):
    lock = ProcessLock(root, "analysis")
    lock.acquire()
    try:
        storage = Storage(root)
        try:
            b_value_tags = config.b_value_tag_order if config else ()
            yield AnalysisWorker(
                storage,
                protocol,
                b_value_tags=b_value_tags,
                single_active_study=config.single_active_study if config else False,
            )
        finally:
            storage.close()
    finally:
        lock.release()


class AnalysisWorker:
    def __init__(
        self,
        storage: Storage,
        protocol: ProtocolSnapshot | tuple[ProtocolSnapshot, ...],
        *,
        b_value_tags: tuple[str, ...] = (),
        single_active_study: bool = False,
    ):
        self.storage = storage
        self.sessions = storage.sessions
        self.history = HistoryRepository(self.sessions)
        candidates = (protocol,) if isinstance(protocol, ProtocolSnapshot) else protocol
        self.candidates = tuple(
            (snapshot, snapshot.protocol.study_identifiers) for snapshot in candidates
        )
        self.manual_protocol = candidates[0] if (
            len(candidates) == 1 and not self.candidates[0][1]
        ) else None
        self.b_value_tags = b_value_tags
        self.single_active_study = single_active_study

    def retry_failed(self) -> None:
        """Requeue rejected objects after their local storage has been repaired."""
        with self.sessions.begin() as session:
            session.execute(
                update(AnalysisJob).where(AnalysisJob.state == "failed").values(state="pending")
            )

    def run_once(self, stopped: Event, *, defer_new_study: bool = False) -> int:
        """Process one snapshot per pending study, in order of oldest outstanding job."""
        with self.sessions() as session:
            studies = list(
                session.scalars(
                    select(DicomInstance.study_uid)
                    .join(AnalysisJob)
                    .where(AnalysisJob.state == "pending")
                    .group_by(DicomInstance.study_uid)
                    .order_by(func.min(AnalysisJob.id))
                )
            )
        failures = 0
        for study_uid in studies:
            if stopped.is_set():
                break
            try:
                failures += self._process_study(study_uid, stopped, defer_new_study=defer_new_study)
            except Exception as error:
                if self.single_active_study:
                    with self.sessions() as session:
                        if not session.scalar(
                            select(exists().where(DicomInstance.study_uid == study_uid))
                        ):
                            continue
                failures += 1
                LOGGER.error("study_analysis_retry type=%s", type(error).__name__)
        return failures

    def _process_study(self, study_uid: str, stopped: Event, *, defer_new_study: bool) -> int:
        with self.sessions() as session:
            inputs = list(
                session.execute(
                    select(DicomInstance, AnalysisJob)
                    .join(AnalysisJob)
                    .where(
                        DicomInstance.study_uid == study_uid,
                        AnalysisJob.state.in_(("done", "pending")),
                    )
                    .order_by(AnalysisJob.id)
                )
            )
            study_exists = session.get(Study, study_uid) is not None
        snapshot = self.history.get_protocol(study_uid) if study_exists else self.manual_protocol
        profile = snapshot.protocol.prostate_checks if snapshot else None

        records = []
        completed = []
        failures = 0
        readable = []
        for instance, job in inputs:
            if stopped.is_set():
                return failures
            if instance.sop_class_uid != MRImageStorage:
                self._set_job_state(job.id, instance.id, "ignored")
                continue
            try:
                records.extend(self._read(instance, profile))
            except (InvalidDicomError, ValueError, TypeError, EOFError, FileNotFoundError):
                failures += self._set_job_state(job.id, instance.id, "failed")
                continue
            readable.append(instance)
            if job.state == "pending":
                completed.append(job.id)
        if not completed or stopped.is_set():
            return failures

        if snapshot is None:
            snapshot = match_study_protocol(records, self.candidates)
            if snapshot is None:
                return failures
            if (profile := snapshot.protocol.prostate_checks) is not None:
                records = [
                    record for instance in readable for record in self._read(instance, profile)
                ]
        branch_id = None
        if not study_exists or self.single_active_study:
            branch_id = self.history.begin_study(
                study_uid, snapshot, actor=ACTOR,
                intake_instance_id=readable[0].id if self.single_active_study else None,
            )
        if not study_exists:
            protocol = snapshot.protocol
            LOGGER.info(
                "study_protocol_selected protocol=%s version=%s mode=%s",
                protocol.protocol_id, protocol.version, protocol.mode,
            )
            if defer_new_study and protocol.study_identifiers:
                return failures
        groups = group_acquisitions(tuple(records))

        sop_to_id = {instance.sop_uid: instance.id for instance, _ in inputs}
        image_findings: dict[str, tuple[ArtifactFinding, ...]] = {}
        registry = default_registry()
        definitions = {
            definition.sequence_id: definition for definition in snapshot.protocol.sequences
        }
        for group in groups:
            if stopped.is_set():
                return failures
            if self.single_active_study:
                with self.sessions() as session:
                    if session.get(DicomInstance, inputs[0][0].id) is None:
                        return 0
            sequence = definitions.get(identify(group, snapshot.protocol).sequence_id)
            if sequence is None or not sequence.image_required:
                continue
            try:
                slices = tuple(
                    sl
                    for index, record in enumerate(group.members)
                    for sop_uid in [record.known("SOPInstanceUID")]
                    if sop_uid and sop_uid in sop_to_id
                    and (
                        sl := load_slice_pixels(
                            self.storage.path(sop_to_id[sop_uid]),
                            phase_direction=record.known("InPlanePhaseEncodingDirection"),
                            slice_position_mm=(
                                group.projected_positions_mm[index]
                                if len(group.projected_positions_mm) == len(group.members)
                                else None
                            ),
                        )
                    ) is not None
                )
                if slices and len(slices) == len(group.members):
                    image_findings[group.key] = registry.evaluate(
                        slices, role=sequence.role, enabled_detectors=sequence.image_checks
                    )
            except Exception as error:
                LOGGER.warning("image_analysis_skipped type=%s", type(error).__name__)

        self.history.save_evaluation(
            study_uid,
            groups,
            request_id=uuid4().hex,
            branch_id=branch_id,
            actor=ACTOR,
            intake_job_ids=tuple(completed),
            image_findings=image_findings or None,
        )
        return failures

    def _read(self, instance: DicomInstance, profile=None) -> tuple[MetadataRecord, ...]:
        path = self.storage.path(instance.id)
        if instance.integrity_state != "stored" or checksum(path) != instance.checksum:
            raise InvalidAnalysisInput("Stored object failed integrity check")
        records = parse_file(path, b_value_tags=self.b_value_tags or None, prostate_checks=profile)
        expected = (
            instance.sop_uid,
            instance.study_uid,
            instance.series_uid,
            instance.sop_class_uid,
        )
        for record in records:
            identity = tuple(
                record.known(field)
                for field in (
                    "SOPInstanceUID",
                    "StudyInstanceUID",
                    "SeriesInstanceUID",
                    "SOPClassUID",
                )
            )
            if identity != expected:
                raise InvalidAnalysisInput("Stored object identity differs from intake")
        return records

    def _set_job_state(self, job_id: int, instance_id: str, state: str) -> bool:
        with self.sessions.begin() as session:
            changed = session.execute(
                update(AnalysisJob)
                .where(AnalysisJob.id == job_id, AnalysisJob.instance_id == instance_id)
                .values(state=state)
            ).rowcount
            if changed and state == "failed":
                session.add(SystemEvent(code="analysis_input_failed", correlation_id=instance_id))
                LOGGER.warning("analysis_input_failed")
        return bool(changed)

