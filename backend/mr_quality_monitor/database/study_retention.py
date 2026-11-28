"""Remove replaced studies within the same write transaction as their replacement."""

from sqlalchemy import text


def retain_study(
    session, study_uid: str, replacement_received_at: str | None = None, *,
    reset: bool = False,
) -> tuple[set[str], set[str]]:
    """Keep one study and return discarded study UIDs and managed file IDs."""
    params = {"study": study_uid, "replacement": replacement_received_at}
    instances = "SELECT id FROM dicom_instances WHERE study_uid != :study"
    runs = "SELECT id FROM validation_runs WHERE study_uid != :study"
    acquisitions = "SELECT sha256 FROM logical_acquisitions WHERE study_uid != :study"
    results = f"SELECT id FROM validation_results WHERE run_id IN ({runs})"
    receptions = f"instance_id IN ({instances}) OR outcome = 'interrupted'"
    unfinished = "1=1" if reset else (
        "state IN ('receiving', 'staged') AND received_at < :replacement"
    )
    intake = (
        f"SELECT intake_id FROM reception_events WHERE {receptions} "
        f"UNION SELECT id FROM intake_records WHERE {unfinished}"
    )
    retired = set(session.scalars(text(
        "SELECT study_uid FROM dicom_instances WHERE study_uid != :study "
        "UNION SELECT study_uid FROM studies WHERE study_uid != :study"
    ), params))
    files = set(session.scalars(text(f"{instances} UNION {intake}"), params))
    if not retired and not files:
        return retired, files

    deletions = {
        "artifact_findings": f"result_id IN ({results})",
        "validation_issues": f"result_id IN ({results})",
        "validation_results": f"run_id IN ({runs})",
        "validation_runs": "study_uid != :study",
        "acquisition_members": f"acquisition_sha256 IN ({acquisitions})",
        "logical_acquisitions": "study_uid != :study",
        "metadata_snapshots": f"instance_id IN ({instances})",
        "history_events": "study_uid != :study",
        "pacs_exports": "study_uid != :study",
        "study_analytics": "study_uid != :study",
        "analysis_branches": "study_uid != :study",
        "series": "study_uid != :study",
        "studies": "study_uid != :study",
        "patients": "id NOT IN (SELECT patient_id FROM studies)",
        "system_events": f"correlation_id IN ({instances} UNION {intake})",
        "analysis_jobs": f"instance_id IN ({instances})",
        "reception_events": receptions,
        "intake_records": (
            "(state NOT IN ('receiving', 'staged') "
            "AND id NOT IN (SELECT intake_id FROM reception_events)) "
            f"OR ({unfinished})"
        ),
        "dicom_instances": "study_uid != :study",
    }
    connection = session.connection()
    connection.exec_driver_sql("PRAGMA secure_delete=ON")
    triggers = dict(connection.exec_driver_sql(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
    ).all())
    for table, condition in deletions.items():
        # Bypass only the corresponding append-only DELETE guard. SQLite DDL is
        # transactional here, so other processes never observe a missing trigger.
        name = f"{table}_no_delete"
        trigger = triggers.get(name)
        if trigger:
            connection.exec_driver_sql(f"DROP TRIGGER {name}")
        session.execute(text(f"DELETE FROM {table} WHERE {condition}"), params)
        if trigger:
            connection.exec_driver_sql(trigger)
    return retired, files
