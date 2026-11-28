import importlib
import json
import os
import socket
import threading
import time
from copy import deepcopy
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.dicom.sender import SendResult
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.presentation import COLORS, STATUS_TEXT
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.protocols.manager import load_protocol_candidates
from mr_quality_monitor.services.analysis_worker import open_worker
from pydicom.uid import generate_uid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = importlib.import_module("PySide6.QtCore")
QtTest = importlib.import_module("PySide6.QtTest")
QtGui = importlib.import_module("PySide6.QtGui")
MainWindow = importlib.import_module("mr_quality_monitor.gui.main_window").MainWindow


@pytest.fixture(scope="module")
def app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


def wait_for(app, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("GUI condition did not complete")
        time.sleep(0.005)
    app.processEvents()


def close_window(app, window):
    window.close()
    wait_for(app, lambda: not window.reader_thread.isRunning())
    app.processEvents()


def test_sender_explains_intentionally_discarded_images(app, tmp_path):
    sender_window = importlib.import_module("mr_quality_monitor.gui.sender").SenderWindow
    window = sender_window(ReceiverConfig(storage_root=tmp_path))
    window.transfer = SimpleNamespace(
        result=SendResult(discarded=3), error="", deleteLater=lambda: None
    )
    try:
        window.show_result()
        message = window.status.text()
        assert "Mimo aktivní vyšetření: 3" in message
        assert "Spustit aplikaci.cmd" in message
        assert "Chyby: 0" in message
        assert "Poslední chyba" not in message
    finally:
        window.close()


def test_live_checklist_and_completion(app, tmp_path, image, protocol_data, monkeypatch):
    protocol_data.update(schema_version=2, mode="CLINICAL", approved_by="Software tests")
    protocol_data["study_identifiers"] = [
        {"field": "StudyDescription", "constraint": {"kind": "exact", "value": "TEST protocol"}}
    ]
    required = protocol_data["sequences"][0]
    required["completion"]["expected_members"] = 1
    optional = deepcopy(required)
    optional.update(sequence_id="OPTIONAL", order=2, display_name="Optional", required=False)
    optional["identifiers"][1]["constraint"]["value"] = "optional"
    protocol_data["sequences"].append(optional)
    path = tmp_path / "clinical.yaml"
    path.write_text(json.dumps(protocol_data), encoding="utf-8")
    config = ReceiverConfig(storage_root=tmp_path, single_active_study=True)
    storage = Storage(tmp_path, 0, single_active_study=True)

    def receive(dataset):
        return storage.receive(
            dataset, str(dataset.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
        )

    image.SeriesDescription = "localizer"
    image.BodyPartExamined = "PROSTATA"
    assert receive(image) == "stored"
    storage.recover(reset_study=True)
    assert not list((tmp_path / "objects").glob("*.dcm"))
    window = MainWindow(config, refresh_interval_ms=60000)
    window.show()

    def refresh(predicate):
        window.refresh()
        wait_for(app, predicate)

    try:
        wait_for(app, lambda: window.snapshot is not None)
        assert not window.snapshot.rows and window.snapshot.completion is None
        assert "první data z MR" in window.message_label.text()
        assert receive(image) == "stored"
        with open_worker(tmp_path, load_protocol_candidates(path), config) as worker:
            assert worker.run_once(Event()) == 0
            refresh(lambda: window.snapshot.study_uid == str(image.StudyInstanceUID))
            assert "Rozpoznává se protokol" in window.message_label.text()
            assert window.snapshot.completion is None

            recognized = deepcopy(image)
            recognized.SOPInstanceUID, recognized.SeriesInstanceUID = generate_uid(), generate_uid()
            recognized.StudyDescription = "TEST protocol"
            assert receive(recognized) == "stored"
            assert worker.run_once(Event(), defer_new_study=True) == 0
            refresh(lambda: window.snapshot.completion is not None)
            assert [row.status for row in window.snapshot.rows] == ["GRAY", "GRAY"]
            assert window.completion_label.text() == (
                "Vyšetření: Údaje o vyšetření zatím nejsou dostupné"
            )
            assert window.completion_label.isVisible()
            assert not window.study_panel.isVisible()
            assert not window.snapshot.history

            recognized.SeriesDescription = "t2"
            recognized.SOPInstanceUID, recognized.SeriesInstanceUID = generate_uid(), generate_uid()
            recognized.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            recognized.ImagePositionPatient = [0, 0, 0]
            recognized.PixelSpacing = [1, 1]
            recognized.RepetitionTime, recognized.EchoTime = 4000, 100
            assert receive(recognized) == "stored"
            assert worker.run_once(Event()) == 0
            refresh(lambda: window.snapshot.rows[0].status == "GREEN")
            assert window.snapshot.examination_name == "PROSTATA – TEST protocol"
            assert window.completion_label.text() == (
                "Vyšetření: PROSTATA – TEST protocol"
            )
            assert "Požadavky splněny" in window.study_findings.toPlainText()
            monkeypatch.setattr(
                QtWidgets.QMessageBox,
                "question",
                lambda *_: QtWidgets.QMessageBox.StandardButton.Yes,
            )
            monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *_: None)
            window.finalize_button.click()
            wait_for(app, lambda: window.completion_label.text().startswith("DONE"))

            # New data on the same study invalidates the verdict immediately.
            recognized.SOPInstanceUID, recognized.SeriesInstanceUID = generate_uid(), generate_uid()
            recognized.RepetitionTime = 3300
            assert receive(recognized) == "stored"
            refresh(lambda: window.snapshot.stale)
            assert window.completion_label.text().startswith("Vyšetření: ")
            assert worker.run_once(Event()) == 0
            refresh(lambda: window.snapshot.rows[0].status == "ORANGE")

            recognized.SOPInstanceUID, recognized.SeriesInstanceUID = generate_uid(), generate_uid()
            recognized.RepetitionTime = 2000
            assert receive(recognized) == "stored"
            assert worker.run_once(Event()) == 0
            refresh(lambda: window.snapshot.rows[0].status == "RED")

            replacement = deepcopy(image)
            replacement.StudyInstanceUID = generate_uid()
            replacement.StudyDescription = "TEST protocol"
            replacement.SOPInstanceUID = generate_uid()
            replacement.SeriesInstanceUID = generate_uid()
            assert receive(replacement) == "stored"
            assert worker.run_once(Event()) == 0
            refresh(lambda: window.snapshot.study_uid == str(replacement.StudyInstanceUID))
            assert [row.status for row in window.snapshot.rows] == ["GRAY", "GRAY"]
            assert receive(recognized) == "discarded"
            window.finalize_button.click()
            wait_for(app, lambda: window.completion_label.text().startswith("INCOMPLETE"))
            assert "Chybí povinná sekvence: DEMO T2" in window.study_findings.toPlainText()
    finally:
        close_window(app, window)
        storage.close()


def test_monitor_switches_to_new_study_and_closes_previous_detail(app, tmp_path, image):
    config = ReceiverConfig(storage_root=tmp_path, single_active_study=True)
    storage = Storage(tmp_path, 0, single_active_study=True)
    window = MainWindow(config, refresh_interval_ms=60000)
    window.show()
    try:
        syntax = str(image.file_meta.TransferSyntaxUID)
        assert storage.receive(image, syntax, "MR_TEST", "127.0.0.1") == "stored"
        wait_for(app, lambda: not window.busy and window.snapshot is not None)
        window.refresh()
        wait_for(app, lambda: window.study_uid == str(image.StudyInstanceUID))
        assert not window.study_combo.isVisible()
        window.sequence_table.selectRow(0)
        window.sequence_details_button.click()
        assert window.detail_dialog.isVisible()

        image.StudyInstanceUID = generate_uid()
        image.SeriesInstanceUID = generate_uid()
        image.SOPInstanceUID = generate_uid()
        assert storage.receive(image, syntax, "MR_TEST", "127.0.0.1") == "stored"
        window.refresh()
        wait_for(app, lambda: window.study_uid == str(image.StudyInstanceUID))
        assert not window.detail_dialog.isVisible()
        assert window.snapshot.received == 1 and window.snapshot.pending == 1
    finally:
        close_window(app, window)
        storage.close()


def test_window_displays_colors_details_and_lazy_metadata(app, gui_demo):
    window = MainWindow(gui_demo.config, refresh_interval_ms=60000)
    window.show()
    try:
        wait_for(app, lambda: window.snapshot is not None)
        assert window.sequence_table.rowCount() == 4
        assert window.sequence_table.columnCount() == 1
        assert [
            window.sequence_table.horizontalHeaderItem(index).text() for index in range(1)
        ] == ["Sekvence"]
        window.resize(660, 540)
        app.processEvents()
        for index, status in enumerate(STATUS_TEXT):
            item = window.sequence_table.item(index, 0)
            assert item.text().startswith(window.snapshot.rows[index].name)
            expected_background = COLORS["GRAY"][0] if status == "GRAY" else "#ffffff"
            assert item.background().color() == QtGui.QColor(expected_background)
        assert not window.detail_dialog.isVisible()
        assert not window.study_panel.isVisible()
        window.study_details_button.click()
        assert window.study_panel.isVisible()
        window.study_details_button.click()
        assert not window.study_panel.isVisible()
        item = window.sequence_table.item(2, 0)
        QtTest.QTest.mouseClick(
            window.sequence_table.viewport(),
            QtCore.Qt.MouseButton.LeftButton,
            pos=window.sequence_table.visualItemRect(item).center(),
        )
        app.processEvents()
        assert window.detail_dialog.isVisible()
        assert window.detail.tabs.tabText(window.detail.tabs.currentIndex()) == "Přehled"
        explanation = window.detail.explanation.text().replace("\u00a0", " ")
        assert "Čas opakování (TR) má hodnotu 6 000 ms" in explanation
        assert "od 3 500 do 4 500 ms" in explanation
        assert "Počet dotčených snímků nebo rámců: 3." in explanation
        rect = window.sequence_table.visualItemRect(item)
        image = window.sequence_table.viewport().grab().toImage()
        scale = image.devicePixelRatio()
        assert image.pixelColor(
            int((rect.right() - 8) * scale), int((rect.top() + 3) * scale)
        ) == QtGui.QColor("#ffffff")
        assert "kritická" in window.detail.heading.text()
        assert "TR: 6000 ms" in window.detail.summary.text()
        assert window.detail.checks.rowCount() == 3
        checks = window.detail.checks
        assert not window.detail.show_valid.isChecked()
        assert [
            checks.item(index, 4).text()
            for index in range(checks.rowCount()) if not checks.isRowHidden(index)
        ] == [STATUS_TEXT["RED"]]
        window.detail.show_valid.click()
        assert all(not checks.isRowHidden(index) for index in range(checks.rowCount()))
        window.detail.show_valid.click()
        assert sum(not checks.isRowHidden(index) for index in range(checks.rowCount())) == 1
        assert window.detail.timeline.rowCount() == 1
        tree = window.detail.metadata
        window.detail.tabs.setCurrentIndex(window.detail.tabs.indexOf(tree))
        assert tree.topLevelItemCount() > 40
        node = next(
            tree.topLevelItem(index)
            for index in range(tree.topLevelItemCount())
            if tree.topLevelItem(index).text(0) == "RepetitionTime"
        )
        assert node.childCount() == 0
        tree.expandItem(node)
        app.processEvents()
        assert node.childCount() == 3
        window.refresh()
        wait_for(app, lambda: not window.busy)
        assert window.detail.metadata.isExpanded(window.detail.metadata.indexFromItem(node))
        assert window.detail.tabs.currentWidget() is tree
        assert not window.detail.show_valid.isChecked()
        assert sum(not checks.isRowHidden(index) for index in range(checks.rowCount())) == 1
        assert window.row_key == "seq:DEMO_CRITICAL"
        window.detail_dialog.close()
        assert not window.detail_dialog.isVisible()
        window.sequence_details_button.click()
        assert window.detail_dialog.isVisible()
    finally:
        close_window(app, window)


def test_slow_read_keeps_event_loop_responsive_and_close_nonblocking(app, gui_demo):
    ready, release = threading.Event(), threading.Event()
    snapshot = MonitorReader(gui_demo.root).read()

    class SlowReader:
        calls = 0

        def read(self, *_args):
            self.calls += 1
            ready.set()
            release.wait(4)
            return snapshot

    reader = SlowReader()
    window = MainWindow(gui_demo.config, reader=reader, refresh_interval_ms=10)
    ticks = []
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    try:
        wait_for(app, ready.is_set)
        wait_for(app, lambda: len(ticks) >= 6)
        assert reader.calls == 1
        started = time.monotonic()
        window.close()
        assert time.monotonic() - started < 0.2
        assert window.closing and window.reader_thread.isRunning()
    finally:
        release.set()
        timer.stop()
        close_window(app, window)


def test_basic_view_shows_issue_icons_inside_sequence_column(app, gui_demo, monkeypatch):
    snapshot = MonitorReader(gui_demo.root).read()
    parameter_row = snapshot.rows[2]
    parameter_row = replace(
        parameter_row,
        plan_role="Povinná",
        evaluations=tuple(
            {**item, "image_required": True, "image_status": "GRAY",
             "reasons": [*item["reasons"], "image_skipped_parameter_error"]}
            for item in parameter_row.evaluations
        ),
    )
    artifact_row = snapshot.rows[0]
    artifact_row = replace(
        artifact_row,
        plan_role="Skupinový požadavek",
        status="RED",
        evaluations=tuple(
            {**item, "image_required": True, "image_status": "RED",
             "checks": [
                 *item["checks"],
                 {"rule_id": "image:motion_ghosting", "field": "motion_ghosting",
                  "status": "RED", "actual": 0.8, "expected": {},
                  "reason": "motion_ghosting_detected", "paths": []},
             ]}
            for item in artifact_row.evaluations
        ),
    )
    warning_row = replace(
        snapshot.rows[1],
        plan_role="Nepovinná",
        evaluations=tuple(
            {**item, "image_required": True, "image_status": "ORANGE"}
            for item in snapshot.rows[1].evaluations
        ),
    )
    clean_row = replace(snapshot.rows[0], key="seq:clean")
    reader = SimpleNamespace(snapshot=replace(
        snapshot, rows=(parameter_row, artifact_row, warning_row, clean_row, snapshot.rows[3]),
    ))
    reader.read = lambda *_: reader.snapshot
    window = MainWindow(gui_demo.config, reader=reader, refresh_interval_ms=60000)
    window.show()
    try:
        wait_for(app, lambda: window.snapshot is not None)
        issues_role = importlib.import_module("mr_quality_monitor.gui.sequence_detail").ISSUES_ROLE

        def issues(index):
            cell = window.sequence_table.item(index, 0)
            return [tuple(issue) for issue in cell.data(issues_role)]

        assert window.sequence_table.columnCount() == 1
        assert window.sequence_table.item(0, 0).text() == parameter_row.name
        assert window.sequence_table.item(1, 0).text() == artifact_row.name
        assert window.sequence_table.item(2, 0).text() == f"{warning_row.name} (nepovinná)"
        assert issues(0) == [(False, "RED"), (True, "GRAY")]
        assert issues(1) == [(False, "GREEN"), (True, "RED")]
        assert issues(2) == [(False, "ORANGE"), (True, "ORANGE")]
        assert issues(3) == [(False, "GREEN")]
        assert issues(4) == [(False, "GREEN")]

        def verify_badge_alignment():
            slots = {False: set(), True: set()}
            table = window.sequence_table
            for row_index in range(table.rowCount()):
                index = table.model().index(row_index, 0)
                option = QtWidgets.QStyleOptionViewItem()
                option.initFrom(table)
                option.rect = table.visualRect(index)
                _, badges = table.itemDelegate()._layout(option, index)
                for image, _, _, rect in badges:
                    slots[image].add((rect.left(), rect.width()))
            assert all(len(positions) == 1 for positions in slots.values())
            assert next(iter(slots[False]))[0] < next(iter(slots[True]))[0]

        verify_badge_alignment()
        assert all(
            window.sequence_table.item(index, 0).background().color() == QtGui.QColor("#ffffff")
            for index in range(4)
        )
        badge_index = window.sequence_table.model().index(1, 0)
        badge_option = QtWidgets.QStyleOptionViewItem()
        badge_option.initFrom(window.sequence_table)
        badge_option.rect = window.sequence_table.visualRect(badge_index)
        _, painted_badges = window.sequence_table.itemDelegate()._layout(badge_option, badge_index)
        rendered = window.sequence_table.viewport().grab().toImage()
        scale = rendered.devicePixelRatio()
        for _, status, _, rect in painted_badges:
            assert rendered.pixelColor(
                int(rect.center().x() * scale), int((rect.top() + 3) * scale),
            ) == QtGui.QColor(COLORS[status][0])
        module = importlib.import_module("mr_quality_monitor.gui.sequence_detail")
        assert "Čas opakování" in window.sequence_table.item(0, 0).data(
            module.BADGE_TOOLTIPS_ROLE
        )[0]
        assert "Pohybové artefakty" in window.sequence_table.item(1, 0).data(
            module.BADGE_TOOLTIPS_ROLE
        )[1]
        assert window.sequence_table.item(1, 0).toolTip() == ""
        hovered = []
        monkeypatch.setattr(QtWidgets.QToolTip, "showText", lambda *args: hovered.append(args[1]))
        monkeypatch.setattr(QtWidgets.QToolTip, "hideText", lambda: hovered.append(None))

        def verify_hover_regions(row_index):
            table = window.sequence_table
            index = table.model().index(row_index, 0)
            option = QtWidgets.QStyleOptionViewItem()
            option.initFrom(table)
            option.rect = table.visualRect(index)
            name_rect, badges = table.itemDelegate()._layout(option, index)
            expected = index.data(module.BADGE_TOOLTIPS_ROLE)
            for point, text in [
                (name_rect.topLeft() + QtCore.QPoint(20, 5), None),
                *((badge[3].center(), text) for badge, text in zip(badges, expected)),
            ]:
                hovered.clear()
                event = QtGui.QHelpEvent(
                    QtCore.QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point)
                )
                app.sendEvent(table.viewport(), event)
                assert hovered == [text]

        verify_hover_regions(1)
        window.resize(window.minimumWidth(), window.height())
        app.processEvents()
        assert window.sequence_table.rowHeight(2) >= 70
        verify_badge_alignment()
        verify_hover_regions(2)
        window.resize(840, 600)
        app.processEvents()
        assert window.sequence_table.rowHeight(2) < 70
        cell = window.sequence_table.item(1, 0)
        QtTest.QTest.mouseClick(
            window.sequence_table.viewport(), QtCore.Qt.MouseButton.LeftButton,
            pos=window.sequence_table.visualItemRect(cell).center(),
        )
        app.processEvents()
        assert window.detail_dialog.isVisible()
        assert window.row_key == artifact_row.key
        reader.snapshot = replace(reader.snapshot, stale=True)
        window.refresh()
        wait_for(app, lambda: window.display_stale)
        assert issues(0) == [(False, "RED"), (True, "GRAY")]
        assert issues(1) == [(True, "RED")]
        assert issues(2) == [(True, "GRAY")]
        assert issues(3) == []
        assert issues(4) == []
    finally:
        close_window(app, window)


