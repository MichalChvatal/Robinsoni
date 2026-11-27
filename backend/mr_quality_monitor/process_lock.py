"""OS-released exclusive locks for independently running local services."""

import os
from pathlib import Path


class ProcessLock:
    def __init__(self, root: Path, service: str):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{service}.lock"
        self.service = service
        self.stream = None

    def acquire(self) -> None:
        stream = self.path.open("a+b")
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            raise RuntimeError(f"Another {self.service} owns this storage directory") from None
        self.stream = stream

    def release(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None


class ReceiverLock(ProcessLock):
    def __init__(self, root: Path):
        super().__init__(root, "receiver")
