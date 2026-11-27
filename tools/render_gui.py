"""Render the real Qt window offscreen after a successful snapshot, without starting a receiver."""

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mr_quality_monitor.config import load_config
from mr_quality_monitor.gui.main_window import MainWindow
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow(load_config(args.config), refresh_interval_ms=60000)
    finished = {"saved": False}

    def render():
        if finished["saved"]:
            return
        if window.sequence_table.rowCount() >= 3:
            window.sequence_table.selectRow(2)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.output)):
            raise RuntimeError("Unable to save Qt render")
        finished["saved"] = True
        window.close()

    window.snapshot_applied.connect(lambda: QTimer.singleShot(100, render))
    window.reader_thread.finished.connect(app.quit)
    QTimer.singleShot(20000, window.close)
    window.show()
    app.exec()
    if not finished["saved"]:
        raise RuntimeError("GUI did not load a snapshot before the render deadline")
    print(f"Saved Qt window: {args.output}")


if __name__ == "__main__":
    main()
