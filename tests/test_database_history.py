import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import CompletionEvidence
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.models import (
    HistoryEvent,
    LogicalAcquisition,
    MetadataSnapshot,
    Patient,
    ProtocolVersion,
    Series,
    Study,
    ValidationIssue,
    ValidationResult,
    ValidationRun,
)
from mr_quality_monitor.database.repository import HistoryRepository, checked_content
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.protocols.manager import ProtocolSnapshot
from mr_quality_monitor.protocols.models import Protocol
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError

PROOF = CompletionEvidence(source="synthetic-test", reference="explicit-test-confirmation")


def groups(images, include_identifiers=False):
    return group_acquisitions(
        tuple(parse_dataset(image, include_identifiers=include_identifiers)[0] for image in images)
    )


@pytest.fixture
def history_case(tmp_path, image, protocol_data):
    store = Storage(tmp_path, minimum_free_bytes=0)
    image.SeriesDescription = "t2_synthetic"
    image.RepetitionTime, image.EchoTime = 4000, 100
    image.PixelSpacing, image.SliceThickness = [0.5, 0.5], 3
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    images = []
    for z in (0, 3, 6):
        item = deepcopy(image)
        item.SOPInstanceUID = generate_uid()
        item.ImagePositionPatient = [0, 0, z]
        store.receive(item, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")
        images.append(item)
    snapshot = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    repo = HistoryRepository(store.sessions)
    uid = str(image.StudyInstanceUID)
    branch = repo.begin_study(uid, snapshot, actor="test-operator")
    yield SimpleNamespace(
        store=store,
        repo=repo,
        images=images,
        uid=uid,
        snapshot=snapshot,
        branch=branch,
        root=tmp_path,
    )
    store.close()


def count(case, model):
    with case.store.sessions() as session:
        return session.scalar(select(func.count()).select_from(model))


def save(case, request="run-1", **kwargs):
    return case.repo.save_evaluation(
        case.uid, groups(case.images), request_id=request, actor="test-operator", **kwargs
    )


def test_persists_full_snapshot_and_reloads_after_restart(history_case):
    case = history_case
    run = save(case, finalization=PROOF)
    payload = case.repo.load_evaluation(run)
    assert payload["status"] == "GREEN"
    assert payload["protocol_sha256"] == case.snapshot.sha256
    assert payload["finalization"]["source"] == "synthetic-test"
    assert count(case, Patient) == 1
    assert count(case, Series) == 1
    assert count(case, MetadataSnapshot) == 3
    assert count(case, ValidationResult) == 2
    with case.store.sessions() as session:
        acquisition = session.scalar(select(LogicalAcquisition)).sha256
    restored = case.repo.load_group(acquisition)
    assert restored.projected_positions_mm == (0, 3, 6)
    assert {member.known("SOPInstanceUID") for member in restored.members} == {
        str(image.SOPInstanceUID) for image in case.images
    }
    assert not restored.complete
    case.store.close()
    engine, sessions = open_database(case.root / "intake.sqlite3")
    try:
        reopened = HistoryRepository(sessions)
        assert reopened.get_protocol(case.uid) == case.snapshot
        assert reopened.load_evaluation(run) == payload
        assert reopened.study_summary(case.uid)["state"] == "closed"
    finally:
        engine.dispose()


def test_retry_is_idempotent_and_conflicting_request_is_rejected(history_case):
    case = history_case
    first = save(case)
    assert save(case) == first
    assert count(case, ValidationRun) == 1
    assert count(case, MetadataSnapshot) == 3
    case.images[0].RepetitionTime = 6000
    with pytest.raises(ValueError, match="Request ID"):
        save(case)
    assert count(case, ValidationRun) == 1


def test_new_run_retains_old_result_and_orders_by_saved_sequence(history_case):
    case = history_case
    first = save(case, evaluated_at=datetime(2026, 11, 27, tzinfo=UTC))
    case.images[0].RepetitionTime = 6000
    second = save(case, "run-2", evaluated_at=datetime(2026, 1, 1, tzinfo=UTC))
    history = case.repo.history(case.uid)
    assert [item["run_id"] for item in history] == [first, second]
    assert history[1]["supersedes_id"] == first
    assert history[1]["sequence_number"] == 2
    assert case.repo.load_evaluation(first)["acquisitions"][0]["metadata_status"] == "GREEN"
    assert case.repo.load_evaluation(second)["status"] == "RED"
    assert count(case, MetadataSnapshot) == 4
    assert count(case, ValidationIssue) > 0
    assert case.repo.study_summary(case.uid)["latest_run_id"] == second


def test_protocol_is_pinned_and_reassessment_is_separate(history_case, protocol_data):
    case = history_case
    original = save(case)
    protocol_data["version"] = "2-demo"
    protocol_data["sequences"][0]["parameters"][0]["constraint"]["maximum"] = 4100
    new = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    with pytest.raises(ValueError, match="pinned"):
        case.repo.begin_study(case.uid, new, actor="test")
    branch = case.repo.create_branch(
        case.uid, new, branch_label="recheck", actor="test", reason="Updated demo requirements"
    )
    alternate = case.repo.save_evaluation(
        case.uid, groups(case.images), request_id="alternate", actor="test", branch_id=branch
    )
    assert case.repo.get_protocol(case.uid) == case.snapshot
    assert case.repo.get_protocol(case.uid, branch) == new
    assert case.repo.history(case.uid)[0]["run_id"] == original
    assert case.repo.history(case.uid, branch)[0]["run_id"] == alternate
    assert count(case, ProtocolVersion) == 2


def test_identifiers_are_not_copied_into_history(history_case):
    case = history_case
    with pytest.raises(ValueError, match="suppressed"):
        case.repo.save_evaluation(
            case.uid,
            groups(case.images, include_identifiers=True),
            request_id="unsafe",
            actor="test",
        )
    assert count(case, MetadataSnapshot) == 0
    assert count(case, ValidationRun) == 0


def test_missing_received_instance_rolls_back_everything(history_case):
    case = history_case
    case.images[1].SOPInstanceUID = generate_uid()
    with pytest.raises(ValueError, match="durably received"):
        save(case)
    assert count(case, MetadataSnapshot) == 0
    assert count(case, LogicalAcquisition) == 0
    assert count(case, ValidationRun) == 0
    with case.store.sessions() as session:
        assert session.get(Study, case.uid).revision == 0


def test_failed_final_commit_rolls_back_input_results_and_issues(history_case):
    case = history_case

    def fail(session):
        if any(
            isinstance(item, HistoryEvent) and item.code == "validation_run_saved"
            for item in session.new
        ):
            raise OSError("synthetic commit failure")

    event.listen(case.store.sessions, "before_commit", fail)
    try:
        with pytest.raises(OSError, match="synthetic commit"):
            save(case)
    finally:
        event.remove(case.store.sessions, "before_commit", fail)
    assert count(case, MetadataSnapshot) == 0
    assert count(case, ValidationRun) == 0
    assert count(case, ValidationResult) == 0
    assert save(case)


def test_concurrent_same_request_creates_one_run(history_case):
    case = history_case
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: save(case), range(6)))
    assert len(set(runs)) == 1
    assert count(case, ValidationRun) == 1
    assert count(case, MetadataSnapshot) == 3


