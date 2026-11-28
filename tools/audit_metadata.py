"""Header-only local ZIP audit; report contains counts, never patient values or UIDs."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from pydicom import dcmread


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    states = defaultdict(Counter)
    cases = set()
    vendors = Counter()
    object_count = 0
    record_count = 0
    group_count = 0
    issues = Counter()
    studies, series = set(), set()
    current_case = None
    case_records = []

    def finish_case() -> None:
        nonlocal group_count
        groups = group_acquisitions(tuple(case_records))
        group_count += len(groups)
        for group in groups:
            issues.update(group.issues)
        case_records.clear()

    with ZipFile(args.archive) as archive:
        for name in sorted(archive.namelist()):
            normalized = name.replace("\\", "/")
            if "/DATA/DICOM/" not in normalized or normalized.endswith("/"):
                continue
            with archive.open(name) as stream:
                dataset = dcmread(stream, stop_before_pixels=True)
            # This test is also an assertion that stop_before_pixels was honored.
            if "PixelData" in dataset:
                raise RuntimeError("Unexpected pixel allocation during header-only audit")
            records = parse_dataset(dataset)
            object_count += 1
            case = normalized.split("/DATA/DICOM/", 1)[0]
            if current_case is not None and current_case != case:
                finish_case()
            current_case = case
            cases.add(case)
            case_records.extend(records)
            record_count += len(records)
            for record in records:
                studies.add(record.known("StudyInstanceUID"))
                series.add(record.known("SeriesInstanceUID"))
                vendors[record.vendor_parser] += 1
                for field, value in record.fields.items():
                    states[field][value.state] += 1
    if not object_count:
        raise RuntimeError("Archive contains no DATA/DICOM objects")
    finish_case()
    report = {
        "result": "passed",
        "objects": object_count,
        "records": record_count,
        "cases": len(cases),
        "studies": len(studies - {None}),
        "physical_series": len(series - {None}),
        "candidate_groups": group_count,
        "group_issues": dict(sorted(issues.items())),
        "vendor_parsers": dict(vendors),
        "field_states": {key: dict(value) for key, value in sorted(states.items())},
        "pixels_decoded": False,
        "clinical_quality_evaluated": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({key: value for key, value in report.items() if key != "field_states"}, indent=2)
    )


if __name__ == "__main__":
    main()
