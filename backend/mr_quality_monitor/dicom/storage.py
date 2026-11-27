"""Durable, idempotent intake with a recoverable filesystem/database boundary."""

import hashlib
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

from ..database.database import open_database
from ..database.models import (
    AnalysisJob,
    DicomInstance,
    IntakeRecord,
    ReceptionEvent,
    SystemEvent,
)

STORAGE_CLASSES = (str(MRImageStorage), str(SecondaryCaptureImageStorage))
TRANSFER_SYNTAXES = (str(ExplicitVRLittleEndian), str(ImplicitVRLittleEndian))


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

    def __init__(self, root: Path, minimum_free_bytes: int = 104857600):
        self.root = root.resolve()
        self.minimum_free_bytes = minimum_free_bytes
        self.lock = threading.RLock()
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
        """Persist an image and return stored/duplicate/conflict after the DB commit.

        Failures leave a journal record for recovery; they never imply success.
        Source identifiers are recorded in the local audit DB, never in log messages.
        """
        validate_dataset(dataset, transfer_syntax)
        with self.lock:
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
        with self.sessions.begin() as session:
            record = session.get(IntakeRecord, record_id)
            if record.checksum != digest or record.sop_uid != str(saved.SOPInstanceUID):
                raise InvalidDicom("journal_integrity_mismatch")
            existing = session.scalar(
                select(DicomInstance).where(DicomInstance.sop_uid == record.sop_uid)
            )
            if existing is None:
                instance = DicomInstance(
                    id=record_id,
                    sop_uid=record.sop_uid,
                    study_uid=str(saved.StudyInstanceUID),
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
        if outcome == "duplicate":
            path.unlink(missing_ok=True)
        return outcome

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

    def recover(self) -> dict[str, int]:
        """Reconcile incomplete receipts before accepting any new association.

        Never reanalyse duplicates. Missing/corrupt previously committed objects are
        marked and cause startup to fail, preserving all evidence for intervention.
        """
        counts = {"recovered": 0, "interrupted": 0, "orphaned": 0}
        with self.lock:
            with self.sessions() as session:
                pending = list(
                    session.scalars(
                        select(IntakeRecord).where(IntakeRecord.state.in_(("receiving", "staged")))
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
            for area in ("staging", "objects"):
                for path in (self.root / area).glob("*.dcm"):
                    if path.stem not in records:
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

    def close(self) -> None:
        self.engine.dispose()