@pytest.mark.parametrize(
    "table",
    [
        "patients",
        "protocol_versions",
        "sequence_definitions",
        "analysis_branches",
        "metadata_snapshots",
        "logical_acquisitions",
        "acquisition_members",
        "validation_runs",
        "validation_results",
        "validation_issues",
        "history_events",
    ],
)
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_history_is_append_only_at_database_level(history_case, table, operation):
    case = history_case
    case.images[0].RepetitionTime = 6000
    save(case)
    # Table names come only from this fixed parametrized list.
    with pytest.raises(IntegrityError, match="append-only history"):
        with case.store.engine.begin() as connection:
            if operation == "DELETE":
                connection.exec_driver_sql(f"DELETE FROM {table}")
            else:
                columns = connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
                name = columns[0][1]
                connection.exec_driver_sql(f"UPDATE {table} SET {name}={name}")


def test_late_received_object_marks_summary_stale_and_reopens(history_case):
    case = history_case
    old = save(case, finalization=PROOF)
    assert case.repo.study_summary(case.uid)["effective_status"] == "GREEN"
    late = deepcopy(case.images[-1])
    late.SOPInstanceUID = generate_uid()
    late.ImagePositionPatient = [0, 0, 9]
    case.store.receive(late, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")
    summary = case.repo.study_summary(case.uid)
    assert summary["stale"] and summary["effective_status"] == "GRAY"
    assert summary["state"] == "active" and summary["recorded_status"] == "GREEN"
    assert summary["unindexed_instances"] == 1
    case.images.append(late)
    save(case, "late")
    assert case.repo.study_summary(case.uid)["state"] == "active"
    assert case.repo.load_evaluation(old)["status"] == "GREEN"


def test_cannot_finalize_unprocessed_received_data(history_case):
    case = history_case
    with pytest.raises(ValueError, match="every received"):
        case.repo.save_evaluation(
            case.uid, groups(case.images[:2]), request_id="subset", actor="test", finalization=PROOF
        )
    assert count(case, ValidationRun) == 0


def test_study_binding_cannot_be_updated_directly(history_case):
    with pytest.raises(IntegrityError, match="immutable study binding"):
        with history_case.store.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE studies SET protocol_sha256=protocol_sha256")


def test_checksum_mismatch_detected_on_read():
    with pytest.raises(ValueError, match="checksum"):
        checked_content('{"changed": true}', "0" * 64)


def test_metadata_snapshot_roundtrip(history_case):
    from mr_quality_monitor.analysis.metadata_normalizer import MetadataRecord

    record = parse_dataset(history_case.images[0])[0]
    assert MetadataRecord.from_dict(json.loads(json.dumps(record.to_dict()))) == record


def test_two_studies_do_not_merge_patient_identity(history_case):
    case = history_case
    other_uid = generate_uid()
    case.repo.begin_study(other_uid, case.snapshot, actor="test")
    assert count(case, Patient) == 2
    with case.store.sessions() as session:
        assert session.get(Study, case.uid).patient_id != session.get(Study, other_uid).patient_id


def test_pending_late_data_does_not_hide_recorded_critical_issue(history_case):
    case = history_case
    case.images[0].RepetitionTime = 6000
    save(case)
    late = deepcopy(case.images[-1])
    late.SOPInstanceUID = generate_uid()
    late.ImagePositionPatient = [0, 0, 9]
    case.store.receive(late, str(ExplicitVRLittleEndian), "MR_TEST", "127.0.0.1")
    summary = case.repo.study_summary(case.uid)
    assert summary["stale"] and summary["effective_status"] == "RED"
