"""Consume durable intake jobs without owning the DICOM receiver or the GUI."""

import logging
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from uuid import uuid4

from pydicom.errors import InvalidDicomError
from pydicom.uid import MRImageStorage
from sqlalchemy import func, select, update

from ..analysis.metadata_normalizer import MetadataRecord
from ..analysis.series_aggregator import group_acquisitions
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
def open_worker(root: Path, protocol: ProtocolSnapshot):
    lock = ProcessLock(root, "analysis")
    lock.acquire()
    try:
        storage = Storage(root)
        try:
            yield AnalysisWorker(storage, protocol)
        finally:
            storage.close()
    finally:
        lock.release()


class AnalysisWorker:
    def __init__(self, storage: Storage, protocol: ProtocolSnapshot):
        self.storage = storage
        self.sessions = storage.sessions
        self.history = HistoryRepository(self.sessions)
        self.protocol = protocol

    def retry_failed(self) -> None:
        """Requeue rejected objects after their local storage has been repaired."""
        with self.sessions.begin() as session:
            session.execute(
                update(AnalysisJob).where(AnalysisJob.state == "failed").values(state="pending")
            )

    def run_once(self, stopped: Event) -> int:
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
                failures += self._process_study(study_uid, stopped)
            except Exception as error:
                failures += 1
                LOGGER.error("study_analysis_retry type=%s", type(error).__name__)
        return failures

    def _process_study(self, study_uid: str, stopped: Event) -> int:
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

        records = []
        completed = []
        failures = 0
        for instance, job in inputs:
            if stopped.is_set():
                return failures
            if instance.sop_class_uid != MRImageStorage:
                self._set_job_state(job.id, "ignored")
                continue
            try:
                records.extend(self._read(instance))
            except (InvalidDicomError, ValueError, TypeError, EOFError, FileNotFoundError):
                self._set_job_state(job.id, "failed", instance.id)
                failures += 1
                continue
            if job.state == "pending":
                completed.append(job.id)
        if not completed or stopped.is_set():
            return failures

        if not study_exists:
            self.history.begin_study(study_uid, self.protocol, actor=ACTOR)
        self.history.save_evaluation(
            study_uid,
            group_acquisitions(tuple(records)),
            request_id=uuid4().hex,
            actor=ACTOR,
            intake_job_ids=tuple(completed),
        )
        return failures

    def _read(self, instance: DicomInstance) -> tuple[MetadataRecord, ...]:
        path = self.storage.path(instance.id)
        if instance.integrity_state != "stored" or checksum(path) != instance.checksum:
            raise InvalidAnalysisInput("Stored object failed integrity check")
        records = parse_file(path)
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

    def _set_job_state(self, job_id: int, state: str, failure_id: str | None = None) -> None:
        with self.sessions.begin() as session:
            session.get(AnalysisJob, job_id).state = state
            if failure_id is not None:
                session.add(SystemEvent(code="analysis_input_failed", correlation_id=failure_id))
                LOGGER.warning("analysis_input_failed")
