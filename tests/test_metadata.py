import json
from copy import deepcopy

import pytest
from mr_quality_monitor.analysis.metadata_normalizer import Evidence, FieldSpec, normalize
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.dicom.metadata_parser import (
    MetadataStructureError,
    parse_dataset,
    parse_file,
)
from mr_quality_monitor.vendors import PrivateBinding, adapter_for
from mr_quality_monitor.vendors.siemens import SiemensAdapter
from pydicom import dcmwrite
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pydicom.uid import generate_uid


@pytest.fixture
def header(image):
    image.Manufacturer = "SIEMENS"
    image.ManufacturerModelName = "SYNTHETIC-MR"
    image.SoftwareVersions = "TEST-1"
    image.RepetitionTime = "4000.0"
    image.EchoTime = "100"
    image.FlipAngle = 90
    image.SliceThickness = 3
    image.SpacingBetweenSlices = 3
    image.PixelSpacing = [0.5, 0.5]
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    image.ImagePositionPatient = [0, 0, 0]
    image.ImageType = ["ORIGINAL", "PRIMARY"]
    return image


def test_standard_metadata_units_and_provenance(header):
    record = parse_dataset(header)[0]
    tr = record.fields["RepetitionTime"]
    assert (tr.value, tr.unit, tr.state) == (4000, "ms", "known")
    assert tr.sources[0].tag == "00180080"
    assert tr.sources[0].path == "RepetitionTime"
    assert tr.sources[0].raw == ("4000.0",)
    assert record.known("PixelSpacing") == (0.5, 0.5)
    assert record.known("ImageType") == ("ORIGINAL", "PRIMARY")
    assert record.vendor_parser == "siemens/1"
    assert record.fields["InversionTime"].state == "missing"
    json.dumps(record.to_dict(), allow_nan=False)


def test_identifiers_are_opt_in_and_not_in_default_snapshot(header):
    record = parse_dataset(header)[0]
    assert record.fields["PatientID"].reason == "suppressed"
    assert "SYNTHETIC-ONLY" not in json.dumps(record.to_dict())
    assert parse_dataset(header, include_identifiers=True)[0].known("PatientID") == "SYNTHETIC-ONLY"
    assert (
        parse_dataset(header, include_identifiers=True)[0].known("PatientName") == "SYNTHETIC^TEST"
    )


def test_parse_file_reads_header_without_pixel_data_access(tmp_path, header, monkeypatch):
    path = tmp_path / "extensionless"
    dcmwrite(path, header, enforce_file_format=True)
    # Parser must not access pixel_array even when pixel decoding would fail.
    monkeypatch.setattr(
        Dataset,
        "pixel_array",
        property(lambda _: (_ for _ in ()).throw(AssertionError("Pixel decoder should not run"))),
    )
    assert parse_file(path)[0].known("Rows") == 4
    assert parse_file(path, include_identifiers=True)[0].known("PatientName") == "SYNTHETIC^TEST"


@pytest.mark.parametrize("raw", [("NaN",), ("inf",), ("not-numeric",), ("1", "2")])
def test_invalid_numeric_values(raw):
    value = normalize(FieldSpec("float", "ms"), (Evidence("test", "00000000", raw, "ms"),))
    assert value.state == "invalid"
    assert value.value is None


def test_units_missing_zero_and_conflicts():
    spec = FieldSpec("float", "ms")
    standard = Evidence("standard", "00180080", ("4000",), "ms")
    vendor = Evidence("private", "00191001", ("4",), "s", "synthetic/1")
    assert normalize(spec, (standard, vendor)).state == "known"
    assert normalize(spec, ()).state == "missing"
    assert normalize(spec, (Evidence("test", "00180080", ("0",), "ms"),)).value == 0
    disagree = Evidence("private", "00191001", ("5",), "s")
    conflict = normalize(spec, (standard, disagree))
    assert conflict.state == "conflicting" and conflict.value == 4000
    assert len(conflict.sources) == 2
    assert normalize(spec, (Evidence("x", "0", ("4",), "unknown"),)).state == "invalid"


@pytest.mark.parametrize("uid", ["1..2", "01.2", "abc", "١.٢", "1" * 65])
def test_invalid_uid_without_warning(uid):
    assert normalize(FieldSpec("uid"), (Evidence("x", "0", (uid,)),)).state == "invalid"


