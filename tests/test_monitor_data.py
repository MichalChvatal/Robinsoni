import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.repository import HistoryRepository
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.demo import seed_demo
from mr_quality_monitor.gui.presentation import effective_status
from mr_quality_monitor.gui.view_data import MonitorReader, measured
from mr_quality_monitor.services.receiver_health import HEALTH_FILE, HealthPublisher, read_health
from pydicom.uid import ExplicitVRLittleEndian, generate_uid


def test_missing_database_is_not_created(tmp_path):
    root = tmp_path / "does-not-exist"
    snapshot = MonitorReader(root).read()
    assert snapshot.status == "GRAY" and not snapshot.studies
    assert not root.exists()


def test_demo_shows_all_four_states_and_actual_metadata(gui_demo):
    reader = MonitorReader(gui_demo.root)
    data = reader.read()
    assert data.study_uid == gui_demo.uid
    assert [row.status for row in data.rows] == ["GREEN", "ORANGE", "RED", "GRAY"]
    assert [row.count for row in data.rows] == [3, 3, 3, 2]
    assert measured(data.rows[2].records, "RepetitionTime") == "6000"
    assert data.status == "RED" and not data.stale
    assert data.received == 11 and data.pending == 0
    assert data.history[0]["statuses"]["seq:DEMO_CRITICAL"] == "RED"
    assert data.patient_token and "SYNTHETIC" not in data.patient_token
    assert reader.read(data.study_uid, data.branch_id).rows == data.rows


def test_reader_keeps_selected_study_when_a_new_one_arrives(gui_demo):
    reader = MonitorReader(gui_demo.root)
    first = reader.read()
    engine, sessions = open_database(gui_demo.root / "intake.sqlite3")
    try:
        HistoryRepository(sessions).begin_study(generate_uid(), gui_demo.snapshot, actor="test")
    finally:
        engine.dispose()
    second = reader.read(first.study_uid, first.branch_id)
    assert len(second.studies) == 2
    assert second.study_uid == first.study_uid


def test_raw_intake_is_visible_before_analytics(tmp_path, image):
    store = Storage(tmp_path, minimum_free_bytes=0)
    try:
        store.receive(image, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")
        result = MonitorReader(tmp_path).read()
        assert result.study_uid == str(image.StudyInstanceUID)
        assert result.pending == result.received == 1
        assert result.rows[0].reason == "metadata_not_processed"
        assert result.rows[0].status == "GRAY"
    finally:
        store.close()


def test_viewer_never_runs_migrations(tmp_path):
    path = tmp_path / "intake.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER)")
        db.execute("INSERT INTO schema_version VALUES (1,1)")
    with pytest.raises(ValueError, match="schema version"):
        MonitorReader(tmp_path).read()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 1
        assert (
            db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='alembic_version'"
            ).fetchone()[0]
            == 0
        )


def test_selected_study_is_not_reset_when_database_disappears(tmp_path):
    with pytest.raises(OSError):
        MonitorReader(tmp_path).read("1.2.3")


def test_demo_refuses_existing_data(gui_demo):
    with pytest.raises(ValueError, match="new or empty"):
        seed_demo(gui_demo.root, gui_demo.snapshot)


def test_heartbeat_fresh_stopped_stale_and_malformed(tmp_path):
    publisher = HealthPublisher(tmp_path, lambda: {"active_associations": 0, "unique_instances": 3})
    publisher.publish()
    assert read_health(tmp_path)["observed_state"] == "listening"
    publisher.publish("stopped")
    assert read_health(tmp_path)["observed_state"] == "stopped"
    assert (
        read_health(tmp_path, now=datetime.now(UTC) + timedelta(seconds=20))["observed_state"]
        == "unavailable"
    )
    (tmp_path / HEALTH_FILE).write_text("[]", encoding="utf-8")
    assert read_health(tmp_path)["observed_state"] == "unknown"
    (tmp_path / HEALTH_FILE).write_text("{broken", encoding="utf-8")
    assert read_health(tmp_path)["observed_state"] == "unknown"


def test_heartbeat_contains_no_patient_values(tmp_path):
    HealthPublisher(tmp_path, lambda: {"unique_instances": 2}).publish()
    text = (tmp_path / HEALTH_FILE).read_text()
    assert "Patient" not in text and "SOPInstance" not in text
    assert json.loads(text)["unique_instances"] == 2


def test_heartbeat_failure_does_not_raise_into_receiver(tmp_path):
    publisher = HealthPublisher(tmp_path / "missing", lambda: {})
    publisher.publish()


def test_stale_data_masks_green_but_retains_critical(gui_demo):
    snapshot = MonitorReader(gui_demo.root).read()
    assert effective_status(snapshot.rows[0], True) == "GRAY"
    assert effective_status(snapshot.rows[2], True) == "RED"


def test_running_receiver_publishes_counts_errors_and_stop(tmp_path, image):
    import socket
    from copy import deepcopy

    from mr_quality_monitor.config import ReceiverConfig
    from mr_quality_monitor.dicom.listener import Listener
    from pydicom.uid import MRImageStorage
    from pynetdicom import AE

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    listener = Listener(ReceiverConfig(storage_root=tmp_path, port=port, minimum_free_bytes=0))
    listener.start()
    ae = AE(ae_title="MR_TEST")
    ae.add_requested_context(MRImageStorage, ExplicitVRLittleEndian)
    association = ae.associate("127.0.0.1", port, ae_title="MR_QUALITY")
    try:
        assert association.is_established
        assert association.send_c_store(image).Status == 0
        assert association.send_c_store(image).Status == 0
        conflicting = deepcopy(image)
        conflicting.PixelData = b"\x02\x00" * 16
        assert association.send_c_store(conflicting).Status == 0xC211
        listener.health.publish()
        health = read_health(tmp_path)
        assert health["observed_state"] == "listening"
        assert health["unique_instances"] == 1
        assert health["active_associations"] >= 1
        assert health["last_received_at"]
        assert health["last_error"] == "conflicting_instance"
    finally:
        association.release()
        listener.stop()
    assert read_health(tmp_path)["observed_state"] == "stopped"
