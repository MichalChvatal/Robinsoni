import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from mr_quality_monitor.database.models import (
    AnalysisJob,
    DicomInstance,
    IntakeRecord,
    ReceptionEvent,
)
from mr_quality_monitor.dicom.storage import InvalidDicom, Storage
from mr_quality_monitor.process_lock import ReceiverLock
from pydicom import dcmread
from pydicom.uid import ExplicitVRLittleEndian
from sqlalchemy import event, func, select


@pytest.fixture
def store(tmp_path):
    storage = Storage(tmp_path, minimum_free_bytes=0)
    yield storage
    storage.close()


def receive(store, image):
    return store.receive(image, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")


def count(store, model):
    with store.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_stores_and_deduplicates_with_one_job(store, image):
    assert receive(store, image) == "stored"
    assert receive(store, image) == "duplicate"
    assert count(store, DicomInstance) == 1
    assert count(store, AnalysisJob) == 1
    assert count(store, ReceptionEvent) == 2
    files = list((store.root / "objects").glob("*.dcm"))
    assert len(files) == 1
    assert str(dcmread(files[0]).SOPInstanceUID) == str(image.SOPInstanceUID)
    assert len(list((store.root / "staging").iterdir())) == 0


def test_conflicting_uid_is_quarantined_and_original_preserved(store, image):
    receive(store, image)
    original = next((store.root / "objects").glob("*.dcm")).read_bytes()
    changed = deepcopy(image)
    changed.PixelData = b"\x02\x00" * 16
    assert receive(store, changed) == "conflict"
    assert next((store.root / "objects").glob("*.dcm")).read_bytes() == original
    assert len(list((store.root / "quarantine").glob("*.dcm"))) == 1
    assert count(store, AnalysisJob) == 1


def test_concurrent_duplicate_receipts(store, image):
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(lambda _: receive(store, image), range(8)))
    assert outcomes.count("stored") == 1
    assert outcomes.count("duplicate") == 7
    assert count(store, AnalysisJob) == 1
    assert count(store, ReceptionEvent) == 8


@pytest.mark.parametrize("mutation", ["missing_uid", "invalid_uid", "short_pixels", "no_pixels"])
def test_invalid_dataset_is_not_committed(store, image, mutation):
    if mutation == "missing_uid":
        del image.StudyInstanceUID
    elif mutation == "invalid_uid":
        image.SeriesInstanceUID = "not-a-uid"
    elif mutation == "short_pixels":
        image.PixelData = b"\x00\x00"
    else:
        del image.PixelData
    with pytest.raises(InvalidDicom):
        receive(store, image)
    assert count(store, DicomInstance) == 0
    assert count(store, AnalysisJob) == 0


@pytest.mark.parametrize("boundary", ["_publish", "_finalize"])
def test_restart_recovers_file_before_and_after_publication(store, image, monkeypatch, boundary):
    original = getattr(store, boundary)

    def crash(_record_id):
        raise OSError("synthetic crash")

    monkeypatch.setattr(store, boundary, crash)
    with pytest.raises(OSError):
        receive(store, image)
    assert count(store, DicomInstance) == 0
    monkeypatch.setattr(store, boundary, original)
    assert store.recover()["recovered"] == 1
    assert count(store, AnalysisJob) == 1
    assert receive(store, image) == "duplicate"


def test_incomplete_file_is_quarantined(store, image, monkeypatch):
    def partial(record_id, _dataset, _syntax):
        store.path(record_id, "staging").write_bytes(b"partial")
        raise OSError("synthetic full disk")

    monkeypatch.setattr(store, "_write_staged", partial)
    with pytest.raises(OSError):
        receive(store, image)
    assert store.recover()["interrupted"] == 1
    assert count(store, AnalysisJob) == 0
    assert len(list((store.root / "quarantine").glob("*.dcm"))) == 1


def test_journal_checksum_mismatch_is_quarantined(store, image, monkeypatch):
    original = store._publish
    monkeypatch.setattr(store, "_publish", lambda _id: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        receive(store, image)
    staged = next((store.root / "staging").glob("*.dcm"))
    with staged.open("ab") as stream:
        stream.write(b"changed")
    monkeypatch.setattr(store, "_publish", original)
    assert store.recover()["interrupted"] == 1
    assert count(store, DicomInstance) == 0


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_committed_file_damage_blocks_startup(store, image, damage):
    receive(store, image)
    path = next((store.root / "objects").glob("*.dcm"))
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="missing or corrupt"):
        store.recover()
    with store.sessions() as session:
        assert session.scalar(select(DicomInstance)).integrity_state == damage


def test_deleted_pending_job_is_reconstructed(store, image):
    receive(store, image)
    with store.sessions.begin() as session:
        session.delete(session.scalar(select(AnalysisJob)))
    store.recover()
    assert count(store, AnalysisJob) == 1


def test_insufficient_space_creates_no_success(store, image, monkeypatch):
    monkeypatch.setattr(store, "minimum_free_bytes", 2**63)
    with pytest.raises(OSError, match="insufficient_storage"):
        receive(store, image)
    assert count(store, IntakeRecord) == 0


def test_receiver_process_lock_is_exclusive_and_reusable(tmp_path):
    first, second = ReceiverLock(tmp_path), ReceiverLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another receiver"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


@pytest.mark.parametrize("boundary", ["_publish", "_finalize"])
def test_abrupt_process_exit_recovers_with_new_database_connection(tmp_path, image, boundary):
    from pydicom import dcmwrite

    source = tmp_path / "synthetic.dcm"
    dcmwrite(source, image, enforce_file_format=True)
    root = tmp_path / "store"
    script = """
import os, sys
from pathlib import Path
from pydicom import dcmread
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.process_lock import ReceiverLock
root, source, boundary = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
lock = ReceiverLock(root)
lock.acquire()
store = Storage(root, minimum_free_bytes=0)
setattr(store, boundary, lambda _record_id: os._exit(91))
image = dcmread(source)
store.receive(image, str(image.file_meta.TransferSyntaxUID), 'MR_TEST', '127.0.0.1')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), str(source), boundary],
        capture_output=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path("backend").resolve())},
    )
    assert result.returncode == 91, result.stderr.decode(errors="replace")
    lock = ReceiverLock(root)
    lock.acquire()
    recovered = Storage(root, minimum_free_bytes=0)
    try:
        assert recovered.recover()["recovered"] == 1
        assert count(recovered, DicomInstance) == 1
        assert count(recovered, AnalysisJob) == 1
        assert receive(recovered, image) == "duplicate"
    finally:
        recovered.close()
        lock.release()


def test_final_database_commit_failure_is_recoverable(store, image):
    def fail_final_commit(session):
        if any(isinstance(item, AnalysisJob) for item in session.new):
            raise OSError("synthetic transaction failure")

    event.listen(store.sessions, "before_commit", fail_final_commit)
    try:
        with pytest.raises(OSError, match="synthetic transaction"):
            receive(store, image)
        assert count(store, DicomInstance) == 0
        assert count(store, AnalysisJob) == 0
    finally:
        event.remove(store.sessions, "before_commit", fail_final_commit)
    assert store.recover()["recovered"] == 1
    assert count(store, DicomInstance) == 1
    assert count(store, AnalysisJob) == 1


def test_orphan_file_is_quarantined(store):
    (store.root / "objects" / "orphan.dcm").write_bytes(b"orphan")
    assert store.recover()["orphaned"] == 1
    assert len(list((store.root / "quarantine").glob("*.dcm"))) == 1
