"""Explicit synthetic fixture creation; never seed into an existing DICOM store."""

from pathlib import Path

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

from ..analysis.series_aggregator import group_acquisitions
from ..analysis.validation_engine import CompletionEvidence
from ..database.repository import HistoryRepository
from ..dicom.metadata_parser import parse_dataset
from ..dicom.storage import Storage
from ..process_lock import ReceiverLock
from ..protocols.manager import ProtocolSnapshot


def seed_demo(root: Path, snapshot: ProtocolSnapshot) -> str:
    """Write eleven synthetic single-frame instances and their DEMO evaluation."""
    if root.exists() and any(root.iterdir()):
        raise ValueError("DEMO requires a new or empty directory")
    lock = ReceiverLock(root)
    lock.acquire()
    store = None
    try:
        if any(path.name != "receiver.lock" for path in root.iterdir()):
            raise ValueError("DEMO requires a new or empty directory")
        store = Storage(root, minimum_free_bytes=0)
        study_uid = generate_uid()
        records = []
        for name, tr, count in (
            ("DEMO_OK", 4000, 3),
            ("DEMO_WARNING", 3200, 3),
            ("DEMO_CRITICAL", 6000, 3),
            ("DEMO_PENDING", 4000, 2),
        ):
            series_uid = generate_uid()
            for index in range(count):
                image = Dataset()
                image.SOPClassUID, image.SOPInstanceUID = MRImageStorage, generate_uid()
                image.StudyInstanceUID, image.SeriesInstanceUID = study_uid, series_uid
                image.PatientID, image.PatientName = "SYNTHETIC-ONLY", "SYNTHETIC^DEMO"
                image.StudyDate = "20261127"
                image.Manufacturer, image.ManufacturerModelName = "SYNTHETIC", "DEMO MR"
                image.Modality, image.ProtocolName, image.SeriesDescription = "MR", name, name
                image.SeriesNumber = {
                    "DEMO_OK": 1,
                    "DEMO_WARNING": 2,
                    "DEMO_CRITICAL": 3,
                    "DEMO_PENDING": 4,
                }[name]
                image.Rows = image.Columns = 16
                image.SamplesPerPixel = 1
                image.PhotometricInterpretation = "MONOCHROME2"
                image.BitsAllocated, image.BitsStored, image.HighBit = 16, 12, 11
                image.PixelRepresentation = 0
                image.PixelData = b"\x00\x00" * 256
                image.RepetitionTime, image.EchoTime, image.SliceThickness = tr, 100, 3
                image.PixelSpacing = [0.5, 0.5]
                image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
                image.ImagePositionPatient = [0, 0, index * 3]
                image.file_meta = FileMetaDataset()
                image.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
                store.receive(image, str(ExplicitVRLittleEndian), "DEMO", "127.0.0.1")
                records.extend(parse_dataset(image))
        repo = HistoryRepository(store.sessions)
        repo.begin_study(study_uid, snapshot, actor="synthetic-demo")
        repo.save_evaluation(
            study_uid,
            group_acquisitions(tuple(records)),
            request_id="demo-seed",
            actor="synthetic-demo",
            finalization=CompletionEvidence(
                source="synthetic-fixture", reference="software-demo-only"
            ),
        )
        return study_uid
    finally:
        if store is not None:
            store.close()
        lock.release()
