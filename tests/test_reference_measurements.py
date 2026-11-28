"""Regression cases with local DICOM archives and independently prepared reports."""

import json
import os
import subprocess
import sys
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
    protocol = load_protocol(Path("tests/fixtures/protocols/mou_prostate.yaml"))
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


@pytest.mark.parametrize("pirads", [True, False], ids=["pirads", "legacy-v2"])
def test_prostate_profile_recognizes_mr50000_and_builds_plan(tmp_path, pirads):
    protocol_path = (
        Path("config/protocols/prostate_pirads_v2_1.yaml") if pirads
        else Path("tests/fixtures/protocols/prostate_v2.yml")
    )
    config = tmp_path / "app.yml"
    config.write_text(
        json.dumps({"storage_root": ".", "single_active_study": True}), encoding="utf-8"
    )
    archive_path = required(DATA / "DICOMDIR_MR50000_ukazka_jednoho_mereni.zip")
    storage = Storage(tmp_path, 0, single_active_study=True)
    try:
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                if "/DATA/DICOM/" in name and not name.endswith("/"):
                    image = dcmread(BytesIO(archive.read(name)))
                    storage.receive(
                        image, str(image.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
                    )
    finally:
        storage.close()
    analysis = subprocess.run(
        [
            sys.executable, "-m", "mr_quality_monitor.worker_cli", "--config", str(config),
            "--protocol", str(protocol_path.parent if pirads else protocol_path), "--once",
        ],
        env={**os.environ, "PYTHONPATH": str(Path("backend").resolve())},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert analysis.returncode == 0, analysis.stderr
    view = MonitorReader(tmp_path, single_active_study=True).read()
    snapshot = load_protocol(protocol_path)
    protocol = snapshot.protocol
    assert f"study_protocol_selected protocol={protocol.protocol_id}" in analysis.stderr
    planned_keys = ["seq:" + seq.sequence_id for seq in protocol.sequences]
    assert [row.key for row in view.rows] == planned_keys
    assert view.can_finalize and view.pending == 0 and view.completion.state == "in_progress"
    counts = {row.key: row.count for row in view.rows}
    if pirads:
        assert counts == {
            "seq:T2_AXIAL": 26, "seq:T2_CORONAL": 24, "seq:T2_SAGITTAL": 28,
            "seq:DWI_ADC": 26, "seq:DWI_HIGH_B": 26, "seq:DCE_T1": 0,
        }
        with open_worker(tmp_path, snapshot) as worker:
            worker.history.finalize_current(view.study_uid, view.branch_id, actor="reference_test")
        closed = MonitorReader(tmp_path, single_active_study=True).read()
        assert closed.completion.state == "incomplete"
        assert "Chybí povinná sekvence: DCE T1W (mpMRI)" in closed.completion.details
        return
    assert [counts["seq:" + sid] for sid in ("T2_TRA", "T2_COR", "T2_SAG")] == [26, 24, 28]
    for sid in ("DWI_LOW_B", "DWI_INTERMEDIATE_B", "DWI_ADC", "DWI_HIGH_B"):
        assert counts["seq:" + sid] == 26
    assert counts["seq:DWI_RESOLVE"] == 0


@pytest.mark.parametrize("omit_low_b", [False, True], ids=["full-archive", "without-low-b"])
def test_legacy_prostate_protocol_on_mr50000(tmp_path, omit_low_b):
    """Keep the historical prostate regression through intake and GUI history."""
    protocol_path = Path("tests/fixtures/protocols/prostate_mou.yaml")
    protocol = load_protocol(protocol_path)
    config_path = tmp_path / "app.yaml"
    config_path.write_text(
        json.dumps({"storage_root": ".", "single_active_study": True}), encoding="utf-8"
    )
    archive_path = required(DATA / "DICOMDIR_MR50000_ukazka_jednoho_mereni.zip")
    store = Storage(tmp_path, 0, single_active_study=True)
    try:
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                if "/DATA/DICOM/" not in name or name.endswith("/"):
                    continue
                member = dcmread(BytesIO(archive.read(name)))
                if (
                    omit_low_b
                    and "tracew" in str(member.get("SeriesDescription", "")).lower()
                    and "b50" in str(member.get("SequenceName", "")).lower()
                ):
                    continue
                store.receive(
                    member, str(member.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
                )
    finally:
        store.close()

    analysis = subprocess.run(
        [
            sys.executable, "-m", "mr_quality_monitor.worker_cli",
            "--config", str(config_path), "--protocol", str(protocol_path), "--once",
        ],
        env={**os.environ, "PYTHONPATH": str(Path("backend").resolve())},
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert analysis.returncode == 0, analysis.stderr
    assert "protocol=PROSTATE_MOU version=1.2 mode=CLINICAL" in analysis.stderr

    with open_worker(tmp_path, protocol) as worker:
        reader = MonitorReader(tmp_path, single_active_study=True)
        view = reader.read()
        assert view.can_finalize and not view.stale
        assert view.completion.state == "in_progress"
        if omit_low_b:
            assert view.received == 321 - 26 and view.pending == 0
            worker.history.finalize_current(view.study_uid, view.branch_id, actor="reference_test")
            closed = reader.read()
            assert closed.completion.state == "incomplete"
            assert "Chybí povinná sekvence: DWI Low b-value (*ez_b50t)" in closed.completion.details
            return
        assert view.received == 321 and view.pending == 0
        assert worker.history.get_protocol(view.study_uid).protocol.protocol_id == "PROSTATE_MOU"
        rows = {row.key: row for row in view.rows}
        assert all(
            rows[f"seq:{sequence}"].evaluations
            for sequence in ("T2_SAG", "T2_COR", "T2_TRA", "DWI_LOW_B", "DWI_HIGH_B")
        )
        image_statuses = tuple(item["image_status"] for item in rows["seq:T2_TRA"].evaluations)
        assert set(image_statuses) - {"GRAY"}
        assert {
            check["rule_id"] for item in rows["seq:T2_TRA"].evaluations
            for check in item["checks"] if check["rule_id"].startswith("image:")
        } == {"image:low_snr", "image:motion_ghosting", "image:slab_discontinuity"}
        low_b_checks = [
            check
            for item in rows["seq:DWI_LOW_B"].evaluations
            for check in item["checks"]
            if check["field"] == "DiffusionBValue"
        ]
        assert len(low_b_checks) == 26
        assert all(
            check["actual"] == 50.0
            and check["paths"] == ["private[SIEMENS MR HEADER].0019100C"]
            for check in low_b_checks
        )
        study_rows = {row.key: row for row in view.study_rows}
        assert study_rows["study:prostate_segm_coverage"].status == "GRAY"
        assert (
            study_rows["study:prostate_segm_coverage"].reason
            == "segmentation_mask_bounds_unavailable"
        )

        worker.history.finalize_current(view.study_uid, view.branch_id, actor="reference_test")
        closed = reader.read()
        assert not closed.can_finalize
        assert closed.completion.state == "incomplete"
        assert not any("Chybí povinná sekvence:" in line for line in closed.completion.details)
        assert any(line.startswith("T2W") for line in closed.completion.details)
        axial = next(row for row in closed.rows if row.key == "seq:T2_TRA")
        assert tuple(item["image_status"] for item in axial.evaluations) == image_statuses
