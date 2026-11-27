"""Desktop C-STORE sender for the configured local receiver."""

import argparse
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..cli import configure_logging
from ..config import ReceiverConfig, load_config
from ..dicom.sender import send
from .fonts import ensure_font


class Transfer(QThread):
    def __init__(self, paths: tuple[Path, ...], config: ReceiverConfig, host: str, parent: QWidget):
        super().__init__(parent)
        self.paths, self.config, self.host = paths, config, host
        self.result = None
        self.error = ""

    def run(self) -> None:
        try:
            self.result = send(
                self.paths,
                host=self.host,
                port=self.config.port,
                called_aet=self.config.ae_title,
                calling_aet=self.config.allowed_calling_aet,
                timeout=self.config.network_timeout_seconds,
            )
        except ConnectionError:
            self.error = "Přijímač odmítl spojení nebo je nedostupný."
        except Exception as error:
            self.error = f"Přenos se přerušil ({type(error).__name__})."


class SenderWindow(QWidget):
    def __init__(self, config: ReceiverConfig):
        super().__init__()
        self.config = config
        self.host = str(config.host)
        if config.host.is_unspecified:
            self.host = "127.0.0.1" if config.host.version == 4 else "::1"
        self.transfer = None
        self.setWindowTitle("Odeslat DICOM")
        self.setMinimumWidth(465)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Cíl  {self.host}:{config.port}  ·  {config.ae_title}"))
        self.files = QPushButton("Vybrat soubory nebo ZIP…")
        self.folder = QPushButton("Vybrat složku…")
        self.status = QLabel("Vybraná data se odešlou po potvrzení.")
        self.status.setWordWrap(True)
        for widget in (self.files, self.folder, self.status):
            layout.addWidget(widget)
        self.files.clicked.connect(self.choose_files)
        self.folder.clicked.connect(self.choose_folder)

    def choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Vybrat DICOM soubory nebo ZIP")
        self.start_transfer(tuple(Path(path) for path in paths))

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Vybrat složku s DICOM snímky")
        if path:
            self.start_transfer((Path(path),))

    def start_transfer(self, paths: tuple[Path, ...]) -> None:
        if not paths or self.transfer is not None:
            return
        if (
            QMessageBox.question(
                self,
                "Potvrdit přenos",
                f"Odeslat vybraná data do {self.config.ae_title} "
                f"na {self.host}:{self.config.port}?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.files.setEnabled(False)
        self.folder.setEnabled(False)
        self.status.setText("Probíhá odesílání a příjem potvrzení…")
        self.transfer = Transfer(paths, self.config, self.host, self)
        self.transfer.finished.connect(self.show_result)
        self.transfer.start()

    def show_result(self) -> None:
        transfer = self.transfer
        self.transfer = None
        self.files.setEnabled(True)
        self.folder.setEnabled(True)
        if transfer.error:
            message = transfer.error
        else:
            result = transfer.result
            message = (
                f"Potvrzeno přijímačem: {result.sent} · "
                f"Přeskočeno: {result.skipped} · Chyby: {result.failed}"
            )
            if result.last_status:
                message += f"\nPoslední chyba: {result.last_status}"
            if result.disconnected:
                message += "\nSpojení se přerušilo. Zbývající soubory nebyly odeslány."
            if not result.sent and not result.failed:
                message += "\nVe výběru se nenašly podporované DICOM snímky."
        self.status.setText(message)
        transfer.deleteLater()

    def closeEvent(self, event) -> None:
        if self.transfer is not None:
            self.status.setText("Před zavřením vyčkejte na dokončení přenosu.")
            event.ignore()
        else:
            event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    configure_logging(config.storage_root, filename="sender.log", console=False)
    app = QApplication([])
    ensure_font()
    window = SenderWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