def binding(**overrides):
    return PrivateBinding(
        **{
            "keyword": "DiffusionBValue",
            "group": 0x0019,
            "creator": "SYNTHETIC_CREATOR",
            "offset": 1,
            "model": "SYNTHETIC-MR",
            "software_version": "TEST-1",
            "unit": "s/mm2",
            **overrides,
        }
    )


def test_private_creator_mapping_and_standard_conflict(header):
    block = header.private_block(0x0019, "SYNTHETIC_CREATOR", create=True)
    block.add_new(1, "DS", "800")
    adapter = SiemensAdapter((binding(),))
    result = parse_dataset(header, adapter=adapter)[0]
    assert result.known("DiffusionBValue") == 800
    assert result.fields["DiffusionBValue"].sources[0].parser == adapter.identifier
    assert parse_dataset(header)[0].fields["DiffusionBValue"].state == "missing"
    header.DiffusionBValue = 900
    result = parse_dataset(header, adapter=adapter)[0]
    assert result.fields["DiffusionBValue"].state == "conflicting"
    assert result.fields["DiffusionBValue"].value == 900


@pytest.mark.parametrize("change", ["creator", "model", "version"])
def test_vendor_binding_requires_matching_identity(header, change):
    block = header.private_block(0x0019, "SYNTHETIC_CREATOR", create=True)
    block.add_new(1, "DS", "800")
    overrides = {"creator": "OTHER"} if change == "creator" else {}
    if change == "model":
        header.ManufacturerModelName = "OTHER"
    if change == "version":
        header.SoftwareVersions = "OTHER"
    assert (
        parse_dataset(header, adapter=SiemensAdapter((binding(**overrides),)))[0].known(
            "DiffusionBValue"
        )
        is None
    )


def test_private_binary_field_is_invalid_without_guessing(header):
    header.private_block(0x0019, "SYNTHETIC_CREATOR", create=True).add_new(1, "OB", b"\x00\x00")
    result = parse_dataset(header, adapter=SiemensAdapter((binding(),)))[0]
    assert result.fields["DiffusionBValue"].state == "invalid"


def test_wrong_vendor_is_rejected(header):
    header.Manufacturer = "PHILIPS"
    with pytest.raises(MetadataStructureError, match="Manufacturer"):
        parse_dataset(header, adapter=SiemensAdapter((binding(),)))


@pytest.mark.parametrize(
    "manufacturer,vendor",
    [
        ("siemens", "siemens"),
        ("PHILIPS MEDICAL SYSTEMS", "philips"),
        ("GE MEDICAL SYSTEMS", "ge"),
        ("CANON MEDICAL SYSTEMS CORPORATION", "canon"),
        ("unknown", "generic"),
        ("SOME SIEMENS COPY", "generic"),
    ],
)
def test_vendor_detection(manufacturer, vendor):
    assert adapter_for(manufacturer).name == vendor


def enhanced_header(header):
    header.NumberOfFrames = 2
    shared = Dataset()
    measures = Dataset()
    measures.PixelSpacing = [0.5, 0.5]
    shared.PixelMeasuresSequence = Sequence([measures])
    header.SharedFunctionalGroupsSequence = Sequence([shared])
    frames = []
    for z, te in [(0, 80), (3, 100)]:
        frame, position, echo = Dataset(), Dataset(), Dataset()
        position.ImagePositionPatient = [0, 0, z]
        echo.EffectiveEchoTime = te
        frame.PlanePositionSequence = Sequence([position])
        frame.MREchoSequence = Sequence([echo])
        frames.append(frame)
    header.PerFrameFunctionalGroupsSequence = Sequence(frames)
    del header.ImagePositionPatient
    del header.EchoTime
    return header


def test_shared_and_per_frame_groups_with_provenance(header):
    records = parse_dataset(enhanced_header(header))
    assert len(records) == 2
    assert records[0].known("EchoTime") == 80
    assert records[1].known("EchoTime") == 100
    assert records[1].known("ImagePositionPatient") == (0, 0, 3)
    assert records[1].frame_index == 1
    assert records[1].fields["EchoTime"].sources[0].path == (
        "PerFrameFunctionalGroupsSequence[1].MREchoSequence[0].EffectiveEchoTime"
    )


