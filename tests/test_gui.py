import importlib
import os
import socket
import threading
import time
from dataclasses import replace

import pytest
from mr_quality_monitor.config import ReceiverConfig
from mr_quality_monitor.gui.view_data import MonitorReader

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = importlib.import_module("PySide6.QtCore")
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
    try:
        wait_for(app, lambda: window.snapshot is not None)
        assert window.sequence_table.rowCount() == 4
        assert "6000" == window.sequence_table.item(2, 3).text()
        assert "Kritická" in window.sequence_table.item(2, 2).text()
        window.sequence_table.selectRow(2)
        app.processEvents()
        assert "kritická" in window.detail.heading.text()
        assert window.detail.checks.rowCount() == 9
        assert window.detail.timeline.rowCount() == 1
        tree = window.detail.metadata
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
        assert window.row_key == "seq:DEMO_CRITICAL"
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
        assert "Nevyhodnoceno" in window.sequence_table.item(0, 2).text()
        assert "Kritická" in window.sequence_table.item(2, 2).text()
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
