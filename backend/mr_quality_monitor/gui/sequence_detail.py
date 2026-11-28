"""Lazy metadata tree, actual/expected checks and per-sequence history."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .presentation import (
    COLORS,
    STATUS_TEXT,
    critical_overview,
    display_value,
    reason_text,
    requirement_text,
)
from .view_data import MonitorRow, known, measured

STATUS_ROLE = Qt.ItemDataRole.UserRole + 1


class StatusDelegate(QStyledItemDelegate):
    """Keep the status color visible even while its whole row is selected."""

    def paint(self, painter, option, index):
        if index.data(STATUS_ROLE) in COLORS:
            option = QStyleOptionViewItem(option)
            option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setItemDelegate(StatusDelegate(widget))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def color_item(item, status):
    item.setData(STATUS_ROLE, status)
    background, foreground = COLORS.get(status, COLORS["GRAY"])
    item.setBackground(QColor(background))
    item.setForeground(QColor(foreground))


class SequenceDetail(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("Vyberte sekvenci pro zobrazení detailu")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setObjectName("detailHeading")
        layout.addWidget(self.heading)
        self.tabs = QTabWidget()
        overview = QScrollArea()
        overview.setWidgetResizable(True)
        overview_content = QWidget()
        overview_layout = QVBoxLayout(overview_content)
        overview_layout.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Proč je sekvence červená?")
        title.setStyleSheet("font-size: 16pt; font-weight: 700; color: #a02424;")
        overview_layout.addWidget(title)
        self.explanation = QLabel()
        self.explanation.setTextFormat(Qt.TextFormat.PlainText)
        self.explanation.setWordWrap(True)
        self.explanation.setStyleSheet("font-size: 12pt;")
        self.explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        overview_layout.addWidget(self.explanation)
        overview_layout.addStretch()
        overview.setWidget(overview_content)
        self.tabs.addTab(overview, "Přehled")
        technical = QWidget()
        technical_layout = QVBoxLayout(technical)
        self.layers = QLabel("")
        self.layers.setTextFormat(Qt.TextFormat.PlainText)
        self.layers.setWordWrap(True)
        technical_layout.addWidget(self.layers)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        technical_layout.addWidget(self.summary)
        self.checks = table(
            ["Parametr / kontrola", "Skutečnost", "Požadavek", "Stav", "Zdroj a důvod"]
        )
        self.metadata = QTreeWidget()
        self.metadata.setHeaderLabels(
            ["Parametr / snímek", "Hodnota", "Jednotka", "Dostupnost", "Zdroj"]
        )
        self.metadata.setAlternatingRowColors(True)
        self.metadata.itemExpanded.connect(self._expand)
        self.timeline = table(["Běh", "Čas hodnocení (UTC)", "Stav vybrané sekvence", "Autor"])
        technical_layout.addWidget(self.checks, 1)
        self.tabs.addTab(technical, "Požadavky a nálezy")
        self.tabs.addTab(self.metadata, "DICOM metadata")
        self.tabs.addTab(self.timeline, "Historie")
        layout.addWidget(self.tabs)
        self.records = ()
        self.row_key = None

    def show_row(self, row: MonitorRow | None, history=(), stale=False, *, opening=False):
        row_key = row.key if row else None
        critical = row is not None and row.status == "RED"
        show_default = opening or row_key != self.row_key or self.tabs.isTabVisible(0) != critical
        self.row_key = row_key
        self.tabs.setTabVisible(0, critical)
        if show_default:
            self.tabs.setCurrentIndex(0 if critical else 1)
        explanation = critical_overview(row) if critical else ""
        if critical and stale:
            explanation = "Zastaralý výsledek. Níže je poslední uložený nález.\n\n" + explanation
        self.explanation.setText(explanation)
        self.checks.setRowCount(0)
        self.metadata.clear()
        self.timeline.setRowCount(0)
        self.records = row.records if row else ()
        summary = [f"Snímky / rámce: {row.count}"] if row else []
        if row:
            for title, field, unit in (
                ("TR", "RepetitionTime", "ms"),
                ("TE", "EchoTime", "ms"),
                ("Tloušťka", "SliceThickness", "mm"),
            ):
                value = measured(row.records, field)
                if value != "—":
                    summary.append(f"{title}: {value} {unit}")
            if row.reason:
                summary.append(reason_text(row.reason))
            if row.action:
                summary.append(row.action)
        self.summary.setText(" · ".join(summary))
        if row and row.evaluations:
            metadata = ", ".join(
                dict.fromkeys(STATUS_TEXT[item["metadata_status"]] for item in row.evaluations)
            )
            completion = ", ".join(
                dict.fromkeys(STATUS_TEXT[item["completeness_status"]] for item in row.evaluations)
            )
            image = (
                "nevyhodnocena"
                if any(item["image_required"] for item in row.evaluations)
                else "není součástí DEMO"
            )
            self.layers.setText(
                f"Metadata: {metadata}   |   Dokončení: {completion}"
                f"   |   Obrazová kontrola: {image}"
            )
        else:
            self.layers.setText(
                "Metadata a dokončení čekají na vyhodnocení; obrazová kvalita nehodnocena."
                if row
                else ""
            )
        self.heading.setText(
            (row.name + (" · zastaralé hodnocení" if stale else ""))
            if row
            else "Vyberte sekvenci pro zobrazení detailu"
        )
        checks = []
        if row:
            for evaluation in row.evaluations:
                checks.extend(evaluation.get("checks", ()))
                if evaluation["sequence_id"] is None:
                    for candidate in evaluation["identification"]["candidates"]:
                        if candidate["decision"] == "mismatch":
                            continue
                        for evidence in candidate["evidence"]:
                            checks.append(
                                {
                                    "field": evidence["field"],
                                    "actual": evidence["actual"],
                                    "expected": evidence["expected"],
                                    "status": "GRAY",
                                    "reason": (
                                        f"Kandidát {candidate['sequence_id']}: "
                                        f"{evidence['decision']}"
                                    ),
                                    "paths": evidence["paths"],
                                    "sop_uid": evidence["sop_uid"],
                                }
                            )
            if not checks:
                checks = [
                    {
                        "field": rule["field"],
                        "actual": None,
                        "expected": rule,
                        "status": "GRAY",
                        "reason": row.reason,
                        "paths": (),
                    }
                    for rule in row.requirements
                ]
        self.checks.setRowCount(len(checks))
        for index, check in enumerate(checks):
            values = (
                check.get("field") or check.get("rule_id", "Kontrola"),
                display_value(check.get("actual")),
                requirement_text(check.get("expected", {})),
                STATUS_TEXT[check["status"]],
                reason_text(check["reason"]) + " · " + ", ".join(check.get("paths", ())),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(str(check.get("sop_uid") or ""))
                self.checks.setItem(index, column, item)
            color_item(self.checks.item(index, 3), check["status"])
        fields = sorted({field for record in self.records for field in record.get("fields", {})})
        for field in fields:
            entries = [record["fields"].get(field, {}) for record in self.records]
            values = {display_value(entry.get("value")) for entry in entries}
            states = sorted({entry.get("state", "missing") for entry in entries})
            suppressed = any(entry.get("reason") == "suppressed" for entry in entries)
            summary = next(iter(values)) if len(values) == 1 else f"{len(values)} různých hodnot"
            item = QTreeWidgetItem(
                [
                    field,
                    summary,
                    str(entries[0].get("unit") or ""),
                    "nezobrazuje se" if suppressed else ", ".join(states),
                    "Rozbalte pro jednotlivé snímky",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, field)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            self.metadata.addTopLevelItem(item)
        self.metadata.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.metadata.header().setStretchLastSection(True)
        self.timeline.setRowCount(len(history))
        for index, run in enumerate(history):
            status = run["statuses"].get(row.key) if row else run["status"]
            values = (
                str(run["sequence_number"]),
                run["evaluated_at"],
                STATUS_TEXT.get(status, "—"),
                run["actor"],
            )
            for column, value in enumerate(values):
                self.timeline.setItem(index, column, QTableWidgetItem(value))
            if status:
                color_item(self.timeline.item(index, 2), status)

    def _expand(self, item):
        field = item.data(0, Qt.ItemDataRole.UserRole)
        if not field or item.childCount():
            return
        for record in self.records:
            value = record["fields"].get(field, {})
            uid = str(known(record, "SOPInstanceUID") or "neznámý")
            frame = record.get("frame_index")
            name = "…" + uid[-12:] + (f" / rámec {frame + 1}" if frame is not None else "")
            paths = ", ".join(source["path"] for source in value.get("sources", ()))
            child = QTreeWidgetItem(
                [
                    name,
                    display_value(value.get("value")),
                    str(value.get("unit") or ""),
                    value.get("state", "missing"),
                    paths,
                ]
            )
            child.setToolTip(0, uid)
            item.addChild(child)
