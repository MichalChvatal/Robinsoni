import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml
from mr_quality_monitor.analysis.sequence_identifier import identify
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.protocols.manager import ProtocolManager, ProtocolSnapshot, load_protocol
from mr_quality_monitor.protocols.models import Protocol
from pydantic import ValidationError


def test_pirads_identifies_dce_and_checks_the_high_b_boundary(image):
    protocol = load_protocol(Path("config/protocols/prostate_pirads_v2_1.yaml")).protocol
    image.ImageType = ["ORIGINAL", "PRIMARY"]
    image.SeriesDescription = "DCE T1 dynamic"
    image.ProtocolName = "DCE T1 dynamic"

    def identified_sequence():
        group, = group_acquisitions(parse_dataset(image))
        return identify(group, protocol).sequence_id

    assert identified_sequence() == "DCE_T1"
    image.ImageType = ["DERIVED", "PRIMARY", "DIFFUSION"]
    image.SeriesDescription = "dwi high"
    image.ProtocolName = "dwi high"
    image.DiffusionBValue = 1399
    assert identified_sequence() is None
    image.DiffusionBValue = 1400
    assert identified_sequence() == "DWI_HIGH_B"


@pytest.mark.parametrize("extension", ["json", "yaml", "yml"])
def test_load_and_hash_are_format_independent(tmp_path, protocol_data, extension):
    path = tmp_path / f"protocol.{extension}"
    path.write_text(
        json.dumps(protocol_data) if extension == "json" else yaml.safe_dump(protocol_data),
        encoding="utf-8",
    )
    snapshot = load_protocol(path)
    assert snapshot == ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    with pytest.raises(FrozenInstanceError):
        snapshot.content = "changed"
    with pytest.raises(ValidationError):
        snapshot.protocol.version = "changed"


def test_load_rejects_unavailable_image_check(tmp_path, protocol_data):
    protocol_data["sequences"][0]["image_required"] = True
    protocol_data["sequences"][0]["image_checks"] = ["low_snr", "typo"]
    path = tmp_path / "protocol.yml"
    path.write_text(yaml.safe_dump(protocol_data), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown image check IDs: typo"):
        load_protocol(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "unit",
        "unknown_field",
        "numeric_string",
        "wrong_components",
        "bounds",
        "half_warning",
        "narrow_warning",
        "negative_tolerance",
        "unknown_tolerance",
        "mixed_operators",
        "duplicate_sequence",
        "duplicate_rule",
        "duplicate_order",
        "names_only",
        "one_signal",
        "not_demo",
        "nan",
        "boolean_value",
        "boolean_bound",
        "boolean_order",
        "false_string",
        "wrong_expected_type",
        "no_count",
        "extra_count",
        "unknown_key",
        "text_range",
    ],
)
def test_invalid_protocol_is_rejected(protocol_data, mutation):
    sequence = protocol_data["sequences"][0]
    rule = sequence["parameters"][0]
    constraint = rule["constraint"]
    if mutation == "unit":
        rule["unit"] = "s"
    elif mutation == "unknown_field":
        rule["field"] = "TR_typo"
    elif mutation == "numeric_string":
        constraint["minimum"] = "3500"
    elif mutation == "wrong_components":
        rule["field"], rule["unit"] = "PixelSpacing", "mm"
    elif mutation == "bounds":
        constraint["minimum"] = 9000
    elif mutation == "half_warning":
        del constraint["warning_maximum"]
    elif mutation == "narrow_warning":
        constraint["warning_minimum"] = 4000
    elif mutation == "negative_tolerance":
        sequence["parameters"][1]["constraint"]["tolerance"] = -1
    elif mutation == "unknown_tolerance":
        sequence["parameters"][1]["constraint"]["tolerance_kind"] = "guess"
    elif mutation == "mixed_operators":
        constraint["value"] = 4000
    elif mutation in {"duplicate_sequence", "duplicate_order"}:
        extra = deepcopy(sequence)
        if mutation == "duplicate_order":
            extra["sequence_id"] = "OTHER"
        else:
            extra["order"] = 2
        protocol_data["sequences"].append(extra)
    elif mutation == "duplicate_rule":
        sequence["parameters"].append(deepcopy(rule))
    elif mutation == "names_only":
        sequence["identifiers"][0] = {
            "field": "ProtocolName",
            "constraint": {"kind": "exact", "value": "T2"},
        }
    elif mutation == "one_signal":
        sequence["identifiers"].pop()
    elif mutation == "not_demo":
        protocol_data["mode"] = "CLINICAL"
    elif mutation == "nan":
        constraint["minimum"] = float("nan")
    elif mutation == "boolean_value":
        rule["constraint"] = {"kind": "exact", "value": True}
    elif mutation == "boolean_bound":
        constraint["minimum"] = True
    elif mutation == "boolean_order":
        sequence["order"] = True
    elif mutation == "false_string":
        sequence["image_required"] = "false"
    elif mutation == "wrong_expected_type":
        rule["constraint"] = {"kind": "exact", "value": "4000"}
    elif mutation == "no_count":
        sequence["completion"] = {"mode": "expected_count"}
    elif mutation == "extra_count":
        sequence["completion"] = {"mode": "explicit", "expected_members": 3}
    elif mutation == "unknown_key":
        protocol_data["hidden_limit"] = 100
    else:
        rule["field"], rule["unit"] = "Modality", None
    with pytest.raises(ValidationError):
        Protocol.model_validate(protocol_data)