def test_critical_overview_groups_findings_and_marks_stale(app, gui_demo):
    snapshot = MonitorReader(gui_demo.root).read()
    row = snapshot.rows[2]
    tr = next(check for check in row.evaluations[0]["checks"] if check["status"] == "RED")
    missing = {
        **tr,
        "field": "SliceThickness",
        "reason": "required_metadata_absent",
        "actual": None,
        "expected": {},
        "action": "",
    }
    warning = {**tr, "field": "EchoTime", "status": "ORANGE"}
    evaluation = {
        **row.evaluations[0],
        "checks": [tr, tr, {**tr, "frame_index": 2}, missing, warning],
    }
    detail = importlib.import_module("mr_quality_monitor.gui.sequence_detail").SequenceDetail()
    detail.show_row(replace(row, evaluations=(evaluation,)), stale=True)
    explanation = detail.explanation.text()
    assert explanation.startswith("Zastaralý výsledek")
    assert explanation.count("Čas opakování (TR)") == 1
    assert "Počet dotčených snímků nebo rámců: 2." in explanation
    assert "chybí povinný údaj „Tloušťka řezu“" in explanation
    assert "Čas echa" not in explanation
    detail.show_row(replace(row, evaluations=()))
    assert detail.explanation.text() == "Konkrétní důvod v uloženém výsledku chybí."


