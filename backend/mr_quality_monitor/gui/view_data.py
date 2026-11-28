"""Read-only, consistent SQLite snapshots and presentation data, without Qt."""

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from pydicom.uid import MRImageStorage

from ..database.repository import checked_content
from ..services.receiver_health import read_health
from .completion import ExaminationCompletion, examination_completion


@dataclass(frozen=True)
class MonitorRow:
    key: str
    order: str
    name: str
    status: str
    records: tuple[dict, ...] = ()
    evaluations: tuple[dict, ...] = ()
    count: int = 0
    reason: str = ""
    action: str = ""
    requirements: tuple[dict, ...] = ()
    checks: tuple[dict, ...] = ()
    awaiting_analysis: bool = False
    plan_role: str = ""


@dataclass(frozen=True)
class MonitorSnapshot:
    studies: tuple[dict, ...]
    study_uid: str | None
    branches: tuple[dict, ...]
    branch_id: str | None
    rows: tuple[MonitorRow, ...]
    history: tuple[dict, ...]
    protocol_name: str
    patient_token: str
    study_date: str
    scanner: str
    status: str
    stale: bool
    received: int
    pending: int
    health: dict
    message: str = ""
    can_finalize: bool = False
    study_rows: tuple[MonitorRow, ...] = ()
    completion: ExaminationCompletion | None = None
    examination_name: str = ""


def known(record: dict, name: str):
    item = record.get("fields", {}).get(name, {})
    return item.get("value") if item.get("state") == "known" else None


def measured(records: tuple[dict, ...], field: str) -> str:
    values = [known(record, field) for record in records]
    numbers = sorted({value for value in values if isinstance(value, (float, int))})
    if not numbers:
        return "—"
    text = f"{numbers[0]:g}" if len(numbers) == 1 else f"{numbers[0]:g}–{numbers[-1]:g}"
    return text + (" *" if None in values else "")


def empty_snapshot(health: dict, message: str, protocol=None) -> MonitorSnapshot:
    waiting = tuple(
        MonitorRow(
            "seq:" + seq["sequence_id"],
            str(seq["order"]),
            seq["display_name"],
            "GRAY",
            reason="required_sequence_pending",
        )
        for seq in sorted((protocol or {}).get("sequences", ()), key=lambda seq: seq["order"])
    )
    return MonitorSnapshot(
        (), None, (), None, waiting, (), "—", "—", "—", "—", "GRAY", True, 0, 0, health, message
    )