@pytest.mark.parametrize(
    "extension,content",
    [
        ("json", '{"mode":"DEMO","mode":"DEMO"}'),
        ("yaml", "mode: DEMO\nmode: DEMO\n"),
        ("yaml", "mode: !!python/object/apply:os.system [echo forbidden]\n"),
    ],
)
def test_unsafe_or_duplicate_key_files_rejected(tmp_path, extension, content):
    path = tmp_path / f"bad.{extension}"
    path.write_text(content, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        load_protocol(path)


def test_snapshot_and_manager_pin_active_study(protocol_data):
    first = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    manager = ProtocolManager()
    manager.register(first)
    manager.select("SYNTHETIC", "1-demo")
    assert manager.begin("1.2.3") == first
    changed = deepcopy(protocol_data)
    changed["display_name"] = "Changed"
    with pytest.raises(ValueError, match="cannot change"):
        manager.register(ProtocolSnapshot.create(Protocol.model_validate(changed)))
    changed["version"] = "2-demo"
    second = ProtocolSnapshot.create(Protocol.model_validate(changed))
    manager.register(second)
    with pytest.raises(RuntimeError, match="active examination"):
        manager.select("SYNTHETIC", "2-demo")
    assert manager.begin("1.2.3") == first
    manager.end("1.2.3")
    manager.select("SYNTHETIC", "2-demo")
    assert manager.begin("1.2.4") == second


def test_shipped_demo_loads():
    from pathlib import Path

    snapshot = load_protocol(Path("tests/fixtures/protocols/demo_mr.yaml"))
    assert snapshot.protocol.mode == "DEMO"


def test_snapshot_tampering_is_rejected(protocol_data):
    snapshot = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    with pytest.raises(ValueError, match="checksum"):
        ProtocolSnapshot(snapshot.content + " ", snapshot.sha256)


def test_oversized_configuration_rejected_before_parsing(tmp_path):
    path = tmp_path / "too-large.yaml"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        load_protocol(path)


def test_duplicate_identifier_signal_rejected(protocol_data):
    sequence = protocol_data["sequences"][0]
    sequence["identifiers"][1] = deepcopy(sequence["identifiers"][0])
    with pytest.raises(ValidationError, match="distinct metadata"):
        Protocol.model_validate(protocol_data)
