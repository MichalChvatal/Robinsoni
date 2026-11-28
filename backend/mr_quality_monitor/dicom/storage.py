"""Durable, idempotent intake with a recoverable filesystem/database boundary."""

import hashlib
import logging
import os
import shutil
import threading
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from pydicom import dcmread, dcmwrite
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.errors import InvalidDicomError
from pydicom.uid import (
    PYDICOM_IMPLEMENTATION_UID,
    UID,
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    MRImageStorage,
    SecondaryCaptureImageStorage,
)
from sqlalchemy import select

from ..analysis.sequence_identifier import match_study_protocol
from ..database.database import open_database
from ..database.models import (
    AnalysisJob,
    DicomInstance,
    IntakeRecord,
    ReceptionEvent,
    SystemEvent,
)
from ..database.study_retention import retain_study
from .metadata_parser import parse_dataset

STORAGE_CLASSES = (str(MRImageStorage), str(SecondaryCaptureImageStorage))
DISCARDED_STUDY_STATUS = 0xC212
TRANSFER_SYNTAXES = (str(ExplicitVRLittleEndian), str(ImplicitVRLittleEndian))
LOGGER = logging.getLogger(__name__)


class InvalidDicom(ValueError):
    """A dataset cannot be accepted by this receiver's supported image profile."""