def test_requirements_show_the_image_frame_and_acquisition(app, gui_demo):
    row = MonitorReader(gui_demo.root).read().rows[2]
    evaluation = row.evaluations[0]
    check = next(item for item in evaluation["checks"] if item["sop_uid"])
    uid = check["sop_uid"]
    record = next(
        record for record in row.records
        if record["fields"]["SOPInstanceUID"]["value"] == uid
    )
    fields = {
        **record["fields"],
        "SeriesNumber": {"state": "known", "value": 7},
        "InstanceNumber": {"state": "known", "value": 12},
    }
    first = {**evaluation, "checks": [check, check, {**check, "frame_index": 1}]}
    second = {
        **evaluation, "group_key": "another-acquisition",
        "checks": [{**check, "sop_uid": None, "frame_index": None}],
    }
    detail = importlib.import_module("mr_quality_monitor.gui.sequence_detail").SequenceDetail()
    detail.show_row(
        replace(
            row,
            records=({**record, "fields": fields}, {**record, "fields": fields, "frame_index": 1}),
            evaluations=(first, second),
        )
    )
    table = detail.checks
    assert table.horizontalHeaderItem(1).text() == "Vyhodnocená data"
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "Společné pro 2 snímků / rámců"
    assert "Akv. 1 · Série 7 · Snímek 12" in table.item(0, 1).toolTip()
    assert f"SOP Instance UID: {uid}" in table.item(0, 1).toolTip()
    assert "rámec 2" in table.item(0, 1).toolTip()
    assert table.item(1, 1).text() == "Akv. 2 · Souhrn akvizice"
    assert [detail.acquisitions.item(index, 0).text() for index in range(2)] == ["1", "2"]
    detail.show_row(replace(row, evaluations=()))
    assert table.item(0, 1).text() == "Plán sekvence"


