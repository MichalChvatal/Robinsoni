import importlib
import os
import socket
import threading
import time
from dataclasses import replace

import pytest
from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.gui.presentation import COLORS, STATUS_SYMBOLS, STATUS_TEXT
from mr_quality_monitor.gui.view_data import MonitorReader

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


def test_window_displays_colors_details_and_lazy_metadata(app, gui_demo):
    window = MainWindow(gui_demo.config, refresh_interval_ms=60000)
    window.show()
    try:
        wait_for(app, lambda: window.snapshot is not None)
        assert window.sequence_table.rowCount() == 4
        assert window.sequence_table.columnCount() == 1
        assert [entry.text() for entry in window.legend.entries] == list(STATUS_TEXT.values())
        assert window.legend.columns == 4
        window.resize(window.minimumWidth(), window.height())
        wait_for(app, lambda: window.legend.columns == 2)
        window.resize(660, 540)
        wait_for(app, lambda: window.legend.columns == 4)
        for index, status in enumerate(STATUS_TEXT):
            item = window.sequence_table.item(index, 0)
            assert item.text().startswith(STATUS_SYMBOLS[status])
            assert item.background().color() == QtGui.QColor(COLORS[status][0])
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
            int((rect.right() - 8) * scale), int(rect.center().y() * scale)
        ) == QtGui.QColor(COLORS["RED"][0])
        assert "kritická" in window.detail.heading.text()
        assert "TR: 6000 ms" in window.detail.summary.text()
        assert window.detail.checks.rowCount() == 9
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
            assert item.text().startswith(STATUS_SYMBOLS[status])
            assert item.background().color() == QtGui.QColor(COLORS[status][0])
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

    snapshot = load_protocol(Path("config/protocols/mou_prostate.yaml"))
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
