from copy import deepcopy
from threading import Event

import pytest
from mr_quality_monitor.config import ReceiverConfig
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


def test_known_protocol_change_replaces_active_measurement(
    tmp_path, image, protocol_data, monkeypatch
):
    prepare(image)
    config = ReceiverConfig(storage_root=tmp_path, single_active_study=True)
    reader = MonitorReader(tmp_path, single_active_study=True)
    protocol_data.update(schema_version=2, mode="CLINICAL", approved_by="Software tests")
    candidates = []
    for name in ("FIRST", "SECOND"):
        protocol_data.update(protocol_id=name, display_name=name)
        protocol_data["study_identifiers"] = [
            {"field": field, "constraint": {"kind": "exact", "value": name}}
            for field in ("StudyDescription", "ProtocolName")
        ]
        candidates.append(ProtocolSnapshot.create(Protocol.model_validate(protocol_data)))
    protocols = tuple(candidates)
    storage = Storage(tmp_path, 0, single_active_study=True, protocols=protocols)
    syntax = str(image.file_meta.TransferSyntaxUID)
    same_uid = deepcopy(image)
    same_uid.StudyDescription = "SECOND"
    same_uid.SOPInstanceUID, same_uid.SeriesInstanceUID = generate_uid(), generate_uid()
    next_study = deepcopy(same_uid)
    next_study.StudyInstanceUID = generate_uid()
    next_study.SOPInstanceUID, next_study.SeriesInstanceUID = generate_uid(), generate_uid()
    image.StudyDescription = "FIRST"

    def receive(dataset):
        return storage.receive(dataset, syntax, "MR_TEST", "127.0.0.1")

    try:
        with open_worker(tmp_path, protocols, config) as worker:
            assert receive(image) == "stored"
            assert worker.run_once(Event()) == 0
            first = reader.read()
            assert first.protocol_name.startswith("FIRST")

            ambiguous = deepcopy(same_uid)
            ambiguous.SOPInstanceUID = generate_uid()
            ambiguous.ProtocolName = "FIRST"
            assert receive(ambiguous) == "stored"
            assert reader.read(first.study_uid, first.branch_id).branch_id == first.branch_id

            read = worker._read

            def switch_during_read(*args):
                patch.setattr(worker, "_read", read)
                assert receive(same_uid) == "stored"
                return read(*args)

            with monkeypatch.context() as patch:
                patch.setattr(worker, "_read", switch_during_read)
                assert worker.run_once(Event()) == 0

            replacement = reader.read(first.study_uid, first.branch_id)
            assert replacement.study_uid == first.study_uid
            assert replacement.branch_id is None and not replacement.history
            assert replacement.received == 1 and replacement.pending == 1
            assert worker.run_once(Event()) == 0
            second = reader.read(first.study_uid, first.branch_id)
            assert second.protocol_name.startswith("SECOND")
            assert second.branch_id != first.branch_id and len(second.history) == 1

            assert receive(next_study) == "stored"
            assert receive(same_uid) == "discarded"  # Older UID, same protocol as active.
            assert receive(image) == "stored"  # Older UID, an explicitly different protocol.
            assert worker.run_once(Event()) == 0
            replay = reader.read()
            assert replay.protocol_name.startswith("FIRST") and replay.received == 1
            assert len(list((tmp_path / "objects").glob("*.dcm"))) == 1
    finally:
        storage.close()


def test_new_study_replaces_active_study_during_analysis(
    tmp_path, image, protocol_data, monkeypatch
):
    prepare(image)
    protocol = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    config = ReceiverConfig(storage_root=tmp_path, single_active_study=True)
    reader = MonitorReader(tmp_path, single_active_study=True)
    storage = Storage(tmp_path, 0, single_active_study=True)
    new = deepcopy(image)
    new.StudyInstanceUID, new.SeriesInstanceUID, new.SOPInstanceUID = (
        generate_uid(), generate_uid(), generate_uid()
    )
    late = deepcopy(image)
    late.SOPInstanceUID = generate_uid()

    def receive(dataset):
        return storage.receive(
            dataset, str(dataset.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
        )

    try:
        assert receive(image) == "stored"
        with open_worker(tmp_path, protocol, config) as worker:
            assert worker.run_once(Event()) == 0
            old = reader.read()
            assert counts(worker) == (1, ["done"])
            assert receive(late) == "stored"
            read = worker._read

            def switch_during_read(*args):
                patch.setattr(worker, "_read", read)
                assert receive(new) == "stored"
                return read(*args)

            with monkeypatch.context() as patch:
                patch.setattr(worker, "_read", switch_during_read)
                assert worker.run_once(Event()) == 0

            assert counts(worker) == (0, ["pending"])
            updated = reader.read(old.study_uid, old.branch_id)
            assert updated.study_uid == str(new.StudyInstanceUID)
            assert updated.pending == 1 and not updated.history
            assert len(updated.studies) == 1
            assert receive(late) == "discarded"
            assert worker.run_once(Event()) == 0
            assert counts(worker) == (1, ["done"])
            with storage.engine.connect() as connection:
                assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                for table in ("patients", "studies", "dicom_instances", "reception_events"):
                    assert connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() == 1
            assert len(list((tmp_path / "objects").glob("*.dcm"))) == 1
    finally:
        storage.close()


def test_replacement_invalidates_closure_in_progress(tmp_path, image, protocol_data, monkeypatch):
    prepare(image)
    protocol_data.update(schema_version=2, mode="CLINICAL", approved_by="Software tests")
    protocol_data["study_rules"] = {}
    protocol = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    config = ReceiverConfig(storage_root=tmp_path, single_active_study=True)
    reader = MonitorReader(tmp_path, single_active_study=True)
    storage = Storage(tmp_path, 0, single_active_study=True)
    syntax = str(image.file_meta.TransferSyntaxUID)
    try:
        assert storage.receive(image, syntax, "MR_TEST", "127.0.0.1") == "stored"
        with open_worker(tmp_path, protocol, config) as worker:
            assert worker.run_once(Event()) == 0
            old = reader.read()
            assert old.can_finalize
            save = worker.history.save_evaluation
            image.StudyInstanceUID = generate_uid()
            image.SeriesInstanceUID = generate_uid()
            image.SOPInstanceUID = generate_uid()

            def replace_before_commit(*args, **kwargs):
                assert storage.receive(image, syntax, "MR_TEST", "127.0.0.1") == "stored"
                return save(*args, **kwargs)

            monkeypatch.setattr(worker.history, "save_evaluation", replace_before_commit)
            with pytest.raises(ValueError):
                worker.history.finalize_current(old.study_uid, old.branch_id, actor="test")
            new = reader.read(old.study_uid, old.branch_id)
            assert new.study_uid == str(image.StudyInstanceUID)
            assert new.pending == 1 and not new.history and new.completion is None
    finally:
        storage.close()


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