def test_per_frame_top_level_disagreement_is_explicit(header):
    enhanced_header(header)
    header.EchoTime = 90
    result = parse_dataset(header)[0].fields["EchoTime"]
    assert result.state == "conflicting" and result.value == 80


@pytest.mark.parametrize("mutation", ["count", "missing", "macro", "shared", "zero"])
def test_malformed_functional_groups_rejected(header, mutation):
    enhanced_header(header)
    if mutation == "count":
        header.NumberOfFrames = 3
    elif mutation == "missing":
        del header.PerFrameFunctionalGroupsSequence
    elif mutation == "macro":
        header.PerFrameFunctionalGroupsSequence[0].MREchoSequence.append(Dataset())
    elif mutation == "shared":
        header.SharedFunctionalGroupsSequence.append(Dataset())
    else:
        header.NumberOfFrames = 0
    with pytest.raises(MetadataStructureError):
        parse_dataset(header)


def slices(header, positions=(6, 0, 3)):
    records = []
    for number, z in enumerate(positions):
        image = deepcopy(header)
        image.SOPInstanceUID = generate_uid()
        image.InstanceNumber = 99 - number
        image.ImagePositionPatient = [0, 0, z]
        records.append(parse_dataset(image)[0])
    return tuple(records)


def test_groups_slices_by_projection_not_filename_or_instance_number(header):
    (group,) = group_acquisitions(slices(header))
    assert group.projected_positions_mm == (0, 3, 6)
    assert group.issues == ()
    assert not group.complete


def test_diffusion_groups_with_same_series_uid_are_separate(header):
    header.DiffusionBValue = 50
    low = slices(header)
    header.DiffusionBValue = 800
    high = slices(header)
    assert len(group_acquisitions((*low, *high))) == 2


def test_different_series_uid_same_series_number_remains_separate(header):
    header.SeriesNumber = 12
    first = slices(header)
    header.SeriesInstanceUID = generate_uid()
    assert len(group_acquisitions((*first, *slices(header)))) == 2


def test_irregular_and_repeated_slice_positions_are_flagged(header):
    (group,) = group_acquisitions(slices(header, (0, 3, 9)))
    assert "irregular_slice_spacing" in group.issues
    assert "declared_spacing_mismatch" in group.issues
    (group,) = group_acquisitions(slices(header, (0, 0, 3)))
    assert "repeated_slice_position" in group.issues


def test_missing_or_invalid_geometry_isolated(header):
    del header.ImageOrientationPatient
    groups = group_acquisitions(slices(header))
    assert len(groups) == 3
    assert all("incomplete_geometry" in group.issues for group in groups)
    header.ImageOrientationPatient = [1, 0, 0, 1, 0, 0]
    assert all("invalid_geometry" in group.issues for group in group_acquisitions(slices(header)))


def test_duplicate_instance_frame_is_idempotent_or_conflicting(header):
    record = parse_dataset(header)[0]
    (group,) = group_acquisitions((record, record))
    assert len(group.members) == 1
    header.EchoTime = 110
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        group_acquisitions((record, parse_dataset(header)[0]))


def test_sagittal_slice_sorting_uses_orientation(header):
    header.ImageOrientationPatient = [0, 1, 0, 0, 0, 1]
    records = []
    for x in (6, 0, 3):
        header.SOPInstanceUID = generate_uid()
        header.ImagePositionPatient = [x, 10, 20]
        records.append(parse_dataset(header)[0])
    (group,) = group_acquisitions(tuple(records))
    assert group.projected_positions_mm == (0, 3, 6)
    assert group.issues == ()


def test_nonfinite_stored_value_is_not_known(header):
    header.RepetitionTime = "NaN"
    assert parse_dataset(header)[0].fields["RepetitionTime"].state == "invalid"


def test_vendor_identifier_tracks_mapping_configuration():
    first = SiemensAdapter((binding(),))
    same = SiemensAdapter((binding(),))
    changed = SiemensAdapter((binding(offset=2),))
    assert first.identifier == same.identifier
    assert first.identifier != changed.identifier
