"""Offline DEMO validation; reports counts, never clinical conclusions or patient values."""

import argparse
import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import evaluate_study
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.protocols.manager import load_protocol
from pydicom import dcmread


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--protocol", type=Path, default=Path("tests/fixtures/protocols/demo_mr.yaml")
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    snapshot = load_protocol(args.protocol)
    object_count = 0
    case_count = 0
    group_count = 0
    current_case = None
    records = []
    statuses, identifications, study_statuses = Counter(), Counter(), Counter()
    required_presence = Counter()

    def finish_case() -> None:
        nonlocal case_count, group_count
        groups = group_acquisitions(tuple(records))
        # Reading the end of an archive does not prove scan/acquisition completion.
        evaluation = evaluate_study(groups, snapshot)
        case_count += 1
        group_count += len(groups)
        study_statuses[evaluation.status] += 1
        for item in evaluation.acquisitions:
            statuses[item.status] += 1
            identifications[item.identification.state] += 1
        for item in evaluation.sequences:
            if item.required:
                required_presence[item.reason] += 1
        records.clear()

    with ZipFile(args.archive) as archive:
        for name in sorted(archive.namelist()):
            normalized = name.replace("\\", "/")
            if "/DATA/DICOM/" not in normalized or normalized.endswith("/"):
                continue
            case = normalized.split("/DATA/DICOM/", 1)[0]
            if current_case is not None and current_case != case:
                finish_case()
            current_case = case
            with archive.open(name) as stream:
                records.extend(parse_dataset(dcmread(stream, stop_before_pixels=True)))
            object_count += 1
    if not records:
        raise ValueError("No DATA/DICOM objects found")
    finish_case()
    report = {
        "technical_run": "passed",
        "mode": "DEMO",
        "clinical_quality_evaluated": False,
        "protocol_id": snapshot.protocol.protocol_id,
        "protocol_version": snapshot.protocol.version,
        "protocol_sha256": snapshot.sha256,
        "objects": object_count,
        "cases": case_count,
        "groups": group_count,
        "acquisition_statuses": dict(statuses),
        "identifications": dict(identifications),
        "study_statuses": dict(study_statuses),
        "required_sequence_presence": dict(required_presence),
        "acquisitions_explicitly_completed": False,
        "studies_finalized": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2)
    args.report.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
