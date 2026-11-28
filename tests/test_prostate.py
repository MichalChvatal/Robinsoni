from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from mr_quality_monitor.analysis.series_aggregator import group_acquisitions
from mr_quality_monitor.analysis.validation_engine import CompletionEvidence, evaluate_study
from mr_quality_monitor.dicom.metadata_parser import parse_dataset
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.presentation import sequence_overview
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.protocols.manager import ProtocolSnapshot, load_protocol
from mr_quality_monitor.protocols.models import Protocol
from mr_quality_monitor.services.analysis_worker import open_worker
from pydicom.uid import generate_uid


@pytest.fixture
def mou():
    return load_protocol(Path("config/protocols/mou_prostate.yaml"))


def series(image, protocol, sequence, positions=(0, 3, 6)):
    image = deepcopy(image)
    image.ProtocolName, image.SequenceName = protocol, sequence
    image.Rows = image.Columns = 300
    image.PixelSpacing = [0.5, 0.6]
    image.SliceThickness = image.SpacingBetweenSlices = 3
    image.InPlanePhaseEncodingDirection = "ROW"
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    image.SeriesInstanceUID = generate_uid()
    for position in positions:
        member = deepcopy(image)
        member.SOPInstanceUID = generate_uid()
        member.ImagePositionPatient = [0, 0, position]
        yield member


def private_b(image, first, second):
    image.Manufacturer = "Siemens Healthineers"
    image.ManufacturerModelName = "MAGNETOM Vida"
    image.SoftwareVersions = "syngo MR XA60"
    if first is not None:
        image.add_new((0x19, 0x0010), "LO", "SIEMENS MR HEADER")
        image.add_new((0x19, 0x100C), "IS", first)
    if second is not None:
        image.add_new((0x21, 0x0011), "LO", "SIEMENS MR SDI 02")
        image.add_new((0x21, 0x1105), "IS", second)


def test_private_b_fallback_and_conflict(image, mou):
    profile = mou.protocol.prostate_checks
    private_b(image, None, 100)
    field = parse_dataset(image, prostate_checks=profile)[0].fields["PrivateDiffusionBValue"]
    assert field.value == 100
    assert field.sources[0].tag == "00211105"
    private_b(image, 251, 100)
    field = parse_dataset(image, prostate_checks=profile)[0].fields["PrivateDiffusionBValue"]
    assert field.state == "conflicting"
    assert [source.tag for source in field.sources] == ["0019100C", "00211105"]


def test_study_rules_and_alignment_with_different_slice_counts(image, mou):
    image.FrameOfReferenceUID = generate_uid()
    low = list(series(image, "ep2d_diff_zoomit_b50_800_tra_DRB", "*ez_b50t", (0, 3, 6, 9)))
    images = [
        *series(image, "t2_tse_cor", "*tse2d1_16"),
        *series(image, "t2_tse_tra", "*tse2d1_16"),
        *low,
    ]
    profile = mou.protocol.prostate_checks

    def groups():
        return group_acquisitions(
            tuple(parse_dataset(member, prostate_checks=profile)[0] for member in images)
        )

    for member in low:
        private_b(member, 100, 100)
    config = mou.protocol.model_dump()
    config["prostate_checks"]["position_tolerance_mm"] = 0.01
    config["prostate_checks"]["orientation_tolerance_degrees"] = 0.01
    snapshot = ProtocolSnapshot.create(Protocol.model_validate(config))
    end = CompletionEvidence(source="test", reference="complete")
    result = evaluate_study(groups(), snapshot, finalization=end)
    assert {item.rule_id: item.status for item in result.checks} == {
        "t2_planes": "GREEN",
        "axial_t2": "GREEN",
        "dwi_set": "RED",
        "resolve_presence": "GRAY",
    }
    acquisition = next(item for item in result.acquisitions if item.sequence_id == "DWI_LOW")
    assert acquisition.status == "GREEN"
    assert next(
        check.actual for check in acquisition.checks if check.rule_id == "dwi_alignment"
    ) == (0, 0, 0)
    assert next(item for item in result.sequences if item.sequence_id == "DWI_ADC").status == "RED"
    for member in low:
        private_b(member, 251, 251)
    result = evaluate_study(groups(), mou, finalization=end)
    acquisition = next(item for item in result.acquisitions if item.sequence_id == "DWI_LOW")
    assert acquisition.status == "RED"
    assert any(check.status == "RED" and check.actual == 251 for check in acquisition.checks)
    assert (
        next(check.status for check in acquisition.checks if check.rule_id == "dwi_alignment")
        == "GRAY"
    )
    for member in low:
        private_b(member, 50, 50)
        member.ImagePositionPatient[2] += 0.2
    result = evaluate_study(groups(), snapshot, finalization=end)
    acquisition = next(item for item in result.acquisitions if item.sequence_id == "DWI_LOW")
    assert next(check.reason for check in acquisition.checks if check.status == "RED") == (
        "alignment_outside_tolerance"
    )


def test_close_requires_processed_images_and_late_data_reopens(tmp_path, image, mou):
    storage = Storage(tmp_path, 0)
    image.PixelData = bytes(300 * 300 * 2)
    first, second, third = series(image, "t2_tse_tra", "*tse2d1_16")
    uid = str(first.StudyInstanceUID)

    def receive(member):
        storage.receive(member, str(member.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1")

    try:
        receive(first)
        with open_worker(tmp_path, mou) as worker:
            assert worker.run_once(Event()) == 0
            reader = MonitorReader(tmp_path)
            view = reader.read()
            assert view.can_finalize
            receive(second)
            with pytest.raises(ValueError, match="not ready"):
                worker.history.finalize_current(uid, view.branch_id, actor="tester")
            assert worker.run_once(Event()) == 0
            worker.history.finalize_current(uid, view.branch_id, actor="tester")
            closed = reader.read()
            assert not closed.can_finalize
            missing = next(row for row in closed.rows if row.key == "seq:DWI_ADC")
            assert missing.status == "RED"
            assert "chyběla tato povinná sekvence" in sequence_overview(missing, {"RED"})
            receive(third)
            assert worker.run_once(Event()) == 0
            assert reader.read().can_finalize
            repeated = deepcopy(third)
            repeated.SeriesInstanceUID = generate_uid()
            repeated.SOPInstanceUID = generate_uid()
            repeated.SliceThickness = 5
            receive(repeated)
            assert worker.run_once(Event()) == 0
            updated = reader.read()
            assert len(updated.rows) == len(closed.rows) == 7
            axial = next(row for row in updated.rows if row.key == "seq:T2_TRA")
            assert axial.status == "RED" and len(axial.evaluations) == 2
            assert "Tloušťka řezu" in sequence_overview(axial, {"RED"})
    finally:
        storage.close()
