"""Atomic local heartbeat; no DICOM/patient contents or network probes."""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

LOG = logging.getLogger(__name__)
HEALTH_FILE = "receiver_status.json"


class HealthPublisher:
    def __init__(self, root: Path, snapshot, *, interval: float = 2):
        self.root, self.snapshot, self.interval = root, snapshot, interval
        self.run_id = uuid4().hex
        self.stopped = threading.Event()
        self.thread = None
        self.write_lock = threading.Lock()

    def publish(self, state="listening") -> None:
        with self.write_lock:
            self._write(state)

    def _write(self, state) -> None:
        path = self.root / f".{self.run_id}.health.tmp"
        try:
            content = {
                "schema_version": 1,
                "run_id": self.run_id,
                "state": state,
                "heartbeat_at": datetime.now(UTC).isoformat(),
                **self.snapshot(),
            }
            path.write_text(json.dumps(content, allow_nan=False), encoding="utf-8")
            os.replace(path, self.root / HEALTH_FILE)
        except Exception:
            LOG.warning("receiver_heartbeat_write_failed")
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def start(self) -> None:
        self.publish()
        self.thread = threading.Thread(target=self._loop, name="receiver-heartbeat", daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            self.thread = None
            LOG.warning("receiver_heartbeat_thread_unavailable")

    def _loop(self) -> None:
        while not self.stopped.wait(self.interval):
            self.publish()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread is not None:
            self.thread.join()
        self.publish("stopped")


def read_health(root: Path, *, now: datetime | None = None, stale_after: float = 7) -> dict:
    """Freshness is a heartbeat observation, not an assertion of scanner connectivity."""
    try:
        with (root / HEALTH_FILE).open("rb") as stream:
            raw = stream.read(32769)
        if len(raw) > 32768:
            raise ValueError("Oversized health snapshot")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != 1
            or data.get("state") not in {"listening", "stopped"}
        ):
            raise ValueError("Unsupported health snapshot")
        stamp = datetime.fromisoformat(data["heartbeat_at"])
        if stamp.tzinfo is None:
            raise ValueError("Unzoned heartbeat")
        age = ((now or datetime.now(UTC)) - stamp).total_seconds()
        data["observed_state"] = "unavailable" if age < -2 or age > stale_after else data["state"]
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {"observed_state": "unknown"}
