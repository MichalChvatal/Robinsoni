import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from mr_quality_monitor.analysis.sequence_identifier import identify
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import (
    CompletionEvidence,
    evaluate,
    evaluate_study,
)
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.protocols.manager import ProtocolSnapshot
from mr_quality_monitor.protocols.models import Protocol
from pydicom.uid import generate_uid

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
CONFIRMED = CompletionEvidence(source="synthetic-test", reference="test-approval-1")


@pytest.fixture
def images(image):
    image.SeriesDescription = "t2_synthetic"
    image.ScanningSequence = "SE"
    image.RepetitionTime = 4000
    image.EchoTime = 100
    image.PixelSpacing = [0.5, 0.5]
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    image.SliceThickness = 3
    result = []
    for position in (0, 3, 6):
        item = deepcopy(image)
        item.SOPInstanceUID = generate_uid()
        item.ImagePositionPatient = [0, 0, position]
        result.append(item)
    return result


def snapshot(data):
    return ProtocolSnapshot.create(Protocol.model_validate(data))


def group(images):
    (result,) = group_acquisitions(tuple(parse_dataset(image)[0] for image in images))
    return result


def result(images, data, **kwargs):
    return evaluate(group(images), snapshot(data), evaluated_at=NOW, **kwargs)


def test_green_metadata_only_demo_has_details(images, protocol_data):
    evaluation = result(images, protocol_data)
    assert (
        evaluation.status == evaluation.metadata_status == evaluation.completeness_status == "GREEN"
    )
    assert evaluation.image_status == "GRAY" and not evaluation.image_required
    assert not evaluation.clinical_quality_evaluated
    assert evaluation.mode == "DEMO"
    assert len(evaluation.checks) == 6  # Both rules run against all three members.
    assert evaluation.checks[0].unit == "ms"
    assert evaluation.checks[0].paths == ("RepetitionTime",)
    assert evaluation.completion_details["actual_members"] == 3
    assert evaluation.protocol_sha256 == snapshot(protocol_data).sha256
    json.dumps(evaluation.to_dict(), allow_nan=False)


@pytest.mark.parametrize(
    "tr,expected",
    [(3500, "GREEN"), (4500, "GREEN"), (3200, "ORANGE"), (5000, "ORANGE"), (5001, "RED")],
)
def test_all_range_boundaries_and_warning_band(images, protocol_data, tr, expected):
    images[1].RepetitionTime = tr
    evaluation = result(images, protocol_data)
    assert evaluation.status == expected
    if expected != "GREEN":
        failure = next(check for check in evaluation.checks if check.status != "GREEN")
        assert failure.sop_uid == str(images[1].SOPInstanceUID)
        assert failure.actual == tr


def test_warning_severity_outside_target(images, protocol_data):
    images[0].EchoTime = 150
    # TE is a group dimension; construct a candidate from the same received series
    # to ensure every member is checked even if an upstream grouping policy changes.
    acquisition = group(images[1:])
    acquisition = replace(acquisition, members=tuple(parse_dataset(item)[0] for item in images))
    assert evaluate(acquisition, snapshot(protocol_data), evaluated_at=NOW).status == "ORANGE"


@pytest.mark.parametrize(
    "missing_policy,expected",
    [
        ("unknown", "GRAY"),
        ("critical", "RED"),
        ("warning", "ORANGE"),
    ],
)
def test_required_missing_metadata_policy(images, protocol_data, missing_policy, expected):
    del images[1].RepetitionTime
    protocol_data["sequences"][0]["parameters"][0]["on_missing"] = missing_policy
    assert result(images, protocol_data).status == expected


def test_optional_missing_metadata_does_not_fail(images, protocol_data):
    del images[1].RepetitionTime
    protocol_data["sequences"][0]["parameters"][0]["required"] = False
    assert result(images, protocol_data).status == "GREEN"


def test_image_required_stays_unknown_despite_good_metadata(images, protocol_data):
    protocol_data["sequences"][0]["image_required"] = True
    evaluation = result(images, protocol_data)
    assert evaluation.status == "GRAY"
    assert evaluation.metadata_status == "GREEN"
    assert "image_not_evaluated" in evaluation.reasons


