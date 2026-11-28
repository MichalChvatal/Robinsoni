"""Optional desktop entry point; receiver remains a separately owned process."""

import argparse
from pathlib import Path

from ..config import load_config
from ..protocols.manager import load_protocol
from .view_data import MonitorReader


def main() -> int:
    parser = argparse.ArgumentParser(description="MR Quality Monitor — DEMO desktop viewer")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protocol", type=Path)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        protocol = load_protocol(args.protocol).protocol.model_dump() if args.protocol else None
    except Exception:
        print("Konfiguraci nelze načíst.")
        return 2
    try:
        from PySide6.QtWidgets import QApplication

        from .main_window import MainWindow
    except ImportError:
        print('GUI vyžaduje instalaci volitelných závislostí: pip install -e ".[gui]"')
        return 2
    app = QApplication([])
    app.setApplicationName("MR Quality Monitor")
    window = MainWindow(config, reader=MonitorReader(config.storage_root, intake_protocol=protocol))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
