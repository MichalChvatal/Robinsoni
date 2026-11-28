"""Independent Storage SCP, accepting only the configured peer and image profile."""

import logging
import threading
from datetime import UTC, datetime
from ipaddress import ip_address

from pynetdicom import AE, evt
from pynetdicom.sop_class import Verification
from sqlalchemy import func, select

from ..config import ReceiverConfig
from ..database.models import DicomInstance
from ..process_lock import ReceiverLock
from ..services.receiver_health import HealthPublisher
from .storage import (
    DISCARDED_STUDY_STATUS,
    STORAGE_CLASSES,
    TRANSFER_SYNTAXES,
    InvalidDicom,
    Storage,
)

LOG = logging.getLogger(__name__)


class Listener:
    """Owns intake resources and a non-GUI DICOM server.

    start() completes recovery before opening the TCP socket; stop() waits for
    active request handlers before disposing storage or releasing the OS lock.
    """

    def __init__(self, config: ReceiverConfig, *, protocols=()):
        self.config = config
        self.protocols = protocols
        self.process_lock = ReceiverLock(config.storage_root)
        self.storage: Storage | None = None
        self.server = None
        self.ae = None
        self.health = None
        self.stats_lock = threading.Lock()
        self.stats = {
            "unique_instances": 0,
            "last_received_at": None,
            "last_error": None,
            "last_error_at": None,
        }

    def start(self) -> None:
        if self.server is not None:
            raise RuntimeError("Listener is already running")
        self.process_lock.acquire()
        try:
            self.storage = Storage(
                self.config.storage_root,
                self.config.minimum_free_bytes,
                single_active_study=self.config.single_active_study,
                protocols=self.protocols,
            )
            recovered = self.storage.recover(reset_study=self.config.single_active_study)
            with self.storage.sessions() as session:
                self.stats = {
                    "unique_instances": session.scalar(
                        select(func.count()).select_from(DicomInstance)
                    ),
                    "last_received_at": session.scalar(select(func.max(DicomInstance.received_at))),
                    "last_error": None,
                    "last_error_at": None,
                }
            LOG.info("recovery_completed counts=%s", recovered)
            ae = AE(ae_title=self.config.ae_title)
            ae.require_called_aet = True
            ae.require_calling_aet = [self.config.allowed_calling_aet]
            ae.maximum_associations = self.config.maximum_associations
            ae.acse_timeout = self.config.network_timeout_seconds
            ae.dimse_timeout = self.config.network_timeout_seconds
            ae.network_timeout = self.config.network_timeout_seconds
            for sop_class in STORAGE_CLASSES:
                ae.add_supported_context(sop_class, TRANSFER_SYNTAXES)
            ae.add_supported_context(Verification)
            self.ae = ae
            self.server = ae.start_server(
                (str(self.config.host), self.config.port),
                block=False,
                evt_handlers=[
                    (evt.EVT_REQUESTED, self._association_requested),
                    (evt.EVT_C_STORE, self._store),
                    (evt.EVT_REJECTED, lambda _event: self._record_error("association_rejected")),
                    (evt.EVT_C_ECHO, lambda _event: 0x0000),
                ],
            )
            self.health = HealthPublisher(self.config.storage_root, self._health_snapshot)
            self.health.start()
            LOG.info("listener_started port=%d", self.config.port)
        except Exception:
            self.stop()
            raise

    def _association_requested(self, event) -> None:
        if ip_address(event.assoc.requestor.address) != self.config.allowed_peer_ip:
            event.assoc.acse.send_reject(0x01, 0x01, 0x01)
            LOG.warning("association_rejected_peer")
            self._record_error("association_rejected_peer")

    def _health_snapshot(self) -> dict:
        with self.stats_lock:
            snapshot = {
                **self.stats,
                "host": str(self.config.host),
                "port": self.config.port,
                "ae_title": self.config.ae_title,
                "active_associations": len(self.ae.active_associations) if self.ae else 0,
            }
        if self.config.single_active_study and self.storage is not None:
            self.storage.retry_discarded_cleanup()
            with self.storage.sessions() as session:
                snapshot["unique_instances"] = session.scalar(
                    select(func.count()).select_from(DicomInstance)
                )
        return snapshot

    def _record_error(self, code: str) -> None:
        with self.stats_lock:
            self.stats["last_error"] = code
            self.stats["last_error_at"] = datetime.now(UTC).isoformat()

    def _store(self, event) -> int:
        try:
            # Defence in depth: do not depend only on association event ordering.
            if (
                ip_address(event.assoc.requestor.address) != self.config.allowed_peer_ip
                or event.assoc.requestor.ae_title.strip() != self.config.allowed_calling_aet
            ):
                LOG.warning("store_rejected_peer")
                self._record_error("association_rejected_peer")
                return 0xC210
            dataset = event.dataset
            if (
                str(dataset.SOPInstanceUID) != str(event.request.AffectedSOPInstanceUID)
                or str(dataset.SOPClassUID) != str(event.request.AffectedSOPClassUID)
                or str(dataset.SOPClassUID) != str(event.context.abstract_syntax)
            ):
                raise InvalidDicom("command_dataset_mismatch")
            outcome = self.storage.receive(
                dataset,
                str(event.context.transfer_syntax),
                event.assoc.requestor.ae_title.strip(),
                event.assoc.requestor.address,
            )
            LOG.info("store_completed outcome=%s", outcome)
            if outcome == "discarded":
                return DISCARDED_STUDY_STATUS
            with self.stats_lock:
                if outcome == "stored":
                    self.stats["unique_instances"] += 1
                if outcome in {"stored", "duplicate"}:
                    self.stats["last_received_at"] = datetime.now(UTC).isoformat()
            if outcome == "conflict":
                self._record_error("conflicting_instance")
            return 0xC211 if outcome == "conflict" else 0x0000
        except (InvalidDicom, AttributeError, ValueError, EOFError):
            LOG.warning("store_invalid_dataset")
            self._record_error("store_invalid_dataset")
            return 0xC210
        except Exception as error:
            # Never log exception text/traceback: it can contain dataset values or PHI paths.
            LOG.error("store_persistence_failed type=%s", type(error).__name__)
            self._record_error("store_persistence_failed")
            return 0xA700

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            if self.ae is not None:
                associations = tuple(self.ae.active_associations)
                for association in associations:
                    association.abort()
                for association in associations:
                    association.join(timeout=self.config.network_timeout_seconds + 1)
                if any(association.is_alive() for association in associations):
                    raise RuntimeError("Receiver handlers have not stopped; storage lock retained")
            self.server.server_close()
            self.server = None
        if self.health is not None:
            self.health.stop()
            self.health = None
        if self.storage is not None:
            self.storage.close()
            self.storage = None
        self.process_lock.release()
        LOG.info("listener_stopped")
