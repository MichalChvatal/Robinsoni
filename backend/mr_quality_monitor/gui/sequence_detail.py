"""Lazy metadata tree, actual/expected checks and per-sequence history."""

import json

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .presentation import (
    COLORS,
    STATUS_TEXT,
    critical_description,
    display_value,
    effective_status,
    reason_text,
    requirement_text,
    sequence_overview,
)
from .view_data import MonitorRow, known, measured

STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
ISSUES_ROLE = Qt.ItemDataRole.UserRole + 2
BADGE_TOOLTIPS_ROLE = Qt.ItemDataRole.UserRole + 3


class StatusDelegate(QStyledItemDelegate):
    """Keep the status color visible even while its whole row is selected."""

    def paint(self, painter, option, index):
        if index.data(STATUS_ROLE) in COLORS:
            option = QStyleOptionViewItem(option)
            option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)


class SequenceStatusDelegate(StatusDelegate):
    """Show parameter and image issues as compact badges inside a single cell."""

    def _badges(self, option, index):
        return [
            (image, status, "Obraz" if image else "Parametry")
            for image, status in (index.data(ISSUES_ROLE) or ())
        ]

    def sizeHint(self, option, index):
        badges = self._badges(option, index)
        width = sum(self._badge_widths(option)) + 16
        stacked = bool(badges) and self.parent().viewport().width() < width + 240
        return QSize(0, 70 if stacked else 44)

    def _badge_widths(self, option):
        return tuple(
            option.fontMetrics.horizontalAdvance(text) + 40 for text in ("Parametry", "Obraz")
        )

    def _layout(self, option, index):
        badges = self._badges(option, index)
        widths = self._badge_widths(option)
        total = sum(widths) + 16
        stacked = option.rect.width() < total + 240
        name_rect = option.rect.adjusted(
            0, 0, -total if not stacked else 0, -32 if stacked else 0,
        )
        x = option.rect.left() + 8 if stacked else option.rect.right() - total + 1
        y = option.rect.bottom() - 31 if stacked else option.rect.center().y() - 14
        positioned = []
        for image, status, text in badges:
            offset = widths[0] + 8 if image else 0
            positioned.append((image, status, text, QRect(x + offset, y, widths[int(image)], 28)))
        return name_rect, positioned

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.Type.ToolTip:
            return super().helpEvent(event, view, option, index)
        _, badges = self._layout(option, index)
        tooltips = index.data(BADGE_TOOLTIPS_ROLE) or ()
        for (_, _, _, rect), tooltip in zip(badges, tooltips):
            if rect.contains(event.pos()):
                QToolTip.showText(event.globalPos(), tooltip, view.viewport(), rect)
                return True
        QToolTip.hideText()
        event.accept()
        return True

    def paint(self, painter, option, index):
        if not index.data(ISSUES_ROLE):
            return super().paint(painter, option, index)
        name_rect, badges = self._layout(option, index)
        painter.save()
        painter.fillRect(option.rect, index.data(Qt.ItemDataRole.BackgroundRole))
        name_option = QStyleOptionViewItem(option)
        name_option.rect = name_rect
        super().paint(painter, name_option, index)
        for image, status, text, rect in badges:
            x, y = rect.x(), rect.y()
            background, foreground = COLORS[status]
            painter.setPen(QPen(QColor(foreground), 1))
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
            painter.save()
            painter.translate(x + 8, y + 6)
            painter.setPen(QPen(QColor(foreground), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if image:
                painter.drawRect(0, 1, 16, 13)
                painter.drawEllipse(10, 3, 3, 3)
                painter.drawLine(1, 12, 6, 7)
                painter.drawLine(6, 7, 10, 11)
                painter.drawLine(10, 11, 13, 8)
                painter.drawLine(13, 8, 16, 11)
            else:
                for line, knob in ((3, 5), (8, 11), (13, 7)):
                    painter.drawLine(0, line, 16, line)
                    painter.setBrush(QColor(background))
                    painter.drawEllipse(knob - 2, line - 2, 4, 4)
            painter.restore()
            painter.setPen(QColor(foreground))
            painter.drawText(rect.adjusted(30, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


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


def _image_reference(record, uid, frame):
    series = known(record, "SeriesNumber")
    number = known(record, "InstanceNumber")
    parts = [f"Série {series}"] if series is not None else []
    parts.append(f"Snímek {number}" if number is not None else "Snímek")
    parts.append(f"…{uid[-12:]}" if len(uid) > 12 else uid)
    if frame is not None:
        parts.append(f"rámec {frame + 1}")
    return " · ".join(parts)


def _check_reference(check, acquisition, records):
    if uid := check.get("sop_uid"):
        frame = check.get("frame_index")
        record = records.get((uid, frame), {})
        image = _image_reference(record, uid, frame)
        reference = f"Akv. {acquisition} · {image}" if acquisition else image
        return reference, f"SOP Instance UID: {uid}"
    if acquisition:
        return f"Akv. {acquisition} · Souhrn akvizice", ""
    return ("Celé vyšetření" if acquisition is None else "Plán sekvence"), ""


def _group_checks(checks):
    groups = {}
    for acquisition, check in checks:
        signature = {
            key: value for key, value in check.items()
            if key not in {"sop_uid", "frame_index", "paths"}
        }
        if check.get("rule_id", "").startswith("image:"):
            signature.update(
                acquisition=acquisition,
                sop_uid=check.get("sop_uid"),
                frame_index=check.get("frame_index"),
            )
        key = (bool(check.get("sop_uid")), json.dumps(signature, sort_keys=True))
        group = groups.setdefault(key, [])
        entry = (acquisition, check)
        if entry not in group:
            group.append(entry)
    return groups.values()


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
        self.overview_title = QLabel()
        overview_layout.addWidget(self.overview_title)
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
            [
                "Parametr / kontrola", "Vyhodnocená data", "Skutečnost",
                "Požadavek", "Stav", "Zdroj a důvod",
            ]
        )
        self.show_valid = QPushButton("Zobrazit vyhovující")
        self.show_valid.setCheckable(True)
        self.show_valid.toggled.connect(self._filter_checks)
        technical_layout.addWidget(self.show_valid, alignment=Qt.AlignmentFlag.AlignLeft)
        self.no_findings = QLabel("Všechny položky vyhovují.")
        self.no_findings.hide()
        technical_layout.addWidget(self.no_findings)
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
        self.acquisitions = table(["Akvizice", "Stav", "Nálezy"])
        self.tabs.addTab(self.acquisitions, "Jednotlivé akvizice")
        layout.addWidget(self.tabs)
        self.records = ()
        self.row_key = None

    def show_row(self, row: MonitorRow | None, history=(), stale=False, *, opening=False):
        row_key = row.key if row else None
        show_default = opening or row_key != self.row_key
        self.row_key = row_key
        if show_default:
            self.tabs.setCurrentIndex(0)
        status = effective_status(row, stale) if row else "GRAY"
        self.overview_title.setText(
            "Proč je sekvence červená?" if status == "RED" else STATUS_TEXT[status]
        )
        self.overview_title.setStyleSheet(
            f"font-size: 16pt; font-weight: 700; color: {COLORS[status][1]};"
        )
        statuses = {"GRAY", "ORANGE"} if status == "GRAY" else {status} - {"GREEN"}
        explanation = sequence_overview(row, statuses) if row else ""
        if row and status == "GREEN" and not row.checks:
            explanation = "Sekvence splnila kontrolované technické požadavky."
        if row and row.checks and status == "GREEN":
            explanation = critical_description(row.checks[0])
        if row:
            alignment = [
                critical_description(check)
                for result in row.evaluations
                for check in result.get("checks", ())
                if check.get("field") == "DWIAlignment" and check.get("status") not in statuses
            ]
            if alignment:
                explanation += "\n\n" + "\n".join(dict.fromkeys(alignment))
            if any(result.get("protocol_id") == "MOU_PROSTATE" for result in row.evaluations):
                explanation += "\n\nObrazová kvalita se touto technickou kontrolou nehodnotí."
        if row and stale:
            explanation = "Zastaralý výsledek. Níže je poslední uložený nález.\n\n" + explanation
        self.explanation.setText(explanation)
        self.checks.setRowCount(0)
        self.metadata.clear()
        self.timeline.setRowCount(0)
        self.records = row.records if row else ()
        repeats = [
            (number, item)
            for number, item in enumerate(row.evaluations if row else (), start=1)
            if item.get("sequence_id")
        ]
        self.tabs.setTabVisible(self.tabs.indexOf(self.acquisitions), len(repeats) > 1)
        self.acquisitions.setRowCount(len(repeats) if len(repeats) > 1 else 0)
        if len(repeats) > 1:
            for index, (number, item) in enumerate(repeats):
                status = "GRAY" if stale and item["status"] != "RED" else item["status"]
                findings = dict.fromkeys(
                    critical_description(check)
                    for check in item["checks"]
                    if check["status"] != "GREEN"
                )
                for column, text in enumerate(
                    (str(number), STATUS_TEXT[status], "\n".join(findings) or "-")
                ):
                    cell = QTableWidgetItem(text)
                    cell.setToolTip(text)
                    self.acquisitions.setItem(index, column, cell)
                color_item(self.acquisitions.item(index, 1), status)
            self.acquisitions.resizeRowsToContents()
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
            image = ", ".join(
                dict.fromkeys(
                    STATUS_TEXT[item.get("image_status", "GRAY")]
                    if item.get("image_status", "GRAY") != "GRAY"
                    else "nevyhodnocena"
                    for item in row.evaluations
                    if item["image_required"]
                )
            ) or "nepožadována"
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
        checks = [(None, check) for check in row.checks] if row else []
        if row:
            for acquisition, evaluation in enumerate(row.evaluations, start=1):
                checks.extend(
                    (acquisition, detail)
                    for check in evaluation.get("checks", ())
                    for detail in (check.get("per_image") or (check,))
                )
                if evaluation["sequence_id"] is None:
                    for candidate in evaluation["identification"]["candidates"]:
                        if candidate["decision"] == "mismatch":
                            continue
                        for evidence in candidate["evidence"]:
                            checks.append(
                                (acquisition, {
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
                                    "frame_index": evidence.get("frame_index"),
                                })
                            )
            if not checks:
                checks = [
                    (0, {
                        "field": rule["field"],
                        "actual": None,
                        "expected": rule,
                        "status": "GRAY",
                        "reason": row.reason,
                        "paths": (),
                    })
                    for rule in row.requirements
                ]
        records = {
            (known(record, "SOPInstanceUID"), record.get("frame_index")): record
            for record in self.records
        }
        grouped_checks = list(_group_checks(checks))
        self.checks.setRowCount(len(grouped_checks))
        for index, group in enumerate(grouped_checks):
            acquisition, check = group[0]
            references = [
                _check_reference(member, number, records) for number, member in group
            ]
            reference, uid_tooltip = references[0]
            if len(group) > 1:
                reference = f"Společné pro {len(group)} snímků / rámců"
                if not check.get("sop_uid"):
                    reference = f"Společné pro {len(group)} akvizic"
                uid_tooltip = "\n".join(
                    text + ("\n" + tooltip if tooltip else "") for text, tooltip in references
                )
            paths = dict.fromkeys(
                path for _, member in group for path in member.get("paths", ())
            )
            values = (
                check.get("field") or check.get("rule_id", "Kontrola"),
                reference,
                display_value(check.get("actual")),
                requirement_text(check.get("expected", {})),
                STATUS_TEXT[check["status"]],
                reason_text(check["reason"]) + " · " + ", ".join(paths),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column == 1 and uid_tooltip:
                    item.setToolTip(uid_tooltip)
                self.checks.setItem(index, column, item)
            color_item(self.checks.item(index, 4), check["status"])
        self._filter_checks()
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

    def _filter_checks(self):
        visible = 0
        for index in range(self.checks.rowCount()):
            valid = self.checks.item(index, 4).data(STATUS_ROLE) == "GREEN"
            hidden = valid and not self.show_valid.isChecked()
            self.checks.setRowHidden(index, hidden)
            visible += not hidden
        self.no_findings.setVisible(self.checks.rowCount() > 0 and visible == 0)

    def _expand(self, item):
        field = item.data(0, Qt.ItemDataRole.UserRole)
        if not field or item.childCount():
            return
        for record in self.records:
            value = record["fields"].get(field, {})
            uid = str(known(record, "SOPInstanceUID") or "neznámý")
            frame = record.get("frame_index")
            name = _image_reference(record, uid, frame)
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
