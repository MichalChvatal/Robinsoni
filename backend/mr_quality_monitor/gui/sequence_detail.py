"""Lazy metadata tree, actual/expected checks and per-sequence history."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
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

from .presentation import COLORS, STATUS_TEXT, display_value, reason_text, requirement_text
from .view_data import MonitorRow, known

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
        self.layers = QLabel("")
        self.layers.setTextFormat(Qt.TextFormat.PlainText)
        self.layers.setWordWrap(True)
        layout.addWidget(self.layers)
        self.tabs = QTabWidget()
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
        self.tabs.addTab(self.checks, "Požadavky a nálezy")
        self.tabs.addTab(self.metadata, "DICOM metadata")
        self.tabs.addTab(self.timeline, "Historie")
        layout.addWidget(self.tabs)
        self.records = ()

    def show_row(self, row: MonitorRow | None, history=(), stale=False):
        self.checks.setRowCount(0)
        self.metadata.clear()
        self.timeline.setRowCount(0)
        self.records = row.records if row else ()
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