def test_critical_failure_wins_over_incomplete_image_and_series(images, protocol_data):
    images[0].RepetitionTime = 6000
    protocol_data["sequences"][0]["image_required"] = True
    evaluation = result(images[:2], protocol_data)
    assert evaluation.status == "RED"
    assert evaluation.completeness_status == "GRAY"
    assert evaluation.image_status == "GRAY"


def test_pending_wins_over_warning_but_keeps_issue(images, protocol_data):
    images[0].RepetitionTime = 3200
    evaluation = result(images[:2], protocol_data)
    assert evaluation.status == "GRAY"
    assert any(check.status == "ORANGE" for check in evaluation.checks)


def test_explicit_completion_requires_evidence_not_group_flag(images, protocol_data):
    protocol_data["sequences"][0]["completion"] = {"mode": "explicit"}
    acquisition = replace(group(images), complete=True)
    assert evaluate(acquisition, snapshot(protocol_data)).status == "GRAY"
    assert evaluate(acquisition, snapshot(protocol_data), completion=CONFIRMED).status == "GREEN"


def test_unknown_and_ambiguous_identification(images, protocol_data):
    for image in images:
        image.SeriesDescription = "other"
    assert result(images, protocol_data).identification.state == "unknown"
    for image in images:
        image.SeriesDescription = "t2_synthetic"
    extra = deepcopy(protocol_data["sequences"][0])
    extra["sequence_id"], extra["order"] = "ALSO_T2", 2
    protocol_data["sequences"].append(extra)
    evaluation = result(images, protocol_data)
    assert evaluation.identification.state == "ambiguous"
    assert evaluation.sequence_id is None and evaluation.status == "GRAY"


def test_possible_candidate_blocks_false_unique_identification(images, protocol_data):
    extra = deepcopy(protocol_data["sequences"][0])
    extra["sequence_id"], extra["order"] = "POSSIBLE", 2
    extra["identifiers"][1] = {
        "field": "ProtocolName",
        "constraint": {"kind": "exact", "value": "missing"},
    }
    protocol_data["sequences"].append(extra)
    identification = identify(group(images), Protocol.model_validate(protocol_data))
    assert identification.state == "ambiguous"
    assert {candidate.decision for candidate in identification.candidates} == {"match", "unknown"}


def test_known_mismatch_excludes_candidate_with_other_missing_metadata(images, protocol_data):
    extra = deepcopy(protocol_data["sequences"][0])
    extra["sequence_id"], extra["order"] = "CT_ONLY", 2
    extra["identifiers"] = [
        {"field": "Modality", "constraint": {"kind": "exact", "value": "CT"}},
        {"field": "ProtocolName", "constraint": {"kind": "exact", "value": "missing"}},
    ]
    protocol_data["sequences"].append(extra)
    assert result(images, protocol_data).sequence_id == "T2"


def test_reproducible_result_and_changed_input_hash(images, protocol_data):
    first = result(images, protocol_data)
    assert result(images, protocol_data) == first
    images[0].RepetitionTime = 4100
    changed = result(images, protocol_data)
    assert changed.input_sha256 != first.input_sha256
    assert changed.protocol_sha256 == first.protocol_sha256


def test_geometry_policy_is_configurable(images, protocol_data):
    acquisition = replace(group(images), issues=("declared_spacing_mismatch",))
    assert evaluate(acquisition, snapshot(protocol_data)).status == "GRAY"
    protocol_data["sequences"][0]["geometry_severity"] = "critical"
    assert evaluate(acquisition, snapshot(protocol_data)).status == "RED"


def test_missing_sequence_pending_then_error_only_at_finalization(protocol_data):
    protocol = snapshot(protocol_data)
    assert evaluate_study((), protocol).status == "GRAY"
    finalized = evaluate_study((), protocol, finalization=CONFIRMED)
    assert finalized.status == "RED"
    assert finalized.sequences[0].reason == "required_sequence_absent_at_finalization"


def test_finalization_does_not_complete_each_acquisition(images, protocol_data):
    protocol_data["sequences"][0]["completion"] = {"mode": "explicit"}
    acquisition = group(images)
    protocol = snapshot(protocol_data)
    assert evaluate_study((acquisition,), protocol, finalization=CONFIRMED).status == "GRAY"
    evaluation = evaluate_study(
        (acquisition,), protocol, completions={acquisition.key: CONFIRMED}, finalization=CONFIRMED
    )
    assert evaluation.status == "GREEN"


