import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.repository import HistoryRepository
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.completion import examination_completion
from mr_quality_monitor.gui.demo import seed_demo
from mr_quality_monitor.gui.presentation import effective_status
from mr_quality_monitor.gui.view_data import MonitorReader, MonitorRow, measured
from mr_quality_monitor.protocols.manager import ProtocolSnapshot, load_protocol
from mr_quality_monitor.protocols.models import Protocol
from mr_quality_monitor.services.analysis_worker import open_worker
from mr_quality_monitor.services.receiver_health import HEALTH_FILE, HealthPublisher, read_health
from pydicom.uid import ExplicitVRLittleEndian, generate_uid


def test_current_protocol_processes_intake_with_previous_version_in_history(tmp_path, image):
    current = load_protocol(Path("config/protocols/prostate_pirads_v2_1.yaml"))
    previous = current.protocol.model_dump()
    previous["version"] = "1.1"
    for sequence in previous["sequences"]:
        for parameter in sequence["parameters"]:
            if parameter["field"] == "FrequencyResolution":
                parameter["constraint"]["maximum"] = 0.4
                parameter["action"] = parameter["action"].replace("0.7", "0.4")
    legacy = ProtocolSnapshot.create(Protocol.model_validate(previous))
    storage = Storage(tmp_path, minimum_free_bytes=0)
    try:
        history = HistoryRepository(storage.sessions)
        previous_uid = generate_uid()
        history.begin_study(previous_uid, legacy, actor="test")
        image.BodyPartExamined = "PROSTATE"
        image.StudyDescription = "Prostate"
        image.SeriesDescription = "t2_tse_tra"
        image.ScanningSequence = "SE"
        image.SequenceName = "*tse2d1_16"
        image.RepetitionTime, image.EchoTime = 4000, 100
        image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        image.PixelSpacing, image.SliceThickness = [0.5, 0.5], 3
        for position in (0, 3, 6):
            member = deepcopy(image)
            member.SOPInstanceUID = generate_uid()
            member.ImagePositionPatient = [0, 0, position]
            storage.receive(member, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")
        with open_worker(tmp_path, (current,)) as worker:
            assert worker.run_once(Event()) == 0
            assert worker.history.get_protocol(previous_uid).sha256 == legacy.sha256
            assert worker.history.get_protocol(str(image.StudyInstanceUID)).sha256 == current.sha256
        view = MonitorReader(tmp_path).read(str(image.StudyInstanceUID))
        assert view.received == 3 and view.pending == 0 and not view.stale
        assert any(row.evaluations for row in view.rows)
    finally:
        storage.close()


def test_missing_database_is_not_created(tmp_path):
    root = tmp_path / "does-not-exist"
    snapshot = MonitorReader(root).read()
    assert snapshot.status == "GRAY" and not snapshot.studies
    assert not root.exists()


def test_live_completion_obeys_group_and_fallback_requirements():
    protocol = {
        "sequences": [
            {"sequence_id": "AX", "order": 1, "required": True, "group": "T2W"},
            {"sequence_id": "COR", "order": 2, "required": False, "group": "T2W"},
            {"sequence_id": "DWI", "order": 3, "required": True, "group": "DWI"},
            {"sequence_id": "RESOLVE", "order": 4, "required": False, "group": "DWI"},
        ],
        "study_rules": {
            "min_valid_groups": [{"rule_id": "planes", "group": "T2W", "min_valid_count": 2}],
            "fallback_groups": [
                {"rule_id": "dwi", "fallback_sequence_ids": ["RESOLVE"]}
            ],
        },
    }
    axial = MonitorRow("seq:AX", "1", "Axial", "GREEN")
    coronal = MonitorRow("seq:COR", "2", "Coronal", "GREEN")
    dwi = MonitorRow("seq:DWI", "3", "DWI", "ORANGE", reason="fallback_sequence_used")
    fallback = MonitorRow("seq:RESOLVE", "4", "RESOLVE", "GREEN")
    group = {"rule_id": "planes", "details": {"valid_sequences": ["AX", "COR"]}}
    substitution = {
        "rule_id": "dwi",
        "reason": "fallback_sequence_used",
        "status": "ORANGE",
        "details": {"primary_missing": ["DWI"], "primary_bad": [], "fallback_ok": ["RESOLVE"]},
    }
    payload = {"finalized": True, "study_checks": [group, substitution]}
    rows = (axial, coronal, dwi, fallback)
    verdict = examination_completion(protocol, payload, rows, stale=False, failed=0)
    assert verdict.state == "done" and any("RESOLVE" in detail for detail in verdict.details)
    assert examination_completion(protocol, payload, rows, stale=False, failed=1).state == (
        "verification_pending"
    )

    group["details"]["valid_sequences"].remove("COR")
    missing = replace(coronal, status="GRAY", reason="optional_sequence_absent")
    rows = (axial, missing, dwi, fallback)
    verdict = examination_completion(protocol, payload, rows, stale=False, failed=0)
    assert verdict.state == "incomplete"
    assert any("1" in line and "Coronal" in line for line in verdict.details)
    ambiguous = replace(missing, reason="identification_uncertain")
    rows = (axial, ambiguous, dwi, fallback)
    verdict = examination_completion(protocol, payload, rows, stale=False, failed=0)
    assert verdict.state == "verification_pending"

    partial = replace(
        missing,
        reason="received_all_repeats_retained",
        evaluations=(
            {
                "sequence_id": "COR",
                "completion_details": {"actual_members": 2, "policy": {"min_members": 5}},
            },
        ),
    )
    verdict = examination_completion(
        protocol, payload, (axial, partial, dwi, fallback), stale=False, failed=0
    )
    assert verdict.state == "incomplete"


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
