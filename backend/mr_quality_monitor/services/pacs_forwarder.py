"""Queue, approve and forward series to external PACS via C-STORE.

Only active when ``config.pacs.enabled`` is True. All writes are transactional;
failed sends are recorded with an error_code and can be retried.
"""

import logging
from datetime import UTC, datetime
from threading import Event

from sqlalchemy import select

from ..config import PacsConfig
from ..database.models import PacsExport
from ..dicom.storage import Storage

LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PacsForwarder:
    """Manages the pacs_exports queue and C-STORE forwarding lifecycle."""

    def __init__(self, storage: Storage, pacs: PacsConfig) -> None:
        self.sessions = storage.sessions
        self.storage = storage
        self.pacs = pacs

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def queue_series(
        self,
        study_uid: str,
        series_uid: str,
        group_key: str,
        overall_status: str,
    ) -> int | None:
        """Add a series to the PACS export queue if PACS is enabled.

        Returns the new PacsExport.id, or None when PACS is disabled.
        """
        if not self.pacs.enabled:
            return None
        if overall_status == "GREEN" and self.pacs.auto_forward_clean:
            status = "queued"
        elif overall_status != "GREEN" and self.pacs.require_manual_approval_on_issues:
            status = "awaiting_approval"
        else:
            status = "queued"

        with self.sessions.begin() as session:
            # De-duplicate: same group_key already queued/sent → skip.
            existing = session.scalar(
                select(PacsExport).where(
                    PacsExport.group_key == group_key,
                    PacsExport.status.notin_(("failed",)),
                )
            )
            if existing is not None:
                return existing.id
            row = PacsExport(
                study_uid=study_uid,
                series_uid=series_uid,
                group_key=group_key,
                status=status,
            )
            session.add(row)
            session.flush()
            export_id = row.id
        LOGGER.info(
            "pacs_export_queued id=%s study=%s series=%s status=%s",
            export_id,
            study_uid,
            series_uid,
            status,
        )
        return export_id

    def approve(self, export_id: int) -> None:
        """Mark an awaiting_approval export as approved, ready for the next run_once."""
        with self.sessions.begin() as session:
            row = session.get(PacsExport, export_id)
            if row is None:
                raise ValueError(f"Unknown PACS export id={export_id}")
            if row.status != "awaiting_approval":
                raise ValueError(
                    f"Export id={export_id} is in state '{row.status}', not awaiting_approval"
                )
            row.status = "approved"
            row.approved_at = _utc_now()
        LOGGER.info("pacs_export_approved id=%s", export_id)

    def pending_approvals(self) -> list[dict]:
        """Return rows that are awaiting operator approval."""
        with self.sessions() as session:
            rows = session.scalars(
                select(PacsExport).where(PacsExport.status == "awaiting_approval")
            )
            return [
                {
                    "id": row.id,
                    "study_uid": row.study_uid,
                    "series_uid": row.series_uid,
                    "group_key": row.group_key,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    # ------------------------------------------------------------------
    # Forwarding loop (called by background thread)
    # ------------------------------------------------------------------

    def run_once(self, stopped: Event) -> int:
        """Send all queued/approved exports. Returns number of failures."""
        if not self.pacs.enabled:
            return 0
        with self.sessions() as session:
            pending = list(
                session.scalars(
                    select(PacsExport).where(PacsExport.status.in_(("queued", "approved")))
                )
            )
        failures = 0
        for row in pending:
            if stopped.is_set():
                break
            failures += self._send(row.id)
        return failures

    def _send(self, export_id: int) -> int:
        """Attempt a single C-STORE; update status accordingly. Returns 0/1."""
        try:
            from ..dicom.sender import send_dataset  # noqa: PLC0415 (lazy, avoids hard dep)
        except ImportError:
            LOGGER.error("pacs_send_unavailable: dicom.sender not importable")
            return 1

        with self.sessions() as session:
            row = session.get(PacsExport, export_id)
            if row is None or row.status not in ("queued", "approved"):
                return 0
            series_uid = row.series_uid

        # Collect all stored files for this series
        try:
            paths = self.storage.series_paths(series_uid)
        except Exception as exc:
            self._mark_failed(export_id, f"storage_error:{type(exc).__name__}")
            return 1

        try:
            for path in paths:
                send_dataset(path, self.pacs)
            with self.sessions.begin() as session:
                row = session.get(PacsExport, export_id)
                if row is not None:
                    row.status = "sent"
                    row.sent_at = _utc_now()
            LOGGER.info("pacs_export_sent id=%s series=%s", export_id, series_uid)
            return 0
        except Exception as exc:
            code = f"send_error:{type(exc).__name__}"
            LOGGER.error("pacs_export_failed id=%s code=%s", export_id, code)
            self._mark_failed(export_id, code)
            return 1

    def _mark_failed(self, export_id: int, code: str) -> None:
        with self.sessions.begin() as session:
            row = session.get(PacsExport, export_id)
            if row is not None:
                row.status = "failed"
                row.error_code = code