def validate_dataset(dataset: Dataset, transfer_syntax: str) -> None:
    """Check identifiers and native image lengths, without clinical pixel analysis."""
    for name in ("SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID"):
        value = str(getattr(dataset, name, ""))
        if not value or not UID(value).is_valid:
            raise InvalidDicom("missing_or_invalid_uid")
    if str(dataset.SOPClassUID) not in STORAGE_CLASSES:
        raise InvalidDicom("unsupported_sop_class")
    if transfer_syntax not in TRANSFER_SYNTAXES:
        raise InvalidDicom("unsupported_transfer_syntax")
    try:
        rows, columns = int(dataset.Rows), int(dataset.Columns)
        samples, bits = int(dataset.SamplesPerPixel), int(dataset.BitsAllocated)
        frames = int(getattr(dataset, "NumberOfFrames", 1))
        stored, high = int(dataset.BitsStored), int(dataset.HighBit)
        representation = int(dataset.PixelRepresentation)
        photo = str(dataset.PhotometricInterpretation)
        if rows <= 0 or columns <= 0 or frames != 1:
            raise InvalidDicom("invalid_image_dimensions")
        if bits not in (8, 16) or not 1 <= stored <= bits or high != stored - 1:
            raise InvalidDicom("unsupported_pixel_layout")
        if representation not in (0, 1):
            raise InvalidDicom("invalid_pixel_representation")
        if samples == 1:
            if photo not in ("MONOCHROME1", "MONOCHROME2"):
                raise InvalidDicom("unsupported_photometric_interpretation")
        elif samples == 3 and str(dataset.SOPClassUID) == str(SecondaryCaptureImageStorage):
            if photo != "RGB" or int(dataset.PlanarConfiguration) not in (0, 1) or bits != 8:
                raise InvalidDicom("unsupported_color_layout")
        else:
            raise InvalidDicom("unsupported_samples_per_pixel")
        expected = rows * columns * samples * bits // 8
        pixels = dataset.PixelData
        # DICOM native values have even length; permit an omitted byte before encoding.
        if len(pixels) not in {expected, expected + (expected % 2)}:
            raise InvalidDicom("pixel_length_mismatch")
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        if isinstance(error, InvalidDicom):
            raise
        raise InvalidDicom("missing_or_invalid_pixel_metadata") from None


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sync_directory(path: Path) -> None:
    """Persist directory updates where supported; Windows uses file fsync + rename.

    Sudden power-loss guarantees on Windows depend on the target filesystem/device;
    process-crash recovery is covered by the journal and restart tests.
    """
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Storage:
    """Single-receiver storage. Caller must hold the process-level ReceiverLock.

    A short in-process lock serializes publication and duplicate decisions across
    pynetdicom handler threads. No network or image analysis runs under DB transactions.
    """

    def __init__(
        self, root: Path, minimum_free_bytes: int = 104857600, *,
        single_active_study: bool = False, protocols=(),
    ):
        self.root = root.resolve()
        self.minimum_free_bytes = minimum_free_bytes
        self.lock = threading.RLock()
        self.single_active_study = single_active_study
        self.active_study: str | None = None
        self.active_protocol: str | None = None
        self.protocol_candidates = tuple(
            (snapshot, snapshot.protocol.study_identifiers) for snapshot in protocols
        )
        self.retired_studies: set[str] = set()
        self.discarded_files: set[str] = set()
        for name in ("staging", "objects", "quarantine"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.engine, self.sessions = open_database(self.root / "intake.sqlite3")

    def path(self, record_id: str, area: str = "objects") -> Path:
        """Only internally generated UUIDs may select a storage path."""
        if len(record_id) != 32 or any(char not in "0123456789abcdef" for char in record_id):
            raise ValueError("Invalid internal intake identifier")
        if area not in {"objects", "staging", "quarantine"}:
            raise ValueError("Invalid storage area")
        return self.root / area / f"{record_id}.dcm"

    def receive(
        self, dataset: Dataset, transfer_syntax: str, source_ae: str, source_ip: str
    ) -> str:
        """Persist an image or discard a late image of a replaced study.

        Failures leave a journal record for recovery; they never imply success.
        Source identifiers are recorded in the local audit DB, never in log messages.
        """
        validate_dataset(dataset, transfer_syntax)
        with self.lock:
            self._unlink_discarded()
            protocol_id = (
                self._identify_protocol(dataset)
                if str(dataset.StudyInstanceUID) in self.retired_studies else None
            )
            if self._discard_image(dataset, protocol_id):
                return "discarded"
            # Reserve for pixels plus metadata; actual ENOSPC is handled as a storage failure.
            estimated_size = len(dataset.PixelData) + 1024 * 1024
            if shutil.disk_usage(self.root).free < self.minimum_free_bytes + estimated_size:
                raise OSError("insufficient_storage")
            record_id = uuid4().hex
            with self.sessions.begin() as session:
                session.add(
                    IntakeRecord(
                        id=record_id,
                        sop_uid=str(dataset.SOPInstanceUID),
                        source_ae=source_ae,
                        source_ip=source_ip,
                    )
                )
            self._write_staged(record_id, dataset, transfer_syntax)
            staged = self.path(record_id, "staging")
            saved = dcmread(staged)
            validate_dataset(saved, transfer_syntax)
            if any(
                str(getattr(saved, key)) != str(getattr(dataset, key))
                for key in (
                    "SOPInstanceUID",
                    "SOPClassUID",
                    "StudyInstanceUID",
                    "SeriesInstanceUID",
                )
            ):
                raise InvalidDicom("stored_identity_mismatch")
            digest = checksum(staged)
            with self.sessions.begin() as session:
                record = session.get(IntakeRecord, record_id)
                record.state, record.checksum = "staged", digest
            self._publish(record_id)
            return self._finalize(record_id)

    def _write_staged(self, record_id: str, dataset: Dataset, transfer_syntax: str) -> None:
        saved = deepcopy(dataset)
        # Deterministic file meta avoids false conflicts caused by sender implementation IDs.
        saved.file_meta = FileMetaDataset()
        saved.file_meta.TransferSyntaxUID = transfer_syntax
        saved.file_meta.MediaStorageSOPClassUID = saved.SOPClassUID
        saved.file_meta.MediaStorageSOPInstanceUID = saved.SOPInstanceUID
        saved.file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID
        saved.preamble = b"\x00" * 128
        with self.path(record_id, "staging").open("xb") as stream:
            dcmwrite(stream, saved, enforce_file_format=True)
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(self.root / "staging")

    def _publish(self, record_id: str) -> None:
        os.replace(self.path(record_id, "staging"), self.path(record_id))
        sync_directory(self.root / "objects")
        sync_directory(self.root / "staging")

    def _finalize(self, record_id: str) -> str:
        path = self.path(record_id)
        saved = dcmread(path)
        validate_dataset(saved, str(saved.file_meta.TransferSyntaxUID))
        digest = checksum(path)
        study_uid = str(saved.StudyInstanceUID)
        protocol_id = self._identify_protocol(saved)
        if self._discard_image(saved, protocol_id):
            self._discard_uncommitted(record_id)
            return "discarded"
        switching = False
        with self.sessions.begin() as session:
            if self.single_active_study:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = session.get(IntakeRecord, record_id)
            if record.checksum != digest or record.sop_uid != str(saved.SOPInstanceUID):
                raise InvalidDicom("journal_integrity_mismatch")
            existing = session.scalar(
                select(DicomInstance).where(DicomInstance.sop_uid == record.sop_uid)
            )
            if existing is None:
                protocol_changed = self._protocol_changed(protocol_id)
                switching = self.single_active_study and (
                    self.active_study != study_uid or protocol_changed
                )
                if switching:
                    retained_uid = "" if protocol_changed and self.active_study == study_uid else (
                        study_uid
                    )
                    retired, files = retain_study(session, retained_uid, record.received_at)
                instance = DicomInstance(
                    id=record_id,
                    sop_uid=record.sop_uid,
                    study_uid=study_uid,
                    series_uid=str(saved.SeriesInstanceUID),
                    sop_class_uid=str(saved.SOPClassUID),
                    transfer_syntax_uid=str(saved.file_meta.TransferSyntaxUID),
                    checksum=digest,
                    size_bytes=path.stat().st_size,
                    received_at=record.received_at,
                )
                session.add(instance)
                session.flush()
                session.add(AnalysisJob(instance_id=record_id))
                outcome, instance_id = "stored", record_id
            else:
                original = self.path(existing.id)
                if not original.exists() or checksum(original) != existing.checksum:
                    raise OSError("committed_original_unavailable")
                outcome = "duplicate" if existing.checksum == digest else "conflict"
                instance_id = existing.id
                if outcome == "conflict":
                    # Move before DB commit; recovery also handles this exact crash window.
                    os.replace(path, self.path(record_id, "quarantine"))
                    sync_directory(self.root / "quarantine")
            record.state = outcome
            session.add(
                ReceptionEvent(intake_id=record_id, instance_id=instance_id, outcome=outcome)
            )
        if switching:
            self.active_study = study_uid
            self.active_protocol = protocol_id
            self.retired_studies.update(retired)
            self.retired_studies.discard(study_uid)
            self.discarded_files.update(files)
            self._unlink_discarded()
        elif outcome == "stored" and protocol_id:
            self.active_protocol = protocol_id
        if outcome == "duplicate":
            path.unlink(missing_ok=True)
        return outcome

    def _identify_protocol(self, dataset: Dataset) -> str | None:
        if (
            not self.single_active_study
            or not self.protocol_candidates
            or dataset.SOPClassUID != MRImageStorage
        ):
            return None
        try:
            match = match_study_protocol(parse_dataset(dataset), self.protocol_candidates)
        except ValueError:
            return None
        return match.protocol.protocol_id if match else None

    def _protocol_changed(self, protocol_id: str | None) -> bool:
        return bool(protocol_id and self.active_protocol and protocol_id != self.active_protocol)

    def _discard_image(self, dataset: Dataset, protocol_id: str | None) -> bool:
        study_uid = str(dataset.StudyInstanceUID)
        return (study_uid in self.retired_studies and not self._protocol_changed(protocol_id)) or (
            self.single_active_study
            and dataset.SOPClassUID != MRImageStorage
            and study_uid != self.active_study
        )

    def _discard_uncommitted(self, record_id: str) -> None:
        with self.sessions.begin() as session:
            session.delete(session.get(IntakeRecord, record_id))
        self.discarded_files.add(record_id)
        self._unlink_discarded()

    def _unlink_discarded(self) -> None:
        for record_id in tuple(self.discarded_files):
            try:
                for area in ("objects", "staging", "quarantine"):
                    self.path(record_id, area).unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("discarded_file_cleanup_pending")
            else:
                self.discarded_files.remove(record_id)

    def retry_discarded_cleanup(self) -> None:
        with self.lock:
            self._unlink_discarded()

    def _interrupt(self, record_id: str) -> None:
        """Quarantine an unconfirmed intake and retain a non-clinical audit event."""
        for area in ("staging", "objects"):
            path = self.path(record_id, area)
            if path.exists():
                os.replace(path, self.path(record_id, "quarantine"))
        sync_directory(self.root / "quarantine")
        with self.sessions.begin() as session:
            session.get(IntakeRecord, record_id).state = "interrupted"
            session.add(ReceptionEvent(intake_id=record_id, outcome="interrupted"))

    def recover(self, *, reset_study: bool = False) -> dict[str, int]:
        """Reconcile incomplete receipts before accepting any new association.

        Never reanalyse duplicates. Missing/corrupt previously committed objects are
        marked and cause startup to fail, preserving all evidence for intervention.
        """
        counts = {"recovered": 0, "interrupted": 0, "orphaned": 0}
        with self.lock:
            latest = None
            if self.single_active_study:
                with self.sessions.begin() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    if not reset_study:
                        latest = session.execute(
                            select(DicomInstance.study_uid, DicomInstance.received_at)
                            .order_by(DicomInstance.received_at.desc(), DicomInstance.id.desc())
                            .limit(1)
                        ).first()
                    self.active_study = latest.study_uid if latest else None
                    retired, files = retain_study(
                        session, self.active_study or "", reset=reset_study
                    )
                if reset_study:
                    self.active_protocol = None
                    self.retired_studies.clear()
                else:
                    self.retired_studies.update(retired)
                self.discarded_files.update(files)
                self._unlink_discarded()
            with self.sessions() as session:
                pending = list(
                    session.scalars(
                        select(IntakeRecord)
                        .where(IntakeRecord.state.in_(("receiving", "staged")))
                        .order_by(IntakeRecord.received_at, IntakeRecord.id)
                    )
                )
            for record in pending:
                if self.path(record.id, "quarantine").exists():
                    self._interrupt(record.id)
                    counts["interrupted"] += 1
                    continue
                candidate = self.path(record.id)
                if not candidate.exists():
                    candidate = self.path(record.id, "staging")
                try:
                    saved = dcmread(candidate)
                    validate_dataset(saved, str(saved.file_meta.TransferSyntaxUID))
                    digest = checksum(candidate)
                    if str(saved.SOPInstanceUID) != record.sop_uid:
                        raise InvalidDicom("recovery_uid_mismatch")
                    if record.checksum is not None and record.checksum != digest:
                        raise InvalidDicom("recovery_checksum_mismatch")
                except (OSError, ValueError, AttributeError, EOFError, InvalidDicomError):
                    self._interrupt(record.id)
                    counts["interrupted"] += 1
                    continue
                if (
                    latest
                    and record.received_at < latest.received_at
                    and str(saved.StudyInstanceUID) != latest.study_uid
                ):
                    self._discard_uncommitted(record.id)
                    counts["interrupted"] += 1
                    continue
                with self.sessions.begin() as session:
                    current = session.get(IntakeRecord, record.id)
                    current.state, current.checksum = "staged", digest
                if candidate.parent.name == "staging":
                    self._publish(record.id)
                self._finalize(record.id)
                counts["recovered"] += 1

            with self.sessions() as session:
                records = {record.id: record for record in session.scalars(select(IntakeRecord))}
                instances = list(session.scalars(select(DicomInstance)))
            # Crash after a duplicate commit but before temporary-copy cleanup.
            for record in records.values():
                if record.state == "duplicate":
                    self.path(record.id).unlink(missing_ok=True)
            areas = ("staging", "objects", "quarantine") if self.single_active_study else (
                "staging", "objects"
            )
            for area in areas:
                for path in (self.root / area).glob("*.dcm"):
                    if path.stem not in records:
                        if self.single_active_study:
                            file_id = path.stem.removeprefix("orphan-")
                            if len(file_id) == 32 and all(c in "0123456789abcdef" for c in file_id):
                                path.unlink(missing_ok=True)
                                counts["orphaned"] += 1
                            continue
                        target = self.root / "quarantine" / f"orphan-{uuid4().hex}.dcm"
                        os.replace(path, target)
                        counts["orphaned"] += 1
                        with self.sessions.begin() as session:
                            session.add(SystemEvent(code="orphan_quarantined"))
            damaged = False
            for instance in instances:
                path = self.path(instance.id)
                integrity = "stored"
                if not path.exists():
                    integrity = "missing"
                elif checksum(path) != instance.checksum:
                    integrity = "corrupt"
                with self.sessions.begin() as session:
                    session.get(DicomInstance, instance.id).integrity_state = integrity
                    if integrity != "stored":
                        damaged = True
                        session.add(
                            SystemEvent(
                                code=f"committed_object_{integrity}", correlation_id=instance.id
                            )
                        )
                    elif (
                        session.scalar(
                            select(AnalysisJob).where(AnalysisJob.instance_id == instance.id)
                        )
                        is None
                    ):
                        session.add(AnalysisJob(instance_id=instance.id))
            if damaged:
                raise RuntimeError("Committed DICOM storage is missing or corrupt")
        return counts

    def series_paths(self, series_uid: str) -> list[Path]:
        """Return stored object paths for all instances in a series."""
        with self.sessions() as session:
            instances = list(
                session.scalars(
                    select(DicomInstance).where(
                        DicomInstance.series_uid == series_uid,
                        DicomInstance.integrity_state == "stored",
                    )
                )
            )
        return [self.path(instance.id) for instance in instances]

    def close(self) -> None:
        self.engine.dispose()
