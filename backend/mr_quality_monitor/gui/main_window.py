"""Single-window, read-only monitor; all storage reads run in a dedicated Qt thread."""

import logging

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import ReceiverConfig
from .fonts import ensure_font
from .presentation import COLORS, STATUS_SYMBOLS, STATUS_TEXT, effective_status
from .sequence_detail import SequenceDetail, color_item, table
from .view_data import MonitorReader

LOG = logging.getLogger(__name__)


def label(text="", name=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


class ReadWorker(QObject):
    loaded = Signal(int, object)
    failed = Signal(int)

    def __init__(self, reader):
        super().__init__()
        ensure_font()
        self.reader = reader

    @Slot(int, object, object)
    def read(self, generation, study_uid, branch_id):
        try:
            self.loaded.emit(generation, self.reader.read(study_uid, branch_id))
        except Exception as error:
            LOG.error("monitor_read_failed type=%s", type(error).__name__)
            self.failed.emit(generation)


class StatusLegend(QWidget):
    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        self.setAccessibleName("Legenda stavů sekvencí")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.entries = []
        for status, caption in STATUS_TEXT.items():
            entry = label(caption)
            entry.setWordWrap(False)
            background, foreground = COLORS[status]
            entry.setStyleSheet(
                f"background: {background}; color: {foreground}; padding: 5px 8px;"
                "border-radius: 5px;"
            )
            self.entries.append(entry)
        self.columns = 0
        self._arrange(2)

    def _arrange(self, columns):
        if columns == self.columns:
            return
        self.columns = columns
        for index, entry in enumerate(self.entries):
            self.grid.addWidget(entry, *divmod(index, columns))

    def resizeEvent(self, event):
        single_row_width = sum(entry.sizeHint().width() for entry in self.entries)
        single_row_width += 3 * self.grid.horizontalSpacing()
        self._arrange(4 if event.size().width() >= single_row_width else 2)
        super().resizeEvent(event)


class MainWindow(QMainWindow):
    request_read = Signal(int, object, object)
    snapshot_applied = Signal()

    def __init__(self, config: ReceiverConfig, *, reader=None, refresh_interval_ms=1000):
        super().__init__()
        self.config = config
        self.study_uid, self.branch_id = None, None
        self.generation = 0
        self.busy = False
        self.pending = False
        self.closing = False
        self.snapshot = None
        self.row_key = None
        self.display_stale = True
        self.setWindowTitle("MR Quality Monitor — DEMO")
        self.resize(660, 540)
        self.setMinimumSize(400, 330)
        self._build()
        self.reader_thread = QThread(self)
        self.worker = ReadWorker(reader or MonitorReader(config.storage_root))
        self.worker.moveToThread(self.reader_thread)
        self.request_read.connect(self.worker.read)
        self.worker.loaded.connect(self._loaded)
        self.worker.failed.connect(self._failed)
        self.reader_thread.finished.connect(self.worker.deleteLater)
        self.reader_thread.finished.connect(self._thread_finished)
        self.reader_thread.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(refresh_interval_ms)
        QTimer.singleShot(0, self.refresh)

    def _build(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)
        self.setCentralWidget(central)
        heading = QHBoxLayout()
        heading.addWidget(label("MR Quality Monitor", "title"))
        heading.addStretch()
        heading.addWidget(label("DEMO · metadata", "demoBadge"))
        outer.addLayout(heading)
        selectors = QHBoxLayout()
        selectors.addWidget(label("Vyšetření"))
        self.study_combo = QComboBox()
        self.study_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.study_combo.setMinimumContentsLength(12)
        self.study_combo.currentIndexChanged.connect(self._study_changed)
        selectors.addWidget(self.study_combo, 2)
        outer.addLayout(selectors)
        self.state_label = label("○ Čeká na data", "stateBadge")
        outer.addWidget(self.state_label)
        self.message_label = label("Načítám místní přehled…", "message")
        outer.addWidget(self.message_label)
        self.legend = StatusLegend()
        outer.addWidget(self.legend)
        self.sequence_table = table(["Sekvence"])
        self.sequence_table.setObjectName("sequenceTable")
        self.sequence_table.setWordWrap(False)
        self.sequence_table.verticalHeader().setDefaultSectionSize(40)
        header = self.sequence_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sequence_table.itemClicked.connect(self._open_sequence)
        self.sequence_table.itemActivated.connect(self._open_sequence)
        self.sequence_table.itemSelectionChanged.connect(self._row_selected)
        outer.addWidget(self.sequence_table, 1)
        actions = QHBoxLayout()
        self.sequence_details_button = QPushButton("Detail sekvence")
        self.sequence_details_button.setEnabled(False)
        self.sequence_details_button.clicked.connect(self._open_sequence)
        actions.addWidget(self.sequence_details_button)
        actions.addStretch()
        self.study_details_button = QPushButton("Podrobnosti vyšetření")
        self.study_details_button.setCheckable(True)
        actions.addWidget(self.study_details_button)
        outer.addLayout(actions)

        card = QFrame()
        self.study_panel = card
        card.setObjectName("studyCard")
        card.hide()
        self.study_details_button.toggled.connect(card.setVisible)
        grid = QGridLayout(card)
        grid.addWidget(label("Větev historie"), 0, 0)
        self.branch_combo = QComboBox()
        self.branch_combo.currentIndexChanged.connect(self._branch_changed)
        grid.addWidget(self.branch_combo, 0, 1)
        refresh_button = QPushButton("Obnovit")
        refresh_button.clicked.connect(self.refresh)
        grid.addWidget(refresh_button, 0, 2)
        self.patient_label = label("Pacient: —")
        self.date_label = label("Datum studie: —")
        self.scanner_label = label("MR přístroj: —")
        self.protocol_label = label("Protokol: —")
        grid.addWidget(self.patient_label, 1, 0)
        grid.addWidget(self.date_label, 1, 1, 1, 2)
        grid.addWidget(self.scanner_label, 2, 0)
        grid.addWidget(self.protocol_label, 2, 1, 1, 2)
        self.receiver_label = label("Přijímač: stav neznámý", "receiverStatus")
        self.receiver_error_label = label("", "receiverError")
        grid.addWidget(self.receiver_label, 3, 0, 1, 3)
        grid.addWidget(self.receiver_error_label, 4, 0, 1, 3)
        outer.addWidget(card)

        self.detail_dialog = QDialog(self)
        self.detail_dialog.setWindowTitle("Detail sekvence")
        self.detail_dialog.resize(920, 560)
        detail_layout = QVBoxLayout(self.detail_dialog)
        self.detail = SequenceDetail()
        detail_layout.addWidget(self.detail, 1)
        close_detail = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_detail.button(QDialogButtonBox.StandardButton.Close).setText("Zavřít")
        close_detail.rejected.connect(self.detail_dialog.close)
        detail_layout.addWidget(close_detail)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fb; color: #243449;
                font-family: 'Segoe UI'; font-size: 10pt; }
            QLabel#title { font-size: 23pt; font-weight: 700; color: #16283e; }
            QLabel#demoBadge { background: #fff0cd; color: #805400; padding: 8px 14px;
                border-radius: 8px; font-weight: 700; }
            QFrame#studyCard { background: white; border: 1px solid #dbe2ec;
                border-radius: 9px; padding: 8px; }
            QFrame#studyCard QLabel { background: transparent; }
            QLabel#stateBadge { padding: 10px; border-radius: 7px; font-weight: 700; }
            QLabel#detailHeading { font-size: 11pt; font-weight: 600; padding: 4px 0; }
            QLabel#message { color: #785700; min-height: 20px; }
            QLabel#receiverStatus { font-weight: 600; padding-top: 4px; }
            QLabel#receiverError { color: #8b3333; }
            QTableWidget, QTreeWidget { background: white; alternate-background-color: #f8fafc;
                gridline-color: #e6ebf1; border: 1px solid #dbe2ec; }
            QHeaderView::section { background: #eaf0f7; padding: 8px 6px; border: none;
                border-bottom: 1px solid #d4deea; font-weight: 600; }
            QTableWidget::item { padding: 5px 4px; }
            QTableWidget::item:selected, QTreeWidget::item:selected {
                background: #dbe9fc; color: #16283e; }
            QComboBox, QPushButton { background: white; border: 1px solid #c9d3e1;
                padding: 7px; border-radius: 5px; }
            QPushButton:hover { background: #eaf1fc; }
            QTabWidget::pane { border: 1px solid #dbe2ec; }
            QTabBar::tab { background: #eaf0f7; padding: 8px 18px; }
            QTabBar::tab:selected { background: white; color: #245f9c; }
        """)

    @Slot()
    def refresh(self):
        if self.closing:
            return
        if self.busy:
            self.pending = True
            return
        self.busy = True
        self.request_read.emit(self.generation, self.study_uid, self.branch_id)

    def _selection_changed(self):
        self.generation += 1
        self.snapshot = None
        self.row_key = None
        self.sequence_table.setRowCount(0)
        self.detail_dialog.close()
        self.detail.show_row(None)
        self.patient_label.setText("Pacient: načítám…")
        self.protocol_label.setText("Protokol: načítám…")
        self.date_label.setText("Datum studie: —")
        self.scanner_label.setText("MR přístroj: —")
        self._state("GRAY", "Načítám vyšetření")
        self.message_label.setText("Načítám zvolené vyšetření…")
        self.message_label.show()
        self.refresh()

    def _study_changed(self):
        selected = self.study_combo.currentData()
        if selected == self.study_uid:
            return
        self.study_uid, self.branch_id = selected, None
        with QSignalBlocker(self.branch_combo):
            self.branch_combo.clear()
        self._selection_changed()

    def _branch_changed(self):
        selected = self.branch_combo.currentData()
        if selected == self.branch_id:
            return
        self.branch_id = selected
        self._selection_changed()

    @Slot(int, object)
    def _loaded(self, generation, snapshot):
        self.busy = False
        if not self.closing and generation == self.generation:
            redraw = (
                self.snapshot is None
                or self.snapshot.rows != snapshot.rows
                or self.snapshot.history != snapshot.history
                or self.display_stale != snapshot.stale
            )
            self.snapshot = snapshot
            self.study_uid, self.branch_id = snapshot.study_uid, snapshot.branch_id
            self.display_stale = snapshot.stale
            with QSignalBlocker(self.study_combo):
                self.study_combo.clear()
                for study in snapshot.studies:
                    self.study_combo.addItem(
                        "Studie …" + study["study_uid"][-22:], study["study_uid"]
                    )
                self.study_combo.setCurrentIndex(self.study_combo.findData(self.study_uid))
            with QSignalBlocker(self.branch_combo):
                self.branch_combo.clear()
                for branch in snapshot.branches:
                    self.branch_combo.addItem(
                        "Původní hodnocení" if branch["label"] == "main" else branch["label"],
                        branch["id"],
                    )
                self.branch_combo.setCurrentIndex(self.branch_combo.findData(self.branch_id))
            token = snapshot.patient_token
            self.patient_label.setText(
                "Pacient: lokální token " + token[:16]
                if token != "—"
                else "Pacient: dosud neindexován"
            )
            self.patient_label.setToolTip(
                "PatientID, jméno a přístupové číslo se v tomto režimu nezobrazují."
            )
            self.date_label.setText("Datum studie: " + snapshot.study_date)
            self.scanner_label.setText("MR přístroj: " + snapshot.scanner)
            self.protocol_label.setText("Protokol: " + snapshot.protocol_name)
            self._state(snapshot.status)
            self.message_label.setText(snapshot.message)
            self.message_label.setVisible(bool(snapshot.message))
            if redraw:
                self._draw_rows()
            self._health(snapshot.health)
            self.snapshot_applied.emit()
        if self.pending and not self.closing:
            self.pending = False
            QTimer.singleShot(0, self.refresh)

    @Slot(int)
    def _failed(self, generation):
        self.busy = False
        if not self.closing and generation == self.generation:
            self.display_stale = True
            self.message_label.setText(
                "Data nelze načíst. Ověřte dostupnost místního úložiště; "
                "zobrazené údaje mohou být zastaralé."
            )
            self.message_label.show()
            self._state(
                "RED" if self.snapshot and self.snapshot.status == "RED" else "GRAY",
                "Přehled není aktuální",
            )
            if self.snapshot:
                self._draw_rows()
            self.receiver_label.setText("Přijímač: stav se nepodařilo obnovit")
        if self.pending and not self.closing:
            self.pending = False
            QTimer.singleShot(0, self.refresh)

    def _state(self, status, text=None):
        background, foreground = COLORS[status]
        self.state_label.setText(text or STATUS_TEXT[status])
        self.state_label.setStyleSheet(f"background: {background}; color: {foreground};")

    def _draw_rows(self):
        snapshot = self.snapshot
        self.sequence_table.setUpdatesEnabled(False)
        with QSignalBlocker(self.sequence_table):
            self.sequence_table.setRowCount(len(snapshot.rows))
            for index, row in enumerate(snapshot.rows):
                status = effective_status(row, self.display_stale)
                item = QTableWidgetItem(f"{STATUS_SYMBOLS[status]}  {row.name}")
                item.setData(Qt.ItemDataRole.UserRole, row.key)
                description = f"{row.name} · {STATUS_TEXT[status]}"
                item.setData(Qt.ItemDataRole.AccessibleTextRole, description)
                item.setToolTip(description)
                color_item(item, status)
                self.sequence_table.setItem(index, 0, item)
            selected = next(
                (index for index, row in enumerate(snapshot.rows) if row.key == self.row_key), 0
            )
            if snapshot.rows:
                self.sequence_table.selectRow(selected)
        self.sequence_table.setUpdatesEnabled(True)
        self._row_selected()

    def _row_selected(self):
        index = self.sequence_table.currentRow()
        rows = self.snapshot.rows if self.snapshot else ()
        row = rows[index] if 0 <= index < len(rows) else None
        self.row_key = row.key if row else None
        self.sequence_details_button.setEnabled(row is not None)
        if self.detail_dialog.isVisible():
            if row:
                self.detail.show_row(row, self.snapshot.history, self.display_stale)
            else:
                self.detail_dialog.close()

    @Slot()
    def _open_sequence(self):
        if self.snapshot is None:
            return
        index = self.sequence_table.currentRow()
        if 0 <= index < len(self.snapshot.rows):
            self.detail.show_row(
                self.snapshot.rows[index], self.snapshot.history, self.display_stale, opening=True
            )
            self.detail_dialog.show()
            self.detail_dialog.raise_()
            self.detail_dialog.activateWindow()

    def _health(self, health):
        state = health.get("observed_state", "unknown")
        if state == "listening":
            associations = health.get("active_associations", 0)
            text = "Naslouchá" + (
                f" · aktivní asociace: {associations}"
                if associations
                else " · bez aktivní asociace"
            )
        else:
            text = {
                "stopped": "Zastaven",
                "unavailable": "Nedostupný — starý heartbeat",
                "unknown": "Stav neznámý",
            }.get(state, "Stav neznámý")
        endpoint = (
            f"{health.get('host', self.config.host)}:{health.get('port', self.config.port)}"
            f" · AE {health.get('ae_title', self.config.ae_title)}"
        )
        self.receiver_label.setText(
            f"Přijímač: {text}   |   {endpoint}"
            f"   |   objekty: {health.get('unique_instances', '—')}"
            f"   |   poslední příjem: {health.get('last_received_at') or '—'}"
        )
        error_messages = {
            "conflicting_instance": "Konflikt přijaté instance",
            "store_persistence_failed": "Příchozí objekt se nepodařilo bezpečně uložit",
            "store_invalid_dataset": "Příchozí objekt má neplatnou strukturu",
            "association_rejected_peer": "Nepovolený odesílatel",
            "association_rejected": "Odmítnuté DICOM spojení",
        }
        error_text = error_messages.get(str(health.get("last_error")), "Provozní událost přijímače")
        self.receiver_error_label.setText(
            f"Poslední provozní chyba: {error_text} · {health.get('last_error_at', '')}"
            if health.get("last_error")
            else ""
        )

    def closeEvent(self, event):
        if self.reader_thread.isRunning():
            self.detail_dialog.close()
            self.closing = True
            self.timer.stop()
            self.reader_thread.quit()
            self.statusBar().showMessage("Dokončuji čtení přehledu…")
            event.ignore()
        else:
            event.accept()

    @Slot()
    def _thread_finished(self):
        if self.closing:
            self.close()
