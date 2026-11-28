"""Evaluate the supplied references locally and compare annotated outcomes."""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import CompletionEvidence, evaluate_study
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.protocols.manager import load_protocol
from pydicom import dcmread
from pydicom.uid import MRImageStorage


def report_key(number, description, name):
    return str(number), description, name


def documented_change(annotation, csv_row):
    text = " ".join(annotation.get("changes") or ()).lower()
    return text or (csv_row.get("What we changed") or "").lower()


def reference_scope(annotation, csv_row):
    if csv_row.get("Image problem") or annotation.get("artefacts"):
        return "image_outside_scope"
    text = documented_change(annotation, csv_row)
    if text == "2nd copy, same protocol, in range":
        return None
    if (
        csv_row["Type"] == "DWI"
        and "pixel spacing" in text
        and not any(signal in text for signal in ("thickness", "b-value", "bvalue", "b value"))
    ):
        return "change_outside_scope"
    if (
        text
        and text != "-"
        and not any(
            signal in text
            for signal in ("thickness", "spacing", "fov", "b-value", "bvalue", "b value")
        )
    ):
        return "change_outside_scope"
    return None


def expected_fields(annotation, row):
    text = documented_change(annotation, row)
    fields = set()
    if "thickness" in text:
        fields.add("SliceThickness")
    if "b-value" in text or "bvalue" in text or "b value" in text:
        fields.add("PrivateDiffusionBValue")
    if "pixel spacing" in text:
        fields.add("PixelSpacing")
    if "fov" in text:
        fields.add("FieldOfView")
    if "gap" in text:
        fields.add("SliceGap")
    return fields


def causes_agree(expected, detected):
    detected = set(detected)
    if detected & {"PhaseResolution", "FrequencyResolution"}:
        detected.add("PixelSpacing")
    return expected <= detected


def pair_series(candidates, trusted, known_uids, peer_count):
    if peer_count > 1:
        return None, "ambiguous"
    if trusted in known_uids:
        return (trusted, None) if trusted in candidates else (None, "uid_conflict")
    if len(candidates) == 1:
        return next(iter(candidates)), None
    return None, "ambiguous" if candidates else "unmatched"