def test_requirements_group_common_values_and_keep_image_exceptions(app, gui_demo):
    row = MonitorReader(gui_demo.root).read().rows[2]
    evaluation = row.evaluations[0]
    original = next(item for item in evaluation["checks"] if item["sop_uid"])
    common = [
        {**original, "sop_uid": f"1.2.{number}", "paths": [f"image/{number}/TR"]}
        for number in range(3)
    ]
    different = {**original, "sop_uid": "1.2.9", "actual": 7000}
    missing = {
        **original, "sop_uid": "1.2.10", "actual": None,
        "status": "GRAY", "reason": "required_metadata_absent",
    }
    detail = importlib.import_module("mr_quality_monitor.gui.sequence_detail").SequenceDetail()
    detail.show_row(replace(
        row,
        evaluations=(
            {**evaluation, "checks": [*common[:2], different, missing]},
            {**evaluation, "checks": common[2:]},
        ),
    ))
    table = detail.checks
    assert table.rowCount() == 3
    assert table.item(0, 1).text() == "Společné pro 3 snímků / rámců"
    assert "Akv. 2" in table.item(0, 1).toolTip()
    assert all(f"image/{number}/TR" in table.item(0, 5).text() for number in range(3))
    assert table.item(1, 2).text() == "7000"
    assert "1.2.9" in table.item(1, 1).text()
    assert table.item(2, 4).text() == STATUS_TEXT["GRAY"]
    assert "1.2.10" in table.item(2, 1).text()


