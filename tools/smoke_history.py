"""Local intake -> metadata -> DEMO validation -> DB/restart check for a single-case ZIP."""

import argparse
import io
import json
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import evaluate_study
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.models import (
    DicomInstance,
    LogicalAcquisition,
    MetadataSnapshot,
    ValidationIssue,
    ValidationResult,
    ValidationRun,
)
from mr_quality_monitor.database.repository import HistoryRepository
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.process_lock import ReceiverLock
from mr_quality_monitor.protocols.manager import load_protocol
from pydicom import dcmread
from sqlalchemy import func, select


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--protocol", type=Path, default=Path("config/protocols/demo_mr.yaml"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    snapshot = load_protocol(args.protocol)
    with tempfile.TemporaryDirectory(prefix="history-smoke-", dir=args.report.parent) as temporary:
        root = Path(temporary)
        lock = ReceiverLock(root)
        lock.acquire()
        store = Storage(root, minimum_free_bytes=0)
        try:
            records = []
            with ZipFile(args.archive) as archive:
                for name in sorted(archive.namelist()):
                    if "/DATA/DICOM/" not in name.replace("\\", "/") or name.endswith("/"):
                        continue
                    dataset = dcmread(io.BytesIO(archive.read(name)))
                    store.receive(
                        dataset, str(dataset.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
                    )
                    records.extend(parse_dataset(dataset))
            if not records:
                raise ValueError("Archive contains no DATA/DICOM objects")
            study_uids = {record.known("StudyInstanceUID") for record in records}
            if len(study_uids) != 1 or None in study_uids:
                raise ValueError("This smoke test accepts one study only")
            study_uid = next(iter(study_uids))
            repository = HistoryRepository(store.sessions)
            repository.begin_study(study_uid, snapshot, actor="offline-demo-test")
            groups = group_acquisitions(tuple(records))
            run = repository.save_evaluation(
                study_uid, groups, request_id="smoke-run", actor="offline-demo-test"
            )
            if (
                repository.save_evaluation(
                    study_uid, groups, request_id="smoke-run", actor="offline-demo-test"
                )
                != run
            ):
                raise AssertionError("Repeated request created another run")
            original = repository.load_evaluation(run)
            with store.sessions() as session:
                acquisitions = list(session.scalars(select(LogicalAcquisition.sha256)))
                counts = {
                    name: session.scalar(select(func.count()).select_from(model))
                    for name, model in (
                        ("instances", DicomInstance),
                        ("metadata_snapshots", MetadataSnapshot),
                        ("acquisitions", LogicalAcquisition),
                        ("validation_runs", ValidationRun),
                        ("validation_results", ValidationResult),
                        ("validation_issues", ValidationIssue),
                    )
                }
        finally:
            store.close()
            lock.release()
        engine, sessions = open_database(root / "intake.sqlite3")
        try:
            restored = HistoryRepository(sessions)
            if (
                restored.get_protocol(study_uid) != snapshot
                or restored.load_evaluation(run) != original
            ):
                raise AssertionError("Restart changed persisted history")
            reloaded_groups = tuple(restored.load_group(sha) for sha in acquisitions)
            replay = evaluate_study(
                reloaded_groups,
                snapshot,
                evaluated_at=datetime.fromisoformat(original["evaluated_at"]),
            )
            expected_hashes = {
                item["group_key"]: item["input_sha256"] for item in original["acquisitions"]
            }
            actual_hashes = {item.group_key: item.input_sha256 for item in replay.acquisitions}
            if actual_hashes != expected_hashes or replay.status != original["status"]:
                raise AssertionError("Persisted inputs did not reproduce the evaluation")
        finally:
            engine.dispose()
    report = {
        "technical_run": "passed",
        "mode": "DEMO",
        "clinical_quality_evaluated": False,
        "counts": counts,
        "restart_verified": True,
        "request_retry_idempotent": True,
        "replay_input_hashes_verified": True,
        "protocol_sha256": snapshot.sha256,
    }
    text = json.dumps(report, indent=2)
    args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
