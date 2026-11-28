"""Only generated identities and pixels are used by automated tests."""

import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid


@pytest.fixture
def image() -> Dataset:
    dataset = Dataset()
    dataset.SOPClassUID = MRImageStorage
    dataset.SOPInstanceUID = generate_uid()
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.PatientID = "SYNTHETIC-ONLY"
    dataset.PatientName = "SYNTHETIC^TEST"
    dataset.Modality = "MR"
    dataset.Rows, dataset.Columns = 4, 4
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated, dataset.BitsStored, dataset.HighBit = 16, 12, 11
    dataset.PixelRepresentation = 0
    dataset.PixelData = b"\x01\x00" * 16
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    return dataset


@pytest.fixture
def protocol_data():
    return {
        "schema_version": 1,
        "protocol_id": "SYNTHETIC",
        "version": "1-demo",
        "mode": "DEMO",
        "display_name": "DEMO synthetic",
        "author": "Software tests",
        "source": "Synthetic data",
        "sequences": [
            {
                "sequence_id": "T2",
                "display_name": "DEMO T2",
                "order": 1,
                "image_required": False,
                "completion": {"mode": "expected_count", "expected_members": 3},
                "identifiers": [
                    {"field": "Modality", "constraint": {"kind": "exact", "value": "MR"}},
                    {
                        "field": "SeriesDescription",
                        "constraint": {"kind": "contains", "value": "t2"},
                    },
                ],
                "parameters": [
                    {
                        "rule_id": "tr",
                        "field": "RepetitionTime",
                        "unit": "ms",
                        "constraint": {
                            "kind": "range",
                            "minimum": 3500,
                            "maximum": 4500,
                            "warning_minimum": 3000,
                            "warning_maximum": 5000,
                        },
                    },
                    {
                        "rule_id": "te",
                        "field": "EchoTime",
                        "unit": "ms",
                        "severity": "warning",
                        "constraint": {
                            "kind": "target",
                            "target": 100,
                            "tolerance": 15,
                            "tolerance_kind": "absolute",
                        },
                    },
                ],
            }
        ],
    }


@pytest.fixture
def gui_demo(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from mr_quality_monitor.config import ReceiverConfig
    from mr_quality_monitor.gui.demo import seed_demo
    from mr_quality_monitor.protocols.manager import load_protocol

    root = tmp_path / "gui-demo"
    snapshot = load_protocol(Path("tests/fixtures/protocols/gui_demo.yaml"))
    uid = seed_demo(root, snapshot)
    return SimpleNamespace(
        root=root, uid=uid, snapshot=snapshot, config=ReceiverConfig(storage_root=root)
    )
