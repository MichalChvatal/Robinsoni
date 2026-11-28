"""Regression cases with local DICOM archives and independently prepared reports."""

import json
from io import BytesIO
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.presentation import critical_description, sequence_overview
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.protocols.manager import load_protocol
from mr_quality_monitor.services.analysis_worker import open_worker
from pydicom import dcmread

from tools.verify_prostate_references import causes_agree, compare_case, pair_series

DATA = Path("data")
ROLES = ("T2_SAG", "T2_COR", "T2_TRA", "DWI_LOW", "DWI_INTERMEDIATE", "DWI_HIGH", "DWI_ADC")


def required(path):
    if not path.is_file():
        pytest.skip(f"Chybí místní referenční soubor {path}")
    return path


@pytest.mark.parametrize("case", ["MR50000", "MR50116", "MR50123", "MR50111"])
def test_reference_measurement(case, tmp_path):
    sample = case == "MR50000"
    archive_name = (
        "DICOMDIR_MR50000_ukazka_jednoho_mereni.zip" if sample else "DICOMDIR_MR50101-MR40132.zip"
    )
    folder = "DICOMDIR_" + case
    protocol = load_protocol(Path("config/protocols/mou_prostate.yaml"))
    reports = DATA / "mereni" if sample else None
    if reports:
        for name in ("report.json", "report.csv"):
            required(reports / name)
    with ZipFile(required(DATA / archive_name)) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.startswith(folder + "/DATA/DICOM/") and not name.endswith("/")
        ]
        assert names
        compared = compare_case(archive, folder, names, protocol, reports)
        for entry in compared["sequences"]:
            assert entry["outcome"] in {
                "match",
                "no_expectation",
                "change_outside_scope",
                "image_outside_scope",
            }, (case, entry)
        store = Storage(tmp_path, 0)
        try:
            for name in names:
                member = dcmread(BytesIO(archive.read(name)))
                store.receive(
                    member, str(member.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
                )
        finally:
            store.close()
    with open_worker(tmp_path, protocol) as worker:
        assert worker.run_once(Event()) == 0
        reader = MonitorReader(tmp_path)
        during = reader.read()
        assert [row.key for row in during.rows] == ["seq:" + role for role in ROLES]
        if case == "MR50111":
            assert all(row.status == "GRAY" and not row.count for row in during.rows[:2])
        if case == "MR50116":
            assert during.rows[3].status == "RED"
        worker.history.finalize_current(during.study_uid, during.branch_id, actor="reference_test")
        view = reader.read()
    assert [row.key for row in view.rows] == ["seq:" + role for role in ROLES]
    assert view.status == compared["study_status"]
    rows = dict(zip(ROLES, view.rows, strict=True))
    if sample:
        ref = json.loads((reports / "report.json").read_text(encoding="utf-8-sig"))
        assert view.received == ref["stats"]["files"] == 321
        assert ref["expected"]["scan_state"] == "ok" and view.status == "GREEN"
        assert all(item["expected"] is None for item in compared["sequences"])
        assert rows["T2_SAG"].status == "RED"
        assert all(row.status == "GREEN" for row in view.rows[1:])
        detail = sequence_overview(rows["T2_SAG"], {"RED"})
        assert all(text in detail for text in ("FOV", "230", "120", "200"))
    elif case == "MR50111":
        assert len(compared["dropped_series"]) == 2
        assert all(item["outcome"] == "missing_confirmed" for item in compared["dropped_series"])
        planes = next(row for row in view.study_rows if row.key == "study:t2_planes")
        assert planes.status == "RED"
        assert planes.checks[0]["actual"] == int(rows["T2_TRA"].status == "GREEN")
        assert "Chybí jedna vyhovující rovina." in critical_description(planes.checks[0])
        assert all(row.reason == "optional_sequence_absent" for row in view.rows[:2])
    else:
        role, field = (
            ("DWI_LOW", "PrivateDiffusionBValue")
            if case == "MR50116"
            else ("T2_TRA", "SliceThickness")
        )
        reference = next(
            item
            for item in compared["sequences"]
            if role in item["identified_roles"] and item["expected"] == "inadequate"
        )
        assert reference["outcome"] == "match"
        checks = [
            check
            for result in rows[role].evaluations
            for check in result["checks"]
            if check["field"] == field and check["status"] == "RED"
        ]
        assert rows[role].status == "RED" and checks
        if case == "MR50116":
            assert all(check["actual"] == 251 for check in checks)
            assert rows["DWI_INTERMEDIATE"].count and rows["DWI_INTERMEDIATE"].status != "RED"


def test_ambiguous_uid_conflict_and_independent_causes():
    assert pair_series({"low", "mid"}, None, {"low", "mid"}, 1)[1] == "ambiguous"
    assert pair_series({"low"}, "mid", {"low", "mid"}, 1)[1] == "uid_conflict"
    assert not causes_agree({"SliceThickness", "PixelSpacing"}, {"PhaseResolution"})
    assert causes_agree({"SliceThickness", "PixelSpacing"}, {"SliceThickness", "PhaseResolution"})