class MonitorReader:
    """Never creates a database or runs migrations. Connections live for one read only."""

    def __init__(self, root: Path, *, intake_protocol=None, single_active_study: bool = False):
        self.root = root
        self.single_active_study = single_active_study
        self.intake_protocol = (
            intake_protocol
            if not single_active_study and (intake_protocol or {}).get("prostate_checks")
            else None
        )
        self._cache_key = None
        self._cache = None

    def read(self, study_uid: str | None = None, branch_id: str | None = None) -> MonitorSnapshot:
        health = read_health(self.root)
        path = self.root / "intake.sqlite3"
        if not path.is_file():
            if study_uid is not None and not self.single_active_study:
                raise OSError("Selected study storage is unavailable")
            return empty_snapshot(
                health, "Čeká na první data z MR.", self.intake_protocol
            )
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            versions = [
                tuple(row) for row in connection.execute("SELECT id,version FROM schema_version")
            ]
            if versions not in ([(1, 2)], [(1, 3)]):
                raise ValueError("Monitor requires migrated schema version 2 or 3")
            return self._read(connection, health, study_uid, branch_id)
        finally:
            connection.rollback()
            connection.close()

    def _read(self, db, health, selected, selected_branch):
        studies = [
            dict(row)
            for row in db.execute(
                "SELECT study_uid,MAX(stamp) AS last_seen FROM ("
                "SELECT study_uid,received_at AS stamp FROM dicom_instances UNION ALL "
                "SELECT study_uid,created_at AS stamp FROM studies) "
                "GROUP BY study_uid ORDER BY last_seen DESC LIMIT ?",
                (1 if self.single_active_study else 500,),
            )
        ]
        if not studies:
            self._cache_key = self._cache = None
            if selected is not None and not self.single_active_study:
                raise ValueError("Selected study is no longer available")
            return empty_snapshot(
                health, "Čeká na první data z MR.", self.intake_protocol
            )
        if self.single_active_study and selected != studies[0]["study_uid"]:
            selected, selected_branch = studies[0]["study_uid"], None
            self._cache_key = self._cache = None
        elif selected is None:
            selected = studies[0]["study_uid"]
        elif selected not in {item["study_uid"] for item in studies}:
            old = db.execute(
                "SELECT study_uid,created_at AS last_seen FROM studies WHERE study_uid=?",
                (selected,),
            ).fetchone()
            if old is None:
                old = db.execute(
                    "SELECT study_uid,MAX(received_at) AS last_seen FROM dicom_instances "
                    "WHERE study_uid=? GROUP BY study_uid",
                    (selected,),
                ).fetchone()
            if old is None:
                raise ValueError("Selected study is unavailable")
            studies.append(dict(old))
        branches = tuple(
            dict(row)
            for row in db.execute(
                "SELECT id,label,protocol_sha256 FROM analysis_branches WHERE study_uid=? "
                "ORDER BY CASE WHEN label='main' THEN 0 ELSE 1 END,created_at",
                (selected,),
            )
        )
        branch = next((item for item in branches if item["id"] == selected_branch), None)
        if selected_branch is not None and branch is None:
            if not self.single_active_study:
                raise ValueError("Selected history branch is unavailable")
            self._cache_key = self._cache = None
        branch = branch or (branches[0] if branches else None)
        received = db.execute(
            "SELECT COUNT(*) FROM dicom_instances WHERE study_uid=?", (selected,)
        ).fetchone()[0]
        pending, failed = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(d.integrity_state!='stored' OR j.state='failed'),0) "
            "FROM dicom_instances d LEFT JOIN analysis_jobs j ON j.instance_id=d.id "
            "WHERE d.study_uid=? AND d.sop_class_uid=? AND "
            "(d.integrity_state!='stored' OR j.state='failed' OR NOT EXISTS "
            "(SELECT 1 FROM metadata_snapshots m WHERE m.instance_id=d.id))",
            (selected, MRImageStorage),
        ).fetchone()
        study = db.execute(
            "SELECT s.revision,p.token FROM studies s JOIN patients p ON p.id=s.patient_id "
            "WHERE s.study_uid=?",
            (selected,),
        ).fetchone()
        protocol = self.intake_protocol
        run = None
        if branch:
            protocol_row = db.execute(
                "SELECT content,sha256 FROM protocol_versions WHERE sha256=?",
                (branch["protocol_sha256"],),
            ).fetchone()
            protocol = checked_content(protocol_row["content"], protocol_row["sha256"])
            run = db.execute(
                "SELECT * FROM validation_runs WHERE branch_id=? "
                "ORDER BY sequence_number DESC LIMIT 1",
                (branch["id"],),
            ).fetchone()
        stale = run is None or run["study_revision"] != study["revision"] or pending > 0
        payload, group_records = self._run_data(db, run)
        rows = self._rows(db, selected, protocol, payload, group_records)
        study_rows = tuple(row for row in rows if row.key.startswith("study:"))
        rows = tuple(row for row in rows if not row.key.startswith("study:"))
        live_protocol = bool(protocol and protocol.get("study_identifiers"))
        if live_protocol or (protocol and protocol.get("prostate_checks")):
            study_rows += tuple(row for row in rows if not row.key.startswith("seq:"))
            rows = tuple(row for row in rows if row.key.startswith("seq:"))
        if live_protocol:
            rules = protocol.get("study_rules") or {}
            alternatives = {rule["group"] for rule in rules.get("min_valid_groups", ())}
            fallbacks = {
                sid for rule in rules.get("fallback_groups", ())
                for sid in rule["fallback_sequence_ids"]
            }
            roles = {
                item["sequence_id"]: (
                    "Povinná" if item["required"]
                    else "Náhradní" if item["sequence_id"] in fallbacks
                    else "Skupinový požadavek" if item["group"] in alternatives
                    else "Nepovinná"
                )
                for item in protocol["sequences"]
            }
            pending_series = {
                row.key.split(":", 1)[1]
                for row in study_rows
                if row.key.startswith(("raw:", "failed:"))
            }
            rows = tuple(
                replace(
                    row,
                    plan_role=roles[row.key.removeprefix("seq:")],
                    awaiting_analysis=any(
                        known(record, "SeriesInstanceUID") in pending_series
                        for record in row.records
                    ),
                )
                for row in rows
            )
        completion = (
            examination_completion(protocol, payload, rows, stale=stale, failed=failed)
            if live_protocol else None
        )
        history = self._history(db, branch["id"]) if branch else ()
        records = tuple(record for members in group_records.values() for record in members)
        examination_fields = [
            next(
                (str(known(record, field)).strip() for record in records if known(record, field)),
                "",
            )
            for field in ("BodyPartExamined", "StudyDescription")
        ]
        examination_name = " – ".join(value for value in examination_fields if value)
        date = next(
            (known(record, "StudyDate") for record in records if known(record, "StudyDate")), "—"
        )
        scanner = next(
            (
                " ".join(
                    str(value)
                    for value in (
                        known(record, "Manufacturer"),
                        known(record, "ManufacturerModelName"),
                    )
                    if value
                )
                for record in records
                if known(record, "Manufacturer")
            ),
            "—",
        )
        status = run["status"] if run else "GRAY"
        if stale and status != "RED":
            status = "GRAY"
        message = ""
        if failed:
            message = f"Analýza selhala u {failed} MR objektů. Zkontrolujte lokální úložiště."
        elif self.single_active_study and not branch:
            message = "Rozpoznává se protokol vyšetření."
        elif stale:
            message = "Čeká na zpracování nebo přehodnocení dat."
        elif payload.get("finalized") and protocol and (
            protocol.get("prostate_checks") or protocol.get("study_rules")
        ):
            message = "Vyšetření bylo uzavřeno."
        return MonitorSnapshot(
            tuple(studies),
            selected,
            branches,
            branch["id"] if branch else None,
            rows,
            history,
            f"{protocol['display_name']} · {protocol['version']}"
            if protocol
            else "Zatím nepřiřazen",
            study["token"] if study else "—",
            date,
            scanner,
            status,
            stale,
            received,
            pending,
            health,
            message,
            bool(
                protocol
                and (
                    live_protocol or protocol.get("prostate_checks") or protocol.get("study_rules")
                )
                and branch
                and branch["label"] == "main"
                and not stale
                and not payload.get("finalized")
            ),
            study_rows,
            completion,
            examination_name,
        )

    def _run_data(self, db, run):
        if run is None:
            return {}, {}
        payload = checked_content(run["content"], run["content_sha256"])
        if self._cache_key == (run["id"], run["content_sha256"]):
            return payload, self._cache
        result = {}
        seen = set()
        for row in db.execute(
            "SELECT a.group_key,a.content AS acquisition_content,a.sha256 AS acquisition_sha,"
            "m.content,m.sha256 FROM validation_results r "
            "JOIN logical_acquisitions a ON a.sha256=r.acquisition_sha256 "
            "JOIN acquisition_members x ON x.acquisition_sha256=a.sha256 "
            "JOIN metadata_snapshots m ON m.sha256=x.metadata_sha256 WHERE r.run_id=?",
            (run["id"],),
        ):
            checked_content(row["acquisition_content"], row["acquisition_sha"])
            key = (row["group_key"], row["sha256"])
            if key not in seen:
                result.setdefault(row["group_key"], []).append(
                    checked_content(row["content"], row["sha256"])
                )
                seen.add(key)
        result = {key: tuple(value) for key, value in result.items()}
        self._cache_key, self._cache = (run["id"], run["content_sha256"]), result
        return payload, result

    @staticmethod
    def _rows(db, study_uid, protocol, payload, records_by_group):
        rows = []
        evaluations = {item["group_key"]: item for item in payload.get("acquisitions", [])}
        presence = {item["sequence_id"]: item for item in payload.get("sequences", [])}
        for definition in (
            sorted(protocol["sequences"], key=lambda item: item["order"]) if protocol else ()
        ):
            item = presence.get(definition["sequence_id"], {})
            matched = tuple(
                value
                for value in evaluations.values()
                if value["sequence_id"] == definition["sequence_id"]
            )
            possible = tuple(
                value
                for value in evaluations.values()
                if value["sequence_id"] is None
                and any(
                    candidate["sequence_id"] == definition["sequence_id"]
                    and candidate["decision"] != "mismatch"
                    for candidate in value["identification"]["candidates"]
                )
            )
            details = matched or possible
            records = tuple(
                record
                for value in details
                for record in records_by_group.get(value["group_key"], ())
            )
            action = next(
                (
                    check.get("action", "")
                    for value in matched
                    for check in value["checks"]
                    if check["status"] == "RED"
                ),
                "",
            )
            if not action:
                action = next(
                    (
                        check.get("action", "")
                        for value in matched
                        for check in value["checks"]
                        if check["status"] != "GREEN"
                    ),
                    "",
                )
            rows.append(
                MonitorRow(
                    "seq:" + definition["sequence_id"],
                    str(definition["order"]),
                    definition["display_name"],
                    item.get("status", "GRAY"),
                    records,
                    details,
                    len(records),
                    item.get("reason", "required_sequence_pending"),
                    action,
                    requirements=tuple(definition["parameters"]),
                )
            )
            if len(matched) > 1 and not protocol.get("prostate_checks"):
                for index, value in enumerate(matched, 1):
                    members = records_by_group.get(value["group_key"], ())
                    name = known(members[0], "SeriesDescription") if members else None
                    rows.append(
                        MonitorRow(
                            "acq:" + value["group_key"],
                            f"{definition['order']}.{index}",
                            "↳ " + (name or "Dílčí akvizice"),
                            value["status"],
                            members,
                            (value,),
                            len(members),
                            ";".join(value["reasons"]),
                            action,
                        )
                    )
        for key, value in evaluations.items():
            if value["sequence_id"] is None:
                members = records_by_group.get(key, ())
                name = known(members[0], "SeriesDescription") if members else None
                rows.append(
                    MonitorRow(
                        "acq:" + key,
                        "—",
                        name or "Neznámá sekvence",
                        value["status"],
                        members,
                        (value,),
                        len(members),
                        ";".join(value["reasons"]),
                        "Ověřit zařazení sekvence",
                    )
                )
        study_names = {
            "t2_planes": "T2W · počet vyhovujících rovin",
            "axial_t2": "T2W · přítomnost axiální roviny",
            "dwi_set": "DWI · stav celé sady",
            "resolve_presence": "DWI · prověření RESOLVE",
        }
        for check in payload.get("checks", ()):
            rows.append(
                MonitorRow(
                    "study:" + check["rule_id"],
                    "—",
                    study_names[check["rule_id"]],
                    check["status"],
                    reason=check["reason"],
                    checks=(check,),
                )
            )
        clinical_study_names = {
            "min_valid_group": "Minimální počet vyhovujících sekvencí",
            "fallback_group": "Kontrola náhradní sekvence",
            "geometry_alignment": "Geometrická shoda sekvencí",
            "anatomical_coverage": "Anatomické pokrytí",
        }
        for check in payload.get("study_checks", ()):
            rows.append(
                MonitorRow(
                    "study:" + check["rule_id"],
                    "-",
                    clinical_study_names.get(check["kind"], check["rule_id"]),
                    check["status"],
                    reason=check["reason"],
                    action=check["action"],
                )
            )
        indexed = {
            known(record, "SOPInstanceUID")
            for records in records_by_group.values()
            for record in records
        }
        unprocessed = {}
        for item in db.execute(
            "SELECT d.sop_uid,d.series_uid,"
            "d.integrity_state!='stored' OR COALESCE(j.state='failed',0) AS failed "
            "FROM dicom_instances d LEFT JOIN analysis_jobs j ON j.instance_id=d.id "
            "WHERE d.study_uid=? AND d.sop_class_uid=?",
            (study_uid, MRImageStorage),
        ):
            if item["failed"] or item["sop_uid"] not in indexed:
                key = (item["series_uid"], item["failed"])
                unprocessed[key] = unprocessed.get(key, 0) + 1
        for (series_uid, failed), count in unprocessed.items():
            rows.append(
                MonitorRow(
                    ("failed:" if failed else "raw:") + series_uid,
                    "—",
                    "Chyba při analýze snímků" if failed else "Přijatá nezpracovaná série",
                    "GRAY",
                    count=count,
                    reason="analysis_input_failed" if failed else "metadata_not_processed",
                    action="Zkontrolovat úložiště" if failed else "Vyčkat na analýzu",
                )
            )
        return tuple(rows)

    @staticmethod
    def _history(db, branch_id):
        runs = [
            dict(row)
            for row in db.execute(
                "SELECT id,sequence_number,evaluated_at,status,actor FROM validation_runs "
                "WHERE branch_id=? ORDER BY sequence_number DESC LIMIT 100",
                (branch_id,),
            )
        ]
        if not runs:
            return ()
        by_id = {row["id"]: row for row in runs}
        for row in runs:
            row["statuses"] = {}
        marks = ",".join("?" for _ in runs)
        for row in db.execute(
            "SELECT r.run_id,r.scope,r.status,s.sequence_id,a.group_key FROM validation_results r "
            "LEFT JOIN sequence_definitions s ON s.id=r.sequence_definition_id "
            "LEFT JOIN logical_acquisitions a ON a.sha256=r.acquisition_sha256 "
            f"WHERE r.run_id IN ({marks})",
            tuple(by_id),
        ):
            key = (
                "seq:" + row["sequence_id"]
                if row["scope"] == "sequence_presence"
                else "acq:" + row["group_key"]
            )
            by_id[row["run_id"]]["statuses"][key] = row["status"]
        return tuple(runs)
