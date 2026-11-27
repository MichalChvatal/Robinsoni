"""Local-only, explicit integration check against a user-supplied DICOM ZIP.

Automated pytest fixtures remain synthetic. This script streams existing data
over loopback, checks persistence and a restart, and prints counts without PHI.
Run from the repository root after installing the project.
"""

import argparse
import io
import json
import socket
import tempfile
from collections import Counter
from pathlib import Path
from threading import Event
from zipfile import ZipFile

from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.database.models import AnalysisJob, DicomInstance, ReceptionEvent
from mr_quality_monitor.dicom.listener import Listener
from mr_quality_monitor.dicom.storage import STORAGE_CLASSES, TRANSFER_SYNTAXES
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.protocols.manager import load_protocol
from mr_quality_monitor.services.analysis_worker import open_worker
from pydicom import dcmread
from pydicom.uid import MRImageStorage
from pynetdicom import AE
from sqlalchemy import func, select


def available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--workdir", type=Path, default=Path("test-results"))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--protocol", type=Path, help="Also verify integrated DEMO analysis")
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dicom-smoke-", dir=args.workdir) as temporary:
        config = ReceiverConfig(
            storage_root=Path(temporary), port=available_port(), minimum_free_bytes=0
        )
        receiver = Listener(config)
        statuses = Counter()
        sop_classes = Counter()
        ae = AE(ae_title="MR_TEST")
        for sop in STORAGE_CLASSES:
            ae.add_requested_context(sop, TRANSFER_SYNTAXES)
        receiver.start()
        try:
            assoc = ae.associate("127.0.0.1", config.port, ae_title=config.ae_title)
            if not assoc.is_established:
                raise RuntimeError("Loopback association failed")
            first = None
            try:
                with ZipFile(args.archive) as archive:
                    members = sorted(
                        name
                        for name in archive.namelist()
                        if "/DATA/DICOM/" in name.replace("\\", "/") and not name.endswith("/")
                    )
                    if not members:
                        raise RuntimeError("Archive has no DATA/DICOM members")
                    for name in members:
                        dataset = dcmread(io.BytesIO(archive.read(name)))
                        if first is None:
                            first = dataset
                        response = assoc.send_c_store(dataset)
                        status = getattr(response, "Status", None)
                        statuses[str(status)] += 1
                        sop_classes[str(dataset.SOPClassUID)] += 1
                        if status != 0:
                            raise RuntimeError(f"Loopback store failed with status {status}")
                if assoc.send_c_store(first).Status != 0:
                    raise RuntimeError("Duplicate store failed")
            finally:
                assoc.release()
            receiver.stop()
            receiver.start()
            assoc = ae.associate("127.0.0.1", config.port, ae_title=config.ae_title)
            try:
                if not assoc.is_established or assoc.send_c_store(first).Status != 0:
                    raise RuntimeError("Post-restart duplicate failed")
            finally:
                assoc.release()
            with receiver.storage.sessions() as session:
                counts = {
                    name: session.scalar(select(func.count()).select_from(model))
                    for name, model in (
                        ("instances", DicomInstance),
                        ("jobs", AnalysisJob),
                        ("receptions", ReceptionEvent),
                    )
                }
                counts["series"] = session.scalar(
                    select(func.count(func.distinct(DicomInstance.series_uid)))
                )
            if counts["instances"] != len(members) or counts["jobs"] != len(members):
                raise RuntimeError("Unexpected instance or queue count")
            if counts["receptions"] != len(members) + 2:
                raise RuntimeError("Unexpected reception count")
            report = {
                "result": "passed",
                "input_objects": len(members),
                "store_statuses_decimal": dict(statuses),
                "sop_classes": dict(sop_classes),
                "counts": counts,
                "restart_verified": True,
                "duplicates_verified": 2,
            }
            if args.protocol:
                with open_worker(config.storage_root, load_protocol(args.protocol)) as worker:
                    if worker.run_once(Event()):
                        raise RuntimeError("Analysis encountered failed inputs")
                    with worker.sessions() as session:
                        states = dict(
                            session.execute(
                                select(AnalysisJob.state, func.count()).group_by(AnalysisJob.state)
                            ).all()
                        )
                        study_uids = list(
                            session.scalars(select(DicomInstance.study_uid).distinct())
                        )
                mr_count = sop_classes[str(MRImageStorage)]
                if states.get("done", 0) != mr_count or states.get("ignored", 0) != (
                    len(members) - mr_count
                ):
                    raise RuntimeError("Unexpected analysis queue outcomes")
                reader = MonitorReader(config.storage_root)
                snapshots = [reader.read(uid) for uid in study_uids]
                if any(
                    study.pending or (study.branch_id is not None and study.stale)
                    for study in snapshots
                ):
                    raise RuntimeError("GUI snapshot has unprocessed MR data")
                report["analysis_jobs"] = states
                report["gui_studies"] = len(snapshots)
        finally:
            receiver.stop()
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
