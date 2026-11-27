from copy import deepcopy
from threading import Event

import pytest
from mr_quality_monitor.database.models import AnalysisJob, SystemEvent, ValidationRun
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.protocols.manager import ProtocolSnapshot
from mr_quality_monitor.protocols.models import Protocol
from mr_quality_monitor.services.analysis_worker import open_worker
from pydicom.uid import SecondaryCaptureImageStorage, generate_uid
from sqlalchemy import func, select
from test_listener import connect, listener  # noqa: F401


def prepare(image):
    image.SeriesDescription = "t2 synthetic"
    image.RepetitionTime = 4000
    image.EchoTime = 100
    image.PixelSpacing = [1, 1]
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    image.ImagePositionPatient = [0, 0, 0]
    image.FrameOfReferenceUID = generate_uid()


def counts(worker):
    with worker.sessions() as session:
        return (
            session.scalar(select(func.count()).select_from(ValidationRun)),
            list(session.scalars(select(AnalysisJob.state).order_by(AnalysisJob.id))),
        )


def test_receiver_to_gui_with_restart_and_late_data(listener, image, protocol_data):  # noqa: F811
    prepare(image)
    protocol = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    root = listener.config.storage_root
    reader = MonitorReader(root)
    stopped = Event()
    association = connect(listener)
    assert association.is_established
    try:
        assert association.send_c_store(image).Status == 0
        assert reader.read().pending == 1
        with open_worker(root, protocol) as worker:
            with pytest.raises(RuntimeError, match="Another analysis"):
                with open_worker(root, protocol):
                    pytest.fail("Two workers acquired the same store")
            assert worker.run_once(stopped) == 0
            assert counts(worker) == (1, ["done"])
        first = reader.read()
        assert first.status == "GRAY"
        assert not first.stale

        report = deepcopy(image)
        report.SOPClassUID = SecondaryCaptureImageStorage
        report.SOPInstanceUID = generate_uid()
        report.SeriesInstanceUID = generate_uid()
        assert association.send_c_store(report).Status == 0
        for index in (1, 2):
            member = deepcopy(image)
            member.SOPInstanceUID = generate_uid()
            member.ImagePositionPatient = [0, 0, index]
            assert association.send_c_store(member).Status == 0
        assert reader.read().pending == 2

        protocol_data["version"] = "2-demo"
        protocol_data["sequences"][0]["completion"]["expected_members"] = 4
        changed = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
        with open_worker(root, changed) as worker:
            assert worker.run_once(stopped) == 0
            assert counts(worker) == (2, ["done", "ignored", "done", "done"])
            assert worker.history.get_protocol(str(image.StudyInstanceUID)) == protocol
            assert association.send_c_store(image).Status == 0
            assert worker.run_once(stopped) == 0
            assert counts(worker) == (2, ["done", "ignored", "done", "done"])
        result = reader.read()
        assert result.status == "GRAY"  # The study has not been explicitly finalized.
        assert not result.stale
        assert result.pending == 0
        assert result.received == 4
        assert any(row.count == 3 and row.status == "GREEN" for row in result.rows)
    finally:
        association.release()


def test_failed_write_retries_and_corrupt_input_is_visible(
    tmp_path, image, protocol_data, monkeypatch
):
    prepare(image)
    protocol = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    storage = Storage(tmp_path, 0)
    try:
        assert storage.receive(
            image, str(image.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
        )
    finally:
        storage.close()
    stopped = Event()
    with open_worker(tmp_path, protocol) as worker:

        def fail(*_args):
            raise RuntimeError("Synthetic transaction failure")

        with monkeypatch.context() as patch:
            patch.setattr(worker.history, "_group", fail)
            assert worker.run_once(stopped) == 1
        assert counts(worker) == (0, ["pending"])

        path = next((tmp_path / "objects").glob("*.dcm"))
        original = path.read_bytes()
        path.write_bytes(b"damaged")
        assert worker.run_once(stopped) == 1
        assert counts(worker) == (0, ["failed"])
        with worker.sessions() as session:
            assert session.scalar(select(SystemEvent.code)) == "analysis_input_failed"
        snapshot = MonitorReader(tmp_path).read()
        assert snapshot.stale
        assert any(row.reason == "analysis_input_failed" for row in snapshot.rows)

        path.write_bytes(original)
        worker.retry_failed()
        assert worker.run_once(stopped) == 0
        assert counts(worker) == (1, ["done"])