@pytest.mark.parametrize(
    "tr,parameter_status,image_allowed",
    [(4000, "GREEN", True), (3200, "ORANGE", True), (6000, "RED", False), (None, "GRAY", False)],
)
def test_image_findings_are_per_image_after_processing_and_finalization(
    app, tmp_path, image, protocol_data, monkeypatch, tr, parameter_status, image_allowed,
):
    from array import array

    from mr_quality_monitor.protocols.manager import ProtocolSnapshot
    from mr_quality_monitor.protocols.models import Protocol

    protocol_data.update(schema_version=2, mode="CLINICAL", approved_by="Software tests")
    protocol_data["study_identifiers"] = [
        {"field": "StudyDescription", "constraint": {"kind": "exact", "value": "Image test"}}
    ]
    protocol_data["sequences"][0]["image_required"] = True
    protocol = ProtocolSnapshot.create(Protocol.model_validate(protocol_data))
    image.StudyDescription, image.SeriesDescription = "Image test", "t2"
    image.Rows = image.Columns = 32
    image.PixelData = array("H", (
        100 if 8 <= r < 24 and 8 <= c < 24 else 0
        for r in range(32) for c in range(32)
    )).tobytes()
    if tr is not None:
        image.RepetitionTime = tr
    image.EchoTime = 100
    image.PixelSpacing, image.SliceThickness = [0.5, 0.5], 3
    image.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    storage = Storage(tmp_path, 0)
    worker_module = importlib.import_module("mr_quality_monitor.services.analysis_worker")
    load_pixels = worker_module.load_slice_pixels
    pixel_reads = []

    def tracked_pixels(*args, **kwargs):
        pixel_reads.append(args[0])
        return load_pixels(*args, **kwargs)

    monkeypatch.setattr(worker_module, "load_slice_pixels", tracked_pixels)
    try:
        uids = set()
        for position in (0, 3, 6):
            member = deepcopy(image)
            member.SOPInstanceUID = generate_uid()
            uids.add(str(member.SOPInstanceUID))
            member.InstanceNumber = position // 3 + 1
            member.ImagePositionPatient = [0, 0, position]
            storage.receive(member, str(member.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1")
        with open_worker(tmp_path, protocol) as worker:
            assert worker.run_once(Event()) == 0
            assert len(pixel_reads) == (3 if image_allowed else 0)
            reader = MonitorReader(tmp_path)
            view = reader.read()
            detail_module = importlib.import_module("mr_quality_monitor.gui.sequence_detail")
            detail = detail_module.SequenceDetail()

            def verify(snapshot):
                assert not snapshot.stale and snapshot.pending == 0
                row = snapshot.rows[0]
                detail.show_row(row)
                assert row.evaluations[0]["metadata_status"] == parameter_status
                if not image_allowed:
                    assert row.evaluations[0]["image_status"] == "GRAY"
                    assert not any(
                        check["rule_id"].startswith("image:")
                        for check in row.evaluations[0]["checks"]
                    )
                    reason = (
                        "image_skipped_parameter_error" if parameter_status == "RED"
                        else "image_waiting_for_parameters"
                    )
                    assert reason in row.evaluations[0]["reasons"]
                    return
                for field in ("low_snr", "slab_discontinuity", "motion_ghosting"):
                    parent = next(
                        check for check in row.evaluations[0]["checks"] if check["field"] == field
                    )
                    assert {check["sop_uid"] for check in parent["per_image"]} == uids
                    indices = [
                        index for index in range(detail.checks.rowCount())
                        if detail.checks.item(index, 0).text() == field
                    ]
                    assert len(indices) == 3
                    assert all(
                        "Snímek" in detail.checks.item(index, 1).text() for index in indices
                    )
                    assert len({
                        detail.checks.item(index, 1).toolTip() for index in indices
                    }) == 3
                assert sum(
                    detail.checks.item(index, 0).text() == "RepetitionTime"
                    for index in range(detail.checks.rowCount())
                ) == 1

            verify(view)
            worker.history.finalize_current(view.study_uid, view.branch_id, actor="test")
            verify(reader.read())
    finally:
        storage.close()


def test_late_response_for_old_selection_is_discarded(app, gui_demo):
    original = MonitorReader(gui_demo.root).read()
    studies = (*original.studies, {"study_uid": "1.2.99", "last_seen": "now"})
    first = replace(original, studies=studies)
    ready, release = threading.Event(), threading.Event()

    class SwitchingReader:
        calls = 0

        def read(self, study_uid, branch_id):
            self.calls += 1
            if self.calls == 2:
                ready.set()
                release.wait(4)
            if study_uid == "1.2.99":
                return replace(first, study_uid="1.2.99", patient_token="SECOND-TOKEN", rows=())
            return first

    window = MainWindow(gui_demo.config, reader=SwitchingReader(), refresh_interval_ms=60000)
    try:
        wait_for(app, lambda: window.snapshot is not None)
        window.refresh()
        wait_for(app, ready.is_set)
        window.study_combo.setCurrentIndex(window.study_combo.findData("1.2.99"))
        assert window.snapshot is None
        release.set()
        wait_for(app, lambda: window.snapshot is not None and window.snapshot.study_uid == "1.2.99")
        assert "SECOND-TOKEN" in window.patient_label.text()
        assert window.sequence_table.rowCount() == 0
    finally:
        release.set()
        close_window(app, window)


def test_read_error_masks_success_and_does_not_switch_study(app, gui_demo):
    snapshot = MonitorReader(gui_demo.root).read()

    class FailingReader:
        failed = False

        def read(self, *_args):
            if self.failed:
                raise OSError("sensitive error must not reach UI")
            return snapshot

    reader = FailingReader()
    window = MainWindow(gui_demo.config, reader=reader, refresh_interval_ms=60000)
    try:
        wait_for(app, lambda: window.snapshot is not None)
        reader.failed = True
        window.refresh()
        wait_for(app, lambda: not window.busy)
        assert window.display_stale
        for index, status in ((0, "GRAY"), (2, "RED")):
            item = window.sequence_table.item(index, 0)
            assert item.text().startswith(snapshot.rows[index].name)
            assert item.background().color() == QtGui.QColor(COLORS["GRAY"][0])
        assert window.study_uid == snapshot.study_uid
        assert "sensitive" not in window.message_label.text()
    finally:
        close_window(app, window)


def test_closing_gui_does_not_stop_receiver(app, tmp_path, image):
    from mr_quality_monitor.dicom.listener import Listener
    from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage
    from pynetdicom import AE

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = ReceiverConfig(storage_root=tmp_path, port=port, minimum_free_bytes=0)
    listener = Listener(config)
    listener.start()
    window = MainWindow(config, refresh_interval_ms=60000)
    try:
        wait_for(app, lambda: window.snapshot is not None)
        close_window(app, window)
        ae = AE(ae_title="MR_TEST")
        ae.add_requested_context(MRImageStorage, ExplicitVRLittleEndian)
        association = ae.associate("127.0.0.1", port, ae_title=config.ae_title)
        try:
            assert association.is_established
            assert association.send_c_store(image).Status == 0
        finally:
            association.release()
    finally:
        close_window(app, window)
        listener.stop()


def test_mou_shows_specific_b_value_error_and_closes_study(app, tmp_path, image, monkeypatch):
    from pathlib import Path

    from mr_quality_monitor.dicom.storage import Storage
    from mr_quality_monitor.protocols.manager import load_protocol
    from mr_quality_monitor.services.analysis_worker import open_worker
    from test_prostate import private_b, series

    snapshot = load_protocol(Path("tests/fixtures/protocols/mou_prostate.yaml"))
    image.PixelData = bytes(300 * 300 * 2)

    def low_dwi(positions):
        return series(image, "ep2d_diff_zoomit_b50_800_tra_DRB", "*ez_b50t", positions)

    storage = Storage(tmp_path, 0)
    try:
        for value, positions in [(251, (0, 3)), (50, (0, 3))]:
            for member in low_dwi(positions):
                private_b(member, value, value)
                storage.receive(
                    member, str(member.file_meta.TransferSyntaxUID), "MR_TEST", "127.0.0.1"
                )
    finally:
        storage.close()
    with open_worker(tmp_path, snapshot) as worker:
        assert worker.run_once(threading.Event()) == 0
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question", lambda *_: QtWidgets.QMessageBox.StandardButton.Yes
    )
    window = MainWindow(ReceiverConfig(storage_root=tmp_path), refresh_interval_ms=60000)
    window.show()
    try:
        wait_for(app, lambda: window.snapshot is not None)
        index = next(i for i, row in enumerate(window.snapshot.rows) if row.key == "seq:DWI_LOW")
        window.sequence_table.selectRow(index)
        window.sequence_details_button.click()
        assert window.sequence_table.rowCount() == 7
        explanation = window.detail.explanation.text()
        assert explanation.count("B-hodnota 251 s/mm² překročila povolené maximum 100 s/mm².") == 1
        assert "Počet dotčených snímků nebo rámců: 2." in explanation
        assert window.detail.acquisitions.rowCount() == 2
        window.detail_dialog.close()
        window.study_details_button.click()
        window.finalize_button.click()
        wait_for(app, lambda: window.snapshot.message == "Vyšetření bylo uzavřeno.")
        assert next(row.status for row in window.snapshot.rows if row.key == "seq:DWI_ADC") == "RED"
        assert "Chybějí 2 vyhovující roviny." in window.study_findings.toPlainText()
    finally:
        close_window(app, window)