def test_unknown_candidate_at_finalization_is_not_proven_missing(images, protocol_data):
    for image in images:
        del image.SeriesDescription
    evaluation = evaluate_study((group(images),), snapshot(protocol_data), finalization=CONFIRMED)
    assert evaluation.status == "GRAY"
    assert evaluation.sequences[0].reason == "identification_uncertain"


def test_optional_absent_sequence_does_not_prevent_green(images, protocol_data):
    extra = deepcopy(protocol_data["sequences"][0])
    extra.update(sequence_id="OPTIONAL", order=2, required=False)
    extra["identifiers"][1]["constraint"]["value"] = "absent"
    protocol_data["sequences"].append(extra)
    evaluation = evaluate_study((group(images),), snapshot(protocol_data), finalization=CONFIRMED)
    assert evaluation.status == "GREEN"
    assert evaluation.sequences[1].reason == "optional_sequence_absent"


def test_mixed_studies_and_duplicate_group_rejected(images, protocol_data):
    first = group(images)
    for image in images:
        image.StudyInstanceUID = generate_uid()
    other = group_acquisitions(tuple(parse_dataset(item)[0] for item in images))
    with pytest.raises(ValueError, match="StudyInstanceUID"):
        evaluate_study((first, *other), snapshot(protocol_data))
    with pytest.raises(ValueError, match="Duplicate acquisition"):
        evaluate_study((first, first), snapshot(protocol_data))


def test_naive_evaluation_time_rejected(images, protocol_data):
    with pytest.raises(ValueError, match="timezone"):
        evaluate(group(images), snapshot(protocol_data), evaluated_at=datetime(2026, 1, 1))


def test_invalid_or_conflicting_metadata_never_passes(images, protocol_data):
    from mr_quality_monitor.analysis.metadata_normalizer import MetadataValue

    acquisition = group(images)
    member = acquisition.members[0]
    fields = {**member.fields, "RepetitionTime": MetadataValue(4000, "ms", "conflicting")}
    acquisition = replace(
        acquisition, members=(replace(member, fields=fields), *acquisition.members[1:])
    )
    assert evaluate(acquisition, snapshot(protocol_data)).status == "GRAY"


def test_good_repeat_does_not_hide_bad_repeat(images, protocol_data):
    first = group(images)
    series_uid = generate_uid()
    for image in images:
        image.SOPInstanceUID = generate_uid()
        image.SeriesInstanceUID = series_uid
        image.RepetitionTime = 6000
    second = group(images)
    evaluation = evaluate_study((first, second), snapshot(protocol_data), finalization=CONFIRMED)
    assert evaluation.status == "RED"
    assert len(evaluation.acquisitions) == 2
    assert len(evaluation.sequences[0].candidate_groups) == 2


def test_study_result_is_json_serializable_and_versioned(images, protocol_data):
    evaluation = evaluate_study((group(images),), snapshot(protocol_data), finalization=CONFIRMED)
    serialized = json.loads(json.dumps(evaluation.to_dict(), allow_nan=False))
    assert serialized["finalization"]["source"] == "synthetic-test"
    assert serialized["protocol_id"] == "SYNTHETIC"
    assert serialized["protocol_version"] == "1-demo"


def test_late_member_reopens_count_completion(images, protocol_data):
    assert result(images, protocol_data).status == "GREEN"
    late = deepcopy(images[-1])
    late.SOPInstanceUID = generate_uid()
    late.ImagePositionPatient = [0, 0, 9]
    evaluation = result([*images, late], protocol_data)
    assert evaluation.completeness_status == "GRAY"
    assert evaluation.status == "GRAY"


def test_duplicate_members_do_not_satisfy_completion_count(images, protocol_data):
    acquisition = group(images[:2])
    acquisition = replace(acquisition, members=(*acquisition.members, acquisition.members[0]))
    evaluation = evaluate(acquisition, snapshot(protocol_data))
    assert evaluation.status == "GRAY"
    assert "completion_identity_uncertain" in evaluation.reasons