def compare_case(archive, folder, names, snapshot, report_dir=None):
    records = []
    profile = snapshot.protocol.prostate_checks
    by_name = defaultdict(set)
    by_sop = {}
    for name in names:
        with archive.open(name) as stream:
            dataset = dcmread(stream, stop_before_pixels=True)
        if dataset.SOPClassUID != MRImageStorage:
            continue
        uid = str(dataset.SeriesInstanceUID)
        by_sop[str(dataset.SOPInstanceUID)] = uid
        key = report_key(
            dataset.get("SeriesNumber"),
            dataset.get("SeriesDescription"),
            dataset.get("SequenceName"),
        )
        by_name[key].add(uid)
        records.extend(parse_dataset(dataset, prostate_checks=profile))
    groups = group_acquisitions(tuple(records))
    evaluation = evaluate_study(
        groups, snapshot, finalization=CompletionEvidence(source="reference", reference=folder)
    )
    by_series = defaultdict(list)
    group_series = {group.key: group.members[0].known("SeriesInstanceUID") for group in groups}
    for result in evaluation.acquisitions:
        by_series[group_series[result.group_key]].append(result)

    def report_text(name):
        if report_dir is not None:
            return (report_dir / name).read_text(encoding="utf-8-sig")
        return archive.read(f"{folder}/{name}").decode("utf-8-sig")

    rows = list(csv.DictReader(report_text("report.csv").splitlines(), delimiter=";"))
    report = json.loads(report_text("report.json"))
    if report["folder"] != folder:
        raise ValueError("Reference report belongs to a different measurement")
    annotations = defaultdict(list)
    for uid, item in report.get("changes", {}).items():
        key = report_key(item["series_number"], item["series_description"], item["sequence_name"])
        annotations[key].append((uid, item))
    reference_counts = Counter(
        report_key(row["SN"], row["SeriesDescription"], row["SeqName"]) for row in rows
    )
    details = []
    for row in rows:
        if row["Type"] not in {"T2W", "DWI"}:
            continue
        key = report_key(row["SN"], row["SeriesDescription"], row["SeqName"])
        peers = [
            (uid, item)
            for uid, item in annotations[key]
            if str(item.get("copy", 0)) == row.get("Copy", "0")
        ]
        ref_uid, annotation = peers[0] if len(peers) == 1 else (None, {})
        candidates = by_name[key]
        trusted = by_sop.get(ref_uid, ref_uid)
        series_uid, pairing_error = pair_series(candidates, trusted, by_series, len(peers))
        if reference_counts[key] > 1 and trusted not in by_series:
            series_uid, pairing_error = None, "ambiguous"
        predicted = by_series.get(series_uid, [])
        expected = row.get("Expected pirads_param") or annotation.get("expected_pirads_param")
        scope = reference_scope(annotation, row)
        checks = sorted(
            {
                (check.field or check.rule_id, check.reason)
                for result in predicted
                for check in result.checks
                if check.status == "RED"
            }
        )
        expected_reasons = expected_fields(annotation, row)
        detected_reasons = {field for field, _ in checks}
        expected_role = {
            "low_b": "DWI_LOW",
            "intermediate_b": "DWI_INTERMEDIATE",
            "adc": "DWI_ADC",
            "high_b_calc": "DWI_HIGH",
        }.get(row["Role"])
        if row["Type"] == "T2W":
            plane = re.search(r"(?:^|_)(sag|cor|tra)(?:_|$)", row["SeriesDescription"].lower())
            if plane:
                expected_role = "T2_" + plane[1].upper()
        if pairing_error:
            outcome = pairing_error
        elif not predicted or any(result.sequence_id is None for result in predicted):
            outcome = "unmatched"
        elif expected_role and any(result.sequence_id != expected_role for result in predicted):
            outcome = "mismatch"
        elif expected not in {"adequate", "inadequate"}:
            outcome = "no_expectation"
        elif scope:
            outcome = scope
        elif any(result.status == "RED" for result in predicted):
            agrees = expected == "inadequate" and causes_agree(expected_reasons, detected_reasons)
            outcome = "match" if agrees else "mismatch"
        elif any(result.status == "GRAY" for result in predicted):
            outcome = "technical_unknown"
        else:
            outcome = "match" if expected == "adequate" else "mismatch"
        details.append(
            {
                "series_number": key[0],
                "series_description": key[1],
                "sequence_name": key[2],
                "copy": row.get("Copy"),
                "matched_series_uid": series_uid,
                "expected": expected,
                "expected_role": expected_role,
                "identified_roles": sorted(
                    {result.sequence_id or "unknown" for result in predicted}
                ),
                "documented_change": documented_change(annotation, row),
                "uncovered_criterion": (
                    row.get("Image problem") or documented_change(annotation, row) or scope
                )
                if scope
                else None,
                "outcome": outcome,
                "status": sorted({result.status for result in predicted}),
                "critical_findings": checks,
                "failure_details": [
                    json.loads(detail)
                    for detail in sorted(
                        {
                            json.dumps(
                                {
                                    "field": check.field,
                                    "reason": check.reason,
                                    "actual": check.actual,
                                    "expected": check.expected,
                                    "unit": check.unit,
                                },
                                sort_keys=True,
                            )
                            for result in predicted
                            for check in result.checks
                            if check.status == "RED"
                        }
                    )
                ],
                "expected_failure_fields": sorted(expected_reasons) if not scope else [],
                "unverified_findings": sorted(
                    {
                        (check.field or check.rule_id, check.reason)
                        for result in predicted
                        for check in result.checks
                        if check.status == "GRAY"
                    }
                ),
            }
        )
    dropped = []
    for entry in report.get("dropped_series", ()):
        match = re.fullmatch(r"SN(\d+) (.+) \[(.+)\]", entry)
        if match:
            dropped.append(
                {
                    "reference": entry,
                    "outcome": "missing_confirmed"
                    if not by_name[match.groups()]
                    else "unexpectedly_present",
                }
            )
        else:
            dropped.append({"reference": entry, "outcome": "unparsed"})
    scan_state = report["expected"]["scan_state"]
    expected_status = {"ok": "GREEN", "inadequate": "RED"}.get(scan_state)
    return {
        "case": folder,
        "sequences": details,
        "dropped_series": dropped,
        "study_status": evaluation.status,
        "reference_scan_state": scan_state,
        "study_outcome": (
            "no_expectation"
            if expected_status is None
            else "match"
            if evaluation.status == expected_status
            else "mismatch"
        ),
        "study_checks": [
            {
                "field": check.field,
                "status": check.status,
                "reason": check.reason,
                "actual": check.actual,
            }
            for check in evaluation.checks
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = load_protocol(Path("config/protocols/mou_prostate.yaml"))
    cases = []
    for path in args.archives:
        with ZipFile(path) as archive:
            case_files = defaultdict(list)
            for name in archive.namelist():
                if "/DATA/DICOM/" in name and not name.endswith("/"):
                    case_files[name.split("/", 1)[0]].append(name)
            for folder, names in sorted(case_files.items()):
                report_dir = Path("data/mereni") if folder == "DICOMDIR_MR50000" else None
                cases.append(compare_case(archive, folder, names, snapshot, report_dir))
    outcomes = Counter(row["outcome"] for case in cases for row in case["sequences"])
    missing = Counter(row["outcome"] for case in cases for row in case["dropped_series"])
    output = {
        "case_count": len(cases),
        "outcomes": {
            name: outcomes[name]
            for name in (
                "match",
                "mismatch",
                "ambiguous",
                "uid_conflict",
                "unmatched",
                "no_expectation",
                "change_outside_scope",
                "image_outside_scope",
                "technical_unknown",
            )
        },
        "study_outcomes": dict(Counter(case["study_outcome"] for case in cases)),
        "dropped_series": dict(missing),
        "cases": cases,
    }
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "cases"}))


if __name__ == "__main__":
    main()
