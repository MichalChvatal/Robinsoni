"""Optional desktop entry point; receiver remains a separately owned process."""

import argparse
from pathlib import Path

from ..config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="MR Quality Monitor — DEMO desktop viewer")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
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
    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
